"""Public entrypoints retain frozen study meaning, budgets, and data sources."""
from dataclasses import fields
import json
from pathlib import Path

import fastjsonschema
import pandas as pd
import pytest

from src.quant_research.automl import assets, cli, data, planning, runner, study
from src.quant_research.automl.config import ResearchSpec
from src.tools import stock_automl_research_tool as task_tool


@pytest.fixture(autouse=True)
def no_environment_loading(monkeypatch):
    # Entrypoint tests must not depend on local credentials or external services.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)


def research_plan():
    common = ResearchSpec(start="2024-01-01", end="2025-12-31", max_trials=5,
                          models=("linear", "tree"), tasks=("classification",),
                          samplers=("all",), feature_sets=("technical",),
                          horizons=(3,), company_holdout_fraction=0).to_dict()
    return planning.normalize_plan({
        "spec": common, "requirement_brief": "分别研究波动率与成交活跃度，共5次实验",
        "design": "两个用户研究角度，共享实验预算，分别留出验证。",
        "directions": [
            {"hypothesis": "历史波动率是否提供预测信息", "spec": {"feature_names": ["volatility_20"]}},
            {"hypothesis": "相对成交量是否提供预测信息", "spec": {"feature_names": ["volume_ratio"]}},
        ],
    })


def test_task_resume_rejects_changed_explicit_spec_with_the_same_requirement(tmp_path, monkeypatch):
    calls = []

    def frozen_study(plan, **kwargs):
        calls.append(plan)
        return kwargs["output_root"], {
            "requirement_brief": plan["requirement_brief"], "design": plan["design"],
            "trial_budget": plan["spec"]["max_trials"], "source": kwargs["source_name"], "strategies": [],
        }

    monkeypatch.setattr(study, "run_study", frozen_study)
    args = {"requirement_brief": "保持我的研究要求", "spec": research_plan()["spec"],
            "source": "demo", "llm_review": False}
    runtime = {"task_run_id": "run-entrypoint", "owner_user_id": "alice", "task_output_dir": str(tmp_path)}
    assert task_tool.run(args, runtime_ctx=runtime)["ok"]
    frozen = (tmp_path / "research_design.json").read_text()
    assert json.loads(frozen)["input_fingerprint"]
    assert task_tool.run(args, runtime_ctx=runtime)["ok"]  # Identical retries remain valid.
    changed = {**args, "spec": {**args["spec"], "target_return": .02}}
    with pytest.raises(ValueError, match="input differs from the frozen"):
        task_tool.run(changed, runtime_ctx=runtime)
    assert len(calls) == 2  # Changed inputs never reach research execution.
    assert (tmp_path / "research_design.json").read_text() == frozen


def test_cli_requirement_executes_all_directions_under_one_budget(tmp_path, monkeypatch, capsys):
    plan = research_plan()
    compiled, executed = [], []

    def compile_request(requirement):
        compiled.append(requirement)
        return plan

    def evaluate(spec, daily, events, **kwargs):
        executed.append(spec)
        raise ValueError("controlled evaluation failure; no model fitting in this entrypoint test")

    monkeypatch.setattr(planning, "compile_research", compile_request)
    monkeypatch.setattr(study, "load_market", lambda *_: (pd.DataFrame(), (), {"source": "synthetic_demo"}))
    monkeypatch.setattr(study, "run_research", evaluate)
    monkeypatch.setattr("sys.argv", ["automl", "--requirement", plan["requirement_brief"],
                                     "--demo", "--output", str(tmp_path)])
    # Real CLI -> real study -> allocated engine calls; failed execution stays failed.
    with pytest.raises(ValueError, match="所有研究方向执行失败"):
        cli.main()
    root = next(tmp_path.glob("study_*"))
    saved_plan = json.loads((root / "research_design.json").read_text())
    assert compiled == [plan["requirement_brief"]]
    assert len(saved_plan["directions"]) == len(executed) == 2
    assert [spec.max_trials for spec in executed] == [3, 2]
    assert sum(spec.max_trials for spec in executed) == plan["spec"]["max_trials"]
    assert [spec.feature_names for spec in executed] == [("volatility_20",), ("volume_ratio",)]
    assert all(plan["requirement_brief"] in spec.objective for spec in executed)


def test_cli_study_resume_uses_frozen_loader_options_for_unstarted_directions(tmp_path, monkeypatch, capsys):
    events_path = tmp_path / "historical-events.csv"
    pd.DataFrame({"symbol": ["000001"], "available_at": ["2024-05-01"], "local_factor": [2.5]}).to_csv(events_path, index=False)
    plan = research_plan()
    plan.update(source="kingdomai", loader_options={"db_env": "FROZEN_RESEARCH_DB", "events_csv": [str(events_path)]})
    (tmp_path / "research_design.json").write_text(json.dumps(plan))
    previous = tmp_path / "direction_1" / "previous-run"
    previous.mkdir(parents=True)
    (tmp_path / "research_checkpoint.json").write_text(json.dumps({"research_dirs": {"direction_1": "direction_1/previous-run"}}))
    loaded, executed = [], []

    def load(spec, *, env_name):
        loaded.append(env_name)
        return pd.DataFrame(), [], {"source": "kingdomai"}

    def evaluate(spec, daily, events, **kwargs):
        executed.append({"spec": spec, "events": events, "resume_dir": kwargs["resume_dir"]})
        raise ValueError("controlled evaluation failure")

    monkeypatch.setattr(data, "load_kingdom", load)
    monkeypatch.setattr(study, "run_research", evaluate)
    # Resume must use the saved source options, including when a later direction
    # has never loaded data. The conflicting CSV deliberately does not exist.
    monkeypatch.setattr("sys.argv", ["automl", "--resume", str(tmp_path),
                                     "--db-env", "CHANGED_DB", "--events-csv", str(tmp_path / "not-used.csv")])
    with pytest.raises(ValueError, match="所有研究方向执行失败"):
        cli.main()
    assert loaded == ["FROZEN_RESEARCH_DB"]
    assert executed[0]["resume_dir"] == previous
    assert not executed[0]["events"]  # The engine resumes its already-frozen panel.
    assert executed[1]["resume_dir"] is None
    frame = executed[1]["events"][0]
    assert frame.to_dict("records") == [{"symbol": "000001", "available_at": "2024-05-01", "local_factor": 2.5}]
    assert len(json.loads((tmp_path / "study.json").read_text())["strategies"]) == 2


def test_cli_legacy_single_run_resume_keeps_existing_engine_entrypoint(tmp_path, monkeypatch, capsys):
    spec = ResearchSpec(start="2024-01-01", end="2025-12-31", objective="旧版单次研究", max_trials=2)
    (tmp_path / "spec.json").write_text(json.dumps(spec.to_dict()))
    executed, exported = [], []

    def resume(value, **kwargs):
        executed.append((value, kwargs))
        return tmp_path, {"run_id": "legacy-run", "successful_trials": 2}

    def unexpected_study(*args, **kwargs):
        raise AssertionError("a legacy run must not be reinterpreted as a new multi-direction study")

    monkeypatch.setattr(runner, "run_research", resume)
    monkeypatch.setattr(study, "run_study", unexpected_study)
    monkeypatch.setattr(assets, "export_strategy", lambda root, **kwargs: exported.append((root, kwargs)))
    monkeypatch.setattr("sys.argv", ["automl", "--resume", str(tmp_path)])
    cli.main()
    assert executed[0][0] == spec
    assert executed[0][1] == {"resume_dir": tmp_path, "advisor": None}
    assert exported == [(tmp_path, {"hypothesis": spec.objective})]
    assert json.loads(capsys.readouterr().out) == {"run_id": "legacy-run", "successful_trials": 2}


def tool_spec_schema():
    definition = Path(__file__).parents[2] / "src/tools/definitions/stock_automl_research.tool.json"
    return json.loads(definition.read_text())["schemas"]["input"]["properties"]["spec"]


def test_tool_schema_accepts_new_execution_controls_and_covers_config():
    schema = tool_spec_schema()
    assert set(schema["properties"]) == {field.name for field in fields(ResearchSpec)}
    validate = fastjsonschema.compile(schema)
    for precision in (None, .75):
        value = {"start": "2024-01-01", "end": "2025-12-31", "max_symbols": None,
                 "feature_names": ["volatility_20", "volume_ratio"], "min_precision": precision,
                 "optimize_threshold": True, "min_signal_dates": 8, "company_holdout_fraction": 0}
        validate(value)
        spec = ResearchSpec.from_dict(value)
        assert spec.max_symbols is None and spec.company_holdout_fraction == 0
        assert spec.min_precision == precision and spec.min_signal_dates == 8


@pytest.mark.parametrize("invalid", [{"feature_names": "volume_ratio"}, {"min_precision": "high"},
                                      {"optimize_threshold": "true"}, {"min_signal_dates": 1.5},
                                      {"max_symbols": True}])
def test_tool_schema_rejects_wrong_types_before_execution(invalid):
    validate = fastjsonschema.compile(tool_spec_schema())
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        validate({"start": "2024-01-01", "end": "2025-12-31", **invalid})

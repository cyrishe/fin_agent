"""Business contract and recovery evidence, independent of task DB or Web process."""
import json
from pathlib import Path

import pytest

from src.quant_research.automl.advisor import ResearchAdvisor
from src.quant_research.automl.config import ResearchSpec
from src.quant_research.automl.demo import synthetic_market
from src.quant_research.automl.planning import compile_research, explicit_plan, UnsupportedResearchRequirement
from src.quant_research.automl.runner import run_research, review_research
from src.tools.stock_automl_research_tool import run


def minimal_spec(**kwargs):
    values = dict(start="2023-01-02", end="2023-12-29", models=("linear",), tasks=("regression",),
                  horizons=(1,), samplers=("all",), feature_sets=("technical",),
                  max_symbols=6, max_trials=2, rounds=1, folds=2)
    values.update(kwargs)
    return ResearchSpec(**values)


@pytest.fixture(scope="module")
def market():
    return synthetic_market(companies=6)


def test_domain_compiler_preserves_constraints_and_frozen_anchor():
    def complete(system, payload):
        assert payload["requirement_brief"] == "银行股3/7日，市值大于100亿元，最多5个模型实验"
        assert payload["default_spec"]["end"] == "2026-10-02"
        return json.dumps({"spec": {"industries": ["银行"], "horizons": [3, 7], "min_market_cap": 1e10,
                                    "max_trials": 5, "models": ["linear", "forest"]},
                           "design": "收盘决策、次日开盘持有到期；银行行业按历史生效区间过滤。", "unsupported_requirements": []})
    plan = compile_research("银行股3/7日，市值大于100亿元，最多5个模型实验", reference_time="2026-10-03T00:00:00+08:00", complete=complete)
    assert plan["spec"]["industries"] == ("银行",)
    assert plan["spec"]["horizons"] == (3, 7)
    assert plan["spec"]["max_trials"] == 5
    assert plan["spec"]["min_market_cap"] == 1e10
    assert plan["spec"]["objective"] == plan["requirement_brief"]


def test_domain_compiler_rejects_unsupported_and_unknown_contracts():
    with pytest.raises(UnsupportedResearchRequirement, match="盘中触及"):
        compile_research("要求盘中触及概率", complete=lambda *a: json.dumps({"spec": {}, "design": "x", "unsupported_requirements": ["盘中触及标签尚不支持"]}))
    with pytest.raises(UnsupportedResearchRequirement, match="shell_command"):
        compile_research("run", complete=lambda *a: json.dumps({"spec": {"shell_command": "bad"}, "design": "x"}))
    with pytest.raises(UnsupportedResearchRequirement, match="future_target"):
        explicit_plan({**minimal_spec().to_dict(), "future_target": "invented"})
    with pytest.raises(UnsupportedResearchRequirement, match="结束日期"):
        compile_research("run", reference_time="2026-10-03", complete=lambda *a: json.dumps({"spec": {"end": "2027-01-01"}, "design": "x"}))


def test_resume_reuses_completed_trials_round_picks_and_frozen_data(market, tmp_path, monkeypatch):
    import src.quant_research.automl.runner as runner
    daily, events = market
    calls, snapshots = [], []
    class Interrupted(Exception): pass
    def complete(system, payload):
        calls.append(payload)
        if "candidates" in payload:
            return json.dumps({"candidate_ids": [c["id"] for c in payload["candidates"][:2]], "analysis": "frozen"})
        return "saved review"
    def checkpoint(value):
        snapshots.append(value)
        if value["completed_trials"] == 1:
            raise Interrupted()
    with pytest.raises(Interrupted):
        run_research(minimal_spec(), daily, events, output_root=tmp_path, advisor=ResearchAdvisor(complete), checkpoint=checkpoint, progress=lambda _: None)
    root = Path(snapshots[-1]["research_dir"])
    state = json.loads((root / "checkpoint.json").read_text())
    first = state["results"][0]
    original = runner.assess_candidate
    evaluated = []
    def wrapped(*args, **kwargs):
        evaluated.append(args[2]["id"])
        return original(*args, **kwargs)
    monkeypatch.setattr(runner, "assess_candidate", wrapped)
    # No daily frame is supplied: resume must load its frozen panel.
    _, report = run_research(minimal_spec(), resume_dir=root, advisor=ResearchAdvisor(complete), progress=lambda _: None)
    assert report["attempted_trials"] == 2 and report["successful_trials"] == 2
    assert len(evaluated) == 1 and first["candidate"]["id"] not in evaluated
    assert sum("candidates" in call for call in calls) == 1
    assert json.loads((root / "checkpoint.json").read_text())["round_picks"] == state["round_picks"]
    assert first in json.loads((root / "development.json").read_text())["results"]
    assert report["sample_counts"]["panel_rows"] == len(daily)


def test_cancelled_fit_consumes_attempt_budget_without_rerunning(market, tmp_path, monkeypatch):
    import src.quant_research.automl.runner as runner
    original = runner.assess_candidate
    snapshots = []
    class Cancelled(Exception): pass
    monkeypatch.setattr(runner, "assess_candidate", lambda *a: (_ for _ in ()).throw(Cancelled()))
    with pytest.raises(Cancelled):
        run_research(minimal_spec(), *market, output_root=tmp_path, checkpoint=snapshots.append, progress=lambda _: None)
    root = Path(snapshots[-1]["research_dir"])
    monkeypatch.setattr(runner, "assess_candidate", original)
    _, report = run_research(minimal_spec(), resume_dir=root, progress=lambda _: None)
    assert report["attempted_trials"] == 2 and report["successful_trials"] == 1
    failures = json.loads((root / "development.json").read_text())["failures"]
    assert len(failures) == 1 and "budget remains consumed" in failures[0]["reason"]


@pytest.mark.parametrize("failure_at", [None, 6, 12])
def test_candidate_progress_is_current_at_round_and_completion_boundaries(market, tmp_path, monkeypatch, failure_at):
    import src.quant_research.automl.runner as runner
    updates, evaluated = [], []

    def assess(data, panel, candidate, spec, extra, check_cancel):
        # Before a fit starts, the UI must not count that in-flight candidate as complete.
        assert updates[-1]["completed"] == len(evaluated)
        evaluated.append(candidate["id"])
        if len(evaluated) == failure_at:
            raise ValueError("controlled unevaluable candidate")
        return {"candidate": candidate, "columns": runner.feature_columns(data, candidate["features"], extra),
                "folds": [], "score": .1, "meets_constraints": False, "constraint_checks": {}, "signal_count": 0}

    # Control candidate outcomes; final fitting, saved checkpoints and report delivery are real.
    monkeypatch.setattr(runner, "assess_candidate", assess)
    spec = minimal_spec(models=("linear", "tree"), horizons=(1, 3, 7), max_trials=12, rounds=2)
    root, report = run_research(spec, *market, output_root=tmp_path, progress=updates.append)
    planning = [event for event in updates if event["stage"] == "模型设计"]
    assert [(event.get("completed"), event.get("total")) for event in planning] == [(0, 12), (6, 12)]
    completions = [event["completed"] for event in updates if event["stage"] == "训练与验证"
                   and event["completed"] == event["attempted"]]
    assert completions == list(range(1, 13))
    assert next(event for event in updates if event["stage"] == "最终训练")["completed"] == 12
    assert updates[-1]["completed"] == report["attempted_trials"] == 12
    assert report["successful_trials"] == (11 if failure_at else 12)
    state = json.loads((root / "checkpoint.json").read_text())
    assert len(state["results"]) + len(state["failures"]) == 12
    assert len(state["failures"]) == (1 if failure_at else 0)


def test_completed_resume_and_review_never_retrain(market, tmp_path, monkeypatch):
    import src.quant_research.automl.runner as runner
    root, report = run_research(minimal_spec(), *market, output_root=tmp_path, progress=lambda _: None)
    def forbidden(*args, **kwargs):
        raise AssertionError("must not fit or reveal a new model")
    monkeypatch.setattr(runner, "fit_model", forbidden)
    monkeypatch.setattr(runner, "assess_candidate", forbidden)
    _, recovered = run_research(minimal_spec(), resume_dir=root, progress=lambda _: None)
    assert recovered["selected"] == report["selected"]
    bad = ResearchAdvisor(lambda *a: (_ for _ in ()).throw(RuntimeError("remote error")))
    assert review_research(root, bad)["completed"] is False
    payloads = []
    def reviewed(system, payload):
        payloads.append(payload)
        return "独立复核成功"
    assert review_research(root, ResearchAdvisor(reviewed))["completed"] is True
    context = payloads[0]["review_context"]
    assert "StandardScaler" in context["methodology"] and "Platt" in context["methodology"]
    assert "不是胜率超过常数基准" in context["constraint_meaning"]
    assert context["actually_evaluated"]["tasks"] == ["regression"]
    assert len(context["development_evidence"]) == 2
    assert json.loads((root / "review_evidence.json").read_text())["evaluation"] == json.loads((root / "report.json").read_text())["evaluation"]
    assert "独立复核成功" in (root / "report.md").read_text()
    assert json.loads((root / "report.json").read_text())["selected"] == report["selected"]


def test_resume_rejects_changed_spec_and_data(market, tmp_path):
    import pandas as pd
    root, _ = run_research(minimal_spec(), *market, output_root=tmp_path, progress=lambda _: None)
    with pytest.raises(ValueError, match="spec differs"):
        run_research(minimal_spec(max_trials=3), resume_dir=root)
    panel = pd.read_pickle(root / "panel.pkl")
    panel.loc[0, "adjclose"] *= 2
    panel.to_pickle(root / "panel.pkl")
    with pytest.raises(ValueError, match="fingerprint"):
        run_research(minimal_spec(), resume_dir=root)


def test_tool_authorized_demo_reports_and_resume(tmp_path):
    args = {"requirement_brief": "显式配置的合成流程验证", "spec": minimal_spec().to_dict(), "source": "demo", "llm_review": False}
    with pytest.raises(ValueError, match="authorized background"):
        run({**args, "_runtime": {"owner_user_id": "spoof"}})
    updates, checkpoints = [], []
    runtime = {"task_run_id": "run1", "owner_user_id": "alice", "task_output_dir": str(tmp_path),
               "task_progress": updates.append, "task_save_checkpoint": checkpoints.append}
    result = run(args, runtime_ctx=runtime)
    assert result["ok"] and "合成数据流程验证" in result["summary"]
    assert result["domain_result"]["sample_counts"]["companies"] == 6
    assert {"研究设计", "训练与验证", "盲测与回测", "完成"} <= {event["stage"] for event in updates}
    assert checkpoints and all(Path(a["path"]).is_relative_to(tmp_path) for a in result["artifacts"])
    assert next(event for event in updates if event["stage"] == "最终训练")["completed"] == 2
    assert not any(a["path"].endswith((".pkl", ".joblib", ".csv")) for a in result["artifacts"])
    assert result["domain_result"]["sample_counts"]["development"]["fit_rows"] > 0
    metrics = {metric["label"]: metric for metric in result["metrics"]}
    assert metrics["候选模型"]["value"] == "Ridge 收益回归"
    assert metrics["持有周期"]["value"] == 1
    for label, evidence in (("原公司新时段", result["domain_result"]["evaluation"]["new_period"]),
                            ("新公司新时段", result["domain_result"]["evaluation"]["new_companies_and_period"])):
        win = evidence["prediction"]["signal_win_rate_after_cost"]
        assert metrics[label + "扣成本信号胜率"]["value"] == (round(win * 100, 2) if win is not None else "无信号")
        assert metrics[label + "组合最大回撤"]["value"] == round(evidence["portfolio"]["max_drawdown"] * 100, 3)
    again = run(args, runtime_ctx=runtime)
    assert again["domain_result"]["selected"] == result["domain_result"]["selected"]
    # A new lease gets a copied isolated attempt directory, not the old path.
    import shutil
    copied = tmp_path.parent / (tmp_path.name + "-new-claim")
    shutil.copytree(tmp_path, copied)
    recovered = run(args, runtime_ctx={**runtime, "task_output_dir": str(copied), "task_checkpoint": checkpoints[-1]})
    assert recovered["domain_result"]["selected"] == result["domain_result"]["selected"]
    assert all(Path(a["path"]).is_relative_to(copied) for a in recovered["artifacts"])


def test_tool_rejects_unauthorized_resume_path(tmp_path):
    (tmp_path / "research_checkpoint.json").write_text(json.dumps({"research_dir": "/tmp/other-user"}))
    with pytest.raises(ValueError, match="outside the authorized"):
        run({"requirement_brief": "explicit", "spec": minimal_spec().to_dict(), "source": "demo", "llm_review": False},
            runtime_ctx={"task_run_id": "run1", "owner_user_id": "alice", "task_output_dir": str(tmp_path)})


def test_industry_universe_is_selected_before_random_sampling(monkeypatch):
    from src.quant_research.automl.data import select_symbols
    calls = []
    def query(conn, sql, args):
        calls.append((sql, args))
        return [{"stk_code": f"60000{i}.SH"} for i in range(8)]
    monkeypatch.setattr("src.quant_research.automl.data.query", query)
    chosen = select_symbols(None, {"kcrp_stock_industry": {}}, minimal_spec(industries=("银行",), max_symbols=5))
    assert len(chosen) == 5 and len(set(chosen)) == 5
    assert "EXISTS" in calls[0][0] and "i.industry_name IN (%s)" in calls[0][0]
    assert calls[0][1] == ("2023-01-02", "2023-01-02", "2023-01-02", "2023-01-02", "银行")
    with pytest.raises(ValueError, match="historical industry"):
        select_symbols(None, {}, minimal_spec(industries=("银行",)))

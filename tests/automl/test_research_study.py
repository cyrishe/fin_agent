"""Several user-directed strategies share budget while keeping independent evidence."""
from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from src.quant_research.automl.config import ResearchSpec
from src.quant_research.automl.demo import synthetic_market
from src.quant_research.automl.features import build_panel, labeled_panel
from src.quant_research.automl.planning import normalize_plan
from src.quant_research.automl.study import allocated_directions, run_study


@pytest.fixture(scope="module")
def market():
    daily, events = synthetic_market(companies=4, sessions=260)
    daily.attrs["market_calendar"] = daily.date.drop_duplicates().dt.strftime("%Y-%m-%d").tolist()
    return daily, events


def plan(*, max_trials=3, first_patch=None):
    common = ResearchSpec(start="2023-01-02", end="2023-12-29", max_trials=max_trials, rounds=1,
        folds=1, models=("linear", "tree"), tasks=("regression",), horizons=(1,),
        samplers=("all",), feature_sets=("technical",), max_train_rows=500, min_signals=3,
        min_signal_dates=2, min_precision=.5, optimize_threshold=True)
    return normalize_plan({"requirement_brief": "分别研究趋势和波动，保留各自的策略与证据。",
        "design": "两个方向独立训练，实验预算共用；不按最终测试成绩挑统一赢家。", "spec": common.to_dict(),
        "directions": [
            {"hypothesis": "趋势收益关系", "spec": {"models": ["linear"],
                "feature_names": ["return_1", "return_7", "ma_distance"], **(first_patch or {})}},
            {"hypothesis": "波动与量能的局部条件", "spec": {"models": ["tree"],
                "feature_names": ["volatility_20", "volume_ratio"]}},
        ]})


def loader_for(market, calls):
    def load(spec, source_name):
        calls.append((spec, source_name))
        return *market, {"source": "synthetic_test", "warnings": ["Synthetic test only."]}
    return load


def read(path):
    return json.loads(Path(path).read_text())


def test_shared_trial_budget_is_split_without_multiplying_it():
    allocation = allocated_directions(plan(max_trials=5))
    assert [spec.max_trials for _, spec in allocation] == [3, 2]
    assert sum(spec.max_trials for _, spec in allocation) == 5
    assert [direction["id"] for direction, _ in allocation] == ["direction_1", "direction_2"]


def test_study_trains_independent_assets_and_reuses_identical_market_loading(market, tmp_path):
    calls, progress = [], []
    root, result = run_study(plan(), source_name="demo", output_root=tmp_path,
                             market_loader=loader_for(market, calls), progress=progress.append)
    assert result["trial_budget"] == 3 and len(calls) == 1
    assert [s["trial_budget"] for s in result["strategies"]] == [2, 1]
    assert sum(s["report"]["attempted_trials"] for s in result["strategies"]) == 3
    assert len({s["strategy_id"] for s in result["strategies"]}) == 2
    assert len({s["research_dir"] for s in result["strategies"]}) == 2
    assert [s["report"]["selected"]["model"] for s in result["strategies"]] == ["linear", "tree"]
    for entry in result["strategies"]:
        directory = root / entry["research_dir"]
        assert directory.is_relative_to(root)
        assert (directory / "model.joblib").is_file()
        asset = read(directory / "strategy.json")
        assert asset["hypothesis"] == entry["hypothesis"]
        assert asset["strategy_id"] == entry["strategy_id"]
        assert read(directory / "spec.json")["max_trials"] == entry["trial_budget"]
        assert read(directory / "selection.json")["frozen_policy"] == asset["decision_policy"]
        assert (directory / "explanation.json").is_file() and (directory / "report.md").is_file()
    planning = [event for event in progress if event["stage"] == "模型设计"]
    assert [(event["completed"], event["total"]) for event in planning] == [(0, 3), (2, 3)]
    assert (root / "study.md").is_file() and (root / "study_plan.json").is_file()
    assert read(root / "study.json")["strategies"][0]["strategy_id"] == result["strategies"][0]["strategy_id"]


def test_completed_study_resume_never_reloads_retrains_or_exports(market, tmp_path, monkeypatch):
    import src.quant_research.automl.assets as assets
    import src.quant_research.automl.study as study_module
    root, original = run_study(plan(max_trials=2), source_name="demo", output_root=tmp_path,
                               market_loader=loader_for(market, []))
    def forbidden(*args, **kwargs):
        raise AssertionError("a completed study must reuse saved assets")
    monkeypatch.setattr(study_module, "run_research", forbidden)
    monkeypatch.setattr(assets, "export_strategy", forbidden)
    _, resumed = run_study(plan(max_trials=2), source_name="demo", output_root=root, market_loader=forbidden)
    assert resumed == read(root / "study.json")
    assert [s["strategy_id"] for s in resumed["strategies"]] == [s["strategy_id"] for s in original["strategies"]]


def test_interrupted_direction_resumes_frozen_candidates_without_rerunning_completed_trials(market, tmp_path, monkeypatch):
    import src.quant_research.automl.runner as runner
    snapshots, loader_calls = [], []
    class Interrupted(Exception):
        pass
    def checkpoint(snapshot):
        snapshots.append(deepcopy(snapshot))
        reference = snapshot.get("research_dirs", {}).get("direction_1")
        if reference and len(read(tmp_path / reference / "checkpoint.json")["results"]) == 1:
            raise Interrupted()
    with pytest.raises(Interrupted):
        run_study(plan(max_trials=4), source_name="demo", output_root=tmp_path,
                  market_loader=loader_for(market, loader_calls), checkpoint=checkpoint)
    first_root = tmp_path / snapshots[-1]["research_dirs"]["direction_1"]
    first_candidate = read(first_root / "checkpoint.json")["results"][0]["candidate"]["id"]
    original_assess, assessed = runner.assess_candidate, []
    def assess(*args, **kwargs):
        assessed.append(args[2]["id"])
        return original_assess(*args, **kwargs)
    monkeypatch.setattr(runner, "assess_candidate", assess)
    _, result = run_study(plan(max_trials=4), source_name="demo", output_root=tmp_path,
                         market_loader=loader_for(market, loader_calls), saved=snapshots[-1])
    assert first_candidate not in assessed
    assert len(assessed) == 3
    assert len(loader_calls) == 2  # Initial direction, then the not-yet-started second direction.
    assert sum(s["report"]["attempted_trials"] for s in result["strategies"]) == 4
    assert result["strategies"][0]["research_dir"] == str(first_root.relative_to(tmp_path))


def test_failed_direction_keeps_its_evidence_and_does_not_discard_other_strategy(market, tmp_path):
    progress = []
    root, result = run_study(plan(max_trials=4, first_patch={"feature_names": ["missing_feature"]}),
        source_name="demo", output_root=tmp_path, market_loader=loader_for(market, []), progress=progress.append)
    failed, successful = result["strategies"]
    assert "no evaluable candidates" in failed["error"]
    failures = read(root / failed["research_dir"] / "development.json")["failures"]
    assert len(failures) == 2 and all("missing_feature" in item["reason"] for item in failures)
    assert failed["attempted_trials"] == 2
    second_planning = [event for event in progress if event["stage"] == "模型设计" and event["direction_id"] == "direction_2"]
    assert second_planning[0]["completed"] == 2 and second_planning[0]["total"] == 4
    assert "strategy_id" not in failed
    assert successful["report"]["successful_trials"] == 2
    assert (root / successful["research_dir"] / "strategy.json").is_file()
    assert "未产出模型" in (root / "study.md").read_text()


def test_resume_rejects_changed_direction_or_budget_even_when_original_words_are_identical(market, tmp_path):
    original = plan(max_trials=2)
    run_study(original, source_name="demo", output_root=tmp_path, market_loader=loader_for(market, []))
    changed = deepcopy(original)
    changed["directions"][0]["spec"]["feature_names"] = ["return_3"]
    with pytest.raises(ValueError, match="differs from frozen research plan"):
        run_study(changed, source_name="demo", output_root=tmp_path)
    with pytest.raises(ValueError, match="differs from frozen research plan"):
        run_study(plan(max_trials=4), source_name="demo", output_root=tmp_path)
    with pytest.raises(ValueError, match="differs from frozen research plan"):
        run_study(original, source_name="kingdomai", output_root=tmp_path)


def test_study_checkpoint_cannot_reference_another_task_directory(tmp_path):
    with pytest.raises(ValueError, match="outside the authorized"):
        run_study(plan(), source_name="demo", output_root=tmp_path,
                  saved={"research_dirs": {"direction_1": "../another-task"}})


def test_single_company_missing_session_is_not_compressed_when_market_calendar_is_provided():
    daily, events = synthetic_market(companies=1, sessions=150)
    calendar = daily.date.dt.strftime("%Y-%m-%d").tolist()
    signal, missing = daily.date.iloc[50], daily.date.iloc[52]
    prices = daily[daily.date != missing].copy()
    prices.attrs["market_calendar"] = calendar
    panel = build_panel(prices, events)
    assert len(panel) == 150
    missing_row = panel[panel.date == missing]
    assert len(missing_row) == 1 and pd.isna(missing_row.adjopen.iloc[0])
    assert not (labeled_panel(panel, 1).date == signal).any()
    # The explicit calendar argument takes priority and is also useful for provided frames.
    prices.attrs["market_calendar"] = []
    explicit = build_panel(prices, events, market_calendar=calendar)
    pd.testing.assert_frame_equal(panel, explicit)


def test_all_execution_failures_are_not_successful_research(tmp_path):
    from src.quant_research.automl.planning import explicit_plan
    plan = explicit_plan({'start': '2023-01-02', 'end': '2023-12-29', 'max_trials': 1}, '数据不可读取')
    def unavailable(*_):
        raise ValueError('required data unavailable')
    with pytest.raises(ValueError, match='所有研究方向执行失败'):
        run_study(plan, source_name='demo', output_root=tmp_path, market_loader=unavailable)
    saved = json.loads((tmp_path / 'study.json').read_text())
    assert saved['strategies'][0]['attempted_trials'] == 0
    assert 'required data unavailable' in saved['strategies'][0]['error']

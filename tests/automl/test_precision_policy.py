"""Precision policy contracts: target fidelity, temporal support and frozen decisions."""
from types import SimpleNamespace
import json

import numpy as np
import pandas as pd
import pytest

from src.quant_research.automl.config import ResearchSpec
from src.quant_research.automl.decision import choose_decision_policy, decision_policy_rank
from src.quant_research.automl.demo import synthetic_market
from src.quant_research.automl.evaluation import backtest_predictions, prediction_metrics, selected, selection_metrics
from src.quant_research.automl.features import build_panel, labeled_panel
from src.quant_research.automl.learning import training_partition
from src.quant_research.automl import runner


def spec(**overrides):
    values = ResearchSpec(start="2023-01-01", end="2024-01-01").to_dict()
    values.update(min_precision=.75, min_signal_dates=3, min_signals=3, optimize_threshold=True, top_k=10)
    values.update(overrides)
    return SimpleNamespace(**values)


def examples():
    return pd.DataFrame({
        "date": pd.to_datetime(["2023-01-02", "2023-01-03", "2023-01-04", "2023-02-01", "2023-02-02", "2023-02-03"]),
        "symbol": ["A", "A", "B", "B", "C", "C"],
        "prediction": [.95, .9, .85, .6, .5, .4],
        "forward_return": [.02, .03, .04, -.02, .01, -.03],
    })


def test_target_precision_is_not_after_cost_win_rate_or_overall_accuracy():
    frame = examples()
    settings = spec(target_return=.025)
    metrics = prediction_metrics(frame, "classification", settings, .5, policy={"threshold": .6, "top_k": 10})
    assert metrics["target_precision"] == .5  # .02 is profitable but does not meet the target.
    assert metrics["signal_win_rate_after_cost"] == .75
    assert [metrics[k] for k in ("true_positive", "false_positive", "false_negative", "true_negative")] == [2, 2, 0, 2]
    assert metrics["positive_rows"] == 2 and metrics["negative_rows"] == 4
    assert metrics["actual_down_count"] == 1 and metrics["actual_down_rate"] == .25
    assert metrics["coverage"] == pytest.approx(4 / 6)
    assert metrics["precision_lift"] == 1.5
    assert metrics["signal_dates"] == 4


def test_no_signal_period_is_retained_and_no_precision_is_invented():
    frame = examples()
    metrics = prediction_metrics(frame, "classification", spec(), .5, policy={"threshold": 1., "top_k": 10})
    for key in ("target_precision", "actual_down_rate", "precision_lift", "date_macro_precision", "worst_active_month_precision"):
        assert metrics[key] is None
    assert metrics["signal_count"] == 0 and metrics["coverage"] == 0
    assert len(metrics["by_month"]) == 2
    assert all(m["n"] == 0 and m["target_precision"] is None for m in metrics["by_month"])
    assert metrics["false_negative"] == 4


def test_development_threshold_finds_small_precise_region_and_freezes_it():
    frame = examples()
    original = frame.copy(deep=True)
    policy = choose_decision_policy(frame, "classification", spec())
    assert policy["eligible"]
    assert policy["threshold"] == .85
    assert policy["metrics"]["signal_count"] == 3
    assert policy["metrics"]["target_precision"] == 1
    # The February block has no signals. It does not make January's adequate support fail.
    assert policy["metrics"]["by_month"][1]["n"] == 0
    assert policy["search_trials"] > 1
    future = frame.copy()
    future["forward_return"] *= -1
    first_picks = selected(future, "classification", spec(probability_threshold=.99), policy=policy)
    assert first_picks.prediction.tolist() == [.95, .9, .85]
    assert prediction_metrics(future, "classification", spec(), .5, policy=policy)["target_precision"] == 0
    assert policy["threshold"] == .85 and policy["metrics"]["target_precision"] == 1
    pd.testing.assert_frame_equal(frame, original)


def test_large_same_day_cluster_does_not_establish_temporal_support():
    frame = pd.DataFrame({"date": pd.to_datetime(["2023-01-02"] * 100),
                          "symbol": [f"S{i}" for i in range(100)],
                          "prediction": [.9] * 100, "forward_return": [.1] * 100})
    policy = choose_decision_policy(frame, "classification", spec(top_k=100, min_signals=50))
    assert policy["metrics"]["target_precision"] == 1
    assert not policy["eligible"]
    assert not policy["checks"]["enough_signal_dates"]


def test_daily_macro_precision_exposes_one_successful_large_cluster():
    frame = pd.DataFrame({"date": pd.to_datetime(["2023-01-02"] * 100 + ["2023-01-03", "2023-01-04"]),
                          "symbol": [f"S{i}" for i in range(102)],
                          "prediction": [.9] * 102, "forward_return": [.1] * 100 + [-.1, -.1]})
    policy = choose_decision_policy(frame, "classification", spec(top_k=100))
    assert policy["metrics"]["target_precision"] == pytest.approx(100 / 102)
    assert policy["metrics"]["date_macro_precision"] == pytest.approx(1 / 3)
    assert not policy["eligible"]
    assert "not independent-trial confidence" in policy["metrics"]["precision_evidence_note"]


def test_too_few_total_signals_cannot_qualify():
    policy = choose_decision_policy(examples(), "classification", spec(min_signals=20))
    assert not policy["eligible"]
    assert not policy["checks"]["enough_signals"]


def test_regression_predictions_use_same_event_target_precision():
    frame = examples()
    frame["prediction"] = [.03, .02, .01, -.01, -.02, -.03]
    policy = choose_decision_policy(frame, "regression", spec())
    assert selected(frame, "regression", spec(), policy=policy).prediction.tolist() == [.03, .02, .01]
    assert policy["eligible"] and policy["metrics"]["target_precision"] == 1
    metrics = prediction_metrics(frame, "regression", spec(), 0, policy=policy)
    assert "rmse" in metrics and metrics["true_positive"] == 3


def test_frozen_top_k_applies_per_date_with_deterministic_symbol_ties():
    frame = examples().iloc[:3].copy()
    frame["date"] = pd.Timestamp("2023-01-02")
    frame["symbol"] = ["C", "B", "A"]
    frame["prediction"] = .9
    policy = {"threshold": .8, "top_k": 1}
    assert selected(frame, "classification", spec(), policy=policy).symbol.tolist() == ["A"]
    assert selection_metrics(frame, "classification", spec(), policy=policy)["signal_count"] == 1
    # Existing callers retain their original, uncapped threshold metrics.
    assert len(selected(frame, "classification", spec(top_k=1))) == 3


def test_backtest_uses_frozen_threshold_and_daily_limit():
    daily, events = synthetic_market(companies=3, sessions=40)
    panel = build_panel(daily, events)
    scored = labeled_panel(panel, 1)
    scored["prediction"] = .9
    candidate = {"task": "classification", "horizon": 1}
    settings = spec(top_k=1)
    result = backtest_predictions(scored, panel, candidate, settings, policy={"threshold": .8, "top_k": 2})
    assert max(len(s["positions"]) for s in result["daily_snapshots"]) == 2
    empty = backtest_predictions(scored, panel, candidate, settings, policy={"threshold": .95, "top_k": 2})
    assert empty["metrics"]["trade_count"] == 0


def test_fixed_threshold_and_none_precision_are_compatible():
    policy = choose_decision_policy(examples(), "classification", spec(optimize_threshold=False, min_precision=None))
    assert policy["threshold"] == .6 and policy["search_trials"] == 1
    assert np.isfinite(np.asarray(decision_policy_rank(policy), dtype=float)).all()


@pytest.mark.parametrize("column", ["prediction", "forward_return"])
def test_nonfinite_development_data_is_rejected(column):
    frame = examples()
    frame.loc[0, column] = np.nan
    with pytest.raises(ValueError, match="finite"):
        choose_decision_policy(frame, "classification", spec())


def test_empty_development_data_is_rejected():
    with pytest.raises(ValueError, match="requires development"):
        choose_decision_policy(examples().iloc[:0], "classification", spec())


def test_final_policy_partition_is_excluded_from_fit_and_calibration_and_explained(monkeypatch, tmp_path):
    daily, events = synthetic_market(companies=3, sessions=220)
    settings = ResearchSpec(start="2023-01-01", end="2024-01-01", min_precision=0., optimize_threshold=True,
                            min_signal_dates=1, min_signals=1, min_win_rate=0., max_trials=2, rounds=2, folds=2,
                            models=("linear",), tasks=("classification",), horizons=(1,), samplers=("all",),
                            feature_sets=("technical",), max_train_rows=200)
    original_fit = runner.fit_model
    partitions = []
    planning, reviews = [], []

    def observed_fit(train, candidate, columns, config):
        fit, calibration, _ = training_partition(train, candidate, config)
        partitions.append((train.copy(), fit, calibration))
        return original_fit(train, candidate, columns, config)

    class Advisor:
        def plan(self, config, candidates, previous, budget):
            planning.append(previous)
            return [candidates[0]["id"]], "继续开发期证据支持的候选"

        def review(self, evidence):
            reviews.append(evidence)
            return "按已冻结门槛审阅目标精确率与扣成本信号胜率。"

    monkeypatch.setattr(runner, "fit_model", observed_fit)
    root, report = runner.run_research(settings, daily, events, output_root=tmp_path, advisor=Advisor(), progress=lambda _: None)
    stage = report["sample_counts"]["policy_selection"]
    start, end = pd.Timestamp(stage["signal_start"]), pd.Timestamp(stage["signal_end"])
    final_input, final_fit, final_calibration = partitions[-1]
    assert final_calibration is not None
    for frame in (final_input, final_fit, final_calibration):
        assert frame.date.max() < start and frame.label_end.max() < start
    assert end < pd.Timestamp(report["sample_counts"]["test_start"])
    assert len(partitions) == settings.max_trials * settings.folds + 1  # No refit after freezing the policy.
    assert planning[1][0]["decision_policy"]["metrics"]["target_precision"] is not None
    assert "signal_win_rate_after_cost" in planning[1][0]["decision_policy"]["metrics"]
    assert "evaluation" not in planning[1][0]
    context = reviews[0]["review_context"]
    assert context["frozen_policy"]["threshold"] == report["decision_policy"]["threshold"]
    assert "最终模型拟合及其内部概率校准均只使用此前" in context["methodology"]
    assert "不是胜率超过常数基准" in context["constraint_meaning"]
    assert "扣成本信号胜率" in context["constraint_meaning"]
    assert "最终门槛冻结于开发选择段" in context["fact_summary"]
    assert "两者是不同事件" in context["fact_summary"]
    assert (root / "policy_selection_backtest.json").exists()

    # Test outcomes cannot change candidate selection, final model fit or its frozen policy.
    poisoned = daily.copy()
    manifest = json.loads((root / "manifest.json").read_text())
    changed = poisoned.symbol.isin(manifest["heldout_companies"]) | (poisoned.date >= pd.Timestamp(manifest["test_start"]))
    poisoned.loc[changed, ["adjopen", "adjhigh", "adjlow", "adjclose"]] *= 2
    root2, _ = runner.run_research(settings, poisoned, events, output_root=tmp_path, progress=lambda _: None)
    first_selection = json.loads((root / "selection.json").read_text())
    second_selection = json.loads((root2 / "selection.json").read_text())
    assert first_selection["candidate"] == second_selection["candidate"]
    assert first_selection["frozen_policy"] == second_selection["frozen_policy"]


@pytest.mark.parametrize("failure", ["net_signal_win_rate_at_least_minimum", "drawdown_within_limit"])
def test_final_policy_must_pass_cost_and_drawdown_constraints(monkeypatch, tmp_path, failure):
    daily, events = synthetic_market(companies=3, sessions=180)
    settings = ResearchSpec(start="2023-01-01", end="2024-01-01", min_precision=0., optimize_threshold=False,
                            probability_threshold=0., min_signal_dates=1, min_signals=1, min_win_rate=.5,
                            max_trials=1, rounds=1, folds=1, models=("linear",), tasks=("classification",),
                            horizons=(1,), samplers=("all",), feature_sets=("technical",))
    original_metrics, original_backtest = runner.prediction_metrics, runner.backtest_predictions
    calls = {"metrics": 0, "backtest": 0}

    def metrics(*args, **kwargs):
        result = original_metrics(*args, **kwargs)
        calls["metrics"] += 1
        result["signal_win_rate_after_cost"] = 0. if calls["metrics"] == 2 and failure.startswith("net_") else 1.
        return result

    def backtest(*args, **kwargs):
        result = original_backtest(*args, **kwargs)
        calls["backtest"] += 1
        result["metrics"]["max_drawdown"] = -.99 if calls["backtest"] == 2 and failure.startswith("drawdown") else -.01
        return result

    monkeypatch.setattr(runner, "prediction_metrics", metrics)
    monkeypatch.setattr(runner, "backtest_predictions", backtest)
    root, report = runner.run_research(settings, daily, events, output_root=tmp_path, progress=lambda _: None)
    selection = json.loads((root / "selection.json").read_text())
    assert selection["meets_constraints"]  # OOF candidate passed, but its final frozen policy did not.
    assert not selection["frozen_policy"]["checks"][failure]
    assert not report["development_constraint_checks"]["final_policy_" + failure]
    assert not report["development_constraints_met"]

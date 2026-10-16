import json

import numpy as np
import pandas as pd
import pytest

from src.quant_research.automl.advisor import ResearchAdvisor
from src.quant_research.automl.config import ResearchSpec
from src.quant_research.automl.data import asof_features, minute_features
from src.quant_research.automl.demo import synthetic_market
from src.quant_research.automl.evaluation import backtest_predictions, prediction_metrics
from src.quant_research.automl.features import build_panel, labeled_panel, feature_columns
from src.quant_research.automl.learning import fit_model, temporal_folds
from src.quant_research.automl.runner import run_research


@pytest.fixture(scope="module")
def market():
    return synthetic_market()


def spec(**kwargs):
    return ResearchSpec(start="2023-01-02", end="2024-01-01", **kwargs)


def test_protocol_compatibility_and_rejections():
    s = ResearchSpec.from_dict({"start": "2023-01-01", "end": "2024-01-01", "horizons": [1,3], "comment": "optional"})
    assert s.horizons == (1,3)
    for kwargs in ({"horizons": (0,)}, {"models": ("shell",)}, {"max_trials": 201}, {"test_fraction": 0},
                   {"slippage_rate": -1}, {"target_return": float("nan")}):
        with pytest.raises(ValueError):
            spec(**kwargs)


def test_asof_never_future_or_overwrite():
    panel = pd.DataFrame({"symbol": ["A", "A"], "date": pd.to_datetime(["2023-01-01", "2023-01-03"]),
                          "decision_at": pd.to_datetime(["2023-01-01 15:10", "2023-01-03 15:10"])})
    event = pd.DataFrame({"symbol": ["A"], "available_at": ["2023-01-02"], "heat": [5.]})
    result = asof_features(panel, [event])
    assert pd.isna(result.heat.iloc[0]) and result.heat.iloc[1] == 5
    with pytest.raises(ValueError):
        asof_features(panel, [event.rename(columns={"heat": "date"})])
    with pytest.raises(ValueError):
        asof_features(panel, [pd.concat([event,event])])


def test_features_invariant_to_future(market):
    daily, events = market
    before = build_panel(daily, events)
    cutoff = pd.Timestamp("2023-08-01")
    changed = daily.copy()
    changed.loc[changed.date > cutoff, ["adjopen", "adjclose", "adjhigh", "adjlow"]] *= 100
    after = build_panel(changed, events)
    pd.testing.assert_frame_equal(before[before.date <= cutoff], after[after.date <= cutoff])


def test_horizon_is_market_days_and_purged(market):
    daily, events = market
    panel = build_panel(daily, events)
    labeled = labeled_panel(panel, 3)
    row = labeled.iloc[0]
    g = panel[panel.symbol == row.symbol].reset_index(drop=True)
    pos = g.index[g.date == row.date][0]
    assert row.forward_return == pytest.approx(g.adjopen.iloc[pos+4] / g.adjopen.iloc[pos+1] - 1)
    for train, validation in temporal_folds(labeled, 3):
        assert train.label_end.max() < validation.date.min()
    missing_day = g.date.iloc[pos+2]
    missing = daily[~((daily.symbol == row.symbol) & (daily.date == missing_day))]
    rebuilt = labeled_panel(build_panel(missing, events), 3)
    assert not ((rebuilt.symbol == row.symbol) & (rebuilt.date == row.date)).any()


@pytest.mark.parametrize("task", ["classification", "regression"])
@pytest.mark.parametrize("name", ["linear", "elastic_net", "tree", "forest", "svm", "hist_gradient_boosting"])
def test_each_estimator_runs_with_temporal_calibration(market, task, name):
    daily, events = market
    data = labeled_panel(build_panel(daily, events), 1)
    cutoff = pd.Timestamp("2023-10-01")
    train = data[data.label_end < cutoff]
    test = data[data.date >= cutoff]
    candidate = {"task": task, "model": name}
    model = fit_model(train, candidate, feature_columns(data, "enriched"), spec(max_train_rows=500))
    prediction = model.predict(test)
    assert np.isfinite(prediction).all()
    if task == "classification":
        assert ((prediction >= 0) & (prediction <= 1)).all()


def test_backtest_next_open_fees_and_no_overlap(market):
    daily, events = market
    panel = build_panel(daily, events)
    data = labeled_panel(panel, 3)
    data = data[data.date.between("2023-10-02", "2023-10-20")].copy()
    data["prediction"] = .8
    c = {"task": "classification", "horizon": 3}
    bt = backtest_predictions(data, panel, c, spec(top_k=2))
    assert bt["trades"][0]["date"] > str(data.date.min().date())
    assert float(bt["metrics"]["total_commission"]) > 0
    assert all(float(s["cash"]) >= 0 for s in bt["daily_snapshots"])
    assert all(len(s["positions"]) <= 2 for s in bt["daily_snapshots"])
    expensive = backtest_predictions(data, panel, c, spec(top_k=2, commission_rate=.01, slippage_rate=.01))
    assert expensive["metrics"]["total_return"] < bt["metrics"]["total_return"]
    data["prediction"] = .1
    no_trade = backtest_predictions(data, panel, c, spec())
    assert no_trade["metrics"]["trade_count"] == 0
    assert prediction_metrics(data, "classification", spec(), .5)["signal_win_rate_after_cost"] is None


def test_advisor_cannot_expand_execution_authority():
    advisor = ResearchAdvisor(lambda s,p: '{"candidate_ids":["invented"],"analysis":"run sql"}')
    with pytest.raises(ValueError):
        advisor.plan(spec(), [{"id":"a"}])


def test_full_research_artifacts_and_blind_independence(market, tmp_path):
    daily, events = market
    s = spec(max_trials=2, rounds=1, folds=2, models=("linear",), tasks=("regression",),
             horizons=(1,), samplers=("all",), feature_sets=("technical",))
    calls = []
    def complete(system, payload):
        calls.append(payload)
        if "candidates" in payload:
            assert "evaluation" not in payload
            return json.dumps({"candidate_ids": [payload["candidates"][0]["id"]], "analysis": "test plan"})
        return "按真实指标评审，尚未验证实盘。"
    root, report = run_research(s, daily, events, output_root=tmp_path, advisor=ResearchAdvisor(complete), progress=lambda _: None)
    assert report["successful_trials"] == 2
    assert (root / "model.joblib").exists() and (root / "report.md").exists()
    manifest = json.loads((root / "manifest.json").read_text())
    changed = daily.copy()
    # Poison ALL heldout-company prices, and all out-of-time returns, without touching development.
    poison = changed.symbol.isin(manifest["heldout_companies"]) | (changed.date >= pd.Timestamp(manifest["test_start"]))
    changed.loc[poison, ["adjopen", "adjhigh", "adjlow", "adjclose"]] *= 2
    root2, report2 = run_research(s, changed, events, output_root=tmp_path, progress=lambda _: None)
    assert report["selected"] == report2["selected"]
    one = json.loads((root / "development.json").read_text())["results"]
    two = json.loads((root2 / "development.json").read_text())["results"]
    assert one == two
    assert len(calls) == 2


def test_duplicate_prices_rejected(market):
    daily, _ = market
    with pytest.raises(ValueError, match="duplicate daily"):
        build_panel(pd.concat([daily, daily.iloc[:1]]))


def test_minute_cutoff_and_availability():
    frame = pd.DataFrame({"symbol": ["000001"] * 3,
        "bar_end_time": ["2023-01-02 14:58", "2023-01-02 15:00", "2023-01-02 15:01"],
        "close": [10, 11, 100]})
    features = minute_features(frame, ["000001.SZ"])
    assert features.minute_bars.iloc[0] == 2
    assert features.available_at.iloc[0] == pd.Timestamp("2023-01-02 15:05")


def test_auxiliary_target_cannot_become_feature():
    panel = pd.DataFrame({"symbol": ["A"], "date": pd.to_datetime(["2023-01-02"]),
                          "decision_at": pd.to_datetime(["2023-01-02 15:10"])})
    event = pd.DataFrame({"symbol": ["A"], "available_at": ["2023-01-01"], "forward_return": [.1]})
    with pytest.raises(ValueError):
        asof_features(panel, [event])


def test_constraints_are_enforced_and_missing_evidence_fails(market):
    from src.quant_research.automl.features import sample_mask
    daily, events = market
    panel = build_panel(daily, events)
    chosen = panel[sample_mask(panel, "all", spec(industries=("industry_0",), min_market_cap=1e10))]
    assert len(chosen) > 0
    assert chosen.industry.eq("industry_0").all() and chosen.total_mv.ge(1e10).all()
    with pytest.raises(ValueError, match="missing feature"):
        sample_mask(panel.drop(columns="total_mv"), "all", spec(min_market_cap=1e10))


def test_calibration_does_not_fit_preprocessor(market):
    daily, events = market
    train = labeled_panel(build_panel(daily, events), 1)
    train = train[train.label_end < "2023-10-01"]
    dates = sorted(train.date.unique())
    cutoff = dates[int(len(dates)*.75)]
    changed = train.copy()
    changed.loc[changed.date >= cutoff, "return_1"] += 100
    c = {"task":"classification", "model":"linear"}
    cols = feature_columns(train, "technical")
    first = fit_model(train, c, cols, spec())
    second = fit_model(changed, c, cols, spec())
    np.testing.assert_allclose(first.pipeline[-1].coef_, second.pipeline[-1].coef_)


def test_one_class_cannot_claim_calibrated_probability(market):
    daily, events = market
    data = labeled_panel(build_panel(daily, events), 1)
    data["forward_return"] = .1
    with pytest.raises(ValueError, match="both outcome classes"):
        fit_model(data, {"task":"classification", "model":"linear"}, feature_columns(data, "technical"), spec())


def test_inference_reuses_saved_pipeline_and_llm_failure_is_reported(market, tmp_path):
    from src.quant_research.automl.inference import predict_latest
    daily, events = market
    def fail(*args):
        raise TimeoutError("provider unavailable")
    s = spec(max_trials=1, rounds=1, folds=1, models=("linear",), tasks=("regression",),
             horizons=(1,), samplers=("all",), feature_sets=("enriched",))
    root, report = run_research(s, daily, events, output_root=tmp_path, advisor=ResearchAdvisor(fail), progress=lambda _:None)
    development = json.loads((root / "development.json").read_text())
    assert development["planning"][0]["fallback"] == "TimeoutError"
    assert "未完成" in (root / "review.md").read_text()
    signals = predict_latest(root, daily, events)
    assert len(signals) == daily.symbol.nunique()
    assert signals.date.nunique() == 1 and np.isfinite(signals.prediction).all()


def test_database_session_is_read_only_and_closed(monkeypatch):
    from src.quant_research.automl.data import kingdom_connection
    statements = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql): statements.append(sql)
    class Connection:
        def cursor(self): return Cursor()
        def rollback(self): statements.append("ROLLBACK")
        def close(self): statements.append("CLOSE")
    monkeypatch.setenv("TEST_MARKET_URL", "mysql+pymysql://tester:placeholder@localhost:3306/kingdomai")
    monkeypatch.setattr("src.quant_research.automl.data.pymysql.connect", lambda **kwargs: Connection())
    with pytest.raises(RuntimeError):
        with kingdom_connection("TEST_MARKET_URL"):
            raise RuntimeError("consumer failed")
    assert "SET SESSION TRANSACTION READ ONLY" in statements
    assert "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY" in statements
    assert statements[-2:] == ["ROLLBACK", "CLOSE"]
    monkeypatch.setenv("TEST_MARKET_URL", "mysql://tester:placeholder@localhost/another_schema")
    with pytest.raises(ValueError, match="kingdomai"):
        with kingdom_connection("TEST_MARKET_URL"):
            pass


def test_negative_engine_drawdown_is_checked_by_magnitude():
    from src.quant_research.automl.runner import constraint_checks
    folds = [{"prediction": {"signal_count":100, "signal_win_rate_after_cost":.8, "skill_vs_constant":.1},
              "portfolio": {"max_drawdown": -.4}}]
    checks = constraint_checks(folds, spec(max_drawdown=.3))
    assert not checks["drawdown_within_limit"]
    assert all(value for key,value in checks.items() if key != "drawdown_within_limit")
    assert all(constraint_checks(folds, spec(max_drawdown=.5)).values())

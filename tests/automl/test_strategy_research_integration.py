"""Keep the three imported strategy studies independently checkable."""

import json
from pathlib import Path

import pandas as pd

from scripts.analyze_automl_deep_v_d5_conditional import first_dip
from scripts.analyze_automl_limit_rebound_2026 import analyze
from scripts.experiment_automl_deep_v_d5 import path_result


ROOT = Path("docs/stock_automl_runs")


def test_limit_rebound_uses_market_days_and_keeps_unfinished_streaks_unknown():
    days = ["2026-01-05", "2026-01-06", "2026-01-07",
            "2026-01-08", "2026-01-09", "2026-01-12"]
    daily = pd.DataFrame([
        {"trade_date": day, "stk_code": "000001.SZ", "preclose": 10,
         "close": 11 if i in (0, 2) else 10, "high": 11,
         "volume": 100, "is_limit_price": int(i in (0, 2))}
        for i, day in enumerate(days)
    ])
    events, _ = analyze(daily)
    assert len(events) == 2
    first = events.set_index("first_limit_date").loc[days[0]]
    assert first.outcome == "limit_up"
    assert first.cooling_days == 1
    assert first.rebound_date == days[2]
    assert events.set_index("first_limit_date").loc[days[2]].outcome == "right_censored"


def test_deep_v_buy_uses_minute_after_observable_trigger():
    morning = pd.date_range("2026-09-02 09:31", periods=120, freq="min")
    afternoon = pd.date_range("2026-09-02 13:01", periods=120, freq="min")
    times = morning.append(afternoon)
    prices = [100.] * 240
    prices[30] = 94.
    prices[31] = 95.
    prices[-1] = 96.
    bars = pd.DataFrame({"bar_end_time": times, "latest_price": prices})
    result = path_result(bars)
    assert result["path_valid"] and result["late_trigger"]
    assert result["trigger_time"] == "10:01:00"
    assert result["entry_time"] == "10:02:00"
    assert abs(result["gross_return"] - (96 / 95 - 1)) < 1e-12
    assert first_dip(bars)["day_entry_time"] == "10:02:00"


def test_imported_research_evidence_is_internally_consistent():
    limit_root = Path("outputs/stock_automl/limit_rebound_20261013")
    limit_summary = json.loads((limit_root / "summary.json").read_text())
    events = pd.read_csv(limit_root / "streak_events.csv")
    monthly = pd.read_csv(limit_root / "monthly.csv")
    assert len(events) == limit_summary["first_board_streaks"] == int(monthly.streaks.sum())
    assert int(events.outcome.eq("limit_up").sum()) == limit_summary["rebound_limit_streaks"]
    assert int(events.outcome.eq("right_censored").sum()) == limit_summary["right_censored"]
    assert int(events.outcome.eq("missing_quote").sum()) == limit_summary["missing_quote"]

    evidence = ROOT / "20261013_deep_v_two_stage_d5/evidence"
    stage1 = pd.read_csv(evidence / "stage1_topk.csv").set_index("topk")
    stage2 = pd.read_csv(evidence / "stage2_topk.csv").set_index(["policy", "topk"])
    conditional = pd.read_csv(evidence / "trigger_conditional_topk.csv").set_index(["policy", "topk"])
    assert stage1.loc[100, "deepv"] == 208
    assert stage1.loc[100, "full240"] == 2293
    assert stage2.loc[("morning_model", 10), "late_deepv"] == 6
    assert stage2.loc[("morning_model", 10), "triggered_trades"] == 166
    assert conditional.loc[("morning_at_1000", 10), "deepv_after_trigger"] == 6
    assert conditional.loc[("morning_at_1000", 10), "triggered"] == 166

from __future__ import annotations

import pandas as pd

from scripts.evaluate_automl_mk_1m_jan_jul_exits import CLOSES, evaluate


def test_same_stock_selected_by_multiple_arms_keeps_all_exit_ledgers():
    picks = pd.DataFrame([{
        "arm": arm, "signal_date": "2026-02-03", "next_date": "2026-02-04",
        "symbol6": "600000", "selection_rank": 1, "name": "ST 样本",
        "predicted_class": "ge3", "p_ge3": .55, "signal_price": 10.0,
        "actual_second_high_return": .04,
    } for arm in ("rolling20_tminus1", "full19_tminus2", "p3_selected_tminus2")])
    rows = pd.DataFrame([{
        "signal_date": "2026-02-03", "next_date": "2026-02-04", "symbol6": "600000",
        **{col: 10.4 for col in CLOSES},
    }])
    intervals = pd.DataFrame([{
        "symbol6": "600000", "st_type": "Y",
        "begin_date": pd.Timestamp("2026-01-01"),
        "end_date": pd.Timestamp("2026-03-01"),
        "ann_date": pd.Timestamp("2026-01-01"),
    }])
    daily = pd.DataFrame([{
        "signal_date": "2026-02-03", "symbol6": "600000",
        "day_preclose": 9.52, "day_close": 10., "is_limit_price": 1,
    }])
    trades, summary = evaluate(picks, rows, intervals, daily)
    assert len(trades) == 9
    assert trades.historical_st_at_limit.all()
    assert all(row["trades"] == 1 and row["ge3"] == 1 for row in summary.values())


def test_missing_next_morning_prices_are_unobservable_not_zero_return():
    picks = pd.DataFrame([{
        "arm": "rolling20_tminus1", "signal_date": "2026-02-03",
        "next_date": "2026-02-04", "symbol6": "600000",
        "selection_rank": 1, "name": "停牌样本", "predicted_class": "ge3",
        "p_ge3": .55, "signal_price": 10.0,
        "actual_second_high_return": float("nan"),
    }])
    rows = pd.DataFrame([{
        "signal_date": "2026-02-03", "next_date": "2026-02-04",
        "symbol6": "600000", **{col: float("nan") for col in CLOSES},
    }])
    intervals = pd.DataFrame([{
        "symbol6": "600000", "st_type": "N",
        "begin_date": pd.Timestamp("2026-01-01"),
        "end_date": pd.Timestamp("2026-03-01"),
        "ann_date": pd.NaT,
    }])
    daily = pd.DataFrame([{
        "signal_date": "2026-02-03", "symbol6": "600000",
        "day_preclose": 9.5, "day_close": 10., "is_limit_price": 0,
    }])
    trades, summary = evaluate(picks, rows, intervals, daily)
    assert trades.exit_return.isna().all()
    assert trades.exit_rule.eq("unobservable").all()
    assert all(v["unobservable_exits"] == 1 and
               v["gross_compounded_return_pct"] is None
               for v in summary.values())

import pandas as pd
import pytest

from scripts.audit_automl_four_class_fixed_minutes import calculate
from scripts.audit_automl_sell_minute_mode import SLOTS


def test_top2_daily_extrema_use_both_stocks_at_the_same_minute():
    rows = []
    for rank, symbol, minute31, minute40 in (
        (1, "000001", 110, 90),
        (2, "000002", 90, 120),
    ):
        row = {"signal_date": "2026-09-02", "next_date": "2026-09-03",
               "symbol6": symbol, "name": symbol, "selection_rank": rank,
               "entry_1440": 100, **{slot: 100 for slot in SLOTS}}
        row["close_31"] = minute31
        row["close_40"] = minute40
        rows.append(row)
    trades, daily, averages = calculate(pd.DataFrame(rows))
    top1 = daily[daily.top_k.eq(1)].iloc[0]
    top2 = daily[daily.top_k.eq(2)].iloc[0]
    assert len(trades) == 2
    assert top1.best_slot == "close_31"
    assert top1.best_return_pct == pytest.approx(10)
    assert top2.best_slot == "close_40"
    assert top2.best_return_pct == pytest.approx(5)
    assert top2.close_40_return_pct == pytest.approx(5)
    assert len(averages) == 66

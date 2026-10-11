from __future__ import annotations

import pandas as pd

from scripts.experiment_automl_market_amount_board import board_of, market_by_day


def test_market_five_days_includes_signal_day_and_sorts_archive_order():
    dates = pd.date_range("2026-01-05", periods=139, freq="D").strftime("%Y-%m-%d")
    amount = pd.DataFrame({"trade_date": dates,
                           "amount_1440_100m_cny": [10000, 20000, 30000, 20000, 19999]
                           + [25000] * 134})
    result = market_by_day(amount.iloc[::-1])
    assert result.trade_date.is_monotonic_increasing
    assert result.amount_avg5_1440_100m.iloc[4] == 19999.8
    assert not bool(result.high_amount.iloc[4])
    assert result.amount_tier.iloc[4] == 1
    assert result.amount_avg5_1440_100m.iloc[5] == 22999.8
    assert bool(result.high_amount.iloc[5])
    assert result.amount_tier.iloc[5] == 2


def test_board_assignment_keeps_000_distinct():
    assert board_of("000001") == "SZ000"
    assert board_of("002001") == "SZOther"
    assert board_of("300001") == "ChiNext"
    assert board_of("600001") == "SHMain"
    assert board_of("688001") == "STAR"

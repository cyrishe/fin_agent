from __future__ import annotations

import pandas as pd
from pathlib import Path

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
    assert result.prior_avg5_1440_100m.iloc[5] == 19999.8
    assert not bool(result.prior_high_amount.iloc[5])


def test_board_assignment_keeps_000_distinct():
    assert board_of("000001") == "SZ000"
    assert board_of("002001") == "SZOther"
    assert board_of("300001") == "ChiNext"
    assert board_of("600001") == "SHMain"
    assert board_of("688001") == "STAR"


def test_saved_daily_reconciles_previous_five_and_including_today():
    root = Path("docs/stock_automl_runs/20261011_mk_1m_market_amount_board")
    daily = pd.read_csv(root / "gate_daily.csv")
    previous = daily[daily["前五日通过2万亿门槛"]]
    including_today = daily[daily["通过2万亿门槛"]]
    assert len(previous) == 97 and len(including_today) == 96
    assert previous["Top1固定09:40收益（%）"].count() == 94
    assert including_today["Top1固定09:40收益（%）"].count() == 94
    assert abs(previous["Top1固定09:40收益（%）"].mean() - .5117235859) < 1e-6
    assert abs(including_today["Top1固定09:40收益（%）"].mean() - .3079341822) < 1e-6

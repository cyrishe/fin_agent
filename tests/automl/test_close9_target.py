from decimal import Decimal

import pandas as pd

from scripts.build_automl_close9_target import build
from scripts.experiment_automl_close9_models import cash_simulation, split_dates


def test_target_uses_nine_minute_closes_and_1440_entry():
    rows = pd.DataFrame({
        "signal_date": ["2026-08-10"] * 3,
        "next_date": ["2026-08-11"] * 3,
        "symbol6": ["000001", "000002", "000003"],
        "signal_price": [10, 10, 10],
    })
    closes = rows[["next_date", "symbol6"]].copy()
    for minute in range(32, 41):
        closes[f"close_{minute:02}"] = [10.00, 10.05, 10.00]
    closes.loc[0, "close_34"] = 10.11
    closes.loc[2, "close_40"] = 10.049
    result = build(rows, closes)
    assert result.target_class.tolist() == [1, 0, -1]
    assert result.max_close9_return.iloc[0] > .01


def test_cash_exits_at_first_qualifying_close_and_respects_buying_units():
    rows = pd.DataFrame({
        "signal_date": ["2026-08-10"] * 2,
        "next_date": ["2026-08-11"] * 2,
        "rank": [1, 2],
        "symbol6": ["000001", "000002"],
        "name": ["a", "b"],
        "score": [.6, .5],
        "entry_1440": [10, 10],
        "max_close9_return": [.02, -.02],
        "target_class": [1, -1],
    })
    for minute in range(32, 41):
        rows[f"close_{minute:02}"] = [10.00, 9.80]
    rows.loc[0, "close_32"] = 10.05
    rows.loc[0, "close_33"] = 10.20
    result, _, trades = cash_simulation(rows, Decimal("100000"))
    assert trades.shares.tolist() == [6000, 4000]
    assert trades.exit_minute.tolist() == ["09:33", "09:40"]
    assert trades.exit_price.tolist() == [10.2, 9.8]
    assert result["ending_cash"] == 100400.0


def test_market_group_split_keeps_dates_disjoint():
    states = pd.DataFrame({"trade_date": [f"2026-08-{day:02}" for day in range(1, 41)],
                           "market_group": [day % 4 for day in range(40)]})
    split = split_dates(states)
    assert len(split) == 40
    assert set(split.values()) == {"train", "validation", "test"}
    for _, group in states.groupby("market_group"):
        assert set(split[day] for day in group.trade_date) == {"train", "validation", "test"}

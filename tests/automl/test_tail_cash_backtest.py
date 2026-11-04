from decimal import Decimal

import pandas as pd

from scripts.backtest_automl_tail_top2_cash import exit_price, simulate, target_price


def test_configurable_limit_uses_tradable_cent_price_and_0940_fallback():
    assert target_price(Decimal("10.01")) == Decimal("10.22")
    assert target_price(Decimal("10.01"), Decimal("0.01")) == Decimal("10.12")
    touched = pd.Series({"entry_1450": 10.01, "next_high10": 10.22,
                         "next_0940": 9.90, "next_open": 10.00})
    missed = touched.copy()
    missed["next_high10"] = 10.21
    assert exit_price(touched, "limit_then_0940") == Decimal("10.22")
    assert exit_price(missed, "limit_then_0940") == Decimal("9.9")
    opened_above = touched.copy()
    opened_above["next_open"] = 10.30
    opened_above["next_high10"] = 10.30
    assert exit_price(opened_above, "limit_then_0940") == Decimal("10.3")


def test_equal_cash_allocation_skips_unaffordable_lots_and_rolls_cash():
    rows = pd.DataFrame([
        ("2026-09-01", "2026-09-02", "600001", "甲", 1, 1, 60, 61, 61, 61),
        ("2026-09-01", "2026-09-02", "000001", "乙", 2, 1, 10, 11, 11, 11),
        ("2026-09-02", "2026-09-03", "688001", "丙", 1, 1, 30, 31, 31, 31),
        ("2026-09-02", "2026-09-03", "000002", "丁", 2, -1, 10, 9, 9, 9),
    ], columns=["signal_date", "next_date", "symbol6", "name", "rank_binary",
                "actual_class", "entry_1450", "next_open", "next_high10", "next_0940"])
    result, daily, trades = simulate(rows, mode="next_open", lot_constrained=True,
                                     starting_cash=Decimal("10000"))
    assert daily[0]["ending_cash"] == 10500
    assert daily[1]["ending_cash"] == 10000
    assert result["ending_cash"] == 10000
    assert result["executed_stocks"] == 2
    assert [x["symbol6"] for x in trades if not x["executed"]] == ["600001", "688001"]

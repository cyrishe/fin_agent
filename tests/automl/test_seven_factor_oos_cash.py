from decimal import Decimal

import pandas as pd

from scripts.backtest_automl_seven_factor_oos import label_oct8_after_ranking, sell_observation, simulate


def test_first_three_minute_observed_price_then_0940_fallback():
    bars = pd.DataFrame({
        "minute": [f"09:{minute:02}" for minute in range(31, 41)],
        "open_price": [10.0] * 10,
        "latest_price": [10.05, 10.12, 10.20, 10.19, 10.18,
                         10.17, 10.16, 10.15, 10.14, 9.9],
    })
    assert sell_observation(Decimal("10"), bars) == (Decimal("10.12"), "09:32收盘", True)
    bars.loc[0, "open_price"] = 10.2
    assert sell_observation(Decimal("10"), bars) == (Decimal("10.2"), "09:31开盘", True)
    bars.loc[0, "open_price"] = 10.0
    bars.loc[:2, "latest_price"] = [10.05, 10.10, 10.10]
    assert sell_observation(Decimal("10"), bars) == (Decimal("9.9"), "09:40收盘", False)


def test_cash_uses_fixed_60_40_and_keeps_unaffordable_allocation_idle():
    rows = pd.DataFrame([
        {"signal_date": "2026-08-05", "next_date": "2026-08-06", "symbol6": "600001",
         "name": "甲", "rank": 1, "score": .8},
        {"signal_date": "2026-08-05", "next_date": "2026-08-06", "symbol6": "600002",
         "name": "乙", "rank": 2, "score": .7},
    ])
    bars = []
    for symbol, entry, exit_price in (("600001", 10.0, 10.2), ("600002", 500.0, 510.0)):
        bars.append({"day": "2026-08-05", "symbol6": symbol, "minute": "14:50",
                     "open_price": entry, "latest_price": entry})
        for minute in range(31, 41):
            bars.append({"day": "2026-08-06", "symbol6": symbol,
                         "minute": f"09:{minute:02}", "open_price": exit_price,
                         "latest_price": exit_price})
    result, daily, trades = simulate(rows, pd.DataFrame(bars))
    assert result["executed_stocks"] == 1
    assert trades.loc[0, "股数"] == 6000
    assert trades.loc[1, "股数"] == 0
    assert daily.loc[0, "期末资金"] == 101200


def test_next_morning_high_labels_after_october_eighth_selection():
    chosen = pd.DataFrame([{"signal_date": "2026-10-08", "next_date": "2026-10-09",
                            "symbol6": "600001", "class": pd.NA}])
    bars = pd.DataFrame([{"day": "2026-10-08", "symbol6": "600001", "minute": "14:50",
                          "latest_price": 10.0, "high_price": 10.0}] + [
        {"day": "2026-10-09", "symbol6": "600001", "minute": f"09:{minute:02}",
         "latest_price": 10.0, "high_price": 10.11 if minute == 32 else 10.0}
        for minute in range(31, 41)])
    assert label_oct8_after_ranking(chosen, bars).iloc[0]["class"] == 1

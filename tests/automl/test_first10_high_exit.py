from decimal import Decimal

import pandas as pd

from scripts.reprice_automl_first10_high_exit import exit_at_observed_high


def bars(highs, last_close=9.8):
    return pd.DataFrame({"minute": [f"09:{n:02}" for n in range(31, 41)],
                         "high_price": highs,
                         "latest_price": [10.0] * 9 + [last_close]})


def test_skip_first_three_then_sell_at_first_qualifying_actual_high():
    morning = bars([10.4, 10.3, 10.2, 10.15, 10.5, 10, 10, 10, 10, 10])
    assert exit_at_observed_high(Decimal("10"), morning) == (
        Decimal("10.15"), "09:34分钟最高价", True)


def test_first_three_only_falls_back_to_0940_and_boundary_is_strict():
    morning = bars([10.4, 10, 10, 10.1, 10.1, 10, 10, 10, 10, 10.1])
    assert exit_at_observed_high(Decimal("10"), morning) == (
        Decimal("9.8"), "09:40收盘价", False)

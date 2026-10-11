from __future__ import annotations

import pandas as pd

from scripts.audit_automl_mk_1m_buyability import classify, minute_evidence


def test_zero_volume_locked_bar_and_later_opening_are_separate_evidence():
    day = "2026-06-03"
    times = [f"{day} 14:{minute:02d}:00" for minute in range(40, 60)] + [f"{day} 15:00:00"]
    bars = pd.DataFrame({"time": times, "open": [5.0] * 21,
                         "high": [5.0] * 21, "low": [5.0] * 21,
                         "close": [5.0] * 21, "volume": [0.0] * 21})
    locked = minute_evidence(bars, 5.0)
    assert locked["signal_zero_volume"]
    assert locked["later_volume_shares"] == 0
    assert not locked["later_traded_below_signal"]

    bars.loc[1, ["low", "close", "volume"]] = [4.99, 4.99, 1000.0]
    opened = minute_evidence(bars, 5.0)
    assert opened["later_traded_below_signal"]
    assert opened["first_later_trade_below_signal"] == f"{day} 14:41:00"


def test_st_near_five_requires_historical_status_and_limit_close():
    source = pd.DataFrame({"signal_return": [.05, .05, .05],
                           "st_type": ["Y", "N", "Y"],
                           "is_limit_price": [1, 1, 0],
                           "day_close": [5.0, 5.0, 4.99],
                           "day_preclose": [4.76, 4.76, 4.76],
                           "signal_price": [5.0, 5.0, 5.0],
                           "later_volume_shares": [0, 100, 100],
                           "signal_flat": [True, True, False],
                           "signal_zero_volume": [True, False, False]})
    result = classify(source)
    assert result.st_near5_at_limit.tolist() == [True, False, False]
    assert result.no_trade_from_signal_to_close.tolist() == [True, False, False]


def test_low_price_rounding_does_not_hide_st_limit():
    row = pd.DataFrame({"signal_return": [1.38 / 1.31 - 1],
                        "st_type": ["Y"], "is_limit_price": [1],
                        "day_close": [1.38], "day_preclose": [1.31],
                        "signal_price": [1.38], "later_volume_shares": [100],
                        "signal_flat": [True], "signal_zero_volume": [True]})
    result = classify(row)
    assert not result.near_five_pct.iloc[0]
    assert result.historical_st_at_limit.iloc[0]

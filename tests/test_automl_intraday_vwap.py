import pandas as pd

from scripts.experiment_automl_intraday_vwap import EXPECTED_TIMES, feature_level, summarize_bars
from scripts.backtest_automl_intraday_vwap_gate import gate
from scripts.analyze_automl_top1_low_vwap import counts


def test_vwap_levels_and_all_above_use_exact_intraday_fraction():
    clocks = sorted(EXPECTED_TIMES)
    bars = pd.DataFrame({"symbol6": "000001", "clock": clocks,
                         "close": [11] * 210 + [9] * 10,
                         "volume": 100, "amount": 1000})
    result = summarize_bars(bars, "2026-07-30", 1).iloc[0]
    assert result.vwap_above_minutes == 210
    assert result.vwap_all_above == 0
    assert result.vwap_level == 2
    bars["close"] = 11
    all_above = summarize_bars(bars, "2026-07-30", 1).iloc[0]
    assert all_above.vwap_all_above == 1
    assert all_above.vwap_above_ratio == 1
    future = pd.concat([bars, pd.DataFrame({"symbol6": ["000001"],
                                            "clock": ["14:41:00"], "close": [1],
                                            "volume": [1_000_000], "amount": [1_000_000]})],
                       ignore_index=True)
    assert summarize_bars(future, "2026-07-30", 1).vwap_above_ratio.iloc[0] == 1
    assert feature_level(.85) == 1
    assert feature_level(.95) == 1
    assert feature_level(.9500001) == 2
    assert feature_level(.8499999) == 0


def test_database_lot_to_share_conversion_and_incomplete_day():
    clocks = sorted(EXPECTED_TIMES)
    bars = pd.DataFrame({"symbol6": "000001", "clock": clocks,
                         "close": 10.1, "volume": 1, "amount": 1000})
    exact = summarize_bars(bars, "2026-07-30", 100)
    assert exact.vwap_all_above.iloc[0] == 1
    wrong_units = summarize_bars(bars, "2026-07-30", 1)
    assert wrong_units.vwap_all_above.iloc[0] == 0
    assert summarize_bars(bars.iloc[:-1], "2026-07-30", 100).empty


def test_each_minute_uses_its_own_cumulative_vwap():
    clocks = sorted(EXPECTED_TIMES)
    bars = pd.DataFrame({"symbol6": "000001", "clock": clocks,
                         "close": [9, 9] + [11] * 218,
                         "volume": 100, "amount": [800, 1200] + [1000] * 218})
    result = summarize_bars(bars, "2026-07-30", 1).iloc[0]
    assert result.vwap_above_minutes == 219
    assert result.vwap_all_above == 0
    bars.loc[0, ["volume", "amount"]] = 0
    no_opening_trade = summarize_bars(bars, "2026-07-30", 1).iloc[0]
    assert no_opening_trade.vwap_above_minutes == 217
    assert no_opening_trade.vwap_all_above == 0


def test_turnover_gate_requires_exact_prior_twenty_days():
    assert gate(10, [10] * 20) == (True, 10.0)
    assert gate(9, [10] * 20) == (False, 10.0)
    assert gate(100, [None] + [10] * 19) == (False, None)


def test_minute_low_ratio_uses_own_cumulative_vwap_and_excludes_later_bar():
    bars = pd.DataFrame({"clock": sorted(EXPECTED_TIMES), "low": [9] + [11] * 219,
                         "close": [11] * 220, "volume": 100, "amount": 1000})
    result = counts(bars, "2026-07-30", "000001", 1)
    assert result["low_above_minutes"] == 219
    assert result["low_above_ratio"] == 219 / 220
    assert result["low_all_above"] == 0
    assert result["close_above_minutes_rechecked"] == 220
    future = pd.concat([bars, pd.DataFrame({"clock": ["14:41:00"], "low": [0.1],
                                            "close": [0.1], "volume": [100],
                                            "amount": [10]})], ignore_index=True)
    assert counts(future, "2026-07-30", "000001", 1)["low_above_minutes"] == 219
    bars["symbol6"] = "000001"
    factor = summarize_bars(bars, "2026-07-30", 1).iloc[0]
    assert factor.low_above_minutes == 219
    assert factor.low_vwap_level == 2

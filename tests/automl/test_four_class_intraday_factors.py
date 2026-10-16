from __future__ import annotations

import pandas as pd

from scripts.experiment_automl_four_class_intraday_factors import (
    VOLUME_TREND, intraday_features, longest_rising_run,
)


def test_longest_rising_run_counts_consecutive_five_minute_blocks():
    assert longest_rising_run([1, 2, 3, 2, 4, 5, 6, 1]) == 4
    assert longest_rising_run([8, 7, 6, 5, 4, 3, 2, 1]) == 1
    assert longest_rising_run([1] * 8) == 1


def test_historical_factor_ends_at_1440_and_uses_exact_eight_blocks():
    day = "2026-09-30"
    times = list(pd.date_range(f"{day} 09:31", f"{day} 11:30", freq="min"))
    times += list(pd.date_range(f"{day} 13:01", f"{day} 14:40", freq="min"))
    assert len(times) == 220
    bars = pd.DataFrame({"symbol6": "000001", "bar_end_time": times,
                         "high_price": 11.0, "volume": 10,
                         "is_finalized": 1, "is_fallback": 0,
                         "source_snapshot_time": times})
    bars.loc[bars.bar_end_time.eq(pd.Timestamp(f"{day} 09:31")), "high_price"] = 12.0
    for block, amount in enumerate([1, 2, 3, 2, 4, 5, 6, 1]):
        left = pd.Timestamp(f"{day} 14:01") + pd.Timedelta(minutes=5 * int(block))
        right = left + pd.Timedelta(minutes=4)
        bars.loc[bars.bar_end_time.between(left, right), "volume"] = amount
    feature = intraday_features(bars, day).iloc[0]
    assert feature.high_until_1440 == 12.0
    assert feature[VOLUME_TREND] == 4
    after = bars.iloc[[-1]].copy()
    after["bar_end_time"] = pd.Timestamp(f"{day} 14:41")
    after["source_snapshot_time"] = after.bar_end_time
    after["high_price"] = 99.0
    with_future = intraday_features(pd.concat([bars, after]), day).iloc[0]
    assert with_future.high_until_1440 == 12.0
    assert with_future[VOLUME_TREND] == 4
    missing = intraday_features(bars[bars.bar_end_time.ne(
        pd.Timestamp(f"{day} 14:40"))], day).iloc[0]
    assert pd.isna(missing.high_until_1440)
    assert pd.isna(missing[VOLUME_TREND])

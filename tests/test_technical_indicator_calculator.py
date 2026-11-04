from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from src.services.technical_indicator_calculator import (
    PRICE_VOLUME_FEATURES,
    calculate_price_volume_features,
)


def bars(count=80):
    return pd.DataFrame(
        {"close": np.arange(1, count + 1, dtype=float), "volume": np.full(count, 100.0)},
        index=pd.date_range("2026-01-01", periods=count, freq="B"),
    )


def test_hand_calculated_windows_and_excluded_current_volume():
    frame = bars()
    frame.iloc[-1, frame.columns.get_loc("volume")] = 500
    got = calculate_price_volume_features(frame).iloc[-1]
    assert tuple(got.index) == PRICE_VOLUME_FEATURES
    assert [got[f"ma{n}"] for n in (5, 10, 20, 60)] == [78, 75.5, 70.5, 50.5]
    for n, denominator in [(1, 79), (5, 75), (20, 60)]:
        assert got[f"return{n}"] == pytest.approx(80 / denominator - 1)
    assert got["prior_volume_mean5"] == 100
    assert got["prior_volume_mean20"] == 100
    assert got["volume_ratio5"] == 5
    assert got["bias20"] == pytest.approx(80 / 70.5 - 1)


def test_complete_window_warmup_and_missing_values_not_filled():
    frame = bars()
    result = calculate_price_volume_features(frame)
    assert result["ma60"].iloc[:59].isna().all()
    assert result["ma60"].iloc[59] == 30.5
    assert result["return20"].iloc[:20].isna().all()
    assert result["return20"].iloc[20] == 20
    assert result["prior_volume_mean5"].iloc[:5].isna().all()
    frame.iloc[-2] = np.nan
    got = calculate_price_volume_features(frame).iloc[-1]
    assert pd.isna(got["return1"])
    assert pd.isna(got["ma5"])
    assert pd.isna(got["volume_ratio5"])
    # Multi-period endpoint return does not pretend to measure the intervening path.
    assert got["return5"] == pytest.approx(80 / 75 - 1)


def test_zero_volume_is_valid_but_zero_baseline_ratio_is_missing():
    frame = bars()
    frame["volume"] = 0.0
    got = calculate_price_volume_features(frame)
    assert got["volume_ratio5"].isna().all()
    assert got["prior_volume_mean5"].iloc[-1] == 0
    assert got["ma5"].iloc[-1] == 78


def test_future_observations_do_not_change_past_and_input_is_unchanged():
    frame = bars()
    original = frame.copy(deep=True)
    prefix = calculate_price_volume_features(frame.iloc[:65])
    complete = calculate_price_volume_features(frame)
    assert_frame_equal(prefix, complete.iloc[:65])
    assert_frame_equal(frame, original)


def test_price_scaling_and_volume_scaling_invariants():
    frame = bars()
    baseline = calculate_price_volume_features(frame)
    scaled = calculate_price_volume_features(frame * {"close": 3.0, "volume": 10.0})
    for col in ("return1", "return5", "return20", "bias20", "volume_ratio5"):
        np.testing.assert_allclose(scaled[col], baseline[col], equal_nan=True)
    np.testing.assert_allclose(scaled["ma20"], baseline["ma20"] * 3, equal_nan=True)
    np.testing.assert_allclose(scaled["prior_volume_mean5"], baseline["prior_volume_mean5"] * 10, equal_nan=True)


def test_database_decimals_nulls_and_extra_columns_are_compatible():
    frame = bars(6)
    frame["close"] = [Decimal(str(n)) for n in range(1, 7)]
    frame["volume"] = pd.array([None, 100, 100, 100, 100, 100], dtype="Float64")
    frame["source"] = "fixture"
    got = calculate_price_volume_features(frame)
    assert got["ma5"].iloc[-1] == 4
    assert pd.isna(got["volume_ratio5"].iloc[-1])
    empty = calculate_price_volume_features(frame.iloc[:0])
    assert empty.empty and tuple(empty.columns) == PRICE_VOLUME_FEATURES


@pytest.mark.parametrize("change", ["reversed", "duplicate", "no_time", "missing_time", "no_close", "bad_price", "zero_price", "negative_volume", "infinite"])
def test_reject_inputs_that_make_the_window_or_arithmetic_invalid(change):
    frame = bars()
    if change == "reversed":
        frame = frame.iloc[::-1]
    elif change == "duplicate":
        frame = pd.concat([frame.iloc[:1], frame])
    elif change == "no_time":
        frame = frame.reset_index(drop=True)
    elif change == "missing_time":
        frame.index = frame.index[:-1].append(pd.DatetimeIndex([pd.NaT]))
    elif change == "no_close":
        frame = frame.drop(columns="close")
    elif change == "bad_price":
        frame["close"] = frame["close"].astype(object)
        frame.iloc[-1, 0] = "bad"
    elif change == "zero_price":
        frame.iloc[-1, 0] = 0
    elif change == "negative_volume":
        frame.iloc[-1, 1] = -1
    else:
        frame.iloc[-1, 0] = np.inf
    with pytest.raises(ValueError):
        calculate_price_volume_features(frame)

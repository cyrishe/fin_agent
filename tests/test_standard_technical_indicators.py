from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from src.services.technical_indicator_calculator import (
    RECURSIVE_TECHNICAL_FEATURES,
    STANDARD_TECHNICAL_FEATURES,
    TECHNICAL_MIN_BARS,
    calculate_standard_technical_features,
    calculate_standard_technical_window,
    required_technical_input_bars,
)
from src.services.technical_indicator_retention import technical_result_archive_dates


def fixture(count=400):
    t = np.arange(count, dtype=float)
    close = 100 + t / 3 + 4 * np.sin(t / 3)
    return pd.DataFrame({"close": close, "high": close + 2, "low": close - 2,
                         "volume": 1000 + t}, index=pd.bdate_range("2024-01-01", periods=count))


def test_each_output_has_its_declared_first_valid_observation():
    got = calculate_standard_technical_features(fixture())
    assert tuple(got.columns) == STANDARD_TECHNICAL_FEATURES
    assert len(got.columns) == 27
    for name, n in TECHNICAL_MIN_BARS.items():
        assert got[name].iloc[:n - 1].isna().all(), name
        assert pd.notna(got[name].iloc[n - 1]), name


def test_prior_extremes_exclude_today_and_boll_volatility_reference():
    frame = fixture(80)
    frame.iloc[-1, frame.columns.get_loc("close")] = 300
    frame.iloc[-1, frame.columns.get_loc("high")] = 302
    frame.iloc[-1, frame.columns.get_loc("low")] = 298
    got = calculate_standard_technical_features(frame).iloc[-1]
    c = frame.close.to_numpy()
    assert got.prior_close_high20 == max(c[-21:-1])
    assert got.prior_close_high60 == max(c[-61:-1])
    assert got.prior_close_low20 == min(c[-21:-1])
    assert got.distance_prior_high20 == pytest.approx(300 / max(c[-21:-1]) - 1)
    mean, std = np.mean(c[-20:]), np.std(c[-20:], ddof=0)
    assert got.boll_mid20 == pytest.approx(mean)
    assert got.boll_upper20 == pytest.approx(mean + 2 * std)
    assert got.boll_lower20 == pytest.approx(mean - 2 * std)
    assert got.boll_width20 == pytest.approx(4 * std / mean)
    assert got.boll_percent_b20 == pytest.approx((c[-1] - mean + 2 * std) / (4 * std))
    expected = np.std(c[-20:] / c[-21:-1] - 1, ddof=1) * np.sqrt(252)
    assert got.volatility20 == pytest.approx(expected)


def test_wilder_seed_and_next_update_against_hand_arithmetic():
    frame = fixture(16)
    c, h, low = (frame[name].to_numpy() for name in ("close", "high", "low"))
    deltas = np.diff(c)
    gain = sum(max(x, 0) for x in deltas[:14]) / 14
    loss = sum(max(-x, 0) for x in deltas[:14]) / 14
    tr = [max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1])) for i in range(1, 16)]
    atr = sum(tr[:14]) / 14
    got = calculate_standard_technical_features(frame)
    assert got.rsi14.iloc[14] == pytest.approx(100 * gain / (gain + loss))
    assert got.atr14.iloc[14] == pytest.approx(atr)
    gain = (13 * gain + max(deltas[14], 0)) / 14
    loss = (13 * loss + max(-deltas[14], 0)) / 14
    assert got.rsi14.iloc[15] == pytest.approx(100 * gain / (gain + loss))
    assert got.atr14.iloc[15] == pytest.approx((13 * atr + tr[14]) / 14)
    assert got.atr_ratio14.iloc[15] == pytest.approx(got.atr14.iloc[15] / c[15])


def test_macd_independent_sma_seeds_and_signal_using_weighted_sum_reference():
    frame = fixture(50)
    c = frame.close.to_numpy()

    def ema_at(period, end):
        alpha = 2 / (period + 1)
        return (np.mean(c[:period]) * (1 - alpha) ** (end - period + 1)
                + sum(alpha * c[j] * (1 - alpha) ** (end - j) for j in range(period, end + 1)))

    dif = [ema_at(12, i) - ema_at(26, i) for i in range(25, 50)]
    got = calculate_standard_technical_features(frame)
    np.testing.assert_allclose(got.macd_dif.iloc[25:], dif, atol=1e-12)
    assert got.macd_dea.iloc[33] == pytest.approx(np.mean(dif[:9]))
    assert got.macd_dea.iloc[34] == pytest.approx(np.mean(dif[:9]) * .8 + dif[9] * .2)
    assert got.macd_hist.iloc[34] == pytest.approx(dif[9] - got.macd_dea.iloc[34])


@pytest.mark.parametrize("slope,rsi", [(0, 50), (1, 100), (-1, 0)])
def test_directional_and_flat_edge_cases(slope, rsi):
    frame = fixture(70)
    frame["close"] = 100 + slope * np.arange(70)
    frame["high"], frame["low"] = frame.close + 1, frame.close - 1
    got = calculate_standard_technical_features(frame).iloc[-1]
    assert got.rsi14 == rsi
    if slope == 0:
        assert got.boll_width20 == 0
        assert pd.isna(got.boll_percent_b20)
        assert got.volatility20 == 0
        assert got.macd_hist == 0


def test_gaps_restart_recursive_warmup_without_inventing_values():
    frame = fixture(100)
    frame.iloc[40] = np.nan
    got = calculate_standard_technical_features(frame)
    assert got.macd_dif.iloc[40:66].isna().all()
    assert pd.notna(got.macd_dif.iloc[66])
    assert got.macd_dea.iloc[40:74].isna().all()
    assert pd.notna(got.macd_dea.iloc[74])
    assert got.rsi14.iloc[40:55].isna().all()
    assert pd.notna(got.rsi14.iloc[55])
    assert got.atr14.iloc[40:55].isna().all()
    assert pd.notna(got.atr14.iloc[55])


def test_prefix_invariance_input_immutability_and_price_scaling():
    frame = fixture()
    original = frame.copy(deep=True)
    full = calculate_standard_technical_features(frame)
    assert_frame_equal(full.iloc[:70], calculate_standard_technical_features(frame.iloc[:70]))
    assert_frame_equal(frame, original)
    scaled = frame.copy()
    scaled[["close", "high", "low"]] *= 7
    got = calculate_standard_technical_features(scaled)
    for name in ("rsi14", "atr_ratio14", "boll_width20", "boll_percent_b20", "volatility20", "distance_prior_high20"):
        np.testing.assert_allclose(got[name], full[name], atol=1e-10, equal_nan=True)
    for name in ("macd_dif", "macd_dea", "macd_hist", "atr14", "boll_upper20", "prior_close_high60"):
        np.testing.assert_allclose(got[name], full[name] * 7, atol=1e-10, equal_nan=True)


def test_finite_windows_are_exact_after_truncation_and_compose_output_length():
    finite = [x for x in STANDARD_TECHNICAL_FEATURES if x not in RECURSIVE_TECHNICAL_FEATURES]
    assert required_technical_input_bars(finite) == 61
    assert required_technical_input_bars(finite, output_bars=60) == 120
    assert required_technical_input_bars(finite, output_bars=252) == 312
    assert required_technical_input_bars(["ma60"]) == 60
    frame = fixture()
    expected = calculate_standard_technical_features(frame).loc[:, finite].tail(60)
    got = calculate_standard_technical_window(frame, as_of=frame.index[-1], output_bars=60, indicators=finite)
    assert_frame_equal(got, expected, atol=1e-10, rtol=1e-10)


def test_asof_and_display_length_preserve_recursive_anchor():
    assert required_technical_input_bars(STANDARD_TECHNICAL_FEATURES) is None
    frame = fixture()
    original = frame.copy()
    asof = frame.index[299]
    one = calculate_standard_technical_window(frame, as_of=asof)
    sixty = calculate_standard_technical_window(frame, as_of=asof, output_bars=60)
    assert_frame_equal(one, sixty.tail(1))
    frame.loc[frame.index > asof, ["close", "high", "low"]] *= 2
    assert_frame_equal(one, calculate_standard_technical_window(frame, as_of=asof))
    expected = calculate_standard_technical_features(original.iloc[:300]).tail(1)
    assert_frame_equal(one, expected)
    # Finite restart counts must not be advertised for recursive formulas.
    short = calculate_standard_technical_features(original.iloc[240:300]).tail(1)
    assert abs(short.macd_dea.iloc[0] - one.macd_dea.iloc[0]) > 1e-6


def test_empty_decimal_compatible_and_short_history():
    from decimal import Decimal
    frame = fixture(10).map(lambda value: Decimal(str(value)))
    frame["source"] = "fixture"
    assert calculate_standard_technical_features(frame).macd_hist.isna().all()
    empty = calculate_standard_technical_features(frame.iloc[:0])
    assert empty.empty and tuple(empty.columns) == STANDARD_TECHNICAL_FEATURES


@pytest.mark.parametrize("change", ["missing_high", "infinite_low", "zero_high", "inverted", "close_outside"])
def test_invalid_ohlc_rejected(change):
    frame = fixture()
    if change == "missing_high":
        frame = frame.drop(columns="high")
    elif change == "infinite_low":
        frame.loc[frame.index[-1], "low"] = np.inf
    elif change == "zero_high":
        frame.loc[frame.index[-1], "high"] = 0
    elif change == "inverted":
        frame.loc[frame.index[-1], "high"] = frame.low.iloc[-1] - 1
    else:
        frame.loc[frame.index[-1], "close"] = frame.high.iloc[-1] + 1
    with pytest.raises(ValueError):
        calculate_standard_technical_features(frame)


@pytest.mark.parametrize("names,count", [([], 1), (["unknown"], 1), ("ma5", 1), (["ma5"], 0), (["ma5"], True)])
def test_window_request_cannot_silently_underread(names, count):
    with pytest.raises(ValueError):
        required_technical_input_bars(names, output_bars=count)


def test_archive_plan_uses_distinct_published_dates_and_preserves_references():
    dates = list(pd.bdate_range("2024-01-01", periods=300).date)
    pinned = [dates[2], dates[47]]
    got = technical_result_archive_dates(dates[::-1] + dates[:10], referenced_trade_dates=pinned)
    assert got == tuple(d for d in dates[:48] if d not in pinned)
    assert not set(got).intersection(dates[-252:])
    assert technical_result_archive_dates(dates[:200]) == ()
    assert technical_result_archive_dates([]) == ()
    # A long holiday/suspension gap does not turn this into calendar-day deletion.
    sparse = [date(2024, 1, 1) + timedelta(days=10 * i) for i in range(5)]
    assert technical_result_archive_dates(sparse, keep_dates=3) == tuple(sparse[:2])


@pytest.mark.parametrize("kwargs", [{"keep_dates": 0}, {"keep_dates": True}, {"referenced_trade_dates": ["2024-01-01"]}])
def test_archive_invalid_retention_or_reference_is_not_treated_as_unpinned(kwargs):
    with pytest.raises(ValueError):
        technical_result_archive_dates([date(2024, 1, 1)], **kwargs)

"""Deterministic price/volume features; no I/O, model calls or persistence.

Input is one security's ascending, unique DatetimeIndex of completed observations.
The caller selects the as-of cutoff and one consistent price basis; volume is in
shares. Missing observations stay missing. Ratios use fractions (0.1 means 10%).
See docs/development_tasks/data_capability_inventory_20260928/11_foundation_plan.md.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


PRICE_VOLUME_REVISION = "price_volume_v1"
PRICE_VOLUME_FEATURES = (
    "ma5", "ma10", "ma20", "ma60",
    "return1", "return5", "return20",
    "prior_volume_mean5", "prior_volume_mean20", "volume_ratio5",
    "bias20",
)

STANDARD_TECHNICAL_REVISION = "technical_daily_v1"
# These counts include the current observation. Recursive features have a first
# output but no finite exact restart window; the caller must preserve the anchor.
TECHNICAL_MIN_BARS = {
    "ma5": 5, "ma10": 10, "ma20": 20, "ma60": 60,
    "return1": 2, "return5": 6, "return20": 21,
    "prior_volume_mean5": 6, "prior_volume_mean20": 21,
    "volume_ratio5": 6, "bias20": 20,
    "prior_close_high20": 21, "prior_close_high60": 61,
    "prior_close_low20": 21, "distance_prior_high20": 21,
    "macd_dif": 26, "macd_dea": 34, "macd_hist": 34,
    "rsi14": 15, "atr14": 15, "atr_ratio14": 15,
    "boll_mid20": 20, "boll_upper20": 20, "boll_lower20": 20,
    "boll_width20": 20, "boll_percent_b20": 20,
    "volatility20": 21,
}
RECURSIVE_TECHNICAL_FEATURES = frozenset(
    ("macd_dif", "macd_dea", "macd_hist", "rsi14", "atr14", "atr_ratio14")
)
STANDARD_TECHNICAL_FEATURES = tuple(TECHNICAL_MIN_BARS)


def required_technical_input_bars(
    indicators: Sequence[str], *, output_bars: int = 1,
) -> int | None:
    """Exact input count for finite windows; None means anchored history needed.

    For D output observations, N input observations for one value require N+D-1.
    This is a calculation requirement, never authorization to prune source data.
    Counts assume contiguous usable inputs; a missing value may leave NaN.
    """
    if isinstance(indicators, str) or not indicators:
        raise ValueError("provide at least one indicator name as a sequence")
    if isinstance(output_bars, bool) or not isinstance(output_bars, int) or output_bars < 1:
        raise ValueError("output_bars must be a positive integer")
    unknown = set(indicators).difference(TECHNICAL_MIN_BARS)
    if unknown:
        raise ValueError(f"unknown indicators: {sorted(unknown)}")
    if RECURSIVE_TECHNICAL_FEATURES.intersection(indicators):
        return None
    return max(TECHNICAL_MIN_BARS[name] for name in indicators) + output_bars - 1


def calculate_price_volume_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Return eleven features at each input observation, without changing bars.

    Means require their complete window. Return N compares t with t-N; prior
    volume windows exclude t. Missing/zero denominators produce NaN, never zero.
    Input ordering and price validity are checked because violations change the
    meaning of every downstream window. Extra input columns are ignored.
    """
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("bars must use a DatetimeIndex")
    if bars.index.hasnans or not bars.index.is_unique or not bars.index.is_monotonic_increasing:
        raise ValueError("bar timestamps must be nonmissing, unique and ascending")
    if not {"close", "volume"}.issubset(bars.columns):
        raise ValueError("close and volume are required")
    close = pd.to_numeric(bars["close"], errors="raise").astype(float)
    volume = pd.to_numeric(bars["volume"], errors="raise").astype(float)
    if np.isinf(close).any() or np.isinf(volume).any():
        raise ValueError("prices and volumes must be finite or missing")
    if (close <= 0).any() or (volume < 0).any():
        raise ValueError("available prices must be positive and volumes nonnegative")

    result = pd.DataFrame(index=bars.index)
    for window in (5, 10, 20, 60):
        result[f"ma{window}"] = close.rolling(window, min_periods=window).mean()
    for window in (1, 5, 20):
        # Explicit division avoids pct_change's version-dependent fill behavior.
        result[f"return{window}"] = close / close.shift(window) - 1
    for window in (5, 20):
        result[f"prior_volume_mean{window}"] = volume.shift(1).rolling(window, min_periods=window).mean()
    denominator = result["prior_volume_mean5"].replace(0, np.nan)
    result["volume_ratio5"] = volume / denominator
    result["bias20"] = close / result["ma20"] - 1
    return result.loc[:, PRICE_VOLUME_FEATURES]


def _seeded_mean(values: pd.Series, *, period: int, alpha: float) -> pd.Series:
    """SMA seed followed by recursive smoothing; gaps restart the full warmup."""
    result = np.full(len(values), np.nan)
    count, total, mean = 0, 0.0, np.nan
    for i, value in enumerate(values.to_numpy(dtype=float)):
        if np.isnan(value):
            count, total, mean = 0, 0.0, np.nan
            continue
        if count < period:
            count += 1
            total += value
            if count == period:
                mean = total / period
        else:
            mean += alpha * (value - mean)
        result[i] = mean
    return pd.Series(result, index=values.index)


def calculate_standard_technical_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Calculate the 27-field daily bundle from one anchored OHLCV history.

    Requires close/high/low/volume; open and extra columns are optional. The
    caller cuts at as-of BEFORE calling and retains the same historical anchor
    for recursive values. Requested display length must not change that anchor.
    Formula details and retention boundaries live in 14_window_retention.md.
    This function performs no database access or destructive retention work.
    """
    result = calculate_price_volume_features(bars)
    if not {"high", "low"}.issubset(bars.columns):
        raise ValueError("high and low are required for the standard bundle")
    close, high, low = (
        pd.to_numeric(bars[name], errors="raise").astype(float)
        for name in ("close", "high", "low")
    )
    if np.isinf(high).any() or np.isinf(low).any() or (high <= 0).any() or (low <= 0).any():
        raise ValueError("available high/low prices must be positive and finite")
    if ((high < low) | (high < close) | (low > close)).any():
        raise ValueError("high/low must enclose the same-basis close")

    prior = close.shift(1)
    for window in (20, 60):
        result[f"prior_close_high{window}"] = prior.rolling(window, min_periods=window).max()
    result["prior_close_low20"] = prior.rolling(20, min_periods=20).min()
    result["distance_prior_high20"] = close / result["prior_close_high20"] - 1

    fast = _seeded_mean(close, period=12, alpha=2 / 13)
    slow = _seeded_mean(close, period=26, alpha=2 / 27)
    result["macd_dif"] = fast - slow
    result["macd_dea"] = _seeded_mean(result["macd_dif"], period=9, alpha=2 / 10)
    result["macd_hist"] = result["macd_dif"] - result["macd_dea"]

    change = close.diff()
    gain = _seeded_mean(change.clip(lower=0), period=14, alpha=1 / 14)
    loss = _seeded_mean((-change).clip(lower=0), period=14, alpha=1 / 14)
    # A flat window has no directional strength; v1 explicitly uses neutral 50.
    total = gain + loss
    result["rsi14"] = (100 * gain / total.replace(0, np.nan)).mask(total == 0, 50.0)

    true_range = pd.concat((high - low, (high - prior).abs(), (low - prior).abs()), axis=1).max(axis=1, skipna=False)
    # Missing close invalidates this observation even if both high and low exist.
    true_range = true_range.where(close.notna())
    result["atr14"] = _seeded_mean(true_range, period=14, alpha=1 / 14)
    result["atr_ratio14"] = result["atr14"] / close

    middle = result["ma20"]
    deviation = close.rolling(20, min_periods=20).std(ddof=0)
    result["boll_mid20"] = middle
    result["boll_upper20"] = middle + 2 * deviation
    result["boll_lower20"] = middle - 2 * deviation
    width = 4 * deviation
    result["boll_width20"] = width / middle
    result["boll_percent_b20"] = (close - result["boll_lower20"]) / width.replace(0, np.nan)
    returns = close / prior - 1
    result["volatility20"] = returns.rolling(20, min_periods=20).std(ddof=1) * np.sqrt(252)
    return result.loc[:, STANDARD_TECHNICAL_FEATURES]


def calculate_standard_technical_window(
    bars: pd.DataFrame, *, as_of: str | pd.Timestamp, output_bars: int = 1,
    indicators: Sequence[str] = STANDARD_TECHNICAL_FEATURES,
) -> pd.DataFrame:
    """Cut at as-of, limit finite inputs, then return only requested observations.

    Recursive requests preserve the supplied source anchor. The adapter must
    always load from that same anchor, irrespective of requested output length.
    Timestamps follow the input index convention (daily dates for daily bars).
    """
    needed = required_technical_input_bars(indicators, output_bars=output_bars)
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.hasnans or not bars.index.is_unique or not bars.index.is_monotonic_increasing:
        raise ValueError("bar timestamps must be nonmissing, unique and ascending")
    cutoff = pd.Timestamp(as_of)
    if pd.isna(cutoff):
        raise ValueError("as_of must be a valid timestamp")
    inputs = bars.loc[bars.index <= cutoff]
    if needed is not None:
        inputs = inputs.tail(needed)
    calculator = (
        calculate_price_volume_features if set(indicators).issubset(PRICE_VOLUME_FEATURES)
        else calculate_standard_technical_features
    )
    return calculator(inputs).loc[:, list(indicators)].tail(output_bars)

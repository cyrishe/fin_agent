"""Cooling-minute features require complete, same-day bars."""
import numpy as np
import pandas as pd

from scripts.experiment_automl_limit_rebound_cooling_minute import extract_cooling_minute


def _bars():
    morning = pd.date_range("2026-05-06 09:31", periods=120, freq="min")
    afternoon = pd.date_range("2026-05-06 13:01", periods=120, freq="min")
    close = np.linspace(10.0, 10.5, 240)
    return pd.DataFrame({"time": [str(t) for t in (*morning, *afternoon)],
                         "open": close, "high": close + .1,
                         "low": close - .1, "close": close,
                         "volume": np.ones(240) * 100,
                         "turnover": close * 100})


def test_complete_cooling_day_uses_only_day_minutes():
    features = extract_cooling_minute(_bars(), 10.5)
    assert features is not None
    assert features["cool_last30_return"] > 0
    assert features["cool_last30_amount_fraction"] > 0
    assert features["cool_high_minute"] == 240
    assert features["cool_low_minute"] == 1


def test_rejects_incomplete_or_mismatched_minute_source():
    bars = _bars()
    assert extract_cooling_minute(bars.iloc[:-1], 10.5) is None
    assert extract_cooling_minute(bars, 11.0) is None

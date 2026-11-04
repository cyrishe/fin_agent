"""Stable four-class boundaries and pre-outcome tree/model selection."""

import pandas as pd

from scripts.experiment_automl_short_window_0940 import daily_distribution
from scripts.experiment_automl_validated_pure_leaves import choose


def test_daily_distribution_counts_0940_and_second_high_separately():
    rows = pd.DataFrame({
        "signal_date": ["2026-09-02"] * 4,
        "signal_price": [100.] * 4,
        "close_40": [99., 100., 101., 103.],
        "second_high": [103., 101., 100., 99.],
    })
    result = daily_distribution(rows)
    assert result.known.eq(4).all()
    assert result[["ge3", "1to3", "0to1", "lt0"]].eq(1).all().all()


def test_pure_leaf_priority_can_fallback_without_using_outcomes():
    scored = pd.DataFrame([
        {"signal_date": "2026-09-02", "symbol6": "000001",
         "p_lt0": .10, "p_0to1": .10, "p_1to3": .25, "p_ge3": .55,
         "predicted_class": "ge3", "strict": False, "moderate": False,
         "valid_smoothed": .35},
        {"signal_date": "2026-09-02", "symbol6": "000002",
         "p_lt0": .20, "p_0to1": .20, "p_1to3": .40, "p_ge3": .20,
         "predicted_class": "1to3", "strict": False, "moderate": True,
         "valid_smoothed": .60},
    ])
    assert choose(scored, "baseline19").symbol6.iloc[0] == "000001"
    assert choose(scored, "moderate_priority_class").symbol6.iloc[0] == "000002"
    assert choose(scored, "strict_only").empty
    scored["moderate"] = False
    assert choose(scored, "moderate_priority_class").symbol6.iloc[0] == "000001"

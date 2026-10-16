"""Random training subsets and next-day selection have fixed time boundaries."""

import pandas as pd

from scripts.experiment_automl_four_class_subset_selection import (
    choose_winner, random_training_subset,
)


def test_random_subset_repeats_seed_and_keeps_every_historical_day():
    days = pd.bdate_range("2026-08-03", periods=19).strftime("%Y-%m-%d")
    rows = pd.DataFrame({"signal_date": days.repeat(10),
                         "symbol6": [f"{i:06d}" for i in range(190)]})
    for fraction, expected in ((.8, 152), (.9, 171)):
        first = random_training_subset(rows, fraction, 42)
        second = random_training_subset(rows, fraction, 42)
        assert first.equals(second)
        assert len(first) == expected
        assert first.groupby("signal_date").size().eq(expected // 19).all()


def test_winner_uses_tminus1_realized_return_and_stable_tie_break():
    results = pd.DataFrame([
        {"subset": "random_80_01", "fraction": .8, "repeat": 1,
         "return_0940": .03},
        {"subset": "random_90_02", "fraction": .9, "repeat": 2,
         "return_0940": .03},
        {"subset": "random_90_01", "fraction": .9, "repeat": 1,
         "return_0940": -.01},
        {"subset": "random_80_02", "fraction": .8, "repeat": 2,
         "return_0940": float("nan")},
    ])
    assert choose_winner(results) == "random_90_02"

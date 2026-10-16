"""T−1 diagnostics rank models using only pre-outcome metrics."""

import pandas as pd

from scripts.analyze_automl_tminus1_model_diagnostics import (
    metric_selections, within_day_correlations,
)


def test_metric_selection_uses_metric_not_realized_return():
    rows = pd.DataFrame([
        {"test_date": "2026-09-02", "subset": "first", "fraction": .8,
         "repeat": 1, "train_loss": .4, "top1_p_ge3": .7,
         "tminus1_return": -.02, "tminus1_symbol6": "000001"},
        {"test_date": "2026-09-02", "subset": "second", "fraction": .9,
         "repeat": 1, "train_loss": .2, "top1_p_ge3": .2,
         "tminus1_return": .04, "tminus1_symbol6": "000002"},
    ])
    for name in ("oob_loss", "all19_loss"):
        rows[name] = rows.train_loss
    for name in ("top1_p_good_minus_bad", "top1_confidence"):
        rows[name] = rows.top1_p_ge3
    selected = metric_selections(rows).set_index("metric")
    assert selected.loc["train_loss", "chosen_subset"] == "second"
    assert selected.loc["top1_p_ge3", "chosen_subset"] == "first"


def test_within_day_correlation_skips_constant_outcome():
    rows = pd.DataFrame({
        "test_date": ["2026-09-02"] * 3,
        "train_loss": [.3, .2, .1],
        "tminus1_return": [.01, .01, .01],
    })
    for name in ("oob_loss", "all19_loss", "top1_p_ge3",
                 "top1_p_good_minus_bad", "top1_confidence"):
        rows[name] = rows.train_loss
    result = within_day_correlations(rows)
    assert result.spearman.isna().all()

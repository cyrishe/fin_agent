from __future__ import annotations

import pandas as pd

from scripts.experiment_automl_four_class_tree_leaves import (
    select_with_leaf, tree_leaf_scores,
)


def test_small_pure_leaf_is_shrunk_to_training_base_rate():
    train = pd.DataFrame({"x": [0] * 25 + [1] * 25,
                          "signal_price": [100.0] * 50,
                          "second_high": [104.0] * 20 + [100.0] * 30})
    test = pd.DataFrame({"x": [0, 1]})
    scored, leaves = tree_leaf_scores(train, test, ("x",),
                                      {"max_depth": 1, "min_samples_leaf": 10})
    assert scored.raw_ge3.tolist() == [.8, 0.0]
    assert scored.smoothed_ge3.tolist() == [.48, .32]
    assert leaves.n.tolist() == [25, 25]


def test_purity_priority_keeps_four_class_gate_and_changes_rank_only():
    scored = pd.DataFrame({
        "symbol6": ["000001", "000002", "000003"],
        "predicted_class": ["lt0", "ge3", "1to3"],
        "p_ge3": [.2, .34, .25], "p_1to3": [.3, .30, .35],
        "small7_n": [30, 100, 30],
        "small7_raw_ge3": [.8, .2, .5],
        "small7_smoothed_ge3": [.3, .18, .22],
        "small7_smoothed_ge1": [.5, .46, .5],
    })
    baseline = select_with_leaf(scored, "baseline")
    priority = select_with_leaf(scored, "small7_purity_first")
    assert baseline.symbol6.tolist() == ["000002", "000003"]
    assert priority.symbol6.tolist() == ["000003", "000002"]
    assert "000001" not in priority.symbol6.tolist()

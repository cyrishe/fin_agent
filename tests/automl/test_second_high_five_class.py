import numpy as np
import pandas as pd

from scripts.audit_automl_four_class_decisions import class_priority_fallback
from scripts.experiment_automl_second_high_five_class import (
    LABELS, cost_matrix, five_class, project_cumulative, probability_metrics)


def test_five_class_boundaries_are_disjoint():
    returns = pd.Series([-.011, -.01, -.0001, 0, .0099, .01, .0299, .03])
    assert list(five_class(returns).astype(str)) == [
        "ltm1", "m1to0", "m1to0", "0to1", "0to1", "1to3", "1to3", "ge3"]


def test_cumulative_projection_preserves_order_and_probability_sum():
    raw = np.array([[.8, .9, .3, .4], [.9, .7, .4, .1]])
    probability = project_cumulative(raw)
    assert probability.shape == (2, 5)
    assert np.all(probability >= 0)
    assert np.allclose(probability.sum(axis=1), 1)
    assert np.allclose(probability[0], [.2, 0, .5, 0, .3])


def test_ordinal_cost_penalizes_overoptimistic_extreme_more():
    cost = cost_matrix()
    assert cost[LABELS.index("ltm1"), LABELS.index("ge3")] == 16
    assert cost[LABELS.index("ge3"), LABELS.index("ltm1")] == 4
    probabilities = np.array([[.9, .025, .025, .025, .025],
                              [.025, .025, .025, .025, .9]])
    metrics = probability_metrics(pd.Series(["ltm1", "ge3"]), probabilities)
    assert metrics["accuracy"] == 1
    assert metrics["log_loss"] > 0


def test_five_class_fallback_uses_only_two_upper_predicted_classes():
    frame = pd.DataFrame([
        ("day1", "000001", "ltm1", .9, .05),
        ("day1", "000002", "1to3", .45, .1),
        ("day1", "000003", "ge3", .25, .3),
        ("day2", "000004", "m1to0", .4, .05),
    ], columns=["signal_date", "symbol6", "predicted_class", "p_1to3", "p_ge3"])
    selected = class_priority_fallback(frame)
    assert selected.symbol6.tolist() == ["000003", "000002"]
    assert selected.selection_rank.tolist() == [1, 2]

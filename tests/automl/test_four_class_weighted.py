import numpy as np
import pandas as pd

from scripts.experiment_automl_four_class_weighted import (
    balanced_class_weights, prediction_metrics)


def test_fold_weights_equalize_class_contribution():
    labels = pd.Series(["lt0"]*8 + ["0to1"]*4 + ["1to3"]*2 + ["ge3"])
    weights = balanced_class_weights(labels)
    assert [8*weights["lt0"], 4*weights["0to1"],
            2*weights["1to3"], weights["ge3"]] == [3.75]*4


def test_log_loss_respects_business_class_order():
    rows = pd.DataFrame({
        "class": ["lt0", "0to1", "1to3", "ge3"],
        "predicted_class": ["lt0", "0to1", "1to3", "ge3"],
        "signal_date": ["day"]*4,
        "p_lt0": [.7, .1, .1, .1],
        "p_0to1": [.1, .7, .1, .1],
        "p_1to3": [.1, .1, .7, .1],
        "p_ge3": [.1, .1, .1, .7],
    })
    assert np.isclose(prediction_metrics(rows)["log_loss_on_natural_distribution"],
                      -np.log(.7))

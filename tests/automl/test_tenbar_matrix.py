import numpy as np

from scripts.experiment_automl_matrix_10bar import target_class


def test_target_thresholds_are_strict_at_one_and_half_percent():
    values = np.array([.010001, .01, .0075, .005, .004999, -.03])
    assert target_class(values).tolist() == [1, 0, 0, 0, -1, -1]

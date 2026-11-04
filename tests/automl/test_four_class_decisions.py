import pandas as pd

from scripts.audit_automl_four_class_decisions import (
    class_priority_fallback, matrix_metrics, selection_columns)
from scripts.experiment_automl_second_high_four_class import CLASSES


def test_matrix_metrics_reads_predicted_columns_and_majority_baseline():
    matrix = pd.DataFrame([
        [4, 1, 2, 1], [2, 3, 1, 1], [1, 1, 3, 2], [1, 1, 1, 5],
    ], index=CLASSES, columns=CLASSES)
    result = matrix_metrics(matrix)
    assert result["correct"] == 15
    assert result["total"] == 30
    assert result["accuracy"] == .5
    assert result["majority_label"] == "lt0"
    assert result["majority_class_accuracy"] == 8/30
    assert result["predicted_column_groups"]["ge3"] == {
        "predicted_count": 9, "actual_ge1": 7,
        "actual_0to1": 1, "actual_lt0": 1}


def test_fallback_prioritizes_ge3_then_ge1_and_can_skip_a_day():
    frame = pd.DataFrame([
        ("day1", "000001", "1to3", .1, .2, .6),
        ("day1", "000002", "ge3", .2, .1, .3),
        ("day1", "000003", "1to3", .1, .4, .2),
        ("day1", "000004", "lt0", .7, .1, .1),
        ("day2", "000005", "lt0", .7, .1, .1),
    ], columns=["signal_date", "symbol6", "predicted_class",
                "p_lt0", "p_1to3", "p_ge3"])
    selected = class_priority_fallback(frame)
    assert selected.symbol6.tolist() == ["000002", "000001"]
    assert selected.selection_rank.tolist() == [1, 2]
    assert selected.signal_date.unique().tolist() == ["day1"]


def test_selection_columns_uses_actual_labels_within_each_selected_prediction():
    frame = pd.DataFrame([
        ("ge3", "1to3"), ("ge3", "lt0"),
        ("1to3", "ge3"), ("1to3", "0to1"),
    ], columns=["predicted_class", "class"])
    result = selection_columns(frame)
    assert result["ge3"] == {
        "selected": 2, "actual_ge1": 1, "actual_0to1": 0, "actual_lt0": 1}
    assert result["1to3"] == {
        "selected": 2, "actual_ge1": 1, "actual_0to1": 1, "actual_lt0": 0}

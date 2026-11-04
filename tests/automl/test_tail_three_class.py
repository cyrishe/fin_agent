import pandas as pd
import pytest

from scripts.experiment_automl_tail_three_class import (
    classify_high10, day_folds, select_online,
)


def test_three_class_boundaries_are_strict():
    assert classify_high10([-0.02, 0.0049, 0.005, 0.01, 0.0101]).tolist() == [
        -1, -1, 0, 0, 1,
    ]
    with pytest.raises(ValueError):
        classify_high10([float("nan")])


def test_expanding_day_folds_keep_future_days_out_of_training():
    dates = pd.bdate_range("2026-08-24", periods=16)
    rows = pd.DataFrame({"signal_date": dates, "symbol6": ["000001"] * len(dates)})
    folds = list(day_folds(rows))
    assert len(folds) == 2
    assert [len(train) for train, _ in folds] == [10, 15]
    assert [len(test) for _, test in folds] == [5, 1]
    for train, test in folds:
        assert train.signal_date.max() < test.signal_date.min()


def test_online_selection_respects_week_and_day_caps_and_can_abstain():
    monday = pd.Timestamp("2026-09-07")
    rows = pd.DataFrame([
        {"signal_date": monday + pd.Timedelta(days=int(day)),
         "symbol6": f"{day*10 + stock:06d}",
         "p_positive": 0.9 - stock * 0.01}
        for day in (0, 1, 2) for stock in range(3)
    ] + [{"signal_date": monday + pd.Timedelta(days=7),
          "symbol6": "600000", "p_positive": 0.7}])
    selected = select_online(rows)
    assert len(selected) == 5
    assert selected.groupby("signal_date").size().max() == 2
    assert selected.signal_date.max() < monday + pd.Timedelta(days=7)

"""Temporal and execution-price checks for the uniform 15-minute study."""

import pandas as pd

from scripts.automl_st_status import ELIGIBLE_TYPES, attach_st_status
from scripts.build_automl_sina_15m_rolling import at_nominal_five_limit
from scripts.experiment_automl_four_class_15m_rolling import add_returns, forward_folds
from scripts.experiment_automl_second_high_four_class import CLASSES, four_class


def test_july_fold_uses_only_twenty_earlier_signal_days():
    training_dates = pd.bdate_range("2026-06-01", periods=20).strftime("%Y-%m-%d")
    rows = pd.DataFrame([
        {"signal_date": date, "class": label}
        for date in [*training_dates, "2026-07-01"] for label in CLASSES
    ])
    date, prior_dates, training, prediction = next(forward_folds(rows))
    assert date == "2026-07-01"
    assert prior_dates == list(training_dates)
    assert training.signal_date.nunique() == 20
    assert training.signal_date.max() < prediction.signal_date.min()
    assert len(prediction) == 4


def test_close_target_and_four_class_cutoffs():
    rows = pd.DataFrame({
        "signal_price": [10.0] * 4,
        "close_1500": [10.0] * 4,
        "close_0945": [9.99, 10.0, 10.1, 10.3],
        "close_1000": [10.0] * 4,
        "close_1015": [10.0] * 4,
        "high_0945": [10.0, 10.1, 10.2, 10.4],
    })
    observed = add_returns(rows)
    assert observed.return_0945_pct.round(5).tolist() == [-.1, 0, 1, 3]
    assert list(four_class((observed.return_0945_pct / 100).round(10))) == list(CLASSES)
    assert observed.return_1000_pct.tolist() == [0] * 4


def test_nominal_five_limit_uses_exchange_cent_rounding():
    assert at_nominal_five_limit(21.03, 22.08)
    assert at_nominal_five_limit(22.08, 23.18)
    assert at_nominal_five_limit(23.18, 24.34)
    assert not at_nominal_five_limit(21.03, 22.07)


def test_historical_st_transition_uses_signal_date():
    rows = pd.DataFrame({"signal_date": ["2026-07-01", "2026-07-02"],
                         "symbol6": ["002977", "002977"]})
    intervals = pd.DataFrame({
        "symbol6": ["002977", "002977"], "st_type": ["N", "Y"],
        "begin_date": pd.to_datetime(["2020-01-01", "2026-07-02"]),
        "end_date": pd.to_datetime(["2026-07-02", "2100-01-01"]),
        "ann_date": pd.to_datetime(["2020-01-01", "2026-07-01"]),
    })
    matched = attach_st_status(rows, intervals)
    assert matched.st_type.tolist() == ["N", "Y"]
    assert "N" in ELIGIBLE_TYPES and "Y" not in ELIGIBLE_TYPES

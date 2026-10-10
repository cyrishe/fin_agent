import pandas as pd

from scripts.backtest_automl_staged_minute_exit import exit_on_closes
from scripts.experiment_automl_second_high_four_class import four_class


def test_four_class_boundaries_are_disjoint():
    values = pd.Series([-.0101, -.0001, 0, .0099, .01, .0299, .03, .031])
    assert list(four_class(values).astype(str)) == [
        "lt0", "lt0", "0to1", "0to1", "1to3", "1to3", "ge3", "ge3"]


def test_staged_exit_respects_time_and_first_trigger():
    early = exit_on_closes(100, [104, 97, 98, 105, 105, 105, 105, 105, 105, 105])
    assert (early["exit_minute"], early["rule"], early["exit_price"]) == (
        "09:31", "first3_over_3pct", 104)
    rebound = exit_on_closes(100, [99, 97, 98, 105, 105, 105, 105, 105, 105, 105])
    assert (rebound["exit_minute"], rebound["rule"], rebound["exit_price"]) == (
        "09:33", "rebound_1pp_from_prior_low", 98)
    late = exit_on_closes(100, [101.5, 101.5, 101.5, 102, 104, 104, 104, 104, 104, 104])
    assert (late["exit_minute"], late["rule"]) == ("09:34", "last7_over_1pct")
    fallback = exit_on_closes(100, [100]*10)
    assert (fallback["exit_minute"], fallback["rule"]) == ("09:40", "0940_close")

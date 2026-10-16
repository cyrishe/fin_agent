import pandas as pd
import pytest

from scripts.analyze_automl_tail_critical import rank_by_day, rule_metrics


def test_neutral_counts_as_pass_and_only_negative_is_critical():
    day = pd.Timestamp("2026-09-08")
    rows = pd.DataFrame({"signal_date": [day] * 3,
                         "class": [-1, 0, 1],
                         "critical": [1, 0, 0],
                         "pass": [0, 1, 1],
                         "below_entry": [1, 0, 0]})
    selected = pd.Series([False, True, True])
    result = rule_metrics(rows, selected, pd.Series({day: 1 / 3}))
    assert (result["n"], result["critical"], result["pass"],
            result["neutral"], result["strong"]) == (2, 0, 2, 1, 1)
    assert result["critical_difference_pp"] == pytest.approx(-100 / 3)


def test_daily_ranks_break_equal_scores_by_stock_code():
    rows = pd.DataFrame({"signal_date": [pd.Timestamp("2026-09-08")] * 3,
                         "symbol6": ["600002", "000001", "600001"],
                         "score": [0.8, 0.8, 0.7]})
    rank_by_day(rows, "score", "rank")
    assert rows["rank"].tolist() == [2, 1, 3]

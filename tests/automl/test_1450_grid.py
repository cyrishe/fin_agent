import numpy as np
import pandas as pd
import pytest

from scripts import experiment_automl_1450_grid as grid

board_limit, metrics, select = grid.board_limit, grid.metrics, grid.select


def test_near_limit_and_unknown_boards_are_excluded():
    assert board_limit("600001", "普通股份") == 0.095
    assert board_limit("300001", "创业板") == 0.195
    assert board_limit("688001", "科创板") == 0.195
    assert np.isnan(board_limit("600001", "*ST示例"))
    assert np.isnan(board_limit("999999", "未知市场"))


def test_score_gate_allows_abstention_and_top_k_per_day():
    rows = pd.DataFrame({"date": pd.to_datetime(["2026-09-01"] * 3 + ["2026-09-02"] * 2),
                         "symbol6": ["000001", "000002", "000003", "000001", "000002"]})
    selected = select(rows, np.array([0.9, 0.8, 0.7, 0.4, 0.3]), 2, 0.75)
    assert selected.symbol6.tolist() == ["000001", "000002"]
    assert selected.date.nunique() == 1


def test_hit_safety_and_exit_are_separate_metrics():
    rows = pd.DataFrame({"date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
                         "label_observed": [True, True],
                         "hit1": [True, False], "high_return": [0.012, 0.005],
                         "low_return": [-0.02, -0.003],
                         "exit_return": [0.01, -0.002]})
    result = metrics(rows, "hit1")
    assert result["hit_rate"] == 0.5
    assert result["hit_after_30bp"] == 0.0
    assert result["safe_rate"] == 0.5
    assert result["severe_down_rate"] == 0.5
    assert result["exit_safe_rate"] == 1.0
    assert result["exit_nonnegative_rate"] == 0.5
    assert result["exit_severe_down_rate"] == 0.0
    assert result["exit_severe_or_unobserved_rate"] == 0.0
    assert result["mean_exit_return_net_30bp"] == 0.001


def test_missing_next_morning_is_not_silently_dropped():
    rows = pd.DataFrame({"date": pd.to_datetime(["2026-09-01", "2026-09-01"]),
                         "label_observed": [True, False], "hit1": [True, False],
                         "high_return": [0.02, np.nan], "low_return": [-0.002, np.nan],
                         "exit_return": [0.01, np.nan]})
    result = metrics(rows, "hit1")
    assert result["signals"] == 2
    assert result["unobserved"] == 1
    assert result["hit_rate"] == 0.5
    assert result["exit_severe_or_unobserved_rate"] == 0.5


def test_recent_factors_use_only_prior_daily_bars(monkeypatch):
    days = pd.date_range("2026-08-01", periods=9, freq="D")
    source = pd.DataFrame({"date": days, "symbol6": ["600001"] * len(days),
                           "close": [10, 11, 12, 13, 14, 15, 16, 17, 18],
                           "preclose": [10, 10, 11, 12, 13, 14, 15, 16, 17],
                           "amount": [20_000_000] * len(days),
                           "turn_ratio": [1.0] * len(days)})
    monkeypatch.setattr(grid, "frame", lambda *_args, **_kwargs: source.copy())
    factors = grid.read_daily(object())
    assert factors.iloc[1].prior_return_1 == pytest.approx(0.0)
    assert factors.iloc[-1].prior_return_1 == pytest.approx(17 / 16 - 1)
    assert factors.iloc[-1].today_preclose == 17


def test_too_few_labeled_dates_rejected():
    source = pd.DataFrame({"date": pd.to_datetime(["2026-09-01"]), "symbol6": ["600001"]})
    with pytest.raises(ValueError, match="too few"):
        grid.experiment(source, ["2026-09-01"])

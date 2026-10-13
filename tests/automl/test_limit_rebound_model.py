"""Check the experiment's temporal separation and daily selection metrics."""
import numpy as np
import pandas as pd

from scripts.experiment_automl_limit_rebound_model import evaluate, temporal_split, topk_by_day


def test_split_purges_one_event_across_period_boundaries():
    rows = pd.DataFrame([
        {"symbol": "000001", "first_limit_date": "2026-06-25", "asof_date": "2026-06-29"},
        {"symbol": "000001", "first_limit_date": "2026-06-25", "asof_date": "2026-07-01"},
        {"symbol": "000002", "first_limit_date": "2026-06-27", "asof_date": "2026-06-29"},
        {"symbol": "000003", "first_limit_date": "2026-08-27", "asof_date": "2026-08-28"},
        {"symbol": "000003", "first_limit_date": "2026-08-27", "asof_date": "2026-09-01"},
        {"symbol": "000004", "first_limit_date": "2026-09-01", "asof_date": "2026-09-01"},
    ])
    train, valid, final_train, test = temporal_split(rows)
    assert train.symbol.tolist() == ["000002"]
    assert valid.symbol.tolist() == ["000001", "000003"]
    assert set(final_train.symbol) == {"000001", "000002"}
    assert set(test.symbol) == {"000003", "000004"}


def test_top_five_random_expectation_uses_each_days_base_rate():
    rows = pd.DataFrame({
        "symbol": [f"{i:06d}" for i in range(12)],
        "first_limit_date": ["2026-01-01"] * 12,
        "streak_end_date": ["2026-01-01"] * 12,
        "asof_date": ["2026-01-02"] * 6 + ["2026-01-05"] * 6,
        "cooling_day": [1] * 12,
        "label_next_limit_up": [1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0],
    })
    score = np.array([.9, .8, .7, .6, .5, .4, .9, .8, .7, .6, .5, .4])
    top = topk_by_day(rows, score, "label_next_limit_up")
    result = evaluate(rows, score, "label_next_limit_up")
    assert len(top) == 10
    assert result["top5_hits"] == 3
    assert abs(result["top5_random_expected_hits"] - 2.5) < 1e-12

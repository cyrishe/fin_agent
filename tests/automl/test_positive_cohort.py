import pandas as pd
import pytest

from scripts import audit_automl_positive_cohort as cohort
from scripts import experiment_automl_positive_first as model_check


def test_adjacent_prediction_dates_reuse_overlapping_daily_history(monkeypatch):
    dates = pd.date_range("2026-09-01", periods=7, freq="D")
    close = [10, 11, 12, 13, 14, 15, 16]
    source = pd.DataFrame({"date": dates, "symbol6": ["600001"] * 7,
                           "close": close, "preclose": [10, *close[:-1]],
                           "avg_price": [10] * 7, "amount": [20_000_000] * 7,
                           "is_limit_price": [0] * 7,
                           "create_time": [f"{d.date()} 16:00:00" for d in dates],
                           "update_time": [f"{d.date()} 16:00:00" for d in dates]})
    monkeypatch.setattr(cohort, "frame", lambda *_args, **_kwargs: source.copy())
    history = cohort.daily_history(object())
    assert history.iloc[5].prior_return_3 == pytest.approx(15 / 12 - 1)
    assert history.iloc[6].prior_return_3 == pytest.approx(16 / 13 - 1)
    assert history.iloc[5].prior_return_5 == pytest.approx(15 / 10 - 1)
    assert history.iloc[6].prior_return_5 == pytest.approx(16 / 11 - 1)
    assert history.iloc[5].history_contiguous6
    assert history.iloc[6].history_contiguous6


def test_suspended_day_does_not_count_as_consecutive_history(monkeypatch):
    dates = pd.date_range("2026-09-01", periods=7, freq="D")
    records = []
    for day in dates:
        for symbol in ("600001", "600002"):
            if symbol == "600001" and day == dates[3]:
                continue
            recorded_at = f"{day.date()} 16:00:00"
            records.append({"date": day, "symbol6": symbol, "close": 10.0,
                            "preclose": 10.0, "avg_price": 10.0, "amount": 20_000_000,
                            "is_limit_price": 0,
                            "create_time": recorded_at,
                            "update_time": recorded_at})
    monkeypatch.setattr(cohort, "frame", lambda *_args, **_kwargs: pd.DataFrame(records))
    history = cohort.daily_history(object())
    final = history[history.date.eq(dates[-1])].set_index("symbol6")
    assert not final.loc["600001", "history_contiguous6"]
    assert final.loc["600002", "history_contiguous6"]


def test_feature_contrast_compares_hits_with_non_hits_within_each_day():
    sample = pd.DataFrame({"date": pd.to_datetime(["2026-09-01"] * 2 +
                                                    ["2026-09-02"] * 2),
                           "hit5": [True, False, True, False],
                           "prior_return_5": [0.03, -0.01, 0.01, 0.0]})
    result = cohort.feature_contrasts(sample, ["prior_return_5"])["prior_return_5"]
    assert result["compared_days"] == 2
    assert result["positive_delta_days"] == 2
    assert result["daily_median_delta"] == pytest.approx(0.025)


def test_t_final_limit_flag_affects_target_only_and_prior_revision_must_arrive():
    signal_day = pd.Timestamp("2026-09-01")
    previous_day = pd.Timestamp("2026-08-31")
    asof = pd.DataFrame([{"date": signal_day, "symbol6": "600001",
                          "prior_amount_ratio_5": 1.2, "market_breadth": 0.6,
                          "day_return": 0.01, "tail_return_20m": 0.002,
                          "observed": True, "hit1": True, "hit1_10m": True,
                          "near_limit_at_entry": False}])
    daily = pd.DataFrame([
        {"date": previous_day, "symbol6": "600001", "prior_return_5": 0.04,
         "prior_amount_ratio_5": 1.2, "prior_avg_bias_3": 0.003,
         "history_create_time": "2026-08-31 16:00:00",
         "history_update_time": "2026-08-31 16:00:00",
         "history_contiguous6": True, "is_limit_price": 0},
        {"date": signal_day, "symbol6": "600001", "prior_return_5": 0.05,
         "prior_amount_ratio_5": 1.3, "prior_avg_bias_3": 0.004,
         "history_create_time": "2026-09-01 16:00:00",
         "history_update_time": "2026-09-01 16:00:00",
         "history_contiguous6": True, "is_limit_price": 1},
    ])
    limited, _ = model_check.prepare_model_frame(asof, daily)
    assert len(limited) == 1
    assert not limited.iloc[0].actionable_hit5
    assert limited.iloc[0].prior_return_5 == pytest.approx(0.04)
    not_limited_daily = daily.copy()
    not_limited_daily.loc[1, "is_limit_price"] = 0
    ordinary, _ = model_check.prepare_model_frame(asof, not_limited_daily)
    assert ordinary.iloc[0].actionable_hit5
    assert limited[list(model_check.WITH_LOCAL)].equals(
        ordinary[list(model_check.WITH_LOCAL)])
    late_revision = daily.copy()
    late_revision.loc[0, "history_update_time"] = "2026-09-01 15:00:00"
    rejected, _ = model_check.prepare_model_frame(asof, late_revision)
    assert rejected.empty
    gap = daily.copy()
    gap.loc[0, "history_contiguous6"] = False
    rejected_gap, _ = model_check.prepare_model_frame(asof, gap)
    assert rejected_gap.empty

import pandas as pd
import pytest

from scripts import experiment_automl_1449_asof as asof


def source_rows():
    day = pd.Timestamp("2026-09-22")
    points = pd.DataFrame({
        "date": [day, day], "symbol6": ["600001", "600002"], "name": ["甲", "乙"],
        "p1410": [10.0, 10.0], "p1430": [10.2, 9.8],
        "amount_prev10": [300_000, 300_000], "amount_tail10": [400_000, 400_000],
        "bars": [22, 22], "fallback": [0, 0], "finalized": [1, 1],
        "source_time": ["2026-09-22 14:30:00"] * 2,
        "fetch_time": ["2026-09-22 14:45:00"] * 2,
        "created_at": ["2026-09-22 14:45:01"] * 2,
        "updated_at": ["2026-09-22 14:45:02"] * 2,
    })
    prior = pd.DataFrame({
        "prior_date": pd.to_datetime(["2026-09-21"] * 2),
        "symbol6": ["600001", "600002"], "prior_close": [10.0, 10.0],
        "prior_amount": [20_000_000] * 2,
        "prior_return_3": [0.02, -0.01], "prior_amount_ratio_5": [1.2, 0.8],
        "daily_created": ["2026-09-21 15:30:00"] * 2,
        "daily_updated": ["2026-09-21 15:30:00"] * 2,
    })
    return points, prior


def test_after_cutoff_row_or_daily_revision_cannot_enter_signal_universe():
    points, prior = source_rows()
    first = asof.prepare_signal(points, prior, "2026-09-22", "2026-09-21")
    assert first.symbol6.tolist() == ["600001", "600002"]
    assert first.market_breadth.tolist() == [0.5, 0.5]

    points.loc[1, "updated_at"] = "2026-09-22 14:50:00"
    second = asof.prepare_signal(points, prior, "2026-09-22", "2026-09-21")
    assert second.symbol6.tolist() == ["600001"]
    assert second.market_breadth.tolist() == [1.0]

    prior.loc[0, "daily_updated"] = "2026-09-22 15:05:00"
    assert asof.prepare_signal(points, prior, "2026-09-22", "2026-09-21").empty


def test_future_entry_change_affects_only_outcome_not_candidates_or_features():
    points, prior = source_rows()
    candidates = asof.prepare_signal(points, prior, "2026-09-22", "2026-09-21")
    candidates["next_date"] = pd.Timestamp("2026-09-23")
    entry = pd.DataFrame({"date": [pd.Timestamp("2026-09-22")],
                          "symbol6": ["600001"], "entry": [10.3],
                          "entry_fallback": [0], "entry_finalized": [1]})
    morning = pd.DataFrame({"next_date": [pd.Timestamp("2026-09-23")],
                            "symbol6": ["600001"], "open931": [10.4],
                            "high5": [10.5], "low5": [10.2], "high10": [10.6],
                            "close935": [10.4], "bars5": [5], "bars10": [10],
                            "volume5": [100], "fallback5": [0], "finalized5": [1],
                            "fallback10": [0], "finalized10": [1]})
    first = asof.attach_outcome(candidates, entry, morning)
    entry.loc[0, "entry"] = 0
    second = asof.attach_outcome(candidates, entry, morning)
    pd.testing.assert_frame_equal(first[["date", "symbol6", *asof.FEATURES]],
                                  second[["date", "symbol6", *asof.FEATURES]])
    assert first.symbol6.tolist() == second.symbol6.tolist()
    assert first.loc[0, "observed"]
    assert not second.loc[0, "observed"]
    assert not second.loc[1, "observed"]


def test_score_gate_abstains_and_keeps_at_most_one_per_day():
    rows = pd.DataFrame({"date": pd.to_datetime(["2026-09-22"] * 2),
                         "symbol6": ["600001", "600002"],
                         "tail_return_20m": [-0.06, -0.03]})
    assert asof.select_top_one(rows, [0.8, 0.7], 0.9).empty
    assert asof.select_top_one(rows, [0.8, 0.7], 0.5).symbol6.tolist() == ["600001"]


def test_sparse_decision_time_market_is_excluded():
    points, prior = source_rows()
    candidates = asof.prepare_signal(points, prior, "2026-09-22", "2026-09-21")
    assert asof.coverage_gate(candidates, minimum=3).empty
    assert len(asof.coverage_gate(candidates, minimum=2)) == 2


def test_prior_daily_rolling_values_carry_the_latest_revision_time(monkeypatch):
    dates = pd.date_range("2026-09-14", periods=6, freq="D")
    close = [10, 11, 12, 13, 14, 15]
    rows = pd.DataFrame({"prior_date": dates, "symbol6": ["600001"] * 6,
                         "prior_close": close, "preclose": [10, *close[:-1]],
                         "prior_amount": [20_000_000, 20_000_000, 20_000_000,
                                          20_000_000, 20_000_000, 30_000_000],
                         "daily_created": [f"{day.date()} 15:30:00" for day in dates],
                         "daily_updated": ["2026-09-22 16:00:00", *
                                           [f"{day.date()} 15:30:00" for day in dates[1:]]]})
    monkeypatch.setattr(asof, "frame", lambda *_args, **_kwargs: rows.copy())
    result = asof.prior_daily(object()).iloc[-1]
    assert result.prior_return_3 == pytest.approx(15 / 12 - 1)
    assert result.prior_amount_ratio_5 == pytest.approx(1.5)
    assert result.daily_updated == pd.Timestamp("2026-09-22 16:00:00")


def test_score_gate_uses_only_training_day_maxima():
    rows = pd.DataFrame({"date": pd.to_datetime(["2026-09-01", "2026-09-01",
                                                "2026-09-02", "2026-09-03"]),
                         "tail_return_20m": [0.0, -0.05, 0.0, 0.0]})
    gate = asof.training_score_gate(rows, [0.6, 0.99, 0.7, 0.8])
    assert gate == pytest.approx(0.895)

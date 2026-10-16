"""Business-boundary tests: time causality, one winner, no substitution."""
from datetime import datetime
import pandas as pd
import pytest

from scripts.automl_b2 import evaluate_morning, valid_bars, definition

DAY = "2026-10-15"
ALL = {"id": "all", "atoms": []}


def bars(opens):
    return pd.DataFrame([{"time": f"{DAY} 09:{31+i:02}:00", "open": v,
                          "close": v, "high": v, "low": v, "volume": 100}
                         for i, v in enumerate(opens)])


def picks():
    return [{"symbol6": "000001", "selection_rank": 1, "preclose": 9.09, "cap": 10., "ordinary": True},
            {"symbol6": "000002", "selection_rank": 2, "preclose": 9.09, "cap": 10., "ordinary": True}]


def evaluate(a, b, clock="09:40"):
    return evaluate_morning(picks(), {"000001": a, "000002": b}, DAY, datetime.fromisoformat(DAY + " " + clock), ALL)


def test_first_four_completion_and_future_minutes_cannot_change_signal():
    a = bars([9.8] * 10); b = bars([9.7] * 10)
    before = evaluate(a, b, "09:33:59")
    assert not any(x["shape_ready"] or x["chosen"] or x["actionable"] for x in before)
    first = evaluate(a, b, "09:34:00")
    a.loc[4:, ["open", "high", "low", "close"]] = 500
    assert evaluate(a, b, "09:34:00") == first
    assert all(x["shape_ready"] for x in first) and not any(x["actionable"] for x in first)


def test_earliest_activation_locks_rank_two_and_waits_one_complete_bar():
    a = bars([9.8]*4 + [10, 9.9, 9.9, 9.9, 9.9, 9.9])
    b = bars([9.7]*4 + [9.8, 9.85, 9.9, 9.9, 9.9, 9.9])
    at_activation = evaluate(a, b, "09:35:00")
    assert at_activation[1]["chosen"] and not any(x["actionable"] for x in at_activation)
    at_entry = evaluate(a, b, "09:36:00")
    assert [x["actionable"] for x in at_entry] == [0, 1]
    assert at_entry[1]["entry_price"] == 9.85
    assert at_entry[1]["entry_at"] == DAY + "T09:35:00"


def test_same_minute_rank_priority_and_body_rejection_no_replacement():
    a = bars([10, 9.8, 9.5, 9.1] + [9.2] * 6)
    b = bars([9.7] * 10)
    z = evaluate(a, b)
    assert z[0]["chosen"] and not z[1]["chosen"]
    assert not any(x["actionable"] for x in z)
    assert "不替补" in z[0]["reason"]


def test_locked_stock_reseals_no_switch_and_last_activation_has_no_delay_window():
    a = bars([9.8]*4 + [9.9] + [10]*5)
    b = bars([9.7]*4 + [10] + [9.8]*5)
    assert not any(x["actionable"] for x in evaluate(a, b))
    a = bars([9.8]*4 + [10]*5 + [9.9]); b = bars([10]*10)
    z = evaluate(a, b)
    assert z[0]["chosen"] and not any(x["actionable"] for x in z)


def test_missing_contender_data_does_not_fabricate_a_lower_rank_winner():
    a = bars([9.8]*10).drop(index=4)
    b = bars([9.7]*10)
    assert not any(x["chosen"] for x in evaluate(a, b))
    with pytest.raises(ValueError, match="重复"):
        evaluate(pd.concat([a, a.iloc[:1]]), b)


def test_invalid_price_and_zero_volume_are_not_buyable():
    a = bars([9.8]*10); b = bars([10]*10)
    a.loc[4:, "volume"] = 0
    assert not any(x["actionable"] for x in evaluate(a, b))
    a.loc[2, "high"] = 9
    with pytest.raises(ValueError, match="不合法"):
        evaluate(a, b)


def test_frozen_business_contract():
    d = definition()["formula"]
    assert d["observation_bars"] == 4 and d["entry_delay_bars"] == 1
    assert len(d["features"]) == 51 and not d["replacement_after_choice"]
    assert d["simulation_only"] and d["refit_validation"] is False

"""The eight-stage four-class pipeline keeps target-day outcomes out of score."""

import numpy as np
import pandas as pd
import pytest
import json

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.run_automl_four_class_pipeline import (
    infer, select_inference_candidates, select_top, similar_days, verify_next_day,
)
from scripts.run_automl_four_class_live import live_market_state
from scripts.build_automl_tail_standard import signal_days_for_build
from scripts.build_automl_1440_market_states import candidate_state
from scripts.verify_automl_four_class_live import run as verify_saved_decision


def test_current_day_projection_and_later_verification():
    rows = pd.DataFrame({
        "signal_date": ["2026-09-30"] * 3,
        "next_date": ["2026-10-08"] * 3,
        "symbol6": ["000001", "000002", "000003"],
        "name": ["A", "B", "C"],
        "signal_price": [10., 10., 10.],
        "second_high": [10.4, 10.2, 9.8],
        "close_40": [10.1, 10.0, 9.9],
        **{feature: [1., 2., 3.] for feature in FEATURES},
    })
    candidates = select_inference_candidates(rows, "2026-09-30")
    assert "second_high" not in candidates and "close_40" not in candidates
    poisoned = rows.copy()
    poisoned["second_high"] = [1., 100., 1000.]
    assert candidates.equals(select_inference_candidates(poisoned, "2026-09-30"))

    class FixedModel:
        classes_ = np.array(["0to1", "1to3", "ge3", "lt0"])

        def predict_proba(self, _):
            return np.array([[.1, .2, .5, .2], [.1, .5, .2, .2], [.1, .2, .1, .6]])

    scored = infer(FixedModel(), candidates)
    picks = select_top(scored)
    assert picks.symbol6.tolist() == ["000001", "000002"]
    verified = verify_next_day(picks, rows)
    assert verified.actual_class.tolist() == ["ge3", "1to3"]


def test_similar_day_selection_uses_only_prior_market_states():
    days = pd.bdate_range("2026-08-03", periods=23).strftime("%Y-%m-%d").tolist()
    state = pd.DataFrame({
        "signal_date": days,
        "candidate_count": [100] * 23,
        "sh_return": [0.] * 23,
        "sz_return": [0.] * 23,
        "bj_return": [0.] * 23,
        "sh_volume_hands": [1_000_000] * 23,
        "sz_volume_hands": [1_000_000] * 23,
        "bj_volume_hands": [1_000_000] * 23,
    })
    state.loc[0, "sh_return"] = .2
    selected, distances = similar_days(state, days[21])
    assert len(selected) == 20
    assert days[0] not in selected
    assert max(selected) < days[21]
    assert len(distances) == 21
    changed_future = state.copy()
    changed_future.loc[22, "candidate_count"] = 1_000_000
    assert similar_days(changed_future, days[21])[0] == selected


def test_live_market_state_uses_snapshot_and_prior_universe():
    codes = [f"600{i:03d}" for i in range(50)] + [f"000{i:03d}" for i in range(50)] + [
        f"920{i:03d}" for i in range(50)]
    quotes = pd.DataFrame({"symbol6": codes,
                           "signal_price": [10.4] * len(codes),
                           "preclose": [10.] * len(codes),
                           "volume_hands": [100.] * len(codes)})
    state = live_market_state(quotes, "2026-09-30", pd.DataFrame({"symbol6": codes}))
    assert state["candidate_count"] == len(codes)
    assert state["market_state_valid"]
    assert all(np.isclose(state[f"{group}_return"], .04) for group in ("sh", "sz", "bj"))
    assert state["sh_volume_hands"] == 5000


def test_open_end_uses_today_morning_to_label_yesterday_without_today_daily():
    completed = list(pd.to_datetime(["2026-10-08", "2026-10-09"]))
    assert signal_days_for_build(completed, "2026-10-08", "2026-10-12", True) == [
        *completed, pd.Timestamp("2026-10-12")]
    with pytest.raises(ValueError, match="Open end must follow"):
        signal_days_for_build(completed, "2026-10-08", "2026-10-09", True)


def test_saved_decision_is_verified_only_after_next_day_refresh(tmp_path):
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps({"signal_date": "2026-09-30", "selections": {
        "recent20": [{"symbol6": "000001", "name": "A", "signal_price": 10.,
                      "selection_rank": 1, "predicted_class": "ge3"}]}}))
    prepared = tmp_path / "prepared.csv"
    pd.DataFrame({"signal_date": ["2026-09-30"], "symbol6": ["000001"],
                  "second_high": [10.4], "close_40": [10.1]}).to_csv(prepared, index=False)
    summary = verify_saved_decision(decision, prepared, tmp_path / "verified.csv")
    assert summary["methods"]["recent20"]["top1"]["ge3"] == 1
    assert (tmp_path / "verified.csv").exists()


def test_no_current_candidates_yields_no_pick():
    rows = pd.DataFrame(columns=["signal_date", "next_date", "symbol6", "name",
                                 "signal_price", *FEATURES])
    candidates = select_inference_candidates(rows, "2026-09-30")
    scored = infer(object(), candidates)
    assert select_top(scored).empty


def test_incomplete_candidate_volume_is_unknown_not_zero():
    rows = pd.DataFrame({"signal_date": ["2026-09-16"] * 20,
                         "symbol6": [f"600{i:03d}" for i in range(20)],
                         "signal_return": [.04] * 20,
                         "minute_volume_shares": [100.] * 18 + [np.nan] * 2})
    state = candidate_state(rows).iloc[0]
    assert state.candidate_count == 20
    assert pd.isna(state.sh_volume_hands)

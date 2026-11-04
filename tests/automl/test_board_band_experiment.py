import pandas as pd

from scripts.build_automl_mk_1m_candidates import candidate_band_mask
from scripts.experiment_automl_board_bands import (
    adjust_outcome_prices, outcome_class, select_top)


def test_board_entry_bands_keep_legacy_default_distinct():
    frame = pd.DataFrame({
        "symbol6": ["000001", "002001", "300001", "688001", "830001"],
        "signal_return": [.02, .05, .04, .08, .06],
    })
    assert candidate_band_mask(frame, "board_volatility").tolist() == [True] * 5
    assert candidate_band_mask(frame, "legacy_3to6").tolist() == [False, True, True,
                                                                    False, True]
    assert candidate_band_mask(frame, "main_3to6_wide_4to8").tolist() == [
        False, True, True, True, True]


def test_board_specific_outcome_boundaries():
    returns = pd.Series([-.001, 0, .005, .02, .009, .01, .04])
    symbols = pd.Series(["000001"] * 4 + ["300001", "688001", "830001"])
    assert outcome_class(returns, symbols, "board").tolist() == [
        "wrong", "neutral", "acceptable", "target", "neutral", "acceptable",
        "target"]
    assert outcome_class(pd.Series([.02]), pd.Series(["000001"]), "legacy").iloc[0] == \
        "acceptable"
    assert outcome_class(pd.Series([0, .009, .01, .029, .03, .039, .04]),
                         pd.Series(["000001"] * 5 + ["300001"] * 2),
                         "board_window").tolist() == [
                             "neutral", "neutral", "acceptable", "acceptable",
                             "target", "acceptable", "target"]
    assert outcome_class(pd.Series([-.001, 0, .009, .01, .029, .03]),
                         pd.Series(["300001", "688001"] * 3),
                         "wide_only_ge3").tolist() == [
                             "wrong", "neutral", "neutral", "acceptable",
                             "acceptable", "target"]


def test_selection_uses_target_then_acceptable_and_two_slots():
    frame = pd.DataFrame({
        "signal_date": ["2025-05-06"] * 4,
        "symbol6": ["000001", "300001", "600001", "688001"],
        "predicted_class": ["acceptable", "target", "target", "wrong"],
        "p_target": [.15, .55, .65, .10],
        "p_acceptable": [.50, .20, .10, .10],
    })
    picks = select_top(frame)
    assert picks.symbol6.tolist() == ["600001", "300001"]
    assert picks.selection_rank.tolist() == [1, 2]


def test_ex_rights_next_morning_prices_are_compared_on_signal_day_basis():
    row = {"signal_date": "2026-06-16", "next_date": "2026-06-17",
           "symbol6": "688388", "signal_price": 70.81,
           "second_high": 48.96, "close_40": 49.52,
           "label_complete": True}
    row.update({f"next_close_{minute:02d}": 49.52 for minute in range(31, 41)})
    daily = pd.DataFrame([
        {"date": "2026-06-16", "symbol6": "688388", "close": 70.90,
         "preclose": 66.79, "volume": 1},
        {"date": "2026-06-17", "symbol6": "688388", "close": 47.45,
         "preclose": 48.93, "volume": 1},
    ])
    adjusted = adjust_outcome_prices(pd.DataFrame([row]), daily).iloc[0]
    assert abs(adjusted.outcome_factor - 70.90 / 48.93) < 1e-12
    assert abs(adjusted.adjusted_close_40 / adjusted.signal_price - 1 -
               (49.52 * 70.90 / 48.93 / 70.81 - 1)) < 1e-12
    assert adjusted.close_40 == 49.52  # raw audit price is retained


def test_next_day_suspension_becomes_neutral_label_without_filtering_signal():
    row = {"signal_date": "2025-07-04", "next_date": "2025-07-07",
           "symbol6": "600387", "signal_price": 2.0,
           "second_high": float("nan"), "close_40": float("nan"),
           "label_complete": False}
    row.update({f"next_close_{minute:02d}": float("nan") for minute in range(31, 41)})
    daily = pd.DataFrame([
        {"date": "2025-07-04", "symbol6": "600387", "close": 2.0,
         "preclose": 2.0, "volume": 1},
        {"date": "2025-07-07", "symbol6": "600387", "close": 2.0,
         "preclose": 2.0, "volume": 0},
    ])
    adjusted = adjust_outcome_prices(pd.DataFrame([row]), daily).iloc[0]
    assert adjusted.next_suspended and adjusted.label_complete
    assert adjusted.adjusted_second_high == adjusted.signal_price
    assert adjusted.adjusted_close_40 == adjusted.signal_price

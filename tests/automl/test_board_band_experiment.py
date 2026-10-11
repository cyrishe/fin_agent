import pandas as pd

from scripts.build_automl_mk_1m_candidates import candidate_band_mask
from scripts.experiment_automl_board_bands import outcome_class, select_top


def test_board_entry_bands_keep_legacy_default_distinct():
    frame = pd.DataFrame({
        "symbol6": ["000001", "002001", "300001", "688001", "830001"],
        "signal_return": [.02, .05, .04, .08, .06],
    })
    assert candidate_band_mask(frame, "board_volatility").tolist() == [True] * 5
    assert candidate_band_mask(frame, "legacy_3to6").tolist() == [False, True, True,
                                                                    False, True]


def test_board_specific_outcome_boundaries():
    returns = pd.Series([-.001, 0, .005, .02, .009, .01, .04])
    symbols = pd.Series(["000001"] * 4 + ["300001", "688001", "830001"])
    assert outcome_class(returns, symbols, "board").tolist() == [
        "wrong", "neutral", "acceptable", "target", "neutral", "acceptable",
        "target"]
    assert outcome_class(pd.Series([.02]), pd.Series(["000001"]), "legacy").iloc[0] == \
        "acceptable"


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

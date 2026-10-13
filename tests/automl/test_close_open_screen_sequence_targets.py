"""Decision-time and target-price boundary checks for the second study."""
from types import SimpleNamespace

import numpy as np
import pandas as pd

from scripts.build_automl_close_open_sequences import (
    CHANNELS, CUTS, FULL_TO_DECISION, afternoon_matrix, morning_labels,
)
from scripts.experiment_automl_close_open_screen_targets import (
    EXPANDED, as_target, daily_spearman, select12, selected_row,
)
from scripts.experiment_automl_close_open_tree_targets import select_top1
from scripts.experiment_automl_close_open_sequence import COLUMNS
from scripts.experiment_automl_close_open_cnn import cnn_scores
from scripts.research_automl_close_open_objectives import FEATURES


def candidate():
    return SimpleNamespace(signal_price=10.4, signal_return=.04, open_gap=.01,
                           minute_volume_shares=22000., turnover_so_far_pct=2.2,
                           avg_volume5_shares=50000., relative_turnover=1.1,
                           float_mv_100m_cny=10., ma5=10., ma10=9.9, ma20=9.8,
                           prior5_return=.03)


def bars():
    return pd.DataFrame([{"clock": time, "open": 10.4, "high": 10.5,
                          "low": 10.35, "close": 10.4, "volume": 100.}
                         for time in (*FULL_TO_DECISION, "14:56:00")])


def test_future_entry_bar_cannot_change_six_cut_features():
    frame = bars()
    original = afternoon_matrix(frame, candidate())
    assert original is not None and len(FULL_TO_DECISION) == 235
    assert all(f"{name}_{clock[:5].replace(':', '')}" in original
               for clock in CUTS for name in CHANNELS)
    changed = frame.copy()
    changed.loc[changed.clock.eq("14:56:00"), ["open", "high", "low", "close", "volume"]] = (
        10.6, 100., 1., 99., 1000000.)
    alternate = afternoon_matrix(changed, candidate())
    assert alternate is not None
    assert original["entry_1456"] != alternate["entry_1456"]
    assert {k: v for k, v in original.items() if k != "entry_1456"} == {
        k: v for k, v in alternate.items() if k != "entry_1456"}
    without_entry = afternoon_matrix(frame.iloc[:-1], candidate())
    assert without_entry is not None and np.isnan(without_entry["entry_1456"])
    assert without_entry["signal_return_1455"] == original["signal_return_1455"]


def test_morning_highs_and_bar_average_are_distinct_targets():
    m = pd.DataFrame([{"clock": f"09:{minute:02d}:00", "open": 10 + minute/100,
                       "high": 10 + minute/100, "low": 9., "close": 10.}
                      for minute in range(31, 41)])
    prices = morning_labels(m)
    assert prices["max_high"] == 10.4 and prices["second_high"] == 10.39
    row = pd.DataFrame(dict(actual=[0.], signal_price=[10.], max_high=[prices["max_high"]],
                            second_high=[prices["second_high"]], open_40=[prices["open_40"]],
                            close_40_raw=[prices["close_40_raw"]]))
    assert as_target(row, "max_high").actual.iloc[0] > as_target(row, "second_high").actual.iloc[0]
    assert np.isclose(as_target(row, "mean_openclose40").actual.iloc[0], .02)


def test_max_high_training_target_never_becomes_realized_sale_price():
    test = pd.DataFrame([dict(symbol6="000001", signal_price=10., actual=-.02,
                              max_high=11., second_high=10.8, open_40=9.8,
                              close_40_raw=9.8)])
    pick = selected_row(test, np.array([.1]), "2025", "2025-01-01", "example", "max_high")
    assert np.isclose(pick["return"], -.02)
    assert np.isclose(pick["target_return"], .10)


def test_train_only_selector_uses_the_exact_20_day_window():
    rng = np.random.default_rng(12)
    days = []
    for day in range(21):
        frame = pd.DataFrame(rng.normal(size=(40, len(EXPANDED))), columns=EXPANDED)
        frame["actual"] = .01*frame.signal_return + rng.normal(scale=.001, size=len(frame))
        frame["signal_date"] = f"2025-03-{day+1:02d}"
        days.append(frame)
    selected = select12(np.stack([daily_spearman(d) for d in days[:20]]))
    days[-1]["actual"] = -100 * days[-1].signal_return
    assert select12(np.stack([daily_spearman(d) for d in days[:20]])) == selected
    assert len(selected) == 12 and "signal_return" in selected


def test_tree_priority_can_abstain_and_prefers_predicted_high_class():
    class FixedModel:
        classes_ = np.array(["0to1", "1to3", "ge3", "lt0"])

        def __init__(self, probabilities):
            self.probabilities = np.asarray(probabilities)

        def predict_proba(self, _):
            return self.probabilities

    candidates = pd.DataFrame(np.zeros((3, len(FEATURES))), columns=FEATURES)
    candidates["symbol6"] = ["000003", "000002", "000001"]
    probabilities = [[.1, .6, .2, .1], [.1, .2, .6, .1], [.7, .1, .1, .1]]
    assert select_top1(FixedModel(probabilities), candidates) == 1
    assert select_top1(FixedModel([[.8, .1, .05, .05]] * 3), candidates) is None


def test_cnn_seed_repeatability_and_future_outcome_is_not_an_input():
    import torch
    torch.set_num_threads(1)
    generator = np.random.default_rng(17)
    train = pd.DataFrame(generator.normal(size=(32, len(COLUMNS))), columns=COLUMNS)
    test = pd.DataFrame(generator.normal(size=(5, len(COLUMNS))), columns=COLUMNS)
    train["return_1456_close40"] = generator.normal(scale=.02, size=len(train))
    test["return_1456_close40"] = generator.normal(scale=.02, size=len(test))
    first = cnn_scores(train, test, 11)
    test["return_1456_close40"] = 100.
    second = cnn_scores(train, test, 11)
    np.testing.assert_array_equal(first, second)

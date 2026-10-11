import pandas as pd
from types import SimpleNamespace

from scripts.evaluate_automl_opening_exit import evaluate_group, feature_record
from scripts.explore_automl_opening_shape import classify_group_maxima, group_close_maxima


def test_334_group_max_closes_and_four_shape_labels():
    closes = [10, 12, 11, 12, 13, 12, 13, 14, 15, 14]
    assert group_close_maxima(closes) == (12, 13, 15)
    assert classify_group_maxima((12, 13, 15)) == "持续升高"
    assert classify_group_maxima((15, 13, 12)) == "持续走低"
    assert classify_group_maxima((12, 15, 13)) == "冲高回落"
    assert classify_group_maxima((12, 12, 13)) == "起伏/其他"


def test_early_exit_counts_only_next_open_vs_0940_close():
    rows = pd.DataFrame({"return_0937open_pct": [2, -1],
                         "return_0940close_pct": [1, 0]})
    result = evaluate_group(rows, [True, False])
    assert result["early_exits"] == 1
    assert result["paired_improvement_pctpt"] == .5
    assert result["early_wins"] == 1
    assert result["policy_mean_pct"] == 1


def test_0936_decision_features_do_not_change_with_last_four_bars():
    prefix = [9, 9, 9, 10, 10, 10]

    def record(future):
        prices = prefix + future
        bars = pd.DataFrame({
            "open": prices, "high": prices, "low": prices, "close": prices,
            "volume": [100] * 10, "turnover": [1000] * 10,
        })
        pick = SimpleNamespace(signal_date="2026-09-01", next_date="2026-09-02",
                               symbol6="000001", name="test", source="test",
                               signal_price=10, close_40=prices[-1], signal_return=4,
                               volume_ratio=2, turnover_so_far_pct=1,
                               float_mv_100m_cny=100)
        return feature_record(pick, bars)

    rising = record([11] * 4)
    falling = record([9] * 4)
    assert rising["shape_label"] != falling["shape_label"]
    decision_keys = [key for key in rising if key.startswith((
        "open_ret_", "high_ret_", "low_ret_", "close_ret_", "log_volume_",
        "log_amount_"))]
    decision_keys += ["first6_move_pct", "first6_range_pct", "g2_minus_g1_pct",
                      "first6_up_bar_count", "signal_return", "volume_ratio",
                      "turnover_so_far_pct", "log_float_mv"]
    assert {key: rising[key] for key in decision_keys} == {
        key: falling[key] for key in decision_keys}

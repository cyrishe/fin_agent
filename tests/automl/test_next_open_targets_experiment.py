import pandas as pd

from scripts.experiment_automl_next_open_targets import matched_day_evidence, rate_table


def test_material_high_open_target_and_same_day_random_lift():
    date = pd.Timestamp("2026-06-01")
    pool = pd.DataFrame({
        "signal_date": [date] * 10,
        "symbol": [f"{i:06d}.SZ" for i in range(10)],
        "gap": [.03, .025, .01, .001, 0, -.001, -.01, -.02, -.03, -.04],
        "price_volatility_20": [.01] * 10,
    })
    rates = rate_table(pool)
    assert rates["high_open_gt_2pct_rate"] == .2
    assert rates["high_open_gt_0_rate"] == .4
    assert rates["near_flat_0_to_2pct_rate"] == .3

    selected = pool.iloc[[0]].copy()
    evidence = matched_day_evidence(pool, selected)
    assert evidence["high2_precision"] == 1.0
    assert evidence["same_day_random_rate"] == .2
    assert evidence["lift_vs_same_day_random"] == 5.0
    assert evidence["lift_vs_day_vol_matched"] == 5.0
    assert evidence["high2_wins"] == 1


def test_volatility_matched_random_baseline_is_stricter_when_events_cluster():
    date = pd.Timestamp("2026-06-01")
    pool = pd.DataFrame({
        "signal_date": [date] * 20,
        "symbol": [f"{i:06d}.SZ" for i in range(20)],
        "gap": [.03] + [0] * 18 + [.04],
        "price_volatility_20": [i / 100 for i in range(20)],
    })
    evidence = matched_day_evidence(pool, pool.iloc[[19]])
    assert evidence["same_day_random_rate"] == .1
    assert evidence["same_day_vol_matched_high2_rate"] == .5
    assert evidence["lift_vs_same_day_random"] == 10
    assert evidence["lift_vs_day_vol_matched"] == 2

"""Check the paired adjustment experiment's price-scale and backtest contract."""
import json
from pathlib import Path

import pandas as pd

from scripts.evaluate_automl_adjusted_basis import attach_factors, monthly_summary, sha256


ROOT = Path("docs/stock_automl_runs/20261012_adjusted_basis_sensitivity")


def test_tiny_factor_change_can_flip_zero_label_but_floor_removes_noise():
    rows = pd.DataFrame([{"signal_date": "2026-01-01", "next_date": "2026-01-02",
                          "symbol6": "000001", "signal_price": 100.,
                          "second_high": 100.}])
    factors = pd.DataFrame([{"date": "2026-01-01", "symbol6": "000001", "factor": 2.},
                            {"date": "2026-01-02", "symbol6": "000001", "factor": 1.99998}])
    all_changes = attach_factors(rows, factors)
    material_only = attach_factors(rows, factors, factor_change_floor=.0001)
    assert all_changes.adjusted_target_return.iloc[0] < 0
    assert material_only.adjusted_target_return.iloc[0] == 0
    assert material_only.raw_target_return.iloc[0] == 0


def test_missing_next_day_factor_does_not_remove_today_candidate():
    rows = pd.DataFrame([{"signal_date": "2026-01-01", "next_date": "2026-01-02",
                          "symbol6": "000001", "signal_price": 100.,
                          "second_high": 103.}])
    factors = pd.DataFrame([{"date": "2026-01-01", "symbol6": "000001", "factor": 2.}])
    result = attach_factors(rows, factors)
    assert len(result) == 1
    assert pd.isna(result.adjusted_second_high.iloc[0])
    assert result.signal_price.iloc[0] == 100


def test_negative_control_reproduces_frozen_selection_and_returns():
    control = ROOT / "no_change_control"
    summary = json.loads((control / "summary.json").read_text())
    paired = pd.read_csv(control / "paired_daily.csv",
                         dtype={"raw_model_top1": str, "adjusted_model_top1": str})
    assert len(paired) == 118
    assert paired.raw_model_top1.fillna("").equals(paired.adjusted_model_top1.fillna(""))
    assert summary["different_top1_days"] == 0
    assert summary["raw_model"]["trades"] == summary["adjusted_label_model"]["trades"] == 94
    assert abs(summary["raw_model"]["sum_raw_0940_return_pct"] -
               summary["adjusted_label_model"]["sum_raw_0940_return_pct"]) < 1e-10


def test_saved_factor_snapshot_and_monthly_rows_match_summaries():
    for folder in (ROOT, ROOT / "material_change", ROOT / "no_change_control"):
        summary = json.loads((folder / "summary.json").read_text())
        assert sha256(Path(summary["factors_file"])) == summary["factors_sha256"]
        paired = pd.read_csv(folder / "paired_daily.csv",
                             dtype={"raw_model_top1": str, "adjusted_model_top1": str})
        expected = monthly_summary(paired)
        saved = pd.read_csv(folder / "monthly.csv")
        pd.testing.assert_frame_equal(saved, expected, check_exact=False, rtol=1e-12)
        assert abs(saved.adjusted_model_sum_return_pct.sum() -
                   summary["adjusted_label_model"]["sum_raw_0940_return_pct"]) < 1e-10

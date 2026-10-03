import numpy as np
import pandas as pd
import pytest

from scripts.audit_automl_linear_explanations import audit, explain_logistic
from src.quant_research.automl.config import ResearchSpec
from src.quant_research.automl.learning import fit_model


@pytest.mark.parametrize("calibration_slope", [0.7, -0.5, 0.0])
def test_explanation_includes_calibration_and_groups_missing_and_category_columns(calibration_slope):
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2024-01-01", periods=160)
    frame = pd.DataFrame({"date": dates, "label_end": dates + pd.offsets.BDay(2),
                          "symbol": "A", "forward_return": rng.normal(0, .02, len(dates)),
                          "x": rng.normal(size=len(dates)), "missing": np.nan,
                          "partial": np.where(np.arange(len(dates)) % 3, 1., np.nan),
                          "industry": np.where(np.arange(len(dates)) % 2, "A", "B")})
    fitted = fit_model(frame, {"model": "linear", "task": "classification"},
                       ["x", "missing", "partial", "industry"],
                       ResearchSpec(start="2024-01-01", end="2025-01-01"))
    fitted.calibrator.coef_[0, 0] = calibration_slope
    reference = frame.copy()
    reference.loc[0, "industry"] = "unseen"
    report = explain_logistic(fitted, reference)
    assert report["reconstruction_max_absolute_error"] < 1e-12
    assert {r["feature"] for r in report["grouped_model_reliance"]} == set(fitted.columns)
    assert report["transformed_feature_count"] > report["raw_feature_count"]
    for row in report["coefficients"]:
        assert row["calibrated_log_odds_coefficient"] == pytest.approx(row["base_coefficient"] * calibration_slope)
    if calibration_slope == 0:
        assert all(r["mean_absolute_centered_log_odds_contribution"] == 0
                   for r in report["grouped_model_reliance"])


def test_audit_refuses_to_write_inside_original_artifact_directory(tmp_path):
    with pytest.raises(ValueError, match="outside the original"):
        audit(tmp_path, tmp_path / "audit")

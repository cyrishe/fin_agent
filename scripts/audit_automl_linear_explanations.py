#!/usr/bin/env python3
"""Explain a trusted local AutoML logistic winner; compare simplifications on development only.

This is an offline research audit, not a production model promotion. Joblib/pickle inputs
must be artifacts produced by this repository that the operator trusts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

import joblib
import numpy as np
import pandas as pd
from scipy.special import expit
import sklearn
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.quant_research.automl.config import ResearchSpec
from src.quant_research.automl.evaluation import backtest_predictions, prediction_metrics
from src.quant_research.automl.features import labeled_panel, sample_mask
from src.quant_research.automl.learning import fit_model, temporal_folds, training_partition
from src.quant_research.automl.runner import constraint_checks, write_json


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def explain_logistic(model, reference):
    """Exact additive decomposition in calibrated log-odds, centered on fit data.

    Grouping one-hot levels and missing indicators avoids comparing an entire
    category with a single standardized numeric coefficient. These are model
    contributions on reference data, not causal or out-of-sample importance.
    """
    estimator = model.pipeline[-1]
    if model.task != "classification" or not isinstance(estimator, LogisticRegression):
        raise ValueError("this audit requires a binary logistic classification winner")
    transformer = model.pipeline[0]
    names = transformer.get_feature_names_out().tolist()
    transformed = np.asarray(transformer.transform(reference[model.columns]))
    base_weights = estimator.coef_[0]
    slope = float(model.calibrator.coef_[0, 0])
    offset = float(model.calibrator.intercept_[0])
    weights = slope * base_weights
    intercept = slope * float(estimator.intercept_[0]) + offset
    center = transformed.mean(axis=0)
    contributions = (transformed - center) * weights
    centered_intercept = float(intercept + center @ weights)
    reconstructed = expit(centered_intercept + contributions.sum(axis=1))
    error = float(np.max(np.abs(reconstructed - model.predict(reference))))
    if error > 1e-10:
        raise AssertionError(f"calibrated explanation does not reproduce predictions: {error}")
    coefficients, groups = [], {}
    for i, name in enumerate(names):
        origin = ("industry" if name.startswith("industry__") else
                  name.removeprefix("numeric__").removeprefix("missingindicator_"))
        groups.setdefault(origin, []).append(i)
        coefficients.append({"transformed_feature": name, "original_feature": origin,
                             "base_coefficient": float(base_weights[i]),
                             "calibrated_log_odds_coefficient": float(weights[i]),
                             "mean_absolute_centered_contribution": float(np.abs(contributions[:, i]).mean())})
    reliance = [{"feature": name, "mean_absolute_centered_log_odds_contribution":
                 float(np.abs(contributions[:, indices].sum(axis=1)).mean())}
                for name, indices in groups.items()]
    reliance.sort(key=lambda row: row["mean_absolute_centered_log_odds_contribution"], reverse=True)
    return {"raw_feature_count": len(model.columns), "transformed_feature_count": len(names),
            "nonzero_coefficients": int(np.count_nonzero(base_weights)),
            "calibration_slope": slope, "calibration_intercept": offset,
            "calibrated_intercept": intercept, "centered_calibrated_intercept": centered_intercept,
            "reconstruction_max_absolute_error": error, "reference_rows": len(reference),
            "coefficients": coefficients, "grouped_model_reliance": reliance,
            "interpretation": "Exact calibrated log-odds contributions on base-fit rows; not percentage-point effects, causality, SHAP, or out-of-sample importance."}


def audit(root, output):
    root, output = root.resolve(), output.resolve()
    if output == root or root in output.parents:
        raise ValueError("write the audit outside the original research directory")
    original_hashes = {p.name: digest(p) for p in root.iterdir() if p.is_file()}
    read = lambda name: json.loads((root / f"{name}.json").read_text())
    spec = ResearchSpec.from_dict(read("spec"))
    manifest, selection = read("manifest"), read("selection")
    candidate, columns = selection["candidate"], selection["columns"]
    implementations = [f"quant_research/automl/{name}.py" for name in ("config", "learning", "features", "evaluation")]
    implementations += [f"backtest/{path.name}" for path in sorted((root / "implementation/backtest").glob("*.py"))]
    for relative in implementations:
        saved, current = root / "implementation" / relative, ROOT / "src" / relative
        if saved.read_bytes() != current.read_bytes():
            raise ValueError(f"reconstruct with the saved implementation before auditing: {relative}")
    panel = pd.read_pickle(root / "panel.pkl")
    fingerprint = hashlib.sha256(pd.util.hash_pandas_object(panel, index=True).values.tobytes()).hexdigest()
    if fingerprint != manifest["panel_sha256"]:
        raise ValueError("saved panel fingerprint changed")
    # Remove held-out companies and dates before constructing any experimental labels.
    panel = panel[(panel.date < pd.Timestamp(manifest["test_start"])) &
                  (~panel.symbol.isin(manifest["heldout_companies"]))].copy()
    development = labeled_panel(panel, candidate["horizon"])
    final_train = development[sample_mask(development, candidate["sampler"], spec)]
    fit, _, counts = training_partition(final_train, candidate, spec)
    model = joblib.load(root / "model.joblib")
    if counts != model.training_counts:
        raise AssertionError("final training partition does not match the saved model")
    explanation = explain_logistic(model, fit)
    reproduced = fit_model(final_train, candidate, columns, spec)
    final_error = float(np.max(np.abs(reproduced.predict(final_train) - model.predict(final_train))))
    if final_error > 1e-10:
        raise AssertionError("original final fit failed to reproduce")
    variants = [
        ("original", candidate, columns),
        ("without_industry", candidate, [c for c in columns if c != "industry"]),
        ("stronger_l2", {**candidate, "model": "linear", "strength": candidate["strength"] / 10}, columns),
        ("sparse_elastic_net", {**candidate, "model": "elastic_net", "strength": candidate["strength"] / 10}, columns),
    ]
    results = []
    for name, config, used_columns in variants:
        folds = []
        for index, (train, validation) in enumerate(temporal_folds(development, spec.folds)):
            train = train[sample_mask(train, candidate["sampler"], spec)]
            validation = validation[sample_mask(validation, candidate["sampler"], spec)].copy()
            fitted = fit_model(train, config, used_columns, spec)
            validation["prediction"] = fitted.predict(validation)
            baseline = float((train.forward_return > spec.target_return).mean())
            metrics = prediction_metrics(validation, "classification", spec, baseline)
            portfolio = backtest_predictions(validation, panel, config, spec)["metrics"]
            if name == "original":
                saved = selection["folds"][index]
                for key in ("brier", "roc_auc", "signal_count", "skill_vs_constant"):
                    if not np.isclose(metrics[key], saved["prediction"][key], atol=1e-10, rtol=0):
                        raise AssertionError(f"original fold {index} failed to reproduce {key}")
                for key in ("total_return", "max_drawdown", "trade_count"):
                    if not np.isclose(float(portfolio[key]), float(saved["portfolio"][key]), atol=1e-10, rtol=0):
                        raise AssertionError(f"original fold {index} failed to reproduce {key}")
            weights = fitted.pipeline[-1].coef_[0]
            folds.append({"validation_start": validation.date.min(), "validation_end": validation.label_end.max(),
                          "training": fitted.training_counts, "transformed_feature_count": len(weights),
                          "nonzero_coefficients": int(np.count_nonzero(weights)),
                          "calibration_slope": float(fitted.calibrator.coef_[0, 0]),
                          "prediction": metrics, "portfolio": portfolio})
        results.append({"variant": name, "columns": used_columns, "C": config["strength"],
                        "folds": folds, "constraint_checks": constraint_checks(folds, spec)})
        print(f"completed {name}", flush=True)
    after_hashes = {p.name: digest(p) for p in root.iterdir() if p.is_file()}
    if original_hashes != after_hashes:
        raise AssertionError("original artifacts changed during the audit")
    report = {"run_id": manifest["run_id"], "audit_git_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "script_sha256": digest(Path(__file__)),
        "environment": {"python": platform.python_version(), "pandas": pd.__version__, "sklearn": sklearn.__version__},
        "source_manifest_sha256": original_hashes["manifest.json"], "source_spec_sha256": original_hashes["spec.json"],
        "implementation_sha256": {relative: digest(ROOT / "src" / relative) for relative in implementations},
        "source_model_sha256": original_hashes["model.joblib"], "source_panel_sha256": manifest["panel_sha256"],
        "candidate": candidate, "training_counts": counts,
        "scope": "development only; no held-out outcome evaluated, no production model replaced",
        "original_artifacts_unchanged": True, "original_final_prediction_max_error": final_error,
        "explanation": explanation,
        "industry_support": [{"industry": str(industry), "companies": int(group.symbol.nunique()), "rows": len(group)}
                             for industry, group in fit.groupby("industry")],
        "development_simplifications": results,
        "limitations": ["Four predeclared variants, two expanding time folds; exploratory comparison, not a fresh blind test.",
                        "A winning development variant still needs untouched companies/time before a generalization claim.",
                        "Signal outcomes overlap in time and are not counts of independent executed round trips."]}
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "linear_explanation.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    report = audit(args.run_dir, args.output_dir)
    print(json.dumps({"output": str(args.output_dir / "linear_explanation.json"),
                      "verified_prediction_error": report["explanation"]["reconstruction_max_absolute_error"]}))

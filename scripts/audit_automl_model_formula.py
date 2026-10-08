"""Export the exact fitted eight-factor logistic formula and stability audit."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import sklearn

from scripts.experiment_automl_tail_three_class import BINARY, COMPACT_FEATURES, read_samples
from scripts.run_automl_tail_today import fitted_model


def audit(samples_path: Path, fold_report_path: Path, output_path: Path):
    samples = read_samples(samples_path)
    model = fitted_model(samples)
    transformer = model.named_steps["columntransformer"]
    scaler = transformer.named_transformers_["numeric"]
    fitted = model.named_steps["logisticregression"]
    numeric = [name for name in COMPACT_FEATURES if name not in BINARY]
    weights = fitted.coef_[0]
    fold_report = json.loads(fold_report_path.read_text())
    folds = fold_report["binary_coefficients_by_fold"]
    features = []
    for i, name in enumerate(numeric):
        beta = float(weights[i])
        features.append({"name": name, "type": "numeric_standardized",
                         "training_mean": float(scaler.mean_[i]),
                         "training_std": float(scaler.scale_[i]),
                         "coefficient": beta,
                         "odds_ratio_per_standard_deviation": float(np.exp(beta)),
                         "fold_coefficients": [item["coefficients_for_pass"][name]
                                               for item in folds]})
    for i, name in enumerate(BINARY, len(numeric)):
        beta = float(weights[i])
        features.append({"name": name, "type": "binary_0_or_1",
                         "training_one_fraction": float(samples[name].mean()),
                         "coefficient": beta,
                         "odds_ratio_from_0_to_1": float(np.exp(beta)),
                         "fold_coefficients": [item["coefficients_for_pass"][name]
                                               for item in folds]})
    example = samples.iloc[:10][list(COMPACT_FEATURES)]
    transformed = transformer.transform(example)
    reconstructed = float(fitted.intercept_[0]) + transformed @ weights
    predicted = model.predict_proba(example)[:, 1]
    reconstructed_probabilities = 1 / (1 + np.exp(-reconstructed))
    if not np.allclose(reconstructed_probabilities, predicted, rtol=0, atol=1e-12):
        raise AssertionError("Exported coefficients do not reconstruct model scores")
    above = samples.price_above_all_ma.eq(1)
    low_above = samples.all_intraday_lows_above_ma.eq(1)
    output = {
        "model": "eight-factor binary logistic regression",
        "positive_label": "original class 0 or 1; next first-ten-minute high / T 14:50 entry proxy - 1 >= 0.005",
        "training_rows": len(samples),
        "training_signal_period": [samples.signal_date.min().date().isoformat(),
                                   samples.signal_date.max().date().isoformat()],
        "regularization_C": float(fitted.C),
        "effective_regularization": "L2" if fitted.l1_ratio == 0 else "other",
        "l1_ratio": float(fitted.l1_ratio),
        "scikit_learn_version": sklearn.__version__,
        "solver": str(fitted.solver),
        "max_iter": int(fitted.max_iter),
        "intercept": float(fitted.intercept_[0]),
        "features": features,
        "ma_state_counts": {"neither": int((~above & ~low_above).sum()),
                            "price_above_only": int((above & ~low_above).sum()),
                            "both": int((above & low_above).sum()),
                            "low_above_only_impossible": int((~above & low_above).sum())},
        "float_mv_training_max_100m_cny": float(samples.float_mv_100m_cny.max()),
        "float_mv_training_999pct_100m_cny": float(
            samples.float_mv_100m_cny.quantile(.999)),
        "formula_reconstruction_max_abs_error": float(np.max(
            np.abs(reconstructed_probabilities - predicted))),
        "samples_sha256": hashlib.sha256(samples_path.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=Path(
        "outputs/stock_automl/tail_standard/trainable_1440.csv"))
    parser.add_argument("--fold-report", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_tail_critical_review/summary.json"))
    parser.add_argument("--output", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_score_reliability/model_formula.json"))
    args = parser.parse_args()
    audit(args.samples, args.fold_report, args.output)

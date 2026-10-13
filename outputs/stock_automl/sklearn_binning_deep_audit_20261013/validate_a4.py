"""Recalculate A4 causal controls and descriptive stability checks from saved scores."""
from pathlib import Path
import json

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
PROBABILITY_COLUMNS = ["p_lt0", "p_0to1", "p_1to3", "p_ge3"]
FEATURES = ["signal_return", "volume_ratio", "turnover_so_far_pct", "float_mv_100m_cny"]


def read_scores(path):
    return pd.read_csv(path, dtype={"signal_date": str, "symbol6": str})


ablation = read_scores(ROOT / "shock_ablation_scores.csv.gz")
controls = {}
for variant, filename in (("old_all", "scores_sklearn_171.csv.gz"),
                          ("new_all", "scores_sklearn_191.csv.gz")):
    reference = read_scores(ROOT / filename)
    paired = ablation.loc[ablation.variant.eq(variant)].merge(
        reference, on=["signal_date", "symbol6"], suffixes=("_ablation", "_reference"),
        validate="one_to_one")
    controls[variant] = {
        "matched_candidates": int(len(paired)),
        "maximum_probability_difference": float(max(
            (paired[f"{column}_ablation"] - paired[f"{column}_reference"]).abs().max()
            for column in PROBABILITY_COLUMNS)),
        "predicted_class_disagreements": int((
            paired.predicted_class_ablation != paired.predicted_class_reference).sum()),
    }

moved = pd.read_csv(ROOT / "shock_moved_samples.csv.gz")
shifts = {}
for feature in FEATURES:
    differences = moved[f"new_bin_{feature}"] - moved[f"old_bin_{feature}"]
    shifts[feature] = {str(int(k)): int(v) for k, v in
                       differences.loc[differences.ne(0)].value_counts().items()}

cohorts = {}
for cohort in ("feb_jul", "aug_oct"):
    fold_file = "folds_sklearn_171.json" if cohort == "feb_jul" else "folds_aug_oct_sklearn_171.json"
    folds = pd.DataFrame(json.loads((ROOT / fold_file).read_text())).set_index("day")
    variants = {}
    for variant, filename in (
        ("midpoint255", "scores_sklearn_171.csv.gz" if cohort == "feb_jul"
         else "scores_aug_oct_sklearn_171.csv.gz"),
        ("avgcdf255", "scores_sklearn_191.csv.gz" if cohort == "feb_jul"
         else "scores_aug_oct_sklearn_191.csv.gz"),
        ("avgcdf63", f"scores_{cohort}_avgcdf63.csv.gz"),
    ):
        frame = read_scores(ROOT / filename)
        known = frame.second_high.notna().to_numpy()
        returns = (frame.second_high / frame.signal_price - 1).to_numpy()
        labels = np.select([returns < 0, returns < .01, returns < .03],
                           [0, 1, 2], default=3)
        probabilities = frame[PROBABILITY_COLUMNS].to_numpy()
        frame["loss"] = -np.log(np.maximum(
            probabilities[np.arange(len(frame)), labels], 1e-15))
        variants[variant] = frame.loc[known].groupby("signal_date").loss.mean()
    folds["coarse_minus_old"] = variants["avgcdf63"] - variants["midpoint255"]
    folds["coarse_minus_new"] = variants["avgcdf63"] - variants["avgcdf255"]
    folds["training_size_tertile"] = pd.qcut(folds.training_rows, 3, labels=False,
                                              duplicates="drop")
    size_groups = []
    for group, part in folds.groupby("training_size_tertile"):
        size_groups.append({
            "tertile": int(group) + 1,
            "days": int(len(part)),
            "mean_training_rows": float(part.training_rows.mean()),
            "mean_daily_loss_coarse_minus_old": float(part.coarse_minus_old.mean()),
            "mean_daily_loss_coarse_minus_new": float(part.coarse_minus_new.mean()),
        })
    picks = pd.read_csv(ROOT / f"binning_variant_top1_{cohort}.csv",
                        dtype={"signal_date": str, "symbol6": str})
    daily_return = picks.pivot(index="signal_date", columns="variant",
                               values="return_0940").fillna(0)
    return_checks = {}
    rng = np.random.default_rng(20261013)
    for variant in ("avgcdf255", "linear255", "midpoint63", "avgcdf63"):
        delta = (daily_return[variant] - daily_return.midpoint255).to_numpy()
        bootstrap = rng.choice(delta, size=(10000, len(delta)), replace=True).mean(axis=1)
        return_checks[variant] = {
            "daily_mean_return_difference": float(delta.mean()),
            "day_bootstrap_95pct": np.quantile(bootstrap, [.025, .975]).tolist(),
        }
    cohorts[cohort] = {
        "training_rows_min": int(folds.training_rows.min()),
        "training_rows_max": int(folds.training_rows.max()),
        "coarse_better_daily_loss_days_vs_old": int(folds.coarse_minus_old.lt(0).sum()),
        "training_rows_vs_coarse_loss_delta_correlation": float(
            folds.training_rows.corr(folds.coarse_minus_old)),
        "training_size_groups": size_groups,
        "paired_top1_return": return_checks,
    }

result = {"shock_ablation_controls": controls,
          "moved_sample_bin_shifts": shifts, "cohorts": cohorts}
(ROOT / "a4_validation.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))

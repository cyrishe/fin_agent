"""Test whether training-only standardization changes the frozen tree scores."""
from pathlib import Path
import argparse
import json

import numpy as np
import pandas as pd
import sklearn

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.run_automl_four_class_pipeline import (
    generate_training_features, generate_training_labels,
    select_inference_candidates, select_training_samples,
)
from outputs.stock_automl.tree_family_comparison_20261013.run_tree_families import (
    DATASETS, OUTPUT as TREE_ROOT, model_for,
)


OUT = Path(__file__).resolve().parent
NUMERIC = FEATURES[:4]
LABELS = ("lt0", "0to1", "1to3", "ge3")
P = [f"p_{label}" for label in LABELS]


def top1(prob, symbols):
    classification = prob.argmax(axis=1)
    eligible = np.flatnonzero(np.isin(classification, [2, 3]))
    if not len(eligible):
        return ""
    priority = np.where(classification == 3, 0, 1)
    score = np.where(priority == 0, prob[:, 3], prob[:, 2] + prob[:, 3])
    return str(symbols[sorted(eligible, key=lambda i: (
        int(priority[i]), -float(score[i]), str(symbols[i])))[0]])


def saved_path(cohort):
    if sklearn.__version__.startswith("1.9"):
        name = "scores_sklearn_191.csv.gz" if cohort == "feb_jul" else \
               "scores_aug_oct_sklearn_191.csv.gz"
        return Path("outputs/stock_automl/sklearn_binning_deep_audit_20261013") / name
    return TREE_ROOT / f"{cohort}_hgb_b1_seed42/scores.csv.gz"


def run(cohort, transform):
    source = DATASETS[cohort]
    rows = pd.read_csv(source["path"], dtype={"signal_date": str,
                                               "next_date": str,
                                               "symbol6": str},
                       float_precision="round_trip")
    dates = source["dates"]
    saved = pd.read_csv(saved_path(cohort), dtype={"signal_date": str,
                                                  "symbol6": str})
    result = {"cohort": cohort, "sklearn_version": sklearn.__version__,
              "transform": transform,
              "prediction_days": 0, "same_top1_days": 0,
              "max_abs_probability_difference": 0.,
              "mean_abs_probability_difference": 0.,
              "probabilities_compared": 0, "different_probability_count_gt_1e-12": 0,
              "different_top1_days": []}
    diff_sum = 0.
    for index in range(source["first"], len(dates)-1):
        day = dates[index]
        training = select_training_samples(rows, dates[index-20:index])
        current = select_inference_candidates(rows, day)
        train_x = generate_training_features(training)
        current_x = current[list(FEATURES)].copy()
        if transform == "zscore":
            offset = train_x[list(NUMERIC)].mean()
            scale = train_x[list(NUMERIC)].std(ddof=0)
        elif transform == "minmax":
            offset = train_x[list(NUMERIC)].min()
            scale = train_x[list(NUMERIC)].max()-offset
        else:
            raise ValueError(transform)
        if scale.le(0).any():
            raise ValueError(f"Zero numeric factor scale on {day}")
        train_x.loc[:, list(NUMERIC)] = (train_x[list(NUMERIC)]-offset)/scale
        current_x.loc[:, list(NUMERIC)] = (current_x[list(NUMERIC)]-offset)/scale
        model = model_for("hgb_b1", 42)
        model.fit(train_x, generate_training_labels(training))
        prob = model.predict_proba(current_x)[:, [
            list(model.classes_).index(label) for label in LABELS]]
        symbols = current.symbol6.to_numpy()
        expected = saved.loc[saved.signal_date.eq(day)].set_index("symbol6").loc[
            symbols, P].to_numpy()
        diff = np.abs(prob-expected)
        result["max_abs_probability_difference"] = max(
            result["max_abs_probability_difference"], float(diff.max()))
        result["different_probability_count_gt_1e-12"] += int((diff>1e-12).sum())
        diff_sum += float(diff.sum())
        result["probabilities_compared"] += diff.size
        result["prediction_days"] += 1
        if top1(prob, symbols) == top1(expected, symbols):
            result["same_top1_days"] += 1
        else:
            result["different_top1_days"].append(day)
        if result["prediction_days"] % 20 == 0:
            print(cohort, result["prediction_days"], day, flush=True)
    result["mean_abs_probability_difference"] = diff_sum/result["probabilities_compared"]
    OUT.mkdir(parents=True, exist_ok=True)
    prefix = "scaling" if transform == "zscore" else "minmax"
    path = OUT / f"{prefix}_{cohort}_sk{sklearn.__version__.replace('.', '')}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(path, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--transform", choices=("zscore", "minmax"),
                        default="zscore")
    args = parser.parse_args()
    for cohort in DATASETS:
        run(cohort, args.transform)

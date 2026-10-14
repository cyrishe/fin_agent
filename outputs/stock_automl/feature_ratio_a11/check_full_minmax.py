"""Refit B1 and six-factor variants after train-fold [0,1] scaling of every input."""
from pathlib import Path
import argparse
import json

import numpy as np
import pandas as pd
import sklearn

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.run_automl_four_class_pipeline import (
    generate_training_labels, select_inference_candidates, select_training_samples,
)
from outputs.stock_automl.feature_ratio_a11.run_feature_variants import (
    OUT, RATIO_SOURCE, make_features,
)
from outputs.stock_automl.tree_family_comparison_20261013.run_tree_families import (
    DATASETS, model_for,
)


PROBS = ["p_lt0", "p_0to1", "p_1to3", "p_ge3"]


def saved_scores(cohort: str, variant: str) -> Path:
    version = sklearn.__version__
    if variant != "none":
        return OUT / f"{cohort}_{variant}_sk{version.replace('.', '')}/scores.csv.gz"
    if version == "1.7.1":
        return Path("outputs/stock_automl/tree_family_comparison_20261013") / f"{cohort}_hgb_b1_seed42/scores.csv.gz"
    name = ("scores_sklearn_191.csv.gz" if cohort == "feb_jul"
            else "scores_aug_oct_sklearn_191.csv.gz")
    return Path("outputs/stock_automl/sklearn_binning_deep_audit_20261013") / name


def select_top1(probabilities: np.ndarray, symbols: np.ndarray) -> str:
    predicted = probabilities.argmax(axis=1)
    eligible = np.flatnonzero(np.isin(predicted, [2, 3]))
    if not len(eligible):
        return ""
    priority = np.where(predicted == 3, 0, 1)
    score = np.where(priority == 0, probabilities[:, 3],
                     probabilities[:, 2] + probabilities[:, 3])
    order = sorted(eligible, key=lambda i: (int(priority[i]), -float(score[i]), str(symbols[i])))
    return str(symbols[order[0]])


def features(frame: pd.DataFrame, variant: str) -> pd.DataFrame:
    return frame[list(FEATURES)].copy() if variant == "none" else make_features(frame, variant)


def run(cohort: str, variant: str) -> None:
    if variant == "ratio" and cohort != "feb_jul":
        raise ValueError("Recent source has no frozen raw ratio inputs")
    source = DATASETS[cohort]
    rows = pd.read_csv(source["path"], dtype={"signal_date": str,
                                               "next_date": str,
                                               "symbol6": str},
                       float_precision="round_trip")
    dates = source["dates"]
    saved = pd.read_csv(saved_scores(cohort, variant),
                        dtype={"signal_date": str, "symbol6": str})
    result = {"cohort": cohort, "variant": variant,
              "sklearn_version": sklearn.__version__,
              "transform": "all-input train-fold minmax [0,1], no prediction clipping",
              "prediction_days": 0, "same_top1_days": 0,
              "max_abs_probability_difference": 0.,
              "probabilities_compared": 0,
              "different_probability_count_gt_1e-12": 0,
              "constant_feature_fold_count": 0,
              "different_top1_days": []}
    for index in range(source["first"], len(dates)-1):
        day = dates[index]
        training = select_training_samples(rows, dates[index-20:index])
        current = select_inference_candidates(rows, day)
        if variant == "ratio":
            raw = rows.loc[rows.signal_date.eq(day), ["symbol6", *RATIO_SOURCE]]
            current = current.merge(raw, on="symbol6", how="left", validate="one_to_one")
        x_train = features(training, variant).astype(float)
        x_current = features(current, variant).astype(float)
        lower = x_train.min()
        span = x_train.max()-lower
        result["constant_feature_fold_count"] += int(span.eq(0).sum())
        span = span.where(span.gt(0), 1.)
        x_train = (x_train-lower)/span
        x_current = (x_current-lower)/span
        model = model_for("hgb_b1", 42)
        model.fit(x_train, generate_training_labels(training))
        got = model.predict_proba(x_current)[:, [
            list(model.classes_).index(c) for c in ("lt0", "0to1", "1to3", "ge3")]]
        symbols = current.symbol6.to_numpy()
        expected = saved.loc[saved.signal_date.eq(day)].set_index("symbol6").loc[
            symbols, PROBS].to_numpy()
        diff = np.abs(got-expected)
        result["max_abs_probability_difference"] = max(
            result["max_abs_probability_difference"], float(diff.max()))
        result["probabilities_compared"] += diff.size
        result["different_probability_count_gt_1e-12"] += int((diff>1e-12).sum())
        result["prediction_days"] += 1
        if select_top1(got, symbols) == select_top1(expected, symbols):
            result["same_top1_days"] += 1
        else:
            result["different_top1_days"].append(day)
        if result["prediction_days"] % 20 == 0:
            print(cohort, variant, sklearn.__version__,
                  result["prediction_days"], flush=True)
    path = OUT / f"full_minmax_{cohort}_{variant}_sk{sklearn.__version__.replace('.', '')}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(path, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", choices=tuple(DATASETS), required=True)
    parser.add_argument("--variant", choices=("none", "drop_volume", "ratio"), required=True)
    args = parser.parse_args()
    run(args.cohort, args.variant)

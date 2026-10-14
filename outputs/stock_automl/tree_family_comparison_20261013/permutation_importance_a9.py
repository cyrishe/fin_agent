"""Out-of-fold candidate permutation diagnostics for the frozen B1 model."""
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
    DATASETS, OUTPUT, model_for,
)

LABELS = ("lt0", "0to1", "1to3", "ge3")
P = [f"p_{label}" for label in LABELS]


def prediction(probabilities, symbols):
    classification = np.argmax(probabilities, axis=1)
    eligible = np.isin(classification, (2, 3))
    if not eligible.any():
        return ""
    priority = np.where(classification == 3, 0, 1)
    score = np.where(priority == 0, probabilities[:, 3],
                     probabilities[:, 2] + probabilities[:, 3])
    return str(symbols[sorted(np.flatnonzero(eligible), key=lambda i: (
        int(priority[i]), -float(score[i]), str(symbols[i])))[0]])


def logloss(probabilities, labels):
    return -np.log(np.maximum(probabilities[np.arange(len(labels)), labels],
                              1e-15))


def run(cohort):
    source = DATASETS[cohort]
    rows = pd.read_csv(source["path"], dtype={"signal_date": str,
                                               "next_date": str,
                                               "symbol6": str},
                       float_precision="round_trip")
    dates = source["dates"]
    saved = pd.read_csv(OUTPUT / f"{cohort}_hgb_b1_seed42/scores.csv.gz",
                        dtype={"signal_date": str, "symbol6": str})
    output = OUTPUT / f"a9_b1_permutation_{cohort}.json"
    sums = {feature: {"loss_delta_sum": 0., "n": 0,
                      "top1_changes": 0, "top1_comparisons": 0,
                      "daily_loss_delta": []} for feature in FEATURES}
    baseline_loss_sum = 0.
    baseline_n = 0
    baseline_days = 0
    max_saved_probability_error = 0.
    example = {}
    for index in range(source["first"], len(dates)-1):
        day = dates[index]
        training = select_training_samples(rows, dates[index-20:index])
        current = select_inference_candidates(rows, day)
        x_train = generate_training_features(training)
        x = current[list(FEATURES)].copy()
        model = model_for("hgb_b1", 42)
        model.fit(x_train, generate_training_labels(training))
        probability = model.predict_proba(x)[:, [list(model.classes_).index(c)
                                                 for c in LABELS]]
        symbols = current.symbol6.to_numpy()
        reference = saved.loc[saved.signal_date.eq(day)].set_index("symbol6")
        expected = reference.loc[symbols, P].to_numpy()
        max_saved_probability_error = max(max_saved_probability_error,
                                          float(np.abs(probability-expected).max()))
        if max_saved_probability_error > 1e-12:
            raise ValueError(f"B1 reproduction mismatch on {day}")
        base_top = prediction(probability, symbols)
        outcome = reference.loc[symbols]
        known = outcome.second_high.notna().to_numpy()
        y_raw = (outcome.second_high / outcome.signal_price - 1).to_numpy()
        y = np.select([y_raw < 0, y_raw < .01, y_raw < .03],
                      [0, 1, 2], default=3)[known]
        base_loss = logloss(probability[known], y)
        baseline_loss_sum += float(base_loss.sum())
        baseline_n += len(y)
        baseline_days += 1
        for feature_index, feature in enumerate(FEATURES):
            for repeat in range(3):
                rng = np.random.default_rng(9173 + index*101 + feature_index*7 + repeat)
                modified = x.copy()
                modified[feature] = rng.permutation(x[feature].to_numpy())
                perturbed = model.predict_proba(modified)[:, [
                    list(model.classes_).index(c) for c in LABELS]]
                delta = logloss(perturbed[known], y) - base_loss
                item = sums[feature]
                item["loss_delta_sum"] += float(delta.sum())
                item["n"] += len(y)
                item["daily_loss_delta"].append(float(delta.mean()))
                item["top1_changes"] += int(prediction(perturbed, symbols) != base_top)
                item["top1_comparisons"] += 1
        if day == "2026-07-30":
            picked_index = int(np.flatnonzero(symbols == base_top)[0])
            example = {"day": day, "symbol6": base_top,
                       "training_rows": len(training),
                       "candidate_rows": len(current),
                       "factors": {feature: float(x.iloc[picked_index][feature])
                                   for feature in FEATURES},
                       "probabilities": dict(zip(LABELS,
                                                 map(float, probability[picked_index]))),
                       "one_factor_replaced_by_training_median": {}}
            for feature in FEATURES:
                modified = x.iloc[[picked_index]].copy()
                median = float(x_train[feature].median())
                modified.loc[:, feature] = median
                replacement = model.predict_proba(modified)[0, [
                    list(model.classes_).index(c) for c in LABELS]]
                example["one_factor_replaced_by_training_median"][feature] = {
                    "median": median,
                    "probabilities": dict(zip(LABELS, map(float, replacement))),
                    "delta_p_ge3": float(replacement[3]-probability[picked_index, 3]),
                    "delta_p_ge1": float(replacement[2:].sum()-
                                         probability[picked_index, 2:].sum()),
                }
        if baseline_days % 20 == 0 or index == len(dates)-2:
            print(cohort, baseline_days, day, flush=True)
    results = {feature: {
        "mean_logloss_increase": item["loss_delta_sum"]/item["n"],
        "top1_changed_fraction": item["top1_changes"]/item["top1_comparisons"],
        "top1_changed_count": item["top1_changes"],
        "top1_comparisons": item["top1_comparisons"],
        "daily_loss_delta_q10_q50_q90": np.quantile(
            item["daily_loss_delta"], [.1, .5, .9]).tolist(),
    } for feature, item in sums.items()}
    report = {"cohort": cohort, "sklearn_version": sklearn.__version__,
              "method": "shuffle one candidate feature within each held-out day, 3 fixed repeats",
              "baseline_logloss": baseline_loss_sum/baseline_n,
              "labeled_candidates": baseline_n, "prediction_days": baseline_days,
              "max_saved_probability_error": max_saved_probability_error,
              "importance": results, "example": example}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(output, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", required=True, choices=tuple(DATASETS))
    args = parser.parse_args()
    run(args.cohort)

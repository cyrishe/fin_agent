"""Fixed-cohort comparison of four predeclared non-histogram tree classifiers."""
from pathlib import Path
import argparse
import hashlib
import json
import time

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import (ExtraTreesClassifier, GradientBoostingClassifier,
                              HistGradientBoostingClassifier, RandomForestClassifier)
from sklearn.tree import DecisionTreeClassifier

from scripts.mk_minute_archive import day_files, trading_day
from scripts.run_automl_four_class_pipeline import (
    generate_training_features, generate_training_labels, infer,
    select_inference_candidates, select_training_samples,
)


OUTPUT = Path(__file__).resolve().parent
DATASETS = {
    "feb_jul": {
        "path": Path("outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz"),
        "dates": [trading_day(p) for p in day_files([Path("outputs/stock_automl/mk_1m_compact")])],
        "first": 20,
    },
    "aug_oct": {
        "path": Path("docs/stock_automl_runs/20261011_aug_oct_independent20/candidates.csv.gz"),
        "dates": pd.read_csv("docs/stock_automl_runs/20261011_aug_oct_independent20/market_context.csv",
                             dtype={"trade_date": str}).trade_date.tolist(),
        "first": 22,
    },
}


def model_for(name, seed):
    if name == "hgb_b1":
        return HistGradientBoostingClassifier(
            max_iter=60, learning_rate=.05, max_leaf_nodes=7,
            min_samples_leaf=100, l2_regularization=5,
            early_stopping=False, random_state=seed)
    if name == "cart_shallow":
        return DecisionTreeClassifier(max_depth=3, min_samples_leaf=300,
                                      random_state=seed)
    if name == "random_forest_128":
        return RandomForestClassifier(n_estimators=128, min_samples_leaf=100,
                                      max_features=1.0, bootstrap=True, n_jobs=1,
                                      random_state=seed)
    if name == "extra_trees_128":
        return ExtraTreesClassifier(n_estimators=128, min_samples_leaf=100,
                                    max_features=1.0, bootstrap=False, n_jobs=1,
                                    random_state=seed)
    if name == "exact_boost_60":
        return GradientBoostingClassifier(n_estimators=60, learning_rate=.05,
                                          max_leaf_nodes=7, min_samples_leaf=100,
                                          random_state=seed)
    raise ValueError(name)


def run(name, seed, cohort, training_days, time_mode):
    source = DATASETS[cohort]
    rows = pd.read_csv(source["path"], dtype={"signal_date": str,
                                               "next_date": str,
                                               "symbol6": str},
                       float_precision="round_trip")
    dates = source["dates"]
    expected_candidate_dates = dates if cohort == "feb_jul" else dates[2:]
    if sorted(rows.signal_date.unique()) != expected_candidate_dates:
        raise ValueError(f"Candidate dates differ from calendar: {cohort}")
    suffix = "" if training_days == 20 else f"_window{training_days}"
    time_suffix = "" if time_mode == "none" else f"_{time_mode}"
    lib_suffix = "_sk191" if name == "hgb_b1" and sklearn.__version__.startswith("1.9") else ""
    output = OUTPUT / f"{cohort}_{name}_seed{seed}{suffix}{time_suffix}{lib_suffix}"
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "experiment": ("A9 inverse-log recency weighting" if time_mode in
                       ("log_weight", "sqrt_log_weight") else
                       "A6-A8 fixed-cohort tree/time comparison"),
        "cohort": cohort, "algorithm": name, "seed": seed,
        "model_params": model_for(name, seed).get_params(),
        "training_days": training_days,
        "time_mode": time_mode,
        "time_definition": (
            "recency rank: oldest T-20=1, newest T-1=20, current T=21"
            if time_mode in ("recency_feature", "recency_weight10",
                             "log_weight", "sqrt_log_weight") else
            "trading-day lag: current T=0, previous signal day T-1=1"),
        "recency_weight": {
            "recency_weight10": "2 ** ((K-20)/10)",
            "log_weight": "1 / log2(22-K) = 1 / log2(lag+1)",
            "sqrt_log_weight": "1 / sqrt(log2(22-K)) = 1 / sqrt(log2(lag+1))",
        }.get(time_mode),
        "weight_normalization": "divide by mean over training rows in each fold"
            if time_mode in ("recency_weight10", "log_weight", "sqrt_log_weight")
            else None,
        "prediction_first": dates[source["first"]],
        "prediction_last": dates[-2],
        "prediction_days": len(dates) - 1 - source["first"],
        "source": str(source["path"]),
        "source_sha256": hashlib.sha256(source["path"].read_bytes()).hexdigest(),
        "sklearn_version": sklearn.__version__,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    all_scores = []
    folds = []
    start = time.monotonic()
    for index in range(source["first"], len(dates) - 1):
        day = dates[index]
        training = select_training_samples(rows, dates[index-training_days:index])
        if training.signal_date.nunique() != training_days or not training.next_date.le(day).all():
            raise ValueError(f"Incomplete historical training: {day}")
        current = select_inference_candidates(rows, day)
        model = model_for(name, seed)
        train_features = generate_training_features(training)
        train_labels = generate_training_labels(training)
        if time_mode == "none":
            model.fit(train_features, train_labels)
            scored = infer(model, current)
        else:
            lags = pd.Series({date: index - j for j, date in
                              enumerate(dates[index-training_days:index],
                                        start=index-training_days)})
            ages = training.signal_date.map(lags)
            if ages.isna().any() or not ages.between(1, training_days).all():
                raise ValueError(f"Invalid trading-day lag on {day}")
            if time_mode == "age_feature":
                train_features["age_trading_days"] = ages.to_numpy(dtype=int)
                model.fit(train_features, train_labels)
                current_features = current[train_features.columns.drop(
                    "age_trading_days")].copy()
                current_features["age_trading_days"] = 0
            elif time_mode == "recency_feature":
                train_features["recency_trading_day_rank"] = (
                    21 - ages.to_numpy(dtype=int))
                model.fit(train_features, train_labels)
                current_features = current[train_features.columns.drop(
                    "recency_trading_day_rank")].copy()
                current_features["recency_trading_day_rank"] = 21
            elif time_mode in ("recency_weight10", "log_weight", "sqrt_log_weight"):
                lag = ages.to_numpy(dtype=float)
                if time_mode == "recency_weight10":
                    weights = np.power(2.0, -(lag - 1) / 10.0)
                elif time_mode == "log_weight":
                    weights = 1.0 / np.log2(lag + 1.0)
                else:
                    weights = 1.0 / np.sqrt(np.log2(lag + 1.0))
                weights /= weights.mean()
                model.fit(train_features, train_labels, sample_weight=weights)
                current_features = current[train_features.columns]
            else:
                raise ValueError(time_mode)
            probabilities = model.predict_proba(current_features)
            scored = current.copy()
            for label in ("lt0", "0to1", "1to3", "ge3"):
                scored[f"p_{label}"] = probabilities[:, list(model.classes_).index(label)]
            scored["predicted_class"] = np.asarray(model.classes_)[
                probabilities.argmax(axis=1)]
        outcomes = rows.loc[rows.signal_date.eq(day),
                            ["symbol6", "second_high", "close_40"]]
        scored = scored.merge(outcomes, on="symbol6", validate="one_to_one")
        all_scores.append(scored[["signal_date", "symbol6", "signal_price",
                                  "second_high", "close_40", "predicted_class",
                                  "p_lt0", "p_0to1", "p_1to3", "p_ge3"]])
        folds.append({"signal_date": day, "training_rows": len(training),
                      "candidate_rows": len(scored)})
        if len(folds) % 10 == 0 or index == len(dates)-2:
            print(cohort, name, seed, f"{len(folds)}/{manifest['prediction_days']}",
                  day, f"{time.monotonic()-start:.1f}s", flush=True)
    pd.concat(all_scores, ignore_index=True).to_csv(
        output / "scores.csv.gz", index=False,
        compression={"method": "gzip", "mtime": 0}, float_format="%.17g")
    (output / "folds.json").write_text(json.dumps(folds, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True,
                        choices=("hgb_b1", "cart_shallow", "random_forest_128",
                                 "extra_trees_128", "exact_boost_60"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--training-days", type=int, default=20,
                        choices=(19, 20))
    parser.add_argument("--cohort", required=True, choices=tuple(DATASETS))
    parser.add_argument("--time-mode", default="none",
                        choices=("none", "age_feature", "recency_feature",
                                 "recency_weight10", "log_weight",
                                 "sqrt_log_weight"))
    args = parser.parse_args()
    run(args.model, args.seed, args.cohort, args.training_days, args.time_mode)

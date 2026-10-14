"""A12: A11 six-factor signed-ratio model with A9 inverse-log recency weights."""
from pathlib import Path
import argparse
import hashlib
import json
import time

import numpy as np
import pandas as pd
import sklearn

from outputs.stock_automl.feature_ratio_a11.run_feature_variants import (
    LABELS, RATIO_SOURCE, make_features,
)
from outputs.stock_automl.tree_family_comparison_20261013.run_tree_families import (
    DATASETS, model_for,
)
from scripts.run_automl_four_class_pipeline import (
    generate_training_labels, select_inference_candidates, select_training_samples,
)


OUT = Path(__file__).resolve().parent


def run() -> None:
    source = DATASETS["feb_jul"]
    rows = pd.read_csv(source["path"], dtype={
        "signal_date": str, "next_date": str, "symbol6": str,
    }, float_precision="round_trip")
    dates = source["dates"]
    target = OUT / f"ratio_log_weight_sk{sklearn.__version__.replace('.', '')}"
    target.mkdir(parents=True, exist_ok=True)
    manifest = {
        "experiment": "A12 six-factor ratio plus inverse-log recency sample weights",
        "cohort": "feb_jul", "training_days": 20,
        "features": list(make_features(rows.iloc[:1], "ratio").columns),
        "weight": "D=index(signal_date to prediction_date); raw=1/log2(D+1); divide by training-row mean",
        "model": model_for("hgb_b1", 42).get_params(),
        "source": str(source["path"]),
        "source_sha256": hashlib.sha256(source["path"].read_bytes()).hexdigest(),
        "sklearn_version": sklearn.__version__,
        "prediction_first": dates[source["first"]],
        "prediction_last": dates[-2],
        "prediction_days": len(dates) - 1 - source["first"],
    }
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    scores, folds = [], []
    started = time.monotonic()
    for index in range(source["first"], len(dates) - 1):
        day = dates[index]
        train_dates = dates[index - 20:index]
        training = select_training_samples(rows, train_dates)
        if training.signal_date.nunique() != 20 or not training.next_date.le(day).all():
            raise ValueError(f"Incomplete historical training: {day}")
        current = select_inference_candidates(rows, day)
        raw = rows.loc[rows.signal_date.eq(day), ["symbol6", *RATIO_SOURCE]]
        current = current.merge(raw, on="symbol6", how="left", validate="one_to_one")
        x_train = make_features(training, "ratio")
        x_current = make_features(current, "ratio")
        lag = pd.Series({date: index - j for j, date in
                         enumerate(train_dates, start=index - 20)})
        ages = training.signal_date.map(lag)
        if ages.isna().any() or not ages.between(1, 20).all():
            raise ValueError(f"Invalid training age: {day}")
        weight = 1.0 / np.log2(ages.to_numpy(dtype=float) + 1.0)
        weight /= weight.mean()
        model = model_for("hgb_b1", 42)
        model.fit(x_train, generate_training_labels(training), sample_weight=weight)
        probability = model.predict_proba(x_current)
        scored = current[["signal_date", "symbol6", "signal_price"]].copy()
        for label in LABELS:
            scored[f"p_{label}"] = probability[:, list(model.classes_).index(label)]
        scored["predicted_class"] = np.asarray(model.classes_)[probability.argmax(axis=1)]
        outcomes = rows.loc[rows.signal_date.eq(day),
                            ["symbol6", "second_high", "close_40"]]
        scored = scored.merge(outcomes, on="symbol6", validate="one_to_one")
        scores.append(scored[["signal_date", "symbol6", "signal_price",
                              "second_high", "close_40", "predicted_class",
                              *[f"p_{label}" for label in LABELS]]])
        folds.append({"signal_date": day, "training_rows": len(training),
                      "candidate_rows": len(current),
                      "weight_min": float(weight.min()),
                      "weight_max": float(weight.max())})
        if len(folds) % 20 == 0 or index == len(dates) - 2:
            print(sklearn.__version__, f"{len(folds)}/{manifest['prediction_days']}",
                  day, f"{time.monotonic() - started:.1f}s", flush=True)
    pd.concat(scores, ignore_index=True).to_csv(
        target / "scores.csv.gz", index=False,
        compression={"method": "gzip", "mtime": 0}, float_format="%.17g")
    (target / "folds.json").write_text(json.dumps(folds, indent=2))


if __name__ == "__main__":
    argparse.ArgumentParser().parse_args()
    run()

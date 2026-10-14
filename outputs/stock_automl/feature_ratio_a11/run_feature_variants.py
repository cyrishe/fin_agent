"""A11 predeclared feature and tail-treatment variants of the B1 tree."""
from pathlib import Path
import argparse
import hashlib
import json
import time

import numpy as np
import pandas as pd
import sklearn

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.run_automl_four_class_pipeline import (
    generate_training_labels, select_inference_candidates,
    select_training_samples,
)
from outputs.stock_automl.tree_family_comparison_20261013.run_tree_families import (
    DATASETS, model_for,
)


OUT = Path(__file__).resolve().parent
BASE = list(FEATURES)
NUMERIC = BASE[:4]
TAIL = ("volume_ratio", "turnover_so_far_pct", "float_mv_100m_cny")
RATIO_SOURCE = ("ma5", "ma10", "ma20", "min_low_so_far", "prior_close")
LABELS = ("lt0", "0to1", "1to3", "ge3")
VARIANTS = ("drop_volume", "drop_volume_cap99", "ratio", "ratio_cap99", "base_cap99")


def make_features(frame, variant):
    if variant == "base_cap99":
        return frame[BASE].copy()
    if variant in ("drop_volume", "drop_volume_cap99"):
        return frame[[x for x in BASE if x != "volume_4of5_increasing"]].copy()
    if variant not in ("ratio", "ratio_cap99"):
        raise ValueError(variant)
    if not set(RATIO_SOURCE).issubset(frame):
        raise ValueError("Frozen source lacks raw ratio inputs")
    raw = frame[list(RATIO_SOURCE)]
    if raw.isna().any().any() or not raw.gt(0).all().all():
        raise ValueError("Ratio inputs must be positive and complete")
    result = frame[NUMERIC].copy()
    result["ma_bull_ratio"] = np.minimum(frame.ma5/frame.ma10-1,
                                         frame.ma10/frame.ma20-1)
    result["intraday_low_over_highest_ma"] = (
        frame.min_low_so_far/frame[["ma5", "ma10", "ma20"]].max(axis=1)-1)
    if not np.array_equal(result.ma_bull_ratio.gt(0).to_numpy(),
                          frame.ma_bull_5_10_20.eq(1).to_numpy()):
        raise ValueError("MA ratio sign does not reproduce B1 flag")
    if not np.array_equal(result.intraday_low_over_highest_ma.gt(0).to_numpy(),
                          frame.all_intraday_lows_above_ma.eq(1).to_numpy()):
        raise ValueError("Low ratio sign does not reproduce B1 flag")
    return result


def run(cohort, variant):
    if variant in ("ratio", "ratio_cap99") and cohort != "feb_jul":
        raise ValueError("Recent frozen mother table has no raw ratio inputs")
    source = DATASETS[cohort]
    rows = pd.read_csv(source["path"], dtype={"signal_date": str,
                                               "next_date": str,
                                               "symbol6": str},
                       float_precision="round_trip")
    dates = source["dates"]
    target = OUT / f"{cohort}_{variant}_sk{sklearn.__version__.replace('.', '')}"
    target.mkdir(parents=True, exist_ok=True)
    manifest = {
        "experiment": "A11 ratio and tail-treatment feature variants",
        "cohort": cohort, "variant": variant,
        "training_days": 20, "model": model_for("hgb_b1", 42).get_params(),
        "features": list(make_features(rows.iloc[:1], variant).columns),
        "tail_treatment": "training-fold 1%/99% quantile clipping on volume_ratio, turnover_so_far_pct, float_mv_100m_cny"
                          if variant.endswith("cap99") else "none",
        "source": str(source["path"]),
        "source_sha256": hashlib.sha256(source["path"].read_bytes()).hexdigest(),
        "sklearn_version": sklearn.__version__,
        "prediction_first": dates[source["first"]],
        "prediction_last": dates[-2],
        "prediction_days": len(dates)-1-source["first"],
    }
    (target/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    scores = []
    folds = []
    start = time.monotonic()
    for index in range(source["first"], len(dates)-1):
        day = dates[index]
        training = select_training_samples(rows, dates[index-20:index])
        current = select_inference_candidates(rows, day)
        if variant in ("ratio", "ratio_cap99"):
            raw_current = rows.loc[rows.signal_date.eq(day),
                                   ["symbol6", *RATIO_SOURCE]]
            current = current.merge(raw_current, on="symbol6",
                                    how="left", validate="one_to_one")
        x_train = make_features(training, variant)
        x_current = make_features(current, variant)
        if variant.endswith("cap99"):
            lower = x_train[list(TAIL)].quantile(.01)
            upper = x_train[list(TAIL)].quantile(.99)
            x_train.loc[:, list(TAIL)] = x_train[list(TAIL)].clip(
                lower=lower, upper=upper, axis=1)
            x_current.loc[:, list(TAIL)] = x_current[list(TAIL)].clip(
                lower=lower, upper=upper, axis=1)
        model = model_for("hgb_b1", 42)
        model.fit(x_train, generate_training_labels(training))
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
                      "candidate_rows": len(current)})
        if len(folds) % 20 == 0 or index == len(dates)-2:
            print(cohort, variant, sklearn.__version__,
                  f"{len(folds)}/{manifest['prediction_days']}", day,
                  f"{time.monotonic()-start:.1f}s", flush=True)
    pd.concat(scores, ignore_index=True).to_csv(
        target/"scores.csv.gz", index=False,
        compression={"method": "gzip", "mtime": 0}, float_format="%.17g")
    (target/"folds.json").write_text(json.dumps(folds, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", choices=tuple(DATASETS), required=True)
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    args = parser.parse_args()
    run(args.cohort, args.variant)

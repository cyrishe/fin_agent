"""Preselected binning alternatives: linear 255, midpoint 63, CDF 63."""
from pathlib import Path
import json
import os
import numpy as np
import pandas as pd

from sklearn.ensemble._hist_gradient_boosting import binning
from scripts.experiment_automl_second_high_four_class import models
from scripts.mk_minute_archive import day_files, trading_day
from scripts.run_automl_four_class_pipeline import (
    generate_training_features, generate_training_labels, infer,
    select_inference_candidates, select_training_samples,
)

ROOT = Path(__file__).resolve().parent
VARIANT = os.environ["FIN_BIN_VARIANT"]
if VARIANT not in ("linear255", "midpoint63", "avgcdf63"):
    raise ValueError(VARIANT)
METHOD = "linear" if VARIANT == "linear255" else "midpoint" if VARIANT == "midpoint63" else None
MAX_BINS = 63 if VARIANT.endswith("63") else 255
original_fit = binning._BinMapper.fit


def patched_fit(self, X, y=None, sample_weight=None):
    result = original_fit(self, X, y, sample_weight=sample_weight)
    if METHOD is None:
        return result
    q = np.linspace(0, 100, self.n_bins)[1:-1]
    for feature_idx in range(4):
        values = np.asarray(X[:, feature_idx], dtype=float)
        values = values[~np.isnan(values)]
        if len(np.unique(values)) <= self.n_bins - 1:
            continue
        thresholds = np.percentile(values, q, method=METHOD)
        self.bin_thresholds_[feature_idx] = thresholds
        self.n_bins_non_missing_[feature_idx] = len(thresholds) + 1
    return result


binning._BinMapper.fit = patched_fit
cohorts = {
    "feb_jul": (
        Path("outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz"),
        [trading_day(p) for p in day_files([Path("outputs/stock_automl/mk_1m_compact")])], 20),
    "aug_oct": (
        Path("docs/stock_automl_runs/20261011_aug_oct_independent20/candidates.csv.gz"),
        pd.read_csv("docs/stock_automl_runs/20261011_aug_oct_independent20/market_context.csv",
                    dtype={"trade_date": str}).trade_date.tolist(), 22),
}
for name, (path, dates, first) in cohorts.items():
    rows = pd.read_csv(path, dtype={"signal_date": str, "next_date": str,
                                    "symbol6": str}, float_precision="round_trip")
    scores = []
    folds = []
    for i in range(first, len(dates) - 1):
        day = dates[i]
        training = select_training_samples(rows, dates[i-20:i])
        if training.signal_date.nunique() != 20 or not training.next_date.le(day).all():
            raise ValueError(f"Incomplete training: {day}")
        model = models()["small_boosted_tree"].set_params(max_bins=MAX_BINS)
        model.fit(generate_training_features(training), generate_training_labels(training))
        current = infer(model, select_inference_candidates(rows, day))
        outcomes = rows.loc[rows.signal_date.eq(day), ["symbol6", "second_high", "close_40"]]
        current = current.merge(outcomes, on="symbol6", validate="one_to_one")
        scores.append(current[["signal_date", "symbol6", "signal_price",
                               "second_high", "close_40", "predicted_class",
                               "p_lt0", "p_0to1", "p_1to3", "p_ge3"]])
        folds.append({"day": day, "training_rows": len(training),
                      "candidate_count": len(current)})
        if len(folds) % 20 == 0:
            print(VARIANT, name, len(folds), day, flush=True)
    pd.concat(scores, ignore_index=True).to_csv(
        ROOT / f"scores_{name}_{VARIANT}.csv.gz", index=False,
        compression={"method": "gzip", "mtime": 0}, float_format="%.17g")
    (ROOT / f"folds_{name}_{VARIANT}.json").write_text(json.dumps(folds, indent=2))
    print(VARIANT, name, "complete", len(folds), flush=True)

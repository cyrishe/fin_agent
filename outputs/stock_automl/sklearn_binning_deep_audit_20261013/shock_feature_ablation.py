"""Swap one feature's bin edges at a time on fixed shock-date folds."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

from sklearn.ensemble._hist_gradient_boosting import binning
from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_second_high_four_class import models
from scripts.mk_minute_archive import day_files, trading_day
from scripts.run_automl_four_class_pipeline import (
    generate_training_features, generate_training_labels, infer,
    select_inference_candidates, select_top, select_training_samples,
)

ROOT = Path(__file__).resolve().parent
DAYS = ("2026-05-28", "2026-06-09", "2026-06-10", "2026-07-15")
dates = [trading_day(p) for p in day_files([Path("outputs/stock_automl/mk_1m_compact")])]
rows = pd.read_csv(
    "outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz",
    dtype={"signal_date": str, "next_date": str, "symbol6": str},
    float_precision="round_trip")
original_fit = binning._BinMapper.fit
mode = "new_all"


def patched_fit(self, X, y=None, sample_weight=None):
    result = original_fit(self, X, y, sample_weight=sample_weight)
    if mode == "new_all":
        return result
    features_to_old = set()
    method = "midpoint"
    if mode == "old_all":
        features_to_old = set(range(4))
    elif mode == "linear_all":
        features_to_old = set(range(4))
        method = "linear"
    elif mode.startswith("new_except_old_"):
        features_to_old = {int(mode.rsplit("_", 1)[1])}
    elif mode.startswith("old_except_new_"):
        features_to_old = set(range(4)) - {int(mode.rsplit("_", 1)[1])}
    else:
        raise ValueError(mode)
    percentiles = np.linspace(0, 100, self.n_bins)[1:-1]
    for feature_idx in features_to_old:
        values = np.asarray(X[:, feature_idx], dtype=float)
        values = values[~np.isnan(values)]
        if len(np.unique(values)) <= self.n_bins - 1:
            continue
        thresholds = np.percentile(values, percentiles, method=method)
        self.bin_thresholds_[feature_idx] = thresholds
        self.n_bins_non_missing_[feature_idx] = len(thresholds) + 1
    return result


binning._BinMapper.fit = patched_fit
variants = ["old_all", "new_all", "linear_all"]
variants += [f"new_except_old_{i}" for i in range(4)]
variants += [f"old_except_new_{i}" for i in range(4)]
predictions = []
summary = []
for day in DAYS:
    i = dates.index(day)
    training = select_training_samples(rows, dates[i-20:i])
    current = select_inference_candidates(rows, day)
    outcomes = rows.loc[rows.signal_date.eq(day), ["symbol6", "close_40"]]
    for variant in variants:
        mode = variant
        model = models()["small_boosted_tree"]
        model.fit(generate_training_features(training), generate_training_labels(training))
        scored = infer(model, current).merge(outcomes, on="symbol6", validate="one_to_one")
        scored["variant"] = variant
        predictions.append(scored[["signal_date", "symbol6", "variant", "predicted_class",
                                   "signal_price", "close_40", "p_lt0", "p_0to1",
                                   "p_1to3", "p_ge3"]])
        top = select_top(scored)
        first = top[top.selection_rank.eq(1)]
        if len(first):
            row = first.iloc[0]
            winner = {"symbol6": row.symbol6,
                      "return_0940": float(row.close_40 / row.signal_price - 1),
                      "predicted_class": row.predicted_class,
                      "p_ge3": float(row.p_ge3),
                      "p_ge1": float(row.p_ge3 + row.p_1to3)}
        else:
            winner = None
        roots = []
        for predictor in model._predictors[0]:
            node = predictor.nodes[0]
            roots.append({"feature_idx": int(node["feature_idx"]),
                          "feature_name": FEATURES[int(node["feature_idx"])],
                          "num_threshold": float(node["num_threshold"]),
                          "gain": float(node["gain"])})
        summary.append({"day": day, "variant": variant,
                        "training_rows": len(training), "candidate_count": len(scored),
                        "top1": winner, "first_round_roots": roots})
        print(day, variant, winner["symbol6"] if winner else None, flush=True)
pd.concat(predictions, ignore_index=True).to_csv(ROOT / "shock_ablation_scores.csv.gz", index=False)
(ROOT / "shock_ablation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

"""Reconstruct 2026-07-30 K/D inputs, one tree split, and Top1 probabilities."""
from pathlib import Path
import json

import numpy as np
import pandas as pd

from scripts.experiment_automl_second_high_four_class import models
from scripts.mk_minute_archive import day_files, trading_day
from scripts.run_automl_four_class_pipeline import (
    generate_training_features, generate_training_labels,
    select_inference_candidates, select_training_samples,
)


DAY = "2026-07-30"
CODE = "688638"
ROOT = Path(__file__).resolve().parent
rows = pd.read_csv("outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz",
                   dtype={"signal_date": str, "next_date": str, "symbol6": str},
                   float_precision="round_trip")
dates = [trading_day(path) for path in day_files([
    Path("outputs/stock_automl/mk_1m_compact")])]
index = dates.index(DAY)
training_dates = dates[index-20:index]
training = select_training_samples(rows, training_dates)
current = select_inference_candidates(rows, DAY)
distance = training.signal_date.map({date: index-j for j, date in
                                     enumerate(training_dates, start=index-20)}).to_numpy()
rank = 21 - distance
X = generate_training_features(training)
y = generate_training_labels(training)
X_distance = X.copy()
X_distance["age_trading_days"] = distance
X_rank = X.copy()
X_rank["recency_trading_day_rank"] = rank
old = models()["small_boosted_tree"]
new = models()["small_boosted_tree"]
old.fit(X_distance, y)
new.fit(X_rank, y)
candidate = current.loc[current.symbol6.eq(CODE)].copy()
if len(candidate) != 1:
    raise ValueError(f"Expected one {CODE} candidate")
x_distance = candidate[list(X.columns)].copy()
x_distance["age_trading_days"] = 0
x_rank = candidate[list(X.columns)].copy()
x_rank["recency_trading_day_rank"] = 21
raw_old = old._raw_predict(x_distance)[0]
raw_new = new._raw_predict(x_rank)[0]
p_old = old.predict_proba(x_distance)[0]
p_new = new.predict_proba(x_rank)[0]
if not np.array_equal(raw_old, raw_new) or not np.array_equal(p_old, p_new):
    raise ValueError("Reversed encoding changed this candidate's raw prediction")
weight_raw = np.power(2.0, (rank - 20) / 10.0)
weight_mean = float(weight_raw.mean())
weight_fit = weight_raw / weight_mean
weights_by_day = []
for K, date in enumerate(training_dates, start=1):
    mask = rank == K
    weights_by_day.append({
        "date": date, "K": K, "D": 21-K, "training_rows": int(mask.sum()),
        "raw_weight_per_row": float(weight_raw[mask][0]),
        "fit_weight_per_row": float(weight_fit[mask][0]),
        "total_fit_weight": float(weight_fit[mask].sum()),
    })
root_old = old._predictors[0][1].nodes
root_new = new._predictors[0][1].nodes
def root_detail(nodes):
    root = nodes[0]
    return {"feature_idx": int(root["feature_idx"]),
            "threshold": float(root["num_threshold"]),
            "gain": float(root["gain"]),
            "left_count": int(nodes[root["left"]]["count"]),
            "right_count": int(nodes[root["right"]]["count"]),
            "left_value": float(nodes[root["left"]]["value"]),
            "right_value": float(nodes[root["right"]]["value"])}
daily = pd.read_csv(ROOT / "time_lag_daily_gate.csv",
                    dtype={"signal_date": str, "symbol6": str})
gate = daily.loc[(daily.signal_date == DAY) &
                 (daily.arm == "sklearn_1.7.1_age_feature")].iloc[0]
outcome = rows.loc[(rows.signal_date == DAY) & rows.symbol6.eq(CODE)].iloc[0]
result = {
    "signal_date": DAY, "symbol6": CODE,
    "training_rows": len(training), "candidate_rows": len(current),
    "label_counts": y.value_counts().to_dict(),
    "model_params": old.get_params(),
    "training_days": weights_by_day,
    "raw_weight_mean": weight_mean,
    "normalized_weight_mean": float(weight_fit.mean()),
    "normalized_weight_ess": float(weight_fit.sum()**2 / np.square(weight_fit).sum()),
    "candidate_factors": candidate.iloc[0][list(X.columns)].to_dict(),
    "candidate_D": 0, "candidate_K": 21,
    "classes_order": old.classes_.tolist(),
    "raw_logits_D": raw_old.tolist(), "raw_logits_K": raw_new.tolist(),
    "probabilities_D": p_old.tolist(), "probabilities_K": p_new.tolist(),
    "first_round_1to3_tree_root_D": root_detail(root_old),
    "first_round_1to3_tree_root_K": root_detail(root_new),
    "signal_price": float(outcome.signal_price),
    "second_high": float(outcome.second_high),
    "second_high_return": float(outcome.second_high / outcome.signal_price - 1),
    "close_40": float(outcome.close_40),
    "return_0940": float(outcome.close_40 / outcome.signal_price - 1),
    "amount_gate": gate.gate,
}
(ROOT / "case_20260730.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps({key: result[key] for key in (
    "signal_date", "symbol6", "training_rows", "candidate_rows",
    "candidate_D", "candidate_K", "classes_order", "raw_logits_D",
    "raw_logits_K", "probabilities_D", "probabilities_K", "amount_gate")},
    ensure_ascii=False, indent=2))

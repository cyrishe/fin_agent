"""Inspect class overlap in the exact seven-factor training sample."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.neighbors import NearestNeighbors

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import BINARY, DATA
from scripts.experiment_automl_fuzzy_boundary import PRICES


OUT = Path("docs/stock_automl_runs/20261009_training_overlap")
SEED = 20261009


def main():
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(PRICES, dtype={"symbol6": str})
    data = base.merge(prices, on=["next_date", "symbol6"], validate="one_to_one")
    days = sorted(data.signal_date.unique())[-20:]
    train = data[data.signal_date.isin(days)].copy().reset_index(drop=True)
    train["next_high_return"] = train.max_high / train.entry_1440 - 1
    train["label"] = np.select([train.next_high_return.gt(.01),
                                train.next_high_return.lt(0)], [1, -1], default=0)
    numeric = [name for name in FEATURES if name not in BINARY]
    center = train[numeric].mean()
    scale = train[numeric].std(ddof=0).replace(0, 1)
    transformed = np.column_stack((
        ((train[numeric] - center) / scale).to_numpy(),
        train[list(BINARY)].to_numpy(dtype=float)))
    train["index"] = np.arange(len(train))
    rng = np.random.default_rng(SEED)
    sample_days = sorted(rng.choice(days, size=4, replace=False).tolist())
    sampled = pd.concat([
        group[group.label.eq(label)].sample(n=2, random_state=SEED + batch*10 + label)
        for batch, day in enumerate(sample_days, start=1)
        for label in (-1, 0, 1)
        for group in [train[train.signal_date.eq(day)]]
    ]).copy()
    sampled["batch"] = sampled.signal_date.map({d: n+1 for n, d in enumerate(sample_days)})
    rows = []
    for row in sampled.itertuples():
        day = train[train.signal_date.eq(row.signal_date)]
        opposite = day[day.label.ne(row.label)]
        exact_binary = opposite[list(BINARY)].eq(
            np.array([getattr(row, name) for name in BINARY])).all(axis=1)
        matches = opposite[exact_binary]
        if matches.empty:
            matches = opposite
        distances = np.linalg.norm(transformed[matches.index] - transformed[row.index], axis=1)
        nearest = matches.iloc[int(np.argmin(distances))]
        same = day[day.label.eq(row.label) & day.symbol6.ne(row.symbol6)]
        same_binary = same[list(BINARY)].eq(
            np.array([getattr(row, name) for name in BINARY])).all(axis=1)
        same_matches = same[same_binary] if same_binary.any() else same
        same_distances = np.linalg.norm(
            transformed[same_matches.index] - transformed[row.index], axis=1)
        rows.append({"batch": int(row.batch), "symbol6": row.symbol6,
                     "name": row.name,
                     "label": int(row.label), "next_high_return": float(row.next_high_return),
                     "nearest_symbol6": nearest.symbol6, "nearest_name": nearest["name"],
                     "nearest_label": int(nearest.label),
                     "nearest_return": float(nearest.next_high_return),
                     "same_three_binary": bool(exact_binary.any()),
                     "standardized_distance": float(np.min(distances)),
                     "nearest_same_label_distance": float(np.min(same_distances)),
                     **{f"sample_{name}": float(getattr(row, name)) for name in FEATURES},
                     **{f"opposite_{name}": float(nearest[name]) for name in FEATURES}})
    match = pd.DataFrame(rows)
    neighborhood = []
    for day, group in train.groupby("signal_date"):
        k = min(20, len(group)-1)
        model = NearestNeighbors(n_neighbors=k).fit(transformed[group.index])
        indices = model.kneighbors(return_distance=False)
        # kneighbors(X=None) excludes the query point itself.
        if indices.shape[1] != k:
            raise ValueError("Unexpected nearest-neighbor count")
        neighbor_labels = group.label.to_numpy()[indices]
        neighborhood.extend({"label": int(label),
                             "neighbor_hit_share": float(np.mean(near == 1)),
                             "neighbor_critical_share": float(np.mean(near == -1))}
                            for label, near in zip(group.label, neighbor_labels))
    nearby = pd.DataFrame(neighborhood)
    feature_auc = {}
    for column in FEATURES:
        score = train[column].to_numpy()
        auc = roc_auc_score(train.label.eq(1), score)
        feature_auc[column] = {"positive_auc_raw_direction": float(auc),
                               "positive_auc_best_direction": float(max(auc, 1-auc)),
                               "median_by_class": {
                                   str(k): float(v) for k, v in
                                   train.groupby("label")[column].median().items()}}
    summary = {"seed": SEED, "sample_days": sample_days,
               "train_rows": len(train),
               "class_counts": {str(k): int(v) for k, v in
                                train.label.value_counts().sort_index().items()},
               "random_sample_rows": len(sampled),
               "matched_opposite_same_binary": int(match.same_three_binary.sum()),
               "median_opposite_distance": float(match.standardized_distance.median()),
               "neighbor_shares_by_true_class": nearby.groupby("label").agg(
                   count=("label", "size"),
                   mean_neighbor_hit=("neighbor_hit_share", "mean"),
                   mean_neighbor_critical=("neighbor_critical_share", "mean")
               ).to_dict("index"),
               "feature_auc": feature_auc}
    OUT.mkdir(parents=True, exist_ok=True)
    columns = ["batch", "symbol6", "name", "label", "next_high_return", *FEATURES]
    sampled[columns].sort_values(["batch", "label", "symbol6"]).to_csv(
        OUT / "random_samples.csv", index=False)
    match.to_csv(OUT / "nearest_opposite.csv", index=False)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(sampled[columns].sort_values(["batch", "label", "symbol6"]).to_string(index=False))
    print(match.to_string(index=False))


if __name__ == "__main__":
    main()

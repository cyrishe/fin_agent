"""Exploratory precision/coverage audit on fixed out-of-day rolling folds."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_fuzzy_boundary import C, PRICES, prepare, targets


OUT = Path("docs/stock_automl_runs/20261009_training_overlap")


def main():
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(PRICES, dtype={"symbol6": str})
    data = base.merge(prices, on=["next_date", "symbol6"], validate="one_to_one")
    days = sorted(data.signal_date.unique())
    if len(days) != 40 or len(data) != 14292:
        raise ValueError("Historical sample changed")
    data["return"] = data.max_high / data.entry_1440 - 1
    data["label"] = np.select([data["return"] > .01, data["return"] < 0],
                              [1, -1], default=0)
    records = []
    for i in range(20, len(days)):
        train = data[data.signal_date.isin(days[i-20:i])]
        test = data[data.signal_date.eq(days[i])]
        x_train, x_test = prepare(train, test)
        _, weights = targets(train["return"], "边界降权")
        weights /= weights.mean()
        three = LogisticRegression(C=C, max_iter=2000, solver="lbfgs")
        three.fit(x_train[:, 1:], train.label, sample_weight=weights)
        binary = LogisticRegression(C=C, max_iter=2000, solver="lbfgs")
        binary.fit(x_train[:, 1:], train.label.eq(1).astype(int),
                   sample_weight=weights)
        for name, model, train_score, test_score in (
            ("three_class_p_hit", three, three.predict_proba(x_train[:, 1:])[:, 2],
             three.predict_proba(x_test[:, 1:])[:, 2]),
            ("binary_p_hit", binary, binary.predict_proba(x_train[:, 1:])[:, 1],
             binary.predict_proba(x_test[:, 1:])[:, 1]),
        ):
            frame = test[["signal_date", "symbol6", "name", "label", "return"]].copy()
            frame["model"] = name
            frame["score"] = test_score
            frame["train_p995"] = float(np.quantile(train_score, .995))
            frame = frame.sort_values(["score", "symbol6"],
                                      ascending=[False, True]).reset_index(drop=True)
            frame["rank"] = np.arange(1, len(frame)+1)
            records.append(frame)
    scored = pd.concat(records, ignore_index=True)
    rows = []
    for model, frame in scored.groupby("model"):
        day_base = frame.label.eq(1).mean()
        for rule, selected in (
            *((f"daily_top_{k}", frame[frame["rank"].le(k)]) for k in (1, 2, 5, 10)),
            ("train_score_top_0.5pct_and_daily_max2",
             frame[frame["rank"].le(2) & frame.score.gt(frame.train_p995)]),
        ):
            n = len(selected)
            rows.append({"model": model, "rule": rule, "test_days": 20,
                         "test_candidates": len(frame), "pool_hit_rate": day_base,
                         "selected": n, "selected_days": selected.signal_date.nunique(),
                         "hit": int(selected.label.eq(1).sum()),
                         "neutral": int(selected.label.eq(0).sum()),
                         "critical": int(selected.label.eq(-1).sum()),
                         "precision": float(selected.label.eq(1).mean()) if n else None,
                         "positive_recall": float(selected.label.eq(1).sum() /
                                                  frame.label.eq(1).sum()),
                         "matched_daily_random_expected_hit": float(
                             sum(group.label.eq(1).mean() * len(
                                 selected[selected.signal_date.eq(day)])
                                 for day, group in frame.groupby("signal_date")))})
    OUT.mkdir(parents=True, exist_ok=True)
    scored.to_csv(OUT / "rolling_out_of_day_scores.csv.gz", index=False,
                  compression="gzip")
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "selective_precision_pilot.csv", index=False)
    (OUT / "selective_precision_pilot.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    (OUT / "selective_precision_manifest.json").write_text(json.dumps({
        "days": days, "folds": 20, "training_window_days": 20,
        "candidates": "signal-day 14:40 return 3%-6%",
        "target": "next morning first ten one-minute bar maximum high / 14:40 entry - 1 > 1%",
        "training": "seven-factor LogisticRegression C=0.2; boundary weights at 0% and 1%; each fold uses only prior 20 signal days",
        "abstention": "same-fold training-score 99.5th percentile; score must exceed it; at most two stocks per day",
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (DATA, PRICES)},
    }, ensure_ascii=False, indent=2) + "\n")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()

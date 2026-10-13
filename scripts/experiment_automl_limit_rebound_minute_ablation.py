"""Exploratory first-board minute-feature ablation within the archive window."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.build_automl_limit_rebound_features import FEATURES
from scripts.experiment_automl_limit_rebound_model import (
    FEATURE_SETS, KEY, evaluate, event_keys, file_sha, make_model,
)


SOURCE = Path("outputs/stock_automl/limit_rebound_features_r1_with_minutes")
OUT = Path("outputs/stock_automl/limit_rebound_model_r1")
MINUTE = tuple(name for name in FEATURES["first_board_minute"]
               if name != "first_minute_complete")


def run(source: Path = SOURCE, out: Path = OUT) -> dict:
    features = pd.read_csv(source / "features.csv.gz", dtype={"symbol": str})
    labels = pd.read_csv(source / "labels.csv.gz", dtype={"symbol": str})
    panel = features.merge(labels, on=KEY, validate="one_to_one")
    panel = panel[panel.label_next_observed.eq(True) &
                  panel.first_minute_complete.eq(1) &
                  panel.asof_date.le("2026-07-31")].copy()
    target = "label_next_limit_up"
    panel[target] = panel[target].astype(int)
    train = panel[panel.asof_date.le("2026-04-29")].copy()
    test = panel[panel.asof_date.between("2026-05-06", "2026-07-31")].copy()
    # May 1-5 exchange holidays; April 30 is the one-market-day embargo.
    train = train[~pd.MultiIndex.from_frame(train[["symbol", "first_limit_date"]]).isin(event_keys(test))]
    if event_keys(train) & event_keys(test):
        raise AssertionError("Event crosses minute-ablation split")
    daily = FEATURE_SETS["shape_flow_market"]
    results = {}
    predictions = test[[*KEY, target]].copy()
    for name, columns in {"daily": daily, "daily_plus_first_board_minute": daily + MINUTE}.items():
        model = make_model("shallow_boost")
        model.fit(train[list(columns)], train[target])
        score = model.predict_proba(test[list(columns)])[:, 1]
        results[name] = {"features": list(columns), **evaluate(test, score, target)}
        predictions[f"p_{name}"] = score
    out.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(out / "minute_ablation_predictions.csv.gz", index=False,
                       compression="gzip")
    summary = {"purpose": "exploratory minute ablation; restricted archive dates, not the Sep-Oct holdout",
               "train": [train.asof_date.min(), train.asof_date.max(), len(train)],
               "test": [test.asof_date.min(), test.asof_date.max(), len(test)],
               "script_sha256": file_sha(Path(__file__)),
               "minute_source_sha256": file_sha(source / "features.csv.gz"),
               "results": results}
    (out / "minute_ablation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"train": summary["train"], "test": summary["test"],
                      "results": {name: {k: val for k, val in result.items() if k != "features"}
                                  for name, result in results.items()}},
                     ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    run()

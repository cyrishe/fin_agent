"""Fixed-model sensitivity to 2024 training rows and late-imported 9-prefix stocks."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.build_automl_limit_rebound_features import KEY
from scripts.experiment_automl_limit_rebound_2y import BASE, daily_topk
from scripts.experiment_automl_limit_rebound_3day import SOURCE, TARGET, split
from scripts.experiment_automl_limit_rebound_model import evaluate, make_model


OUT = Path("outputs/stock_automl/limit_rebound_daily_sensitivity_r1")


def run(source: Path = SOURCE, out: Path = OUT) -> dict:
    features = pd.read_csv(source / "features.csv.gz", dtype={"symbol": str})
    labels = pd.read_csv(source / "labels.csv.gz", dtype={"symbol": str})
    panel = features.merge(labels, on=KEY, validate="one_to_one")
    panel = panel[panel.label_next3_observed.eq(True)].copy()
    panel[TARGET] = panel[TARGET].astype(int)
    results = {}
    for name, allowed, from_year in (
        ("all_2024_onward", ("0", "3", "6", "9"), "2024-10-01"),
        ("train_2025_onward", ("0", "3", "6", "9"), "2025-01-01"),
        ("no_9_prefix", ("0", "3", "6"), "2024-10-01"),
        ("no_2024_or_9_prefix", ("0", "3", "6"), "2025-01-01"),
    ):
        subset = panel[panel.symbol.str.startswith(allowed)]
        _, _, refit, test = split(subset)
        refit = refit[refit.asof_date.ge(from_year)]
        model = make_model("shallow_boost")
        model.fit(refit[list(BASE)], refit[TARGET])
        score = model.predict_proba(test[list(BASE)])[:, 1]
        results[name] = {"train_n": len(refit), "test_n": len(test),
                         "features": "fixed 31 daily/market features; no minute bars",
                         "metrics": evaluate(test, score, TARGET),
                         "top3": daily_topk(test, score, 3, TARGET)}
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({name: {"train_n": r["train_n"], "test_n": r["test_n"],
                             "ap": r["metrics"]["average_precision"],
                             "top5": [r["metrics"]["top5_hits"], r["metrics"]["top5_picks"]],
                             "top3": [r["top3"]["hits"], r["top3"]["picks"]]}
                      for name, r in results.items()}, ensure_ascii=False, indent=2))
    return results


if __name__ == "__main__":
    run()

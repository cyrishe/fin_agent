"""Fixed-design quarterly forward checks for one-day and three-day targets."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.build_automl_limit_rebound_features import KEY
from scripts.experiment_automl_limit_rebound_2y import BASE
from scripts.experiment_automl_limit_rebound_model import evaluate, event_keys, make_model


SOURCE = Path("outputs/stock_automl/limit_rebound_2y_r1")
OUT = Path("outputs/stock_automl/limit_rebound_walkforward_r1")
WINDOWS = (
    ("2025-04-01", "2025-06-30"),
    ("2025-07-01", "2025-09-30"),
    ("2025-10-01", "2025-12-31"),
    ("2026-01-01", "2026-03-31"),
    ("2026-04-01", "2026-06-30"),
    ("2026-07-01", "2026-09-30"),
)


def run(source: Path = SOURCE, out: Path = OUT) -> list[dict]:
    features = pd.read_csv(source / "features.csv.gz", dtype={"symbol": str})
    labels = pd.read_csv(source / "labels.csv.gz", dtype={"symbol": str})
    panel = features.merge(labels, on=KEY, validate="one_to_one")
    calendar = sorted(panel.asof_date.unique())
    rows = []
    for horizon, observed_col, target in (
        (1, "label_next_observed", "label_next_limit_up"),
        (3, "label_next3_observed", "label_next3_any_limit_up"),
    ):
        known = panel[panel[observed_col].eq(True)].copy()
        known[target] = known[target].astype(int)
        for start, end in WINDOWS:
            first = next((i for i, day in enumerate(calendar) if day >= start), None)
            if first is None or first <= horizon:
                continue
            train_end = calendar[first - horizon - 1]
            train = known[known.asof_date.le(train_end)].copy()
            test = known[known.asof_date.between(start, end)].copy()
            train = train[~pd.MultiIndex.from_frame(train[["symbol", "first_limit_date"]]).isin(event_keys(test))]
            if train.empty or test.empty:
                continue
            model = make_model("shallow_boost")
            model.fit(train[list(BASE)], train[target])
            score = model.predict_proba(test[list(BASE)])[:, 1]
            rows.append({"horizon": horizon, "test_start": test.asof_date.min(),
                         "test_end": test.asof_date.max(), "train_end": train.asof_date.max(),
                         "train_n": len(train), **evaluate(test, score, target)})
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "quarterly.csv", index=False)
    (out / "summary.json").write_text(json.dumps({"fixed_features": list(BASE),
        "model": "shallow_boost", "windows": rows,
        "note": "Fixed-design retrospective forward checks; design was studied in 2026 and earlier windows are not pristine prospective tests."},
        ensure_ascii=False, indent=2) + "\n")
    print(pd.DataFrame(rows)[["horizon", "test_start", "test_end", "n", "positive",
                              "average_precision", "top5_hits", "top5_picks", "top5_lift"]].to_string(index=False))
    return rows


if __name__ == "__main__":
    run()

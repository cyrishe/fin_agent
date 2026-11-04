"""Separate three-trading-day rebound target with a horizon-safe time split."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd

from scripts.build_automl_limit_rebound_features import KEY
from scripts.experiment_automl_limit_rebound_2y import BASE, SETS, daily_topk, same_day_random_test
from scripts.experiment_automl_limit_rebound_model import (
    block_bootstrap_top5, evaluate, event_keys, make_model, topk_by_day,
)


SOURCE = Path("outputs/stock_automl/limit_rebound_2y_r1")
OUT = Path("outputs/stock_automl/limit_rebound_3day_model_r1")
TARGET = "label_next3_any_limit_up"


def split(panel: pd.DataFrame):
    # The last label day from each earlier window precedes the next decision window.
    train = panel[panel.asof_date.between("2024-10-01", "2026-03-26")].copy()
    valid = panel[panel.asof_date.between("2026-04-01", "2026-06-25")].copy()
    test = panel[panel.asof_date.between("2026-07-01", "2026-10-10")].copy()
    train = train[~pd.MultiIndex.from_frame(train[["symbol", "first_limit_date"]]).isin(event_keys(valid))]
    refit = panel[panel.asof_date.between("2024-10-01", "2026-06-25")].copy()
    refit = refit[~pd.MultiIndex.from_frame(refit[["symbol", "first_limit_date"]]).isin(event_keys(test))]
    if event_keys(train) & event_keys(valid) or event_keys(refit) & event_keys(test):
        raise AssertionError("Event crosses three-day split")
    return train, valid, refit, test


def week_bootstrap_topk(frame: pd.DataFrame, score, k: int = 5,
                        repeats: int = 5000) -> dict:
    """Resample weeks so overlapping three-day outcomes move together."""
    import numpy as np

    picks = topk_by_day(frame, score, TARGET, k)
    base = frame.groupby("asof_date")[TARGET].mean()
    picks["expected"] = picks.asof_date.map(base)
    picks["week"] = pd.to_datetime(picks.asof_date).dt.to_period("W").astype(str)
    weeks = picks.groupby("week").agg(hits=(TARGET, "sum"),
                                       selected=(TARGET, "size"),
                                       expected=("expected", "sum"))
    rng = np.random.default_rng(20261013)
    sample = rng.integers(0, len(weeks), size=(repeats, len(weeks)))
    hits = weeks.hits.to_numpy()[sample].sum(axis=1)
    selected = weeks.selected.to_numpy()[sample].sum(axis=1)
    expected = weeks.expected.to_numpy()[sample].sum(axis=1)
    return {"weeks": len(weeks),
            "precision_95pct_week_bootstrap": np.quantile(hits / selected, [.025, .975]).tolist(),
            "lift_95pct_week_bootstrap": np.quantile(hits / expected, [.025, .975]).tolist()}


def run(source: Path = SOURCE, out: Path = OUT) -> dict:
    features = pd.read_csv(source / "features_industry.csv.gz", dtype={"symbol": str})
    labels = pd.read_csv(source / "labels.csv.gz", dtype={"symbol": str})
    panel = features.merge(labels, on=KEY, validate="one_to_one")
    panel = panel[panel.label_next3_observed.eq(True)].copy()
    panel[TARGET] = panel[TARGET].astype(int)
    train, valid, refit, test = split(panel)
    candidates = []
    for set_name, columns in SETS.items():
        for name in ("logistic", "shallow_boost"):
            model = make_model(name)
            model.fit(train[list(columns)], train[TARGET])
            score = model.predict_proba(valid[list(columns)])[:, 1]
            candidates.append({"feature_set": set_name, "model": name,
                               "feature_count": len(columns), **evaluate(valid, score, TARGET)})
    candidates.sort(key=lambda row: (-row["average_precision"], row["brier"], row["feature_count"]))
    winner = candidates[0]
    columns = SETS[winner["feature_set"]]
    model = make_model(winner["model"])
    model.fit(refit[list(columns)], refit[TARGET])
    score = model.predict_proba(test[list(columns)])[:, 1]
    holdout = evaluate(test, score, TARGET)
    holdout.update(block_bootstrap_top5(test, score, TARGET))
    holdout.update(week_bootstrap_topk(test, score))
    # One selected event can appear on consecutive cooling days; count its first pick once.
    top5 = topk_by_day(test, score, TARGET, 5)
    once = top5.sort_values("asof_date").drop_duplicates(["symbol", "first_limit_date"])
    days = sorted(test.asof_date.unique())
    nonoverlap = {}
    for offset in range(3):
        selected = test.asof_date.isin(days[offset::3]).to_numpy()
        nonoverlap[str(offset)] = daily_topk(test[selected], score[selected], 5, TARGET)
    base_model = make_model(winner["model"])
    base_model.fit(refit[list(BASE)], refit[TARGET])
    base_score = base_model.predict_proba(test[list(BASE)])[:, 1]
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(candidates).to_csv(out / "validation_candidates.csv", index=False)
    predictions = test[[*KEY, TARGET]].copy()
    predictions["score"] = score
    predictions["base_score"] = base_score
    predictions.to_csv(out / "holdout_predictions.csv.gz", index=False, compression="gzip")
    joblib.dump(model, out / "selected_model.joblib")
    summary = {
        "target": "any sealed limit-up in next three market days after a cooling close",
        "selected_on_validation_ap": winner, "features": list(columns),
        "split": {"train": [train.asof_date.min(), train.asof_date.max(), len(train)],
                  "validation": [valid.asof_date.min(), valid.asof_date.max(), len(valid)],
                  "refit": [refit.asof_date.min(), refit.asof_date.max(), len(refit)],
                  "test": [test.asof_date.min(), test.asof_date.max(), len(test)],
                  "embargo": "Three market days near March/April and June/July boundaries; cross-split events purged"},
        "holdout": holdout,
        "holdout_fixed_base_comparator": evaluate(test, base_score, TARGET),
        "topk": {str(k): daily_topk(test, score, k, TARGET) for k in (1, 3, 5, 10)},
        "top3_week_interval": week_bootstrap_topk(test, score, k=3),
        "every_third_day_offsets": nonoverlap,
        "same_day_random_test": same_day_random_test(test, score, TARGET),
        "top5_first_pick_per_event": {"events": len(once), "hits": int(once[TARGET].sum()),
                                       "precision": float(once[TARGET].mean())},
        "holdout_by_month": {
            month: evaluate(group, score[test.asof_date.str[:7].eq(month).to_numpy()], TARGET)
            for month, group in test.groupby(test.asof_date.str[:7])},
        "holdout_by_cooling_day": {
            str(day): evaluate(group, score[test.cooling_day.eq(day).to_numpy()], TARGET)
            for day, group in test.groupby("cooling_day")},
        "source_sha256": hashlib.sha256((source / "features_industry.csv.gz").read_bytes()).hexdigest(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"selected": winner, "holdout": holdout,
                      "base_comparator": summary["holdout_fixed_base_comparator"],
                      "topk": summary["topk"], "once": summary["top5_first_pick_per_event"]},
                     ensure_ascii=False, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    run()

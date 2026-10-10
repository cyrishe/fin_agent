"""Daily 09:40 class mix and shorter rolling four-class training windows."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from scripts.experiment_automl_recovered_morning_labels import recovered_rows
from scripts.experiment_automl_second_high_four_class import four_class, models
from scripts.run_automl_four_class_pipeline import (
    PREPARED, generate_training_features, generate_training_labels, infer,
    select_inference_candidates, select_top, select_training_samples,
    train_model, verify_next_day,
)


API = Path("docs/stock_automl_runs/20261010_four_class_baseline_recent20/minute_api_label_audit.csv")
OUT = Path("docs/stock_automl_runs/20261010_four_class_short_window_0940")
ARMS = ("recent5", "recent5_leaf50", "recent5_leaf25", "recent10",
        "recent5_hold_tminus1")
CLASSES = ("ge3", "1to3", "0to1", "lt0")


def daily_distribution(rows: pd.DataFrame) -> pd.DataFrame:
    records = []
    for date, group in rows.groupby("signal_date", sort=True):
        entry = group.signal_price
        for outcome, price in (("0940_close", group.close_40),
                               ("second_high", group.second_high)):
            known = price.notna() & entry.gt(0)
            labels = four_class(price[known] / entry[known] - 1)
            counts = pd.Series(labels).value_counts()
            records.append({"signal_date": date, "outcome": outcome,
                            "candidate_rows": len(group), "known": int(known.sum()),
                            "unknown": int((~known).sum()),
                            **{label: int(counts.get(label, 0)) for label in CLASSES}})
    frame = pd.DataFrame(records)
    if not frame[list(CLASSES)].sum(axis=1).eq(frame.known).all():
        raise ValueError("Daily class counts do not reconcile")
    return frame


def run(rows: pd.DataFrame, output: Path) -> dict:
    dates = sorted(rows.signal_date.unique())
    if len(rows) != 14783 or len(dates) != 40:
        raise ValueError("Frozen 40-day candidate pool changed")
    distribution = daily_distribution(rows)
    scored_rows, picks_rows, folds = [], [], []
    for ix in range(20, 40):
        day = dates[ix]
        current = select_inference_candidates(rows, day)
        for arm, train_dates in (("recent5", dates[ix-5:ix]),
                                 ("recent5_leaf50", dates[ix-5:ix]),
                                 ("recent5_leaf25", dates[ix-5:ix]),
                                 ("recent10", dates[ix-10:ix]),
                                 ("recent5_hold_tminus1", dates[ix-6:ix-1])):
            training = select_training_samples(rows, train_dates)
            if len(train_dates) != (10 if arm == "recent10" else 5):
                raise ValueError("Wrong rolling window length")
            if not training.next_date.le(day).all():
                raise ValueError("Training label is not mature by the signal time")
            features = generate_training_features(training)
            labels = generate_training_labels(training)
            if arm in ("recent5_leaf50", "recent5_leaf25"):
                model = models()["small_boosted_tree"].set_params(
                    min_samples_leaf=50 if arm.endswith("50") else 25)
                model.fit(features, labels)
            else:
                model = train_model(features, labels)
            scored = infer(model, current)
            scored["arm"] = arm
            scored_rows.append(scored)
            picked = verify_next_day(select_top(scored), rows)
            picked["arm"] = arm
            picks_rows.append(picked)
            folds.append({"test_date": day, "arm": arm,
                          "train_dates": "|".join(train_dates),
                          "train_rows": len(training), "candidates": len(current)})
            if arm == "recent5_hold_tminus1":
                prior = select_inference_candidates(rows, dates[ix-1])
                prior_picks = verify_next_day(select_top(infer(model, prior)), rows)
                prior_picks["arm"] = arm
                prior_picks["test_date"] = day
                prior_picks["stage"] = "T−1"
                picks_rows.append(prior_picks)
        print(f"short window {day} complete", flush=True)
    scored = pd.concat(scored_rows, ignore_index=True)
    picks = pd.concat(picks_rows, ignore_index=True)
    picks["stage"] = picks.stage.fillna("T")
    picks["actual_0940_class"] = np.where(
        picks.actual_0940_return.notna(),
        four_class(picks.actual_0940_return).astype(str), "UNKNOWN")
    folds = pd.DataFrame(folds)
    output.mkdir(parents=True, exist_ok=True)
    distribution.to_csv(output / "daily_candidate_class_distribution.csv", index=False)
    scored.to_csv(output / "all_predictions.csv.gz", index=False, compression="gzip")
    picks.to_csv(output / "top2_verified.csv", index=False)
    folds.to_csv(output / "folds.csv", index=False)
    summary = {"test_dates": dates[20:], "candidate_pool_rows": len(rows),
               "daily_distribution": {}, "arms": {}}
    for outcome in ("0940_close", "second_high"):
        day_rows = distribution[distribution.signal_date.isin(dates[20:]) &
                                distribution.outcome.eq(outcome)]
        summary["daily_distribution"][outcome] = {
            "known": int(day_rows.known.sum()),
            "unknown": int(day_rows.unknown.sum()),
            "counts": {label: int(day_rows[label].sum()) for label in CLASSES},
            "equal_day_fraction": {
                label: float((day_rows[label] / day_rows.known).mean())
                for label in CLASSES}}
    for (arm, stage), group in picks.groupby(["arm", "stage"]):
        top1 = group[group.selection_rank.eq(1)]
        known = top1[top1.actual_class.ne("UNKNOWN")]
        measured = {"top1_picks": len(top1), "known_top1": len(known),
                    "class_counts": {label: int(known.actual_class.eq(label).sum())
                                     for label in CLASSES},
                    "sell_0940_class_counts": {
                        label: int(known.actual_0940_class.eq(label).sum())
                        for label in CLASSES},
                    "mean_0940_return_pct": float(known.actual_0940_return.mean()*100)
                    if len(known) else None,
                    "training_rows_mean": float(folds.loc[folds.arm.eq(arm),
                                                      "train_rows"].mean())}
        if stage == "T":
            checked = scored[scored.arm.eq(arm)].merge(
                rows[["signal_date", "symbol6", "second_high", "signal_price"]]
                .rename(columns={"signal_price": "actual_entry"}),
                on=["signal_date", "symbol6"], validate="one_to_one")
            checked = checked[checked.second_high.notna()]
            actual = four_class(checked.second_high / checked.actual_entry - 1)
            measured["all_candidate_ge3_auc"] = float(roc_auc_score(
                pd.Series(actual).eq("ge3"), checked.p_ge3))
        summary["arms"][f"{arm}_{stage}"] = measured
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                   indent=2) + "\n")
    return summary


def main() -> None:
    rows = recovered_rows(pd.read_csv(PREPARED, dtype={"symbol6": str}),
                          pd.read_csv(API, dtype={"symbol6": str}))
    summary = run(rows, OUT)
    summary["sources_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (PREPARED, API, Path(__file__))}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

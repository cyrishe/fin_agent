"""Independent 14:45 seven-factor, exact next-09:40 rolling experiment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_st_status import load_st_intervals
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection
from scripts.experiment_automl_mk_1m_st_policies import POLICIES, make_cohorts
from scripts.experiment_automl_second_high_four_class import four_class, models
from scripts.run_automl_four_class_pipeline import select_top


EARLY = Path("outputs/stock_automl/mk_1m_1445_exact0940/trainable_candidates.csv.gz")
LATE = Path("outputs/stock_automl/db_1m_1445_exact0940/trainable_candidates.csv.gz")
OUT = Path("docs/stock_automl_runs/20261012_1445_exact0940")


def cohort(source: Path) -> pd.DataFrame:
    frame = pd.read_csv(source, dtype={"signal_date": str, "next_date": str,
                                        "symbol6": str}, low_memory=False)
    frame = frame.drop(columns=["preclose"], errors="ignore")
    with db_connection(Path(".env")) as db:
        intervals = load_st_intervals(db)
    frame = make_cohorts(frame, intervals)[0][POLICIES[1]]
    if (frame.duplicated(["signal_date", "symbol6"]).any() or
            frame[list(FEATURES)].isna().any().any()):
        raise ValueError("Incomplete 14:45 cohort")
    return frame


def replay(rows: pd.DataFrame, calendar: list[str], segment: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    picks, folds = [], []
    labeled_days = set(rows.loc[rows.second_high.notna(), "signal_date"])
    for index in range(20, len(calendar)):
        day = calendar[index]
        if segment == "jan_jul" and day == "2026-07-31":
            continue
        previous = calendar[index-20:index]
        if not set(previous).issubset(labeled_days):
            continue
        train = rows[rows.signal_date.isin(previous) & rows.second_high.notna() &
                     rows.next_date.le(day)]
        current = rows[rows.signal_date.eq(day)]
        if current.empty:
            continue
        if train.signal_date.nunique() != 20:
            raise ValueError(f"Immature 14:45 labels on {day}")
        labels = four_class(train.second_high / train.signal_price - 1).astype(str)
        model = models()["small_boosted_tree"]
        model.fit(train[list(FEATURES)], labels)
        probabilities = model.predict_proba(current[list(FEATURES)])
        scored = current[["signal_date", "next_date", "symbol6", "name", "signal_price",
                          "signal_return", *FEATURES[1:]]].copy()
        for label in ("lt0", "0to1", "1to3", "ge3"):
            scored[f"p_{label}"] = probabilities[:, list(model.classes_).index(label)]
        scored["predicted_class"] = np.asarray(model.classes_)[probabilities.argmax(axis=1)]
        top = select_top(scored)
        top = top[top.selection_rank.eq(1)].copy()
        top = top.merge(current[["signal_date", "symbol6", "second_high", "close_40"]],
                        on=["signal_date", "symbol6"], validate="one_to_one")
        top["actual_second_high_return"] = top.second_high / top.signal_price - 1
        top["actual_0940_return"] = top.close_40 / top.signal_price - 1
        top["actual_class"] = np.where(top.second_high.notna(),
                                        four_class(top.actual_second_high_return).astype(str),
                                        "UNKNOWN")
        top["segment"] = segment
        picks.append(top)
        folds.append({"segment": segment, "signal_date": day,
                      "train_start": previous[0], "train_end": previous[-1],
                      "train_rows": len(train), "candidates": len(current),
                      "top1_count": len(top)})
        print(segment, day, "train", len(train), "candidates", len(current),
              "picks", len(top), flush=True)
    return pd.concat(picks, ignore_index=True), pd.DataFrame(folds)


def run(source: Path, segment: str, output: Path = OUT) -> None:
    rows = cohort(source)
    calendar = sorted(rows.signal_date.unique())
    if segment == "aug_oct":
        from scripts.experiment_automl_intraday_vwap import LATE_MARKET
        calendar = pd.read_csv(LATE_MARKET).trade_date.astype(str).tolist()
    picks, folds = replay(rows, calendar, segment)
    output.mkdir(parents=True, exist_ok=True)
    picks.to_csv(output / f"top1_{segment}.csv", index=False)
    folds.to_csv(output / f"folds_{segment}.csv", index=False)
    values = picks.actual_0940_return.dropna()
    result = {"segment": segment, "candidate_rows": len(rows),
              "signal_days": int(rows.signal_date.nunique()),
              "calendar_days": len(calendar), "prediction_days": len(folds),
              "top1_picks": len(picks), "observed_top1": len(values),
              "target_ge3": int(picks.actual_second_high_return.ge(.03).sum()),
              "target_lt0": int(picks.actual_second_high_return.lt(0).sum()),
              "sale_ge3": int(values.ge(.03).sum()),
              "sale_lt0": int(values.lt(0).sum()),
              "mean_0940_pct": float(values.mean()*100)}
    (output / f"summary_{segment}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("segment", choices=("jan_jul", "aug_oct"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    run(args.source or (EARLY if args.segment == "jan_jul" else LATE),
        args.segment, args.output)

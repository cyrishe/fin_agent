"""One 20-day full-cohort model per day for the tradable-ST sensitivity study."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_automl_four_class_subset_selection import fit_tree
from scripts.experiment_automl_mk_1m_rolling import arm_summary, evaluate
from scripts.experiment_automl_second_high_four_class import four_class
from scripts.run_automl_four_class_pipeline import select_training_samples


ARM = "rolling20_tminus1"


def one_fold(rows: pd.DataFrame, dates: list[str], index: int,
             checkpoint: Path) -> None:
    day = dates[index]
    training_dates = dates[index-20:index]
    training = select_training_samples(rows, training_dates)
    if training.signal_date.nunique() != 20 or not training.next_date.le(day).all():
        raise ValueError(f"Incomplete or immature 20-day training window: {day}")
    model = fit_tree(training, day)
    scores, picks = evaluate(model, rows, day, ARM)
    scores.to_csv(checkpoint / f"{day}_scores.csv.gz", index=False, compression="gzip")
    picks.to_csv(checkpoint / f"{day}_picks.csv", index=False)
    (checkpoint / f"{day}_fold.json").write_text(json.dumps({
        "test_date": day, "training_dates": training_dates,
        "training_rows": len(training), "inference_candidates": len(scores),
    }, ensure_ascii=False, indent=2) + "\n")


def aggregate(rows: pd.DataFrame, dates: list[str], checkpoint: Path,
              output: Path, source: Path) -> dict:
    test_days = dates[20:-1]
    folds = [json.loads((checkpoint / f"{day}_fold.json").read_text())
             for day in test_days]
    picks = pd.concat([pd.read_csv(checkpoint / f"{day}_picks.csv",
                                   dtype={"symbol6": str}) for day in test_days],
                      ignore_index=True)
    picks["actual_0940_class"] = np.where(
        picks.actual_0940_return.notna(),
        four_class(picks.actual_0940_return).astype(str), "UNKNOWN")
    top1 = picks[picks.selection_rank.eq(1)].copy()
    output.mkdir(parents=True, exist_ok=True)
    picks.to_csv(output / "top2_verified.csv", index=False)
    top1.to_csv(output / "top1_verified.csv", index=False)
    (output / "folds.json").write_text(json.dumps(
        folds, ensure_ascii=False, indent=2) + "\n")
    summary = {
        "source_rows": len(rows), "signal_dates": len(dates),
        "test_days": len(test_days), "test_first": test_days[0],
        "test_last": test_days[-1], "arm": ARM,
        "result": arm_summary(top1, len(test_days)),
        "data_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (output / "summary.json").write_text(json.dumps(
        summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--calendar-archive", type=Path, action="append", required=True)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--worker-index", type=int, default=0)
    p.add_argument("--max-folds", type=int)
    p.add_argument("--aggregate-only", action="store_true")
    a = p.parse_args()
    rows = pd.read_csv(a.data, dtype={"symbol6": str, "signal_date": str,
                                      "next_date": str}, low_memory=False)
    dates = sorted(path.stem for root in a.calendar_archive
                   for path in root.glob("2026-*.csv"))
    if len(dates) < 21 or len(dates) != len(set(dates)):
        raise ValueError("Trading calendar is incomplete or duplicated")
    if a.workers < 1 or not 0 <= a.worker_index < a.workers:
        raise ValueError("Invalid worker shard")
    a.checkpoint.mkdir(parents=True, exist_ok=True)
    test_days = dates[20:-1]
    if a.max_folds is not None:
        test_days = test_days[:a.max_folds]
    if not a.aggregate_only:
        for index, day in enumerate(test_days, start=20):
            if (index-20) % a.workers != a.worker_index:
                continue
            paths = [a.checkpoint / f"{day}_{suffix}" for suffix in
                     ("fold.json", "picks.csv", "scores.csv.gz")]
            if not all(path.exists() for path in paths):
                one_fold(rows, dates, index, a.checkpoint)
            print(f"completed {day} ({index-19}/{len(dates)-21})", flush=True)
    if a.max_folds is None and (a.aggregate_only or a.workers == 1):
        print(json.dumps(aggregate(rows, dates, a.checkpoint, a.output, a.data),
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

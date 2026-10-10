"""Replay the frozen 20d, full19d, and T-1 P3-selected 19d models on raw 1m."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze_automl_0940_random_lift import poisson_binomial_pmf
from scripts.experiment_automl_four_class_subset_selection import (
    fit_tree, random_training_subset,
)
from scripts.experiment_automl_second_high_four_class import four_class
from scripts.run_automl_four_class_pipeline import (
    infer, select_inference_candidates, select_top, select_training_samples,
    verify_next_day,
)


DATA = Path("outputs/stock_automl/mk_1m_study/trainable_candidates.csv.gz")
CHECKPOINT = Path("outputs/stock_automl/mk_1m_study/rolling_checkpoints")
OUT = Path("docs/stock_automl_runs/20261010_mk_1m_rolling_backtest")
ARMS = ("rolling20_tminus1", "full19_tminus2", "p3_selected_tminus2")
CLASSES = ("ge3", "1to3", "0to1", "lt0")


def top1_p3(model, rows: pd.DataFrame, day: str) -> float:
    scored = infer(model, select_inference_candidates(rows, day))
    picks = select_top(scored)
    first = picks[picks.selection_rank.eq(1)]
    return float(first.p_ge3.iloc[0]) if len(first) else float("nan")


def evaluate(model, rows: pd.DataFrame, day: str, arm: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    scores = infer(model, select_inference_candidates(rows, day))
    scores["arm"] = arm
    picks = verify_next_day(select_top(scores), rows)
    picks["arm"] = arm
    return scores, picks


def one_fold(rows: pd.DataFrame, dates: list[str], index: int,
             checkpoint: Path) -> None:
    day, tminus1 = dates[index], dates[index - 1]
    train20_dates = dates[index - 20:index]
    train19_dates = dates[index - 20:index - 1]
    train20 = select_training_samples(rows, train20_dates)
    train19 = select_training_samples(rows, train19_dates)
    if train20.signal_date.nunique() != 20 or train19.signal_date.nunique() != 19:
        raise ValueError(f"Not all historical dates have labeled candidates: {day}")
    if not train20.next_date.le(day).all() or not train19.next_date.le(tminus1).all():
        raise ValueError(f"Training target not mature at prediction time: {day}")
    model20 = fit_tree(train20, day)
    model19 = fit_tree(train19, tminus1)
    best_key, best_model, best_details = None, None, None
    for fraction in (.8, .9):
        for repeat in range(1, 11):
            seed = 20261010 + index * 1000 + int(fraction * 100) * 10 + repeat
            sample = random_training_subset(train19, fraction, seed)
            model = fit_tree(sample, tminus1)
            p3 = top1_p3(model, rows, tminus1)
            if np.isnan(p3):
                continue
            key = (p3, fraction, -repeat)
            if best_key is None or key > best_key:
                best_key, best_model = key, model
                best_details = {"fraction": fraction, "repeat": repeat,
                                "seed": seed, "selection_p3": p3,
                                "training_rows": len(sample)}
    if best_model is None:
        raise ValueError(f"No T-1 Top1 with P3 among the 20 subset models: {day}")
    scores, picks = [], []
    for arm, model in zip(ARMS, (model20, model19, best_model)):
        a, b = evaluate(model, rows, day, arm)
        scores.append(a)
        picks.append(b)
    score_file = checkpoint / f"{day}_scores.csv.gz"
    pick_file = checkpoint / f"{day}_picks.csv"
    meta_file = checkpoint / f"{day}_fold.json"
    pd.concat(scores, ignore_index=True).to_csv(score_file, index=False, compression="gzip")
    pd.concat(picks, ignore_index=True).to_csv(pick_file, index=False)
    meta = {"test_date": day, "tminus1_date": tminus1,
            "train20_dates": train20_dates, "train19_dates": train19_dates,
            "train20_rows": len(train20), "train19_rows": len(train19),
            "candidates": len(select_inference_candidates(rows, day)),
            "p3_selection": best_details}
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")


def arm_summary(top1: pd.DataFrame, days: int) -> dict:
    known = top1[top1.actual_0940_return.notna()]
    return {"test_days": days, "selected_days": len(top1),
            "known_outcome_days": len(known), "unknown_outcome_days": len(top1)-len(known),
            "second_high_counts": {c: int(top1.actual_class.eq(c).sum()) for c in CLASSES},
            "sale_0940_counts": {c: int(top1.actual_0940_class.eq(c).sum()) for c in CLASSES},
            "mean_0940_return_pct_per_trade": float(known.actual_0940_return.mean()*100)
            if len(known) else None,
            "median_0940_return_pct_per_trade": float(known.actual_0940_return.median()*100)
            if len(known) else None}


def aggregate(rows: pd.DataFrame, dates: list[str], checkpoint: Path,
              output: Path) -> dict:
    test_days = dates[20:-1]  # July 31 has no Aug 3 morning label.
    folds = [json.loads((checkpoint / f"{day}_fold.json").read_text()) for day in test_days]
    picks = pd.concat([pd.read_csv(checkpoint / f"{day}_picks.csv",
                                   dtype={"symbol6": str}) for day in test_days], ignore_index=True)
    picks["actual_0940_class"] = np.where(
        picks.actual_0940_return.notna(),
        four_class(picks.actual_0940_return).astype(str), "UNKNOWN")
    top1 = picks[picks.selection_rank.eq(1)].copy()
    output.mkdir(parents=True, exist_ok=True)
    picks.to_csv(output / "top2_verified.csv", index=False)
    top1.to_csv(output / "top1_verified.csv", index=False)
    pd.DataFrame(folds).to_json(output / "folds.json", orient="records", indent=2,
                                force_ascii=False)
    pool = rows[rows.signal_date.isin(test_days)].copy()
    pool["sale_class"] = np.where(pool.close_40.notna(),
                                  four_class(pool.close_40 / pool.signal_price - 1).astype(str),
                                  "UNKNOWN")
    daily = pool.groupby("signal_date").agg(
        candidates=("symbol6", "size"),
        known=("close_40", "count"),
        ge3=("sale_class", lambda s: int(s.eq("ge3").sum())),
        ge1=("sale_class", lambda s: int(s.isin(("ge3", "1to3")).sum())),
        lt0=("sale_class", lambda s: int(s.eq("lt0").sum()))).reset_index()
    # Estimate the daily random-draw rate only among candidates with a
    # measurable next-morning close; missing outcomes are reported separately.
    daily["unknown"] = daily.candidates - daily.known
    daily["random_p_ge3"] = daily.ge3 / daily.known
    daily.to_csv(output / "daily_candidate_0940.csv", index=False)
    summaries = {arm: arm_summary(top1[top1.arm.eq(arm)], len(test_days)) for arm in ARMS}
    pmf = poisson_binomial_pmf(daily.random_p_ge3.tolist())
    for arm in ARMS:
        hits = summaries[arm]["sale_0940_counts"]["ge3"]
        summaries[arm]["random_expected_ge3_days"] = float(daily.random_p_ge3.sum())
        summaries[arm]["random_p_at_least_ge3_hits"] = float(sum(pmf[hits:]))
    summary = {"source_rows": len(rows), "signal_dates": len(dates),
               "test_first": test_days[0], "test_last": test_days[-1],
               "test_days": len(test_days), "arms": summaries,
               "data_sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
               "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--max-folds", type=int)
    args = parser.parse_args()
    rows = pd.read_csv(args.data, dtype={"symbol6": str, "signal_date": str,
                                         "next_date": str}, low_memory=False)
    dates = sorted(path.stem for path in
                   Path("outputs/stock_automl/mk_archive/mk/none").glob("2026-*.csv"))
    if len(dates) != 62 or len(rows) < 1000:
        raise ValueError("Source cohort or trading calendar is incomplete")
    args.checkpoint.mkdir(parents=True, exist_ok=True)
    test_days = dates[20:-1]
    if args.max_folds is not None:
        test_days = test_days[:args.max_folds]
    for index, day in enumerate(test_days, start=20):
        meta = args.checkpoint / f"{day}_fold.json"
        pick = args.checkpoint / f"{day}_picks.csv"
        score = args.checkpoint / f"{day}_scores.csv.gz"
        if not (meta.exists() and pick.exists() and score.exists()):
            one_fold(rows, dates, index, args.checkpoint)
        print(f"completed {day} ({index-19}/{len(dates)-21})", flush=True)
    if args.max_folds is None:
        print(json.dumps(aggregate(rows, dates, args.checkpoint, args.output),
                         ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

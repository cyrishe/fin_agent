"""Keep ST in 20-day training, then rerank all scored non-ST candidates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_st_status import load_st_intervals
from scripts.benchmark_automl_1440_inference import db_connection
from scripts.experiment_automl_four_class_subset_selection import fit_tree
from scripts.experiment_automl_mk_1m_st_policies import (
    OUT, POLICIES, SOURCE, make_cohorts, settle_suspensions,
)
from scripts.run_automl_four_class_pipeline import (
    infer, select_inference_candidates, select_top, select_training_samples,
    verify_next_day,
)


RERANK_OUT = OUT / "rerank_nonst"


def select_next_nonst(scored: pd.DataFrame) -> pd.DataFrame:
    """Apply the original selection rule to every scored, non-ST candidate."""
    if scored.historical_st.isna().any():
        raise ValueError("A scored candidate has no as-of ST state")
    return select_top(scored.loc[~scored.historical_st])


def rerank_day(rows: pd.DataFrame, dates: list[str], index: int,
               original: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    day = dates[index]
    training = select_training_samples(rows, dates[index-20:index])
    if training.signal_date.nunique() != 20 or not training.next_date.le(day).all():
        raise ValueError(f"Incomplete or immature training window: {day}")
    model = fit_tree(training, day)
    scored = infer(model, select_inference_candidates(rows, day))
    original_top = select_top(scored)
    expected = original[original.signal_date.eq(day)].sort_values("selection_rank")
    if (original_top.symbol6.tolist() != expected.symbol6.tolist() or
            original_top.selection_rank.tolist() != expected.selection_rank.tolist()):
        raise ValueError(f"Original Top2 changed on {day}; cannot compare rerank")
    for column in ("p_ge3", "p_1to3", "p_0to1", "p_lt0"):
        if not np.allclose(original_top[column], expected[column], atol=1e-10):
            raise ValueError(f"Original {column} changed on {day}")

    flags = rows.loc[rows.signal_date.eq(day),
                     ["signal_date", "symbol6", "historical_st", "entry_at_limit"]]
    scored = scored.merge(flags, on=["signal_date", "symbol6"],
                          validate="one_to_one")
    selected = select_next_nonst(scored)
    selected = verify_next_day(selected, rows)
    return selected, {"day": day, "training_rows": len(training),
                      "all_candidates": len(scored),
                      "nonst_candidates": int((~scored.historical_st).sum()),
                      "picks": len(selected)}


def run(source: Path, output: Path, env_file: Path) -> dict:
    rows = pd.read_csv(source, dtype={"symbol6": str, "signal_date": str,
                                        "next_date": str}, low_memory=False)
    dates = sorted(rows.signal_date.unique())
    if len(dates) != 139:
        raise ValueError("Expected 139 source signal dates")
    with db_connection(env_file) as db:
        intervals = load_st_intervals(db)
    cohort = make_cohorts(rows, intervals)[0][POLICIES[2]]
    original = pd.read_csv(OUT / "top2_verified.csv",
                           dtype={"symbol6": str, "signal_date": str})
    original = original[original.policy.eq(POLICIES[2])]
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = output / "checkpoints"
    checkpoint.mkdir(exist_ok=True)
    picked, folds = [], []
    for index, day in enumerate(dates[20:-1], start=20):
        pick_file = checkpoint / f"{day}_picks.csv"
        fold_file = checkpoint / f"{day}_fold.json"
        if pick_file.exists() and fold_file.exists():
            selected = pd.read_csv(pick_file, dtype={"symbol6": str})
            fold = json.loads(fold_file.read_text())
        else:
            selected, fold = rerank_day(cohort, dates, index, original)
            selected.to_csv(pick_file, index=False)
            fold_file.write_text(json.dumps(fold, ensure_ascii=False) + "\n")
        picked.append(selected)
        folds.append(fold)
        print(f"{day}: {fold['training_rows']} train, "
              f"{fold['nonst_candidates']} non-ST, {fold['picks']} picks", flush=True)
    picks = pd.concat(picked, ignore_index=True)
    picks = settle_suspensions(picks, env_file)
    if picks.historical_st.any() or picks.duplicated(
            ["signal_date", "selection_rank"]).any():
        raise ValueError("Reranked picks include ST or duplicate ranks")
    picks.to_csv(output / "top2_verified.csv", index=False)
    pd.DataFrame(folds).to_csv(output / "folds.csv", index=False)
    summary = {"days": len(folds), "top1_days": int(picks.selection_rank.eq(1).sum()),
               "top2_days": int(picks.selection_rank.eq(2).sum()),
               "top1_mean_return_pct": float(picks.loc[picks.selection_rank.eq(1),
                                                     "actual_0940_return"].mean() * 100),
               "top2_mean_return_pct": float(picks.loc[picks.selection_rank.eq(2),
                                                     "actual_0940_return"].mean() * 100),
               "unknown_picks": int(picks.actual_0940_return.isna().sum())}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                    indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=RERANK_OUT)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output, args.env_file),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

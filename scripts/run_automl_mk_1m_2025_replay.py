"""Replay the existing two ST policies over a continuous 2025 six-month window."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.automl_st_status import load_st_intervals
from scripts.benchmark_automl_1440_inference import db_connection
from scripts.experiment_automl_mk_1m_st_policies import (
    POLICIES, compact_tables, make_cohorts, one_fold, settle_suspensions,
)
from scripts.mk_minute_archive import day_files, trading_day


BASE = Path("outputs/stock_automl/mk_1m_2025_mar_aug")
POLICY_NAMES = (POLICIES[1],)


def run(source: Path, calendar_root: Path, output: Path, env_file: Path,
        first_test: str = "2025-03-05", last_test: str = "2025-08-29",
        policies: tuple[str, ...] = POLICY_NAMES) -> dict:
    rows = pd.read_csv(source, dtype={"symbol6": str, "signal_date": str,
                                          "next_date": str}, low_memory=False)
    dates = [trading_day(path) for path in day_files((calendar_root,))]
    if len(dates) < 22:
        raise ValueError("Fewer than 20 training days plus test and label days")
    if sorted(rows.signal_date.unique()) != dates:
        raise ValueError("Candidate dates do not match the minute calendar")
    test_days = dates[20:-1]
    if test_days[0] != first_test or test_days[-1] != last_test:
        raise ValueError("Unexpected out-of-sample test window")
    with db_connection(env_file) as db:
        intervals = load_st_intervals(db)
    cohorts, counts = make_cohorts(rows, intervals)
    checkpoint = output / "checkpoints"
    checkpoint.mkdir(parents=True, exist_ok=True)
    fold_rows, pick_rows = [], []
    for policy in policies:
        cohort = cohorts[policy]
        for index, day in enumerate(test_days, start=20):
            stem = f"{POLICIES.index(policy) + 1}_{day}"
            pick_file = checkpoint / f"{stem}_picks.csv"
            fold_file = checkpoint / f"{stem}_fold.json"
            if pick_file.exists() and fold_file.exists():
                selected = pd.read_csv(pick_file, dtype={"symbol6": str})
                fold = json.loads(fold_file.read_text())
            else:
                selected, fold = one_fold(cohort, dates, index)
                selected.to_csv(pick_file, index=False)
                fold_file.write_text(json.dumps(fold, ensure_ascii=False) + "\n")
            selected["policy"] = policy
            fold["policy"] = policy
            pick_rows.append(selected)
            fold_rows.append(fold)
            print(f"{policy} {day}: {fold['training_rows']} train, "
                  f"{fold['candidates']} candidates, {fold['picks']} picks", flush=True)
    picks = pd.concat(pick_rows, ignore_index=True)
    flags = pd.concat([
        cohorts[name][["signal_date", "symbol6", "historical_st",
                       "entry_at_limit"]].assign(policy=name)
        for name in policies], ignore_index=True)
    picks = picks.merge(flags, on=["policy", "signal_date", "symbol6"],
                        how="left", validate="many_to_one")
    if picks[["historical_st", "entry_at_limit"]].isna().any().any():
        raise ValueError("A selected stock has no as-of ST or entry status")
    picks = settle_suspensions(picks, env_file)
    monthly, daily = compact_tables(picks, test_days)
    monthly = monthly[monthly["方案"].isin(policies)].copy()
    daily = daily[daily["方案"].isin(policies)].copy()
    output.mkdir(parents=True, exist_ok=True)
    picks.to_csv(output / "top2_verified.csv", index=False)
    pd.DataFrame(fold_rows).to_csv(output / "folds.csv", index=False)
    monthly.to_csv(output / "monthly.csv", index=False)
    daily.to_csv(output / "daily.csv", index=False)
    summary = {
        "test_first": test_days[0], "test_last": test_days[-1],
        "test_days": len(test_days), "source_rows": len(rows),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "policy_counts": {name: counts[name] for name in policies},
        "per_policy": {name: {
            "top1_days": int((picks.policy.eq(name) & picks.selection_rank.eq(1)).sum()),
            "top2_days": int((picks.policy.eq(name) & picks.selection_rank.eq(2)).sum()),
            "suspension_zero_picks": int((picks.policy.eq(name) & picks.suspension_zero).sum()),
            "unknown_picks": int((picks.policy.eq(name) &
                                  picks.actual_0940_return.isna()).sum()),
            "mean_training_rows": float(pd.DataFrame(fold_rows).loc[
                pd.DataFrame(fold_rows).policy.eq(name), "training_rows"].mean()),
        } for name in policies},
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                   indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=BASE / "cohort/trainable_candidates.csv.gz")
    parser.add_argument("--calendar-root", type=Path, default=BASE / "source_days")
    parser.add_argument("--output", type=Path, default=BASE / "backtest")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--first-test", default="2025-03-05")
    parser.add_argument("--last-test", default="2025-08-29")
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.calendar_root, args.output, args.env_file,
                         args.first_test, args.last_test),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

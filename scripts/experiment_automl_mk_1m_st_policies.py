"""Compare three as-of ST cohorts with the same recent-20 four-class model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.audit_automl_mk_1m_entry_limit import mark_entry_limit
from scripts.automl_st_status import load_st_intervals
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_automl_four_class_subset_selection import fit_tree
from scripts.prepare_automl_mk_1m_nonst import REFERENCE, annotate
from scripts.run_automl_four_class_pipeline import (
    infer, select_inference_candidates, select_top, select_training_samples,
    verify_next_day,
)


SOURCE = Path("outputs/stock_automl/mk_1m_jan_jul/trainable_candidates.csv.gz")
OUT = Path("outputs/stock_automl/mk_1m_three_st_policies")
POLICIES = ("去掉全部ST", "只去掉14:40涨停ST", "保留涨停ST并控制比例")
LABELS = {"ge3": "达标", "1to3": "可接受", "0to1": "中性", "lt0": "错误"}
KEEP_NONLIMIT_ST_FRACTION = .22


def retained_by_hash(day: str, symbol: str) -> bool:
    """Fixed stock-day rule, independent of later prices and outcomes."""
    digest = hashlib.blake2b(f"{day}:{symbol}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64 < KEEP_NONLIMIT_ST_FRACTION


def make_cohorts(rows: pd.DataFrame, intervals: pd.DataFrame) -> tuple[dict, dict]:
    marked = mark_entry_limit(annotate(rows, intervals).rename(
        columns={"t_reference_preclose": "preclose"}))
    if not marked.historical_status_known.all() or marked.duplicated(
            ["signal_date", "symbol6"]).any():
        raise ValueError("Historical ST state or candidate key is incomplete")
    st = marked.historical_st
    limit = st & marked.entry_at_limit
    sampled = pd.Series([
        retained_by_hash(day, symbol) for day, symbol in zip(
            marked.signal_date, marked.symbol6)], index=marked.index)
    masks = {
        POLICIES[0]: ~st,
        POLICIES[1]: ~limit,
        POLICIES[2]: ~st | limit | sampled,
    }
    cohorts = {name: marked.loc[mask].copy() for name, mask in masks.items()}
    counts = {name: {"rows": len(frame),
                     "st_rows": int(frame.historical_st.sum()),
                     "st_share": float(frame.historical_st.mean()),
                     "st_at_limit_rows": int((frame.historical_st &
                                              frame.entry_at_limit).sum())}
              for name, frame in cohorts.items()}
    return cohorts, counts


def one_fold(rows: pd.DataFrame, dates: list[str], index: int) -> tuple[pd.DataFrame, dict]:
    day = dates[index]
    training_dates = dates[index-20:index]
    training = select_training_samples(rows, training_dates)
    if (training.signal_date.nunique() != 20 or
            not training.next_date.le(day).all()):
        raise ValueError(f"Immature or incomplete training window: {day}")
    model = fit_tree(training, day)
    scored = infer(model, select_inference_candidates(rows, day))
    selected = verify_next_day(select_top(scored), rows)
    return selected, {"day": day, "training_rows": len(training),
                      "candidates": len(scored), "picks": len(selected)}


def settle_suspensions(picks: pd.DataFrame, env_file: Path) -> pd.DataFrame:
    result = picks.copy()
    result["suspension_zero"] = False
    unknown = result.actual_0940_return.isna()
    if unknown.any():
        missing = result.loc[unknown, ["next_date", "symbol6"]].drop_duplicates()
        with db_connection(env_file) as db:
            daily_parts = []
            for day, group in missing.groupby("next_date"):
                symbols = group.symbol6.tolist()
                placeholders = ",".join(["%s"] * len(symbols))
                daily_parts.append(query(
                    db, "SELECT trade_date AS next_date, "
                    "LEFT(stk_code,6) AS symbol6, volume "
                    "FROM kcrp_stock_price WHERE trade_date=%s "
                    f"AND LEFT(stk_code,6) IN ({placeholders})",
                    (day, *symbols)))
        daily = pd.concat(daily_parts, ignore_index=True).reindex(
            columns=["next_date", "symbol6", "volume"])
        if not daily.empty:
            daily["next_date"] = pd.to_datetime(daily.next_date).dt.strftime("%Y-%m-%d")
            daily["volume"] = pd.to_numeric(daily.volume)
        result = result.merge(daily, on=["next_date", "symbol6"], how="left",
                              validate="many_to_one")
        stopped = result.actual_0940_return.isna() & result.volume.eq(0)
        result.loc[stopped, "actual_0940_return"] = 0.0
        result.loc[stopped, "suspension_zero"] = True
        result = result.drop(columns="volume")
    return result


def compact_tables(picks: pd.DataFrame, dates: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    records = []
    for policy in POLICIES:
        group = picks[picks.policy.eq(policy)]
        for day in dates:
            on_day = group[group.signal_date.eq(day)].set_index("selection_rank")
            row = {"选股日": day, "方案": policy}
            for rank in (1, 2):
                if rank not in on_day.index:
                    label, ret = "—", "—"
                else:
                    pick = on_day.loc[rank]
                    if pick.suspension_zero:
                        label = "中性"
                    else:
                        label = LABELS.get(pick.actual_class, "缺数据")
                    ret = (f"{pick.actual_0940_return * 100:+.2f}%"
                           if pd.notna(pick.actual_0940_return) else "—")
                row[f"Top{rank}结果"] = label
                row[f"Top{rank} 09:40收益"] = ret
            records.append(row)
    daily = pd.DataFrame(records)
    daily["方案"] = pd.Categorical(daily["方案"], categories=POLICIES,
                                  ordered=True)
    daily = daily.sort_values(["选股日", "方案"]).reset_index(drop=True)
    monthly_rows = []
    for (month, policy), group in daily.groupby([daily["选股日"].str[:7], "方案"],
                                                sort=True, observed=True):
        row = {"月份": month, "方案": policy}
        for rank in (1, 2):
            labels = group[f"Top{rank}结果"]
            row[f"Top{rank}结果"] = "/".join(
                f"{label}{int(labels.eq(label).sum())}" for label in LABELS.values())
            returns = picks.loc[
                picks.policy.eq(policy) & picks.signal_date.str[:7].eq(month)
                & picks.selection_rank.eq(rank), "actual_0940_return"]
            row[f"Top{rank} 09:40收益"] = (f"{returns.mean() * 100:+.2f}%"
                                             if returns.notna().any() else "—")
        monthly_rows.append(row)
    monthly = pd.DataFrame(monthly_rows)
    monthly["方案"] = pd.Categorical(monthly["方案"], categories=POLICIES,
                                    ordered=True)
    monthly = monthly.sort_values(["月份", "方案"]).reset_index(drop=True)
    return monthly, daily


def run(source: Path, output: Path, env_file: Path,
        policy_index: int | None = None, checkpoint_only: bool = False) -> dict:
    rows = pd.read_csv(source, dtype={"symbol6": str, "signal_date": str,
                                          "next_date": str}, low_memory=False)
    dates = sorted(rows.signal_date.unique())
    if len(dates) != 139 or len(rows) != 59959:
        raise ValueError("Expected original 139-day candidate cohort")
    with db_connection(env_file) as db:
        intervals = load_st_intervals(db)
    cohorts, counts = make_cohorts(rows, intervals)
    reference = pd.read_csv(REFERENCE, dtype={"symbol6": str}, low_memory=False)
    reference["preclose"] = reference.signal_price / (1 + reference.signal_return)
    reference = mark_entry_limit(annotate(reference, intervals))
    reference_st = reference.historical_st
    reference_counts = {
        "rows": len(reference), "st_rows": int(reference_st.sum()),
        "st_share": float(reference_st.mean()),
        "st_at_limit_rows": int((reference_st & reference.entry_at_limit).sum()),
    }
    test_dates = dates[20:-1]  # T+1 source ends at 07-31.
    checkpoint = output / "checkpoints"
    checkpoint.mkdir(parents=True, exist_ok=True)
    fold_rows, pick_rows = [], []
    selected_policies = POLICIES if policy_index is None else (POLICIES[policy_index-1],)
    for policy in selected_policies:
        cohort = cohorts[policy]
        for index, day in enumerate(test_dates, start=20):
            stem = f"{POLICIES.index(policy)+1}_{day}"
            pick_file, fold_file = checkpoint / f"{stem}_picks.csv", checkpoint / f"{stem}_fold.json"
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
    if checkpoint_only:
        return {"completed_folds": len(fold_rows), "policies": selected_policies}
    if policy_index is not None:
        raise ValueError("Final tables require all three policies")
    picks = pd.concat(pick_rows, ignore_index=True)
    pick_flags = pd.concat([
        cohorts[name][["signal_date", "symbol6", "historical_st",
                       "entry_at_limit"]].assign(policy=name)
        for name in POLICIES], ignore_index=True)
    picks = picks.merge(pick_flags, on=["policy", "signal_date", "symbol6"],
                        how="left", validate="many_to_one")
    if picks.historical_st.isna().any() or picks.entry_at_limit.isna().any():
        raise ValueError("A selected stock has no as-of ST or entry status")
    picks = settle_suspensions(picks, env_file)
    folds = pd.DataFrame(fold_rows)
    monthly, daily = compact_tables(picks, test_dates)
    picks.to_csv(output / "top2_verified.csv", index=False)
    folds.to_csv(output / "folds.csv", index=False)
    monthly.to_csv(output / "monthly.csv", index=False)
    daily.to_csv(output / "daily.csv", index=False)
    summary = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "test_days": len(test_dates), "policy_counts": counts,
               "aug_sep_reference_counts": reference_counts,
               "per_policy": {name: {
                   "top1_days": int(((picks.policy.eq(name)) &
                                      picks.selection_rank.eq(1)).sum()),
                   "top2_days": int(((picks.policy.eq(name)) &
                                      picks.selection_rank.eq(2)).sum()),
                   "suspension_zero_picks": int((picks.policy.eq(name) &
                                                 picks.suspension_zero).sum()),
                   "unknown_picks": int((picks.policy.eq(name) &
                                         picks.actual_0940_return.isna()).sum()),
                   "st_top1_picks": int((picks.policy.eq(name) &
                                         picks.selection_rank.eq(1) &
                                         picks.historical_st).sum()),
                   "limit_st_top1_picks": int((picks.policy.eq(name) &
                                               picks.selection_rank.eq(1) &
                                               picks.historical_st &
                                               picks.entry_at_limit).sum()),
                   "mean_training_rows": float(folds.loc[folds.policy.eq(name),
                                                     "training_rows"].mean())}
                   for name in POLICIES}}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                    indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--policy-index", type=int, choices=(1, 2, 3))
    parser.add_argument("--checkpoint-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output, args.env_file,
                         args.policy_index, args.checkpoint_only),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

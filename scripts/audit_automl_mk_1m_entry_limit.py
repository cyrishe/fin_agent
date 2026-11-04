"""Rerank saved 14:40 scores after excluding candidates already at their price cap."""
from __future__ import annotations

import argparse
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_st_status import attach_st_status, load_st_intervals
from scripts.analyze_automl_0940_random_lift import poisson_binomial_pmf
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.run_automl_four_class_pipeline import select_top, verify_next_day


CHANGE_DATE = "2026-07-06"


def mark_entry_limit(rows: pd.DataFrame) -> pd.DataFrame:
    result = rows.copy()
    is_growth = result.symbol6.str.startswith(("300", "301", "688"))
    is_main_st = result.st_type.isin(("S", "Y")) & ~is_growth
    old_main_st = is_main_st & result.signal_date.lt(CHANGE_DATE)
    result["limit_pct"] = np.where(is_growth, .20, np.where(old_main_st, .05, .10))
    result["limit_price"] = [
        float((Decimal(str(price)) * Decimal(str(1 + pct))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP))
        if pd.notna(price) else np.nan
        for price, pct in zip(result.preclose, result.limit_pct)
    ]
    result["entry_at_limit"] = np.isclose(
        result.signal_price, result.limit_price, rtol=0, atol=.001)
    result["entry_status_known"] = (
        result.preclose.notna() & result.st_type.notna()
        & (result.ann_date.le(pd.to_datetime(result.signal_date))
           | (result.st_type.isin(("N", "R")) & result.ann_date.isna())))
    result["entry_eligible"] = result.entry_status_known & ~result.entry_at_limit
    return result


def run(checkpoint: Path, candidates_path: Path, output: Path,
        allow_partial: bool = False) -> dict:
    score_files = sorted(checkpoint.glob("*_scores.csv.gz"))
    if not score_files:
        raise ValueError("No saved fold scores")
    if not allow_partial and len(score_files) != 118:
        raise ValueError(f"Expected 118 complete fold score files, got {len(score_files)}")
    scores = pd.concat([pd.read_csv(path, dtype={"symbol6": str,
                                                "signal_date": str,
                                                "next_date": str})
                        for path in score_files], ignore_index=True)
    unique = scores[["signal_date", "symbol6", "signal_price"]].drop_duplicates()
    if unique.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Arms disagree on the saved 14:40 candidate price")
    with db_connection(Path(".env")) as db:
        intervals = load_st_intervals(db)
        daily = query(db,
                      "SELECT trade_date AS signal_date, LEFT(stk_code,6) AS symbol6, "
                      "preclose FROM kcrp_stock_price "
                      "WHERE trade_date BETWEEN %s AND %s",
                      (unique.signal_date.min(), unique.signal_date.max()))
    daily["signal_date"] = pd.to_datetime(daily.signal_date).dt.strftime("%Y-%m-%d")
    daily["preclose"] = pd.to_numeric(daily.preclose)
    status = attach_st_status(unique, intervals)
    status["signal_date"] = status.signal_date.dt.strftime("%Y-%m-%d")
    status = status.merge(daily, on=["signal_date", "symbol6"], how="left",
                          validate="one_to_one")
    status = mark_entry_limit(status)
    scores = scores.merge(status[["signal_date", "symbol6", "entry_eligible"]],
                          on=["signal_date", "symbol6"], validate="many_to_one")
    candidate_rows = pd.read_csv(candidates_path,
                                 dtype={"signal_date": str, "next_date": str,
                                        "symbol6": str}, low_memory=False)
    candidate_outcomes = status.merge(
        candidate_rows[["signal_date", "symbol6", "close_40"]],
        on=["signal_date", "symbol6"], validate="one_to_one")
    candidate_outcomes["sale_0940_return"] = (
        candidate_outcomes.close_40 / candidate_outcomes.signal_price - 1)
    eligible_pool = candidate_outcomes[candidate_outcomes.entry_eligible]
    daily_pool = eligible_pool.groupby("signal_date").agg(
        known=("sale_0940_return", "count"),
        ge3=("sale_0940_return", lambda s: int(s.ge(.03).sum())))
    daily_pool["random_p_ge3"] = daily_pool.ge3 / daily_pool.known
    picks = []
    for (arm, day), group in scores.groupby(["arm", "signal_date"], sort=True):
        selected = select_top(group[group.entry_eligible].drop(columns="entry_eligible"))
        first = selected[selected.selection_rank.eq(1)].copy()
        if not first.empty:
            first["arm"] = arm
            picks.append(verify_next_day(first, candidate_rows))
    filtered = pd.concat(picks, ignore_index=True)
    raw_files = sorted(checkpoint.glob("*_picks.csv"))
    raw = pd.concat([pd.read_csv(path, dtype={"symbol6": str,
                                            "signal_date": str})
                     for path in raw_files], ignore_index=True)
    raw = raw[raw.selection_rank.eq(1)].merge(
        status[["signal_date", "symbol6", "st_type", "preclose", "limit_pct",
                "limit_price", "entry_at_limit", "entry_status_known"]],
        on=["signal_date", "symbol6"], validate="many_to_one")
    output.mkdir(parents=True, exist_ok=True)
    status.to_csv(output / "candidate_entry_limit.csv.gz", index=False,
                  compression="gzip")
    raw.to_csv(output / "raw_top1_entry_limit.csv", index=False)
    filtered.to_csv(output / "nonlimit_top1_verified.csv", index=False)
    summary = {
        "fold_days": len(score_files),
        "candidate_stock_days": len(status),
        "unknown_entry_status": int((~status.entry_status_known).sum()),
        "at_limit_candidate_stock_days": int(status.entry_at_limit.sum()),
        "eligible_candidate_pool": {
            "stock_days": len(eligible_pool),
            "known_sale_stock_days": int(eligible_pool.sale_0940_return.notna().sum()),
            "sale_ge3_stock_days": int(eligible_pool.sale_0940_return.ge(.03).sum()),
        },
        "arms": {},
    }
    for arm, group in raw.groupby("arm"):
        selected = filtered[filtered.arm.eq(arm)]
        random_rates = daily_pool.loc[selected.signal_date, "random_p_ge3"].tolist()
        observed_hits = int(selected.actual_0940_return.ge(.03).sum())
        random_pmf = poisson_binomial_pmf(random_rates)
        kept = group[group.entry_status_known & ~group.entry_at_limit]
        missing = kept.actual_0940_return.isna()
        idle_days = len(group) - len(kept)
        summary["arms"][arm] = {
            "raw_top1_days": len(group),
            "raw_top1_at_limit": int(group.entry_at_limit.sum()),
            "drop_original_top1_without_replacement": {
                "selected_days": len(kept), "idle_days": idle_days,
                "unknown_sale_days": int(missing.sum()),
                "sale_ge3": int(kept.actual_0940_return.ge(.03).sum()),
                "mean_pct_per_selected_trade": float(
                    kept.actual_0940_return.mean() * 100)
                if len(kept) > int(missing.sum()) else None,
                "mean_pct_per_test_day_with_idle_zero": float(
                    kept.actual_0940_return.fillna(0).sum() / len(group) * 100)
                if not missing.any() else None,
            },
            "nonlimit_top1_days": len(selected),
            "nonlimit_known_sale_days": int(selected.actual_0940_return.notna().sum()),
            "nonlimit_sale_ge3": int(selected.actual_0940_return.ge(.03).sum()),
            "nonlimit_random_expected_ge3": float(sum(random_rates)),
            "nonlimit_random_p_at_least_ge3_hits": float(
                sum(random_pmf[observed_hits:])),
            "nonlimit_sale_mean_pct": float(selected.actual_0940_return.mean() * 100)
            if selected.actual_0940_return.notna().any() else None,
        }
    (output / "entry_limit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--candidates", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--allow-partial", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.checkpoint, a.candidates, a.output, a.allow_partial),
                     ensure_ascii=False, indent=2))

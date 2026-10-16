"""Paired sensitivity test of daily-adjusted labels on the frozen 2026 cohort.

The original MK/post minute archive is no longer present. This uses the
KingdomAI daily adjclose/close factor on the same unadjusted minute prices;
it is an adjusted-label proxy, not a replay of the vendor's MK/post files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql

from scripts.benchmark_automl_1440_inference import db_connection
from scripts.experiment_automl_four_class_subset_selection import fit_tree
from scripts.experiment_automl_mk_1m_rolling import evaluate
from scripts.experiment_automl_second_high_four_class import four_class
from scripts.run_automl_four_class_pipeline import select_training_samples


SOURCE = Path("outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz")
BASELINE = Path("docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top1_verified.csv")
GATE = Path("docs/stock_automl_runs/20261011_mk_1m_market_amount_board/gate_daily.csv")
OUTPUT = Path("docs/stock_automl_runs/20261012_adjusted_basis_sensitivity")
EXPECTED_SOURCE_SHA256 = "d160e906712f8d9f0d7b1f052079c023fd2e8d7def5611fd71c31c738e4b90f2"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_factors(rows: pd.DataFrame, target: Path) -> pd.DataFrame:
    wanted = set(zip(rows.signal_date, rows.symbol6))
    wanted.update(zip(rows.next_date.dropna(), rows.loc[rows.next_date.notna(), "symbol6"]))
    records = []
    conn = db_connection()
    try:
        with conn.cursor(pymysql.cursors.SSDictCursor) as cur:
            cur.execute("""SELECT trade_date, LEFT(stk_code,6) AS symbol6,
                       close, adjclose, preclose, adjpreclose
                       FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s
                       ORDER BY trade_date, stk_code""",
                        (min(rows.signal_date), max(rows.next_date.dropna())))
            for row in cur:
                day = str(row["trade_date"])
                code = str(row["symbol6"]).zfill(6)
                if (day, code) in wanted:
                    records.append({"date": day, "symbol6": code,
                                    **{key: float(row[key]) if row[key] is not None else np.nan
                                       for key in ("close", "adjclose", "preclose", "adjpreclose")}})
    finally:
        conn.rollback()
        conn.close()
    factors = pd.DataFrame(records).sort_values(["date", "symbol6"])
    if factors.duplicated(["date", "symbol6"]).any():
        raise ValueError("Duplicate daily adjustment factor")
    factors["factor"] = factors.adjclose / factors.close
    target.parent.mkdir(parents=True, exist_ok=True)
    factors.to_csv(target, index=False, compression="gzip", float_format="%.17g")
    return factors


def attach_factors(rows: pd.DataFrame, factors: pd.DataFrame,
                   factor_change_floor: float = 0) -> pd.DataFrame:
    current = factors[["date", "symbol6", "factor"]].rename(
        columns={"date": "signal_date", "factor": "factor_t"})
    following = factors[["date", "symbol6", "factor"]].rename(
        columns={"date": "next_date", "factor": "factor_next"})
    merged = rows.merge(current, on=["signal_date", "symbol6"], how="left", validate="many_to_one")
    merged = merged.merge(following, on=["next_date", "symbol6"], how="left", validate="many_to_one")
    merged["factor_ratio"] = merged.factor_next / merged.factor_t
    valid = merged.factor_t.gt(0) & merged.factor_next.gt(0) & np.isfinite(merged.factor_ratio)
    merged["effective_factor_ratio"] = np.where(
        (merged.factor_ratio - 1).abs().gt(factor_change_floor), merged.factor_ratio, 1.0)
    merged["adjusted_second_high"] = np.where(
        valid & merged.second_high.notna(),
        merged.second_high * merged.effective_factor_ratio, np.nan)
    merged["raw_target_return"] = merged.second_high / merged.signal_price - 1
    merged["adjusted_target_return"] = merged.adjusted_second_high / merged.signal_price - 1
    return merged


def gate_summary(picks: pd.DataFrame, gate: pd.DataFrame) -> dict:
    joined = gate[["signal_date", "gate_pass"]].merge(
        picks[["signal_date", "symbol6", "actual_0940_return"]],
        on="signal_date", how="left", validate="one_to_many")
    selected = joined[joined.gate_pass & joined.symbol6.notna()].copy()
    ret = selected.actual_0940_return.fillna(0)
    return {"gate_pass_days": int(joined.gate_pass.sum()),
            "trades": len(selected), "mean_raw_0940_return_pct": float(ret.mean() * 100),
            "sum_raw_0940_return_pct": float(ret.sum() * 100),
            "price_proxy_compound_pct": float((1 + ret).prod() * 100 - 100),
            "missing_exit_prices": int(selected.actual_0940_return.isna().sum())}


def monthly_summary(paired: pd.DataFrame) -> pd.DataFrame:
    frame = paired.copy()
    frame["month"] = frame.signal_date.str[:7]
    frame["changed"] = frame.raw_model_top1.fillna("").ne(
        frame.adjusted_model_top1.fillna(""))
    for prefix in ("raw_model", "adjusted_model"):
        frame[f"{prefix}_trade"] = frame.gate_pass & frame[f"{prefix}_top1"].notna()
        frame[f"{prefix}_daily_return"] = np.where(
            frame[f"{prefix}_trade"], frame[f"{prefix}_sale_return"].fillna(0), 0)
    records = []
    for month, part in frame.groupby("month", sort=True):
        row = {"month": month, "test_days": len(part),
               "gate_pass_days": int(part.gate_pass.sum()),
               "different_top1_days": int(part.changed.sum())}
        for prefix in ("raw_model", "adjusted_model"):
            trade = part[f"{prefix}_trade"]
            returns = part[f"{prefix}_daily_return"]
            row[f"{prefix}_trades"] = int(trade.sum())
            row[f"{prefix}_sum_return_pct"] = float(returns.sum() * 100)
            row[f"{prefix}_mean_trade_return_pct"] = float(returns[trade].mean() * 100) if trade.any() else np.nan
            row[f"{prefix}_compound_pct"] = float(((1 + returns).prod() - 1) * 100)
        records.append(row)
    return pd.DataFrame(records)


def run(output: Path = OUTPUT, refresh_factors: bool = False,
        factor_change_floor: float = 0, factor_file: Path | None = None) -> dict:
    if sha256(SOURCE) != EXPECTED_SOURCE_SHA256:
        raise ValueError("Frozen candidate cohort changed")
    rows = pd.read_csv(SOURCE, dtype={"symbol6": str, "signal_date": str,
                                     "next_date": str}, low_memory=False)
    output.mkdir(parents=True, exist_ok=True)
    if not 0 <= factor_change_floor < 1:
        raise ValueError("factor_change_floor must be in [0,1)")
    factor_file = factor_file or output / "daily_adjustment_factors.csv.gz"
    if refresh_factors or not factor_file.exists():
        factors = fetch_factors(rows, factor_file)
    else:
        factors = pd.read_csv(factor_file, dtype={"date": str, "symbol6": str},
                              float_precision="round_trip")
    joined = attach_factors(rows, factors, factor_change_floor)
    mature = joined[joined.second_high.notna()].copy()
    with_factor = mature[mature.adjusted_second_high.notna()].copy()
    raw_class = pd.Series(four_class(with_factor.raw_target_return).astype(str), index=with_factor.index)
    adj_class = pd.Series(four_class(with_factor.adjusted_target_return).astype(str), index=with_factor.index)
    ratios = with_factor.factor_ratio
    audit = {"candidate_rows": len(rows), "mature_rows": len(mature),
             "factor_change_floor": factor_change_floor,
             "mature_factor_rows": len(with_factor),
             "missing_mature_factors": len(mature) - len(with_factor),
             "class_flips": int(raw_class.ne(adj_class).sum()),
             "raw_ge3": int(raw_class.eq("ge3").sum()),
             "adjusted_ge3": int(adj_class.eq("ge3").sum()),
             "ratio_abs_over_1pct": int((ratios - 1).abs().gt(.01).sum()),
             "ratio_abs_over_5pct": int((ratios - 1).abs().gt(.05).sum()),
             "ratio_abs_over_10pct": int((ratios - 1).abs().gt(.10).sum()),
             "ratio_min": float(ratios.min()), "ratio_max": float(ratios.max())}
    adjusted = joined.copy()
    adjusted["second_high"] = adjusted.adjusted_second_high
    dates = sorted(rows.signal_date.unique())
    test_days = dates[20:-1]
    picks = []
    fold_records = []
    for day in test_days:
        train_dates = dates[dates.index(day)-20:dates.index(day)]
        train = select_training_samples(adjusted, train_dates)
        if train.signal_date.nunique() != 20 or not train.next_date.le(day).all():
            raise ValueError(f"Incomplete adjusted-label training dates: {day}")
        model = fit_tree(train, day)
        _, chosen = evaluate(model, adjusted, day, "daily_adj_label_proxy")
        first = chosen[chosen.selection_rank.eq(1)].copy()
        if len(first):
            picks.append(first)
        fold_records.append({"signal_date": day, "training_rows": len(train),
                             "top1_symbol6": first.symbol6.iloc[0] if len(first) else ""})
        print(f"adjusted-label fold {day}", flush=True)
    new_picks = pd.concat(picks, ignore_index=True)
    new_picks.to_csv(output / "adjusted_label_top1.csv", index=False)
    pd.DataFrame(fold_records).to_csv(output / "folds.csv", index=False)
    baseline = pd.read_csv(BASELINE, dtype={"signal_date": str, "symbol6": str})
    gate = pd.read_csv(GATE, dtype={"选股日": str})[["选股日", "是否交易"]].rename(
        columns={"选股日": "signal_date", "是否交易": "gate_pass"})
    if not gate.gate_pass.isin([True, False]).all():
        raise ValueError("Invalid saved gate")
    if sorted(gate.signal_date) != test_days:
        raise ValueError("Saved gate days do not match paired test days")
    baseline = baseline[baseline.signal_date.isin(test_days)]
    paired = pd.DataFrame({"signal_date": test_days}).merge(
        baseline[["signal_date", "symbol6", "actual_0940_return"]],
        on="signal_date", how="left", validate="one_to_one").rename(
            columns={"symbol6": "raw_model_top1", "actual_0940_return": "raw_model_sale_return"})
    paired = paired.merge(new_picks[["signal_date", "symbol6", "actual_0940_return"]],
                          on="signal_date", how="left", validate="one_to_one").rename(
        columns={"symbol6": "adjusted_model_top1", "actual_0940_return": "adjusted_model_sale_return"})
    paired = paired.merge(gate, on="signal_date", validate="one_to_one")
    paired.to_csv(output / "paired_daily.csv", index=False)
    monthly_summary(paired).to_csv(output / "monthly.csv", index=False)
    changed = paired.raw_model_top1.fillna("").ne(paired.adjusted_model_top1.fillna(""))
    summary = {"source_sha256": sha256(SOURCE), "factors_file": str(factor_file),
               "factors_sha256": sha256(factor_file),
               "factor_source": "kcrp_stock_price.adjclose / close, read on 2026-10-12",
               "scope": "daily-factor adjusted-label proxy; not original MK/post minute bars",
               "test_days": len(test_days), "label_audit": audit,
               "raw_model": gate_summary(baseline, gate),
               "adjusted_label_model": gate_summary(new_picks, gate),
               "different_top1_days": int(changed.sum()),
               "different_top1_gate_days": int((paired.gate_pass & changed).sum())}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-factors", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--factor-file", type=Path)
    parser.add_argument("--factor-change-floor", type=float, default=0)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.refresh_factors,
                         args.factor_change_floor, args.factor_file),
                     ensure_ascii=False, indent=2))

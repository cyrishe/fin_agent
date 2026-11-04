"""Independently reconcile saved adjustment experiments with frozen raw prices."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.evaluate_automl_adjusted_basis import BASELINE, GATE, OUTPUT, SOURCE


ARMS = {"all_factor_changes": (Path("."), 0.0),
        "material_changes": (Path("material_change"), .0001),
        "no_change_control": (Path("no_change_control"), .99)}
TEXT_COLUMNS = {"signal_date", "next_date", "symbol6", "name", "predicted_class",
                "arm", "actual_class", "top1_symbol6", "raw_model_top1",
                "adjusted_model_top1", "month"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def equal_numbers(left, right, label: str, tolerance: float = 1e-11) -> float:
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if a.shape != b.shape or not np.array_equal(np.isnan(a), np.isnan(b)):
        raise ValueError(f"{label}: shape or missing-value mismatch")
    delta = np.abs(a - b)
    maximum = float(np.nanmax(delta)) if delta.size else 0.0
    if maximum > tolerance:
        raise ValueError(f"{label}: maximum difference {maximum}")
    return maximum


def classes(ret: np.ndarray) -> np.ndarray:
    return np.select((ret < 0, ret < .01, ret < .03),
                     ("lt0", "0to1", "1to3"), default="ge3")


def compare_csv(fresh: Path, saved: Path, label: str) -> None:
    left = pd.read_csv(fresh, dtype=str, keep_default_na=False)
    right = pd.read_csv(saved, dtype=str, keep_default_na=False)
    if list(left.columns) != list(right.columns) or left.shape != right.shape:
        raise ValueError(f"{label}: columns or shape differ")
    for column in left:
        a, b = left[column], right[column]
        if a.equals(b):
            continue
        if column in TEXT_COLUMNS:
            raise ValueError(f"{label}: {column} differs")
        numeric_a = pd.to_numeric(a, errors="coerce")
        numeric_b = pd.to_numeric(b, errors="coerce")
        if numeric_a.notna().equals(numeric_b.notna()) and numeric_a.notna().any():
            equal_numbers(numeric_a, numeric_b, f"{label}: {column}")
        else:
            raise ValueError(f"{label}: {column} differs")


def verify(root: Path = OUTPUT, fresh_root: Path | None = None) -> dict:
    rows = pd.read_csv(SOURCE, dtype={"signal_date": str, "next_date": str,
                                     "symbol6": str}, low_memory=False)
    factors_path = root / "daily_adjustment_factors.csv.gz"
    factors = pd.read_csv(factors_path, dtype={"date": str, "symbol6": str},
                          float_precision="round_trip")
    if rows.duplicated(["signal_date", "symbol6"]).any() or factors.duplicated(["date", "symbol6"]).any():
        raise ValueError("Duplicate candidate or factor key")
    equal_numbers(factors.factor, factors.adjclose / factors.close, "daily factor", 1e-12)
    t = factors[["date", "symbol6", "factor"]].rename(
        columns={"date": "signal_date", "factor": "factor_t"})
    n = factors[["date", "symbol6", "factor"]].rename(
        columns={"date": "next_date", "factor": "factor_next"})
    cohort = rows.merge(t, on=["signal_date", "symbol6"], how="left", validate="many_to_one")
    cohort = cohort.merge(n, on=["next_date", "symbol6"], how="left", validate="many_to_one")
    mature = cohort[cohort.second_high.notna()].copy()
    if not (mature.factor_t.gt(0) & mature.factor_next.gt(0)).all():
        raise ValueError("Mature label missing a valid daily adjustment factor")
    mature["factor_ratio"] = mature.factor_next / mature.factor_t
    raw_ret = mature.second_high.to_numpy() / mature.signal_price.to_numpy() - 1
    raw_class = classes(raw_ret)
    dates = sorted(rows.signal_date.unique())
    test_days = dates[20:-1]
    gate = pd.read_csv(GATE, dtype={"选股日": str}).set_index("选股日")["是否交易"]
    if gate.index.tolist() != test_days or len(test_days) != 118 or int(gate.sum()) != 97:
        raise ValueError("Saved decision calendar or gate differs")
    baseline = pd.read_csv(BASELINE, dtype={"signal_date": str, "symbol6": str})
    candidate_prices = rows[["signal_date", "symbol6", "signal_price", "close_40"]]
    result = {"verified_ref": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "source_sha256": sha(SOURCE), "factor_snapshot_sha256": sha(factors_path),
              "source_rows": len(rows), "factor_rows": len(factors),
              "mature_rows": len(mature), "test_days": len(test_days),
              "gate_pass_days": int(gate.sum()), "arms": {}}
    artifact_hashes = {}
    for name, (relative, floor) in ARMS.items():
        directory = (root / relative).resolve()
        summary = json.loads((directory / "summary.json").read_text())
        if summary["source_sha256"] != result["source_sha256"] or summary["factors_sha256"] != result["factor_snapshot_sha256"]:
            raise ValueError(f"{name}: input hash mismatch")
        ratio = np.where(np.abs(mature.factor_ratio.to_numpy() - 1) > floor,
                         mature.factor_ratio.to_numpy(), 1.0)
        adjusted_ret = mature.second_high.to_numpy() * ratio / mature.signal_price.to_numpy() - 1
        label_flips = int(np.count_nonzero(raw_class != classes(adjusted_ret)))
        if label_flips != summary["label_audit"]["class_flips"]:
            raise ValueError(f"{name}: label flip count mismatch")
        picks = pd.read_csv(directory / "adjusted_label_top1.csv",
                            dtype={"signal_date": str, "symbol6": str})
        if picks.duplicated("signal_date").any():
            raise ValueError(f"{name}: duplicate Top1 date")
        price = picks.merge(candidate_prices, on=["signal_date", "symbol6"],
                            how="left", validate="one_to_one", suffixes=("", "_source"))
        if len(price) != len(picks) or price.signal_price_source.isna().any():
            raise ValueError(f"{name}: selected stock missing from original candidate table")
        independent_returns = price.close_40_source / price.signal_price_source - 1
        max_sale_delta = equal_numbers(picks.actual_0940_return, independent_returns,
                                       f"{name}: unadjusted sale return")
        paired = pd.read_csv(directory / "paired_daily.csv",
                             dtype={"signal_date": str, "raw_model_top1": str,
                                    "adjusted_model_top1": str})
        if paired.signal_date.tolist() != test_days or paired.gate_pass.tolist() != gate.tolist():
            raise ValueError(f"{name}: paired calendar or gate mismatch")
        observed = paired[["signal_date", "adjusted_model_top1", "adjusted_model_sale_return"]].merge(
            picks[["signal_date", "symbol6", "actual_0940_return"]], on="signal_date",
            how="left", validate="one_to_one")
        if not observed.adjusted_model_top1.fillna("").equals(observed.symbol6.fillna("")):
            raise ValueError(f"{name}: paired pick differs from selected Top1")
        equal_numbers(observed.adjusted_model_sale_return, observed.actual_0940_return,
                      f"{name}: paired sale return")
        raw = paired[["signal_date", "raw_model_top1", "raw_model_sale_return"]].merge(
            baseline[["signal_date", "symbol6", "actual_0940_return"]], on="signal_date",
            how="left", validate="one_to_one")
        if not raw.raw_model_top1.fillna("").equals(raw.symbol6.fillna("")):
            raise ValueError(f"{name}: paired raw pick differs from frozen baseline")
        equal_numbers(raw.raw_model_sale_return, raw.actual_0940_return,
                      f"{name}: paired raw sale return")
        traded = paired.gate_pass & paired.adjusted_model_top1.notna()
        returns = paired.loc[traded, "adjusted_model_sale_return"].fillna(0).to_numpy()
        metrics = summary["adjusted_label_model"]
        if int(traded.sum()) != metrics["trades"]:
            raise ValueError(f"{name}: trade count mismatch")
        equal_numbers([returns.mean() * 100, returns.sum() * 100,
                       (np.prod(1 + returns) - 1) * 100],
                      [metrics["mean_raw_0940_return_pct"], metrics["sum_raw_0940_return_pct"],
                       metrics["price_proxy_compound_pct"]], f"{name}: aggregate return")
        changed = paired.raw_model_top1.fillna("").ne(paired.adjusted_model_top1.fillna(""))
        if (int(changed.sum()) != summary["different_top1_days"] or
                int((changed & paired.gate_pass).sum()) != summary["different_top1_gate_days"]):
            raise ValueError(f"{name}: changed-pick count mismatch")
        monthly = pd.read_csv(directory / "monthly.csv")
        if len(monthly) != 6 or int(monthly.adjusted_model_trades.sum()) != len(returns):
            raise ValueError(f"{name}: monthly trade count mismatch")
        equal_numbers([monthly.adjusted_model_sum_return_pct.sum()],
                      [metrics["sum_raw_0940_return_pct"]], f"{name}: monthly return sum")
        if fresh_root is not None:
            fresh = fresh_root / {"all_factor_changes": "all", "material_changes": "material",
                                  "no_change_control": "control"}[name]
            if json.loads((fresh / "summary.json").read_text()) != summary:
                raise ValueError(f"{name}: fresh rerun summary differs")
            for filename in ("adjusted_label_top1.csv", "folds.csv", "paired_daily.csv", "monthly.csv"):
                compare_csv(fresh / filename, directory / filename, f"{name}: {filename}")
        artifact_hashes[name] = {filename: sha(directory / filename) for filename in
                                 ("summary.json", "adjusted_label_top1.csv", "folds.csv",
                                  "paired_daily.csv", "monthly.csv")}
        result["arms"][name] = {"factor_change_floor": floor,
                                "label_flips": label_flips, "selected_days": len(picks),
                                "changed_top1_days": int(changed.sum()),
                                "gate_trades": len(returns),
                                "mean_raw_0940_return_pct": metrics["mean_raw_0940_return_pct"],
                                "price_proxy_compound_pct": metrics["price_proxy_compound_pct"],
                                "max_sale_return_abs_difference": max_sale_delta,
                                "fresh_118_fold_rerun_matched": fresh_root is not None}
    if result["arms"]["no_change_control"]["changed_top1_days"] != 0:
        raise ValueError("No-change control does not reproduce the frozen baseline")
    result["artifact_sha256"] = artifact_hashes
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(fresh_root=args.fresh_root)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    print(serialized)

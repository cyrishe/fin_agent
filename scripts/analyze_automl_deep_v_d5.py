"""Summarize held-out daily classification and minute-close trade simulations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path("outputs/stock_automl/deep_v_20261013_d5/model_v1")


def policy_rows(top: pd.DataFrame) -> pd.DataFrame:
    records = []
    for day, group in top.groupby("signal_date", sort=True):
        e = group[group.stage2_eligible].copy()
        if not e.path_valid.all():
            raise ValueError(f"eligible candidate lacks valid full minute path: {day}")
        for name, rank in (("stage1_eligible", "stage1_rank"),
                           ("stage2", "stage2_rank")):
            chosen = e.nsmallest(20, rank)
            if len(chosen) != 20:
                raise ValueError(f"not enough eligible candidates: {day}")
            trades = chosen[chosen.late_trigger]
            gross = trades.gross_return.astype(float)
            records.append({
                "day": day, "month": day[:7], "policy": name,
                "candidate_count": len(chosen), "late_success": int(chosen.late_success.sum()),
                "trades": len(trades), "positive_trades": int(gross.gt(0).sum()),
                "mean_trade_return": float(gross.mean()) if len(gross) else np.nan,
                "median_trade_return": float(gross.median()) if len(gross) else np.nan,
                "slot_return_gross": float(gross.sum() / 20),
                "slot_return_cost20bp": float((gross.sum() - len(gross) * 0.002) / 20),
            })
    return pd.DataFrame(records)


def topk_tables(top: pd.DataFrame, root: Path) -> None:
    prior = []
    for n in (1, 5, 10, 20, 100):
        chosen = top[top.stage1_rank.le(n)]
        full = chosen[chosen.full240]
        prior.append({"topk": n, "candidates": len(chosen), "full240": len(full),
                      "range5": int(full.is_event.sum()),
                      "deepv": int(full.label.eq(1).sum()),
                      "deepv_precision": float(full.label.eq(1).mean())})
    pd.DataFrame(prior).to_csv(root / "stage1_topk.csv", index=False)

    conditional = []
    eligible = top[top.stage2_eligible]
    for n in (1, 5, 10, 20):
        for policy, rank in (("prior_rank", "stage1_rank"),
                             ("morning_model", "stage2_rank")):
            chosen = pd.concat([g.nsmallest(n, rank)
                                for _, g in eligible.groupby("signal_date", sort=True)])
            trades = chosen[chosen.late_trigger]
            daily = chosen.groupby("signal_date").gross_return.sum() / n
            conditional.append({"topk": n, "policy": policy,
                                "candidates": len(chosen),
                                "late_deepv": int(chosen.late_success.sum()),
                                "late_deepv_precision": float(chosen.late_success.mean()),
                                "triggered_trades": len(trades),
                                "positive_trades": int(trades.gross_return.gt(0).sum()),
                                "gross_compound": float((1 + daily).prod() - 1)})
    pd.DataFrame(conditional).to_csv(root / "stage2_topk.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root
    top = pd.read_csv(root / "two_stage_trades.csv.gz", dtype={"symbol6": str})
    daily = pd.read_csv(root / "two_stage_daily.csv")
    for col in ("stage2_eligible", "path_valid", "late_trigger", "late_success"):
        top[col] = top[col].astype(bool)
    topk_tables(top, root)
    policy = policy_rows(top)
    policy.to_csv(root / "two_stage_policy_daily.csv", index=False)
    by_policy = policy.groupby("policy").agg(
        days=("day", "size"), candidates=("candidate_count", "sum"),
        late_success=("late_success", "sum"), trades=("trades", "sum"),
        positive_trades=("positive_trades", "sum"),
        mean_daily_slot_return=("slot_return_gross", "mean"),
        mean_daily_slot_return_cost20bp=("slot_return_cost20bp", "mean"))
    by_policy["precision20"] = by_policy.late_success / by_policy.candidates
    by_policy["trade_hit_rate"] = by_policy.positive_trades / by_policy.trades
    monthly = policy.groupby(["month", "policy"]).agg(
        days=("day", "size"), late_success=("late_success", "sum"),
        trades=("trades", "sum"), positive_trades=("positive_trades", "sum"),
        mean_daily_slot_return=("slot_return_gross", "mean"))
    monthly.to_csv(root / "two_stage_policy_monthly.csv")
    wide = policy.pivot(index="day", columns="policy",
                        values=["late_success", "slot_return_gross"])
    rng = np.random.default_rng(17)
    diff_precision = (wide["late_success"]["stage2"].to_numpy() -
                      wide["late_success"]["stage1_eligible"].to_numpy()) / 20
    diff_return = (wide["slot_return_gross"]["stage2"].to_numpy() -
                   wide["slot_return_gross"]["stage1_eligible"].to_numpy())
    sample = rng.integers(0, len(diff_precision), size=(10000, len(diff_precision)))
    ci_precision = np.quantile(diff_precision[sample].mean(axis=1), [.025, .975]).tolist()
    ci_return = np.quantile(diff_return[sample].mean(axis=1), [.025, .975]).tolist()
    summary = {
        "test_days": len(daily),
        "train_window_days": 20,
        "stage1": {
            "test_full240": int(daily.full240.sum()),
            "deepv_total": int(daily.deepv_all.sum()),
            "base_rate": float(daily.deepv_all.sum() / daily.full240.sum()),
            "mean_daily_auc": float(daily.stage1_auc.mean()),
            "mean_daily_ap": float(daily.stage1_ap.mean()),
            "top100_events": int(daily.s1_top100_events.sum()),
            "top100_deepv": int(daily.s1_top100_deepv.sum()),
            "top100_late_success": int(daily.s1_top100_late_success.sum()),
        },
        "stage2": {
            "eligible_count": int(daily.stage2_eligible.sum()),
            "early_completed_excluded": int(daily.early_completed_excluded.sum()),
            "late_success_in_eligible": int(daily.stage2_late_success.sum()),
            "base_rate": float(daily.stage2_late_success.sum() / daily.stage2_eligible.sum()),
            "mean_daily_auc": float(daily.stage2_auc.mean()),
            "mean_daily_ap": float(daily.stage2_ap.mean()),
            "days_with_both_classes": int(daily.stage2_auc.notna().sum()),
        },
        "policy": by_policy.reset_index().to_dict(orient="records"),
        "paired_bootstrap_95pct": {
            "precision20_difference": ci_precision,
            "daily_slot_return_difference": ci_return,
        },
    }
    (root / "two_stage_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

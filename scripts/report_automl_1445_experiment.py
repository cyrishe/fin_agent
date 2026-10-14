"""Compare 14:40 baseline with the independent 14:45 rolling replays."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_automl_1445_exact0940 import OUT
from scripts.experiment_automl_intraday_vwap import LATE_MARKET


BASE = Path("docs/stock_automl_runs/20261012_intraday_vwap")


def arm_rows(segment: str, arm: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if arm == "14:40 baseline":
        picks = pd.read_csv(BASE / "paired_top1.csv", dtype={"symbol6": str})
        picks = picks[picks.segment.eq("feb_jul" if segment == "jan_jul" else "sep_oct") &
                      picks.arm.eq("baseline7")].copy()
        gate = pd.read_csv(BASE / "paired_top1_amount_gate.csv", dtype={"symbol6": str})
        gate = gate[gate.segment.eq("feb_jul" if segment == "jan_jul" else "sep_oct") &
                    gate.arm.eq("baseline7")].copy()
    else:
        folder = OUT / "timeonly" if arm == "14:45 1m" and segment == "aug_oct" else OUT
        picks = pd.read_csv(folder / f"top1_{segment}.csv", dtype={"symbol6": str})
        gate = pd.read_csv(folder / "top1_amount_gate.csv", dtype={"symbol6": str})
        gate = gate[gate.segment.eq(segment)].copy()
    if picks.duplicated("signal_date").any() or gate.duplicated("signal_date").any():
        raise ValueError("Multiple Top1 records per date")
    return picks, gate


def calendar(segment: str) -> list[str]:
    if segment == "jan_jul":
        rows = pd.read_csv("outputs/stock_automl/mk_1m_1445_exact0940/"
                           "trainable_candidates.csv.gz", usecols=["signal_date"])
        return [d for d in sorted(rows.signal_date.unique())[20:] if d <= "2026-07-30"]
    return [d for d in pd.read_csv(LATE_MARKET).trade_date.astype(str)
            if "2026-09-02" <= d <= "2026-10-09"]


def run(output: Path = OUT) -> dict:
    daily_parts = []
    summaries = []
    overlap = []
    for segment in ("jan_jul", "aug_oct"):
        days = calendar(segment)
        arms = ("14:40 baseline", "14:45 1m") if segment == "jan_jul" else (
            "14:40 baseline", "14:45 1m", "14:45 hybrid")
        picks_by_arm = {}
        for arm in arms:
            picks, gate = arm_rows(segment, arm)
            picks_by_arm[arm] = picks
            if not set(picks.signal_date).issubset(days):
                raise ValueError(f"Top1 date outside matched calendar: {arm}")
            detail = (pd.DataFrame({"signal_date": days})
                      .merge(picks[["signal_date", "symbol6", "actual_second_high_return",
                                    "actual_0940_return"]], on="signal_date", how="left",
                             validate="one_to_one")
                      .merge(gate[["signal_date", "actionable", "amount_1440" if
                                   arm == "14:40 baseline" else "amount_1445"]],
                             on="signal_date", how="left", validate="one_to_one"))
            detail["actionable"] = detail.actionable.eq(True)
            detail["realized_return"] = np.where(
                detail.actionable, detail.actual_0940_return, 0.0)
            detail["segment"] = segment
            detail["arm"] = arm
            detail["month"] = detail.signal_date.str[:7]
            daily_parts.append(detail)
            trades = detail[detail.actionable]
            summaries.append({"segment": segment, "arm": arm, "days": len(days),
                              "top1_picks": len(picks), "target_ge3_picks": int(
                                  picks.actual_second_high_return.ge(.03).sum()),
                              "sale_negative_picks": int(
                                  picks.actual_0940_return.lt(0).sum()),
                              "raw_mean_per_pick_pct": float(
                                  picks.actual_0940_return.mean()*100),
                              "trades": len(trades), "idle_days": len(days)-len(trades),
                              "target_ge3_trades": int(
                                  trades.actual_second_high_return.ge(.03).sum()),
                              "sale_negative_trades": int(
                                  trades.actual_0940_return.lt(0).sum()),
                              "mean_per_trade_pct": float(
                                  trades.actual_0940_return.mean()*100),
                              "mean_per_day_pct": float(
                                  detail.realized_return.mean()*100),
                              "compound_proxy_pct": float(
                                  ((1+detail.realized_return).prod()-1)*100)})
        for arm in arms[1:]:
            pair = picks_by_arm["14:40 baseline"][['signal_date', 'symbol6']].merge(
                picks_by_arm[arm][['signal_date', 'symbol6']], on='signal_date',
                suffixes=('_baseline', '_other'))
            overlap.append({"segment": segment, "arm": arm,
                            "both_picked_days": len(pair),
                            "same_stock_days": int(pair.symbol6_baseline.eq(
                                pair.symbol6_other).sum())})
    daily = pd.concat(daily_parts, ignore_index=True)
    daily.to_csv(output / "daily_comparison.csv", index=False)
    pd.DataFrame(summaries).to_csv(output / "comparison_summary.csv", index=False)
    monthly = (daily.groupby(["segment", "month", "arm"])
               .agg(days=("signal_date", "size"),
                    picks=("symbol6", "count"),
                    trades=("actionable", "sum"),
                    target_ge3_picks=("actual_second_high_return",
                                      lambda x: int(x.ge(.03).sum())),
                    negative_sales=("actual_0940_return",
                                    lambda x: int(x.lt(0).sum())),
                    mean_per_day_pct=("realized_return", lambda x: x.mean()*100),
                    compound_proxy_pct=("realized_return", lambda x: ((1+x).prod()-1)*100))
               .reset_index())
    monthly.to_csv(output / "comparison_monthly.csv", index=False)
    pd.DataFrame(overlap).to_csv(output / "selection_overlap.csv", index=False)
    manifest = {"baseline": str(BASE), "results": str(output),
                "segments": summaries, "overlap": overlap}
    (output / "comparison_manifest.json").write_text(json.dumps(
        manifest, ensure_ascii=False, indent=2)+"\n")
    print(pd.DataFrame(summaries).to_string(index=False))
    return manifest


if __name__ == "__main__":
    run()

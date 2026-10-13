"""Common-date and seed-stability diagnostics for close→open follow-up studies."""
from __future__ import annotations

import json

import pandas as pd

from scripts.experiment_automl_close_open_screen_targets import ROOT, metrics


def common_dates(frame: pd.DataFrame) -> pd.DataFrame:
    missing = frame.loc[frame.net.isna(), ["period", "signal_date"]].drop_duplicates()
    known = frame.merge(missing.assign(exclude=True), on=["period", "signal_date"], how="left")
    return known[known.exclude.isna()].drop(columns="exclude")


def run() -> None:
    screen = pd.read_csv(ROOT / "screen_target_picks.csv", dtype={"period": str, "symbol6": str})
    tree = pd.read_csv(ROOT / "tree_target_picks.csv", dtype={"period": str, "symbol6": str})
    sequence = pd.read_csv(ROOT / "sequence_picks.csv", dtype={"period": str, "symbol6": str})
    cnn = pd.read_csv(ROOT / "cnn_monthly_picks.csv", dtype={"period": str, "symbol6": str})
    for prefix, frame in (("screen_tree", pd.concat([screen, tree], ignore_index=True)),
                          ("sequence", sequence), ("cnn_monthly", cnn)):
        complete = common_dates(frame)
        summary, monthly = metrics(complete)
        summary.to_csv(ROOT / f"{prefix}_common_complete_summary.csv", index=False)
        monthly.to_csv(ROOT / f"{prefix}_common_complete_monthly.csv", index=False)
        print(prefix, complete.groupby("period").signal_date.nunique().to_dict())
    chosen = cnn[cnn.model.str.startswith("cnn_seed")].pivot(
        index=["period", "signal_date"], columns="model", values="symbol6")
    distinct = chosen.nunique(axis=1)
    overlap = distinct.rename("distinct_top1").reset_index()
    overlap["all3_same"] = overlap.distinct_top1.eq(1)
    overlap["at_least2_same"] = overlap.distinct_top1.le(2)
    overlap["month"] = overlap.signal_date.str[:7]
    overlap.to_csv(ROOT / "cnn_seed_agreement_daily.csv", index=False)
    overlap.groupby("month")[["all3_same", "at_least2_same"]].mean().to_csv(
        ROOT / "cnn_seed_agreement_monthly.csv")
    sep = screen[(screen.period.eq("2026s")) & screen.model.eq("target_f11_second_high")]
    if len(sep) != 16 or sep.net.isna().any():
        raise ValueError("September target tail audit expected 16 known trades")
    tail = {"sep_days": len(sep), "net_mean_pct": sep.net.mean()*100,
            "median_net_pct": sep.net.median()*100,
            "without_top1_net_pct": sep.net.drop(sep.net.nlargest(1).index).mean()*100,
            "without_top3_net_pct": sep.net.drop(sep.net.nlargest(3).index).mean()*100,
            "winsor5_net_pct": sep.net.clip(-.05, .05).mean()*100,
            "top3": sep.nlargest(3, "net")[["signal_date", "symbol6", "net"]].to_dict("records")}
    (ROOT / "september_second_high_tail.json").write_text(
        json.dumps(tail, ensure_ascii=False, indent=2) + "\n")
    print("CNN three-seed same Top1", overlap.all3_same.mean())
    print("September second-high tail", tail)


if __name__ == "__main__":
    run()

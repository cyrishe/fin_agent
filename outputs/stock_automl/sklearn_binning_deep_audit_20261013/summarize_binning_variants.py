"""Pairwise probability and Top1 comparison for preselected binning variants."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
VARIANTS = ("midpoint255", "avgcdf255", "linear255", "midpoint63", "avgcdf63")
LABELS = ("lt0", "0to1", "1to3", "ge3")
P = ["p_" + label for label in LABELS]
RNG = np.random.default_rng(20261013)


def score_path(dataset, variant):
    if variant in ("midpoint255", "avgcdf255"):
        version = "171" if variant == "midpoint255" else "191"
        prefix = "scores" if dataset == "feb_jul" else f"scores_{dataset}"
        return ROOT / f"{prefix}_sklearn_{version}.csv.gz"
    return ROOT / f"scores_{dataset}_{variant}.csv.gz"


def selected(frame):
    e = frame[frame.predicted_class.isin(("ge3", "1to3"))].copy()
    e["priority"] = e.predicted_class.map({"ge3": 0, "1to3": 1})
    e["score"] = e.p_ge3.where(e.priority.eq(0), e.p_ge3 + e.p_1to3)
    e = e.sort_values(["signal_date", "priority", "score", "symbol6"],
                      ascending=[True, True, False, True])
    e = e.drop_duplicates("signal_date")
    e["return_0940"] = e.close_40 / e.signal_price - 1
    return e[["signal_date", "symbol6", "predicted_class", "return_0940"]]


report = {}
for dataset in ("feb_jul", "aug_oct"):
    frames = {}
    for variant in VARIANTS:
        frame = pd.read_csv(score_path(dataset, variant),
                            dtype={"signal_date": str, "symbol6": str})
        frames[variant] = frame.sort_values(["signal_date", "symbol6"]).reset_index(drop=True)
    ref = frames["midpoint255"]
    known = ref.second_high.notna().to_numpy()
    returns = ref.second_high.to_numpy() / ref.signal_price.to_numpy() - 1
    y = np.select([returns < 0, returns < .01, returns < .03], [0, 1, 2], default=3)
    metrics = {}
    daily_losses = {}
    picks = {}
    for variant, frame in frames.items():
        if not ref[["signal_date", "symbol6"]].equals(frame[["signal_date", "symbol6"]]):
            raise ValueError(f"Candidate mismatch: {dataset}/{variant}")
        for col in ("signal_price", "second_high", "close_40"):
            if not np.allclose(ref[col], frame[col], equal_nan=True):
                raise ValueError(f"Outcome mismatch: {dataset}/{variant}/{col}")
        p = frame.loc[known, P].to_numpy()
        target = y[known]
        losses = -np.log(np.maximum(p[np.arange(len(p)), target], 1e-15))
        brier = np.square(p - np.eye(4)[target]).sum(axis=1)
        dates = frame.loc[known, "signal_date"].to_numpy()
        daily_losses[variant] = pd.Series(losses).groupby(dates).mean()
        picks[variant] = selected(frame)
        metrics[variant] = {
            "logloss": float(losses.mean()),
            "brier4": float(brier.mean()),
            "top1_days": len(picks[variant]),
            "top1_mean_0940_return": float(picks[variant].return_0940.mean()),
        }
    baseline = daily_losses["midpoint255"]
    for variant in VARIANTS:
        delta = (daily_losses[variant] - baseline).to_numpy()
        sample = RNG.choice(delta, size=(10000, len(delta)), replace=True).mean(axis=1)
        metrics[variant]["daily_mean_logloss_delta_vs_midpoint255"] = float(delta.mean())
        metrics[variant]["daily_delta_day_bootstrap_95pct"] = np.quantile(sample, [.025, .975]).tolist()
        old = picks["midpoint255"].rename(columns={"symbol6": "old_symbol"})
        other = picks[variant].rename(columns={"symbol6": "other_symbol"})
        pair = old[["signal_date", "old_symbol"]].merge(
            other[["signal_date", "other_symbol"]], on="signal_date", how="outer")
        metrics[variant]["top1_different_or_missing_vs_midpoint255"] = int(
            (~pair.old_symbol.eq(pair.other_symbol)).sum())
    old63 = picks["midpoint63"].rename(columns={"symbol6": "old_symbol"})
    new63 = picks["avgcdf63"].rename(columns={"symbol6": "new_symbol"})
    pair63 = old63[["signal_date", "old_symbol"]].merge(
        new63[["signal_date", "new_symbol"]], on="signal_date", how="outer")
    report[dataset] = {"days": int(ref.signal_date.nunique()),
                       "candidate_rows": len(ref), "labeled_rows": int(known.sum()),
                       "metrics": metrics,
                       "coarse63_old_new_top1_disagreements": int(
                           (~pair63.old_symbol.eq(pair63.new_symbol)).sum())}
    all_picks = pd.concat([p.assign(variant=name) for name, p in picks.items()],
                          ignore_index=True)
    all_picks.to_csv(ROOT / f"binning_variant_top1_{dataset}.csv", index=False)
(ROOT / "binning_variant_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))

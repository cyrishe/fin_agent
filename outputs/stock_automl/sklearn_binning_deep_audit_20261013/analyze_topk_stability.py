"""Compare two sklearn versions' class-priority Top-K on frozen scores."""
from pathlib import Path
import json

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
K_VALUES = (1, 2, 3, 5)
SHOCK_DAYS = ("2026-05-28", "2026-06-09", "2026-06-10", "2026-07-15")


def ranking(path):
    scores = pd.read_csv(path, dtype={"signal_date": str, "symbol6": str})
    all_days = sorted(scores.signal_date.unique())
    eligible = scores.loc[scores.predicted_class.isin(("ge3", "1to3"))].copy()
    eligible["priority"] = eligible.predicted_class.map({"ge3": 0, "1to3": 1})
    eligible["score"] = eligible.p_ge3.where(
        eligible.priority.eq(0), eligible.p_ge3 + eligible.p_1to3)
    eligible = eligible.sort_values(
        ["signal_date", "priority", "score", "symbol6"],
        ascending=[True, True, False, True])
    eligible["rank"] = eligible.groupby("signal_date").cumcount() + 1
    eligible["return_0940"] = eligible.close_40 / eligible.signal_price - 1
    first_five = eligible.loc[eligible["rank"].le(5)]
    if first_five.return_0940.isna().any():
        raise ValueError(f"Missing selected return: {path}")
    return all_days, {day: group for day, group in first_five.groupby("signal_date")}


def summary_stats(values):
    return {"mean": float(np.mean(values)),
            "daily_standard_deviation": float(np.std(values, ddof=1))}


report = {}
all_daily = []
for cohort, file_prefix in (("feb_jul", "scores_sklearn_"),
                            ("aug_oct", "scores_aug_oct_sklearn_")):
    days_old, old = ranking(ROOT / f"{file_prefix}171.csv.gz")
    days_new, new = ranking(ROOT / f"{file_prefix}191.csv.gz")
    if days_old != days_new:
        raise ValueError(f"Different prediction days: {cohort}")
    rows = []
    for day in days_old:
        a = old.get(day)
        b = new.get(day)
        symbols_a = a.symbol6.tolist() if a is not None else []
        symbols_b = b.symbol6.tolist() if b is not None else []
        row = {
            "cohort": cohort, "signal_date": day,
            "old_count_up_to5": len(symbols_a), "new_count_up_to5": len(symbols_b),
            "old_top1": symbols_a[0] if symbols_a else "",
            "new_top1": symbols_b[0] if symbols_b else "",
            "old_top5": "|".join(symbols_a), "new_top5": "|".join(symbols_b),
            "top1_changed": symbols_a[:1] != symbols_b[:1],
            "top5_overlap": len(set(symbols_a) & set(symbols_b)),
            "old_top1_in_new_top5": bool(symbols_a and symbols_a[0] in symbols_b),
            "new_top1_in_old_top5": bool(symbols_b and symbols_b[0] in symbols_a),
            "same_top5_set": set(symbols_a) == set(symbols_b),
        }
        for version, frame in (("old", a), ("new", b)):
            for k in K_VALUES:
                chosen = frame.loc[frame["rank"].le(k)] if frame is not None else None
                row[f"{version}_k{k}_return"] = (
                    float(chosen.return_0940.mean()) if chosen is not None else 0.0)
        rows.append(row)
    daily = pd.DataFrame(rows)
    saved = pd.read_csv(
        ROOT / ("top1_paired.csv" if cohort == "feb_jul" else "top1_paired_aug_oct.csv"),
        dtype={"signal_date": str, "symbol6_171": str, "symbol6_191": str})
    merged = daily.merge(saved[["signal_date", "symbol6_171", "symbol6_191"]],
                         on="signal_date", how="left", validate="one_to_one")
    for version, name in (("old", "symbol6_171"), ("new", "symbol6_191")):
        if not merged[f"{version}_top1"].fillna("").eq(merged[name].fillna("")).all():
            raise ValueError(f"Top1 mismatch with frozen comparison: {cohort}/{version}")
    active = daily.loc[daily.old_count_up_to5.gt(0) | daily.new_count_up_to5.gt(0)]
    full = daily.loc[daily.old_count_up_to5.eq(5) & daily.new_count_up_to5.eq(5)]
    changed = daily.loc[daily.top1_changed]
    cohort_result = {
        "prediction_days": int(len(daily)),
        "days_with_any_eligible_candidate": int(len(active)),
        "days_with_full_top5_both_versions": int(len(full)),
        "full_top5_mean_overlap": float(full.top5_overlap.mean()),
        "full_top5_median_overlap": float(full.top5_overlap.median()),
        "full_top5_exact_same_set_days": int(full.same_top5_set.sum()),
        "top1_changed_days": int(len(changed)),
        "changed_days_both_top1_in_other_top5": int((
            changed.old_top1_in_new_top5 & changed.new_top1_in_old_top5).sum()),
        "changed_days_old_top1_outside_new_top5": int((
            ~changed.old_top1_in_new_top5).sum()),
        "changed_days_new_top1_outside_old_top5": int((
            ~changed.new_top1_in_old_top5).sum()),
        "changed_days_exact_same_top5_set": int(changed.same_top5_set.sum()),
        "changed_days_mean_absolute_version_return_difference": {
            str(k): float((changed[f"new_k{k}_return"] -
                           changed[f"old_k{k}_return"]).abs().mean())
            for k in K_VALUES},
        "active_day_portfolio_returns": {},
    }
    for version in ("old", "new"):
        cohort_result["active_day_portfolio_returns"][version] = {
            str(k): summary_stats(active[f"{version}_k{k}_return"].to_numpy())
            for k in K_VALUES}
    for day in SHOCK_DAYS:
        if day in set(daily.signal_date):
            row = daily.loc[daily.signal_date.eq(day)].iloc[0]
            cohort_result.setdefault("shock_days", {})[day] = {
                "top5_overlap": int(row.top5_overlap),
                "old_top5": row.old_top5,
                "new_top5": row.new_top5,
                "old_top1_return": float(row.old_k1_return),
                "new_top1_return": float(row.new_k1_return),
                "old_equal_weight_up_to5_return": float(row.old_k5_return),
                "new_equal_weight_up_to5_return": float(row.new_k5_return),
            }
    report[cohort] = cohort_result
    all_daily.append(daily)

pd.concat(all_daily, ignore_index=True).to_csv(ROOT / "topk_stability_daily.csv", index=False)
(ROOT / "topk_stability_summary.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))

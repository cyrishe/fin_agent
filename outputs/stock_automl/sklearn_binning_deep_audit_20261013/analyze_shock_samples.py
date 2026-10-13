"""Trace rows moved by old/new bin edges on large Top1 disagreement days."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.mk_minute_archive import day_files, trading_day
from scripts.run_automl_four_class_pipeline import select_training_samples

ROOT = Path(__file__).resolve().parent
DAYS = ("2026-05-28", "2026-06-09", "2026-06-10", "2026-07-15")
dates = [trading_day(p) for p in day_files([Path("outputs/stock_automl/mk_1m_compact")])]
rows = pd.read_csv(
    "outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz",
    dtype={"signal_date": str, "next_date": str, "symbol6": str},
    float_precision="round_trip")
paired_top1 = pd.read_csv(ROOT / "top1_paired.csv",
                          dtype={"symbol6_171": str, "symbol6_191": str})
percentiles = np.linspace(0, 100, 256)[1:-1]
summary = []
details = []
for day in DAYS:
    i = dates.index(day)
    train = select_training_samples(rows, dates[i-20:i]).copy()
    train["second_high_return"] = train.second_high / train.signal_price - 1
    train["return_0940"] = train.close_40 / train.signal_price - 1
    train["class_ge1"] = train.second_high_return >= .01
    train["class_ge3"] = train.second_high_return >= .03
    moved_any = np.zeros(len(train), bool)
    per_feature = {}
    picks = paired_top1[paired_top1.signal_date.eq(day)].iloc[0]
    chosen = {str(picks[k]) for k in ("symbol6_171", "symbol6_191")
              if pd.notna(picks[k])}
    current = rows[rows.signal_date.eq(day) & rows.symbol6.isin(chosen)]
    direct_candidate_changes = {code: [] for code in chosen}
    for feature in FEATURES[:4]:
        x = train[feature].to_numpy(float)
        old = np.percentile(x, percentiles, method="midpoint")
        new = np.unique(np.percentile(x, percentiles, method="averaged_inverted_cdf"))
        old_bin = np.searchsorted(old, x, side="left")
        new_bin = np.searchsorted(new, x, side="left")
        moved = old_bin != new_bin
        moved_any |= moved
        per_feature[feature] = {
            "changed_rows": int(moved.sum()),
            "moved_up_bins": int((new_bin > old_bin).sum()),
            "moved_down_bins": int((new_bin < old_bin).sum()),
            "moved_ge1_rate": float(train.class_ge1.to_numpy()[moved].mean()),
            "moved_ge3_rate": float(train.class_ge3.to_numpy()[moved].mean()),
            "unmoved_ge1_rate": float(train.class_ge1.to_numpy()[~moved].mean()),
            "unmoved_ge3_rate": float(train.class_ge3.to_numpy()[~moved].mean()),
        }
        train[f"old_bin_{feature}"] = old_bin
        train[f"new_bin_{feature}"] = new_bin
        train[f"moved_{feature}"] = moved
        for _, candidate in current.iterrows():
            if np.searchsorted(old, float(candidate[feature]), side="left") != \
                    np.searchsorted(new, float(candidate[feature]), side="left"):
                direct_candidate_changes[candidate.symbol6].append(feature)
    moved = train.loc[moved_any].copy()
    moved["prediction_day"] = day
    columns = ["prediction_day", "signal_date", "symbol6", "second_high_return",
               "return_0940", "class_ge1", "class_ge3", *FEATURES,
               *[f"old_bin_{f}" for f in FEATURES[:4]],
               *[f"new_bin_{f}" for f in FEATURES[:4]],
               *[f"moved_{f}" for f in FEATURES[:4]]]
    details.append(moved[columns])
    summary.append({
        "prediction_day": day, "training_rows": len(train),
        "moved_any_rows": int(moved_any.sum()),
        "moved_any_ge1_rate": float(train.class_ge1.to_numpy()[moved_any].mean()),
        "unmoved_ge1_rate": float(train.class_ge1.to_numpy()[~moved_any].mean()),
        "moved_any_ge3_rate": float(train.class_ge3.to_numpy()[moved_any].mean()),
        "unmoved_ge3_rate": float(train.class_ge3.to_numpy()[~moved_any].mean()),
        "moved_by_training_day": moved.signal_date.value_counts().sort_index().to_dict(),
        "top1_candidate_direct_bin_changes": direct_candidate_changes,
        "by_feature": per_feature,
    })
pd.concat(details, ignore_index=True).to_csv(ROOT / "shock_moved_samples.csv.gz", index=False)
(ROOT / "shock_moved_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False, indent=2))

"""Exploratory sensitivity comparison for the fixed 2026 September four-class study."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.audit_automl_four_class_decisions import BASE, class_priority_fallback
from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_second_high_four_class import four_class, models
from scripts.experiment_automl_second_high_regression import SECOND_HIGHS


ROOT = Path("docs/stock_automl_runs")
OUT = ROOT / "20261010_historical_st_8to9_revalidation"
old = pd.read_csv(DATA, dtype={"symbol6": str})
clean = pd.read_csv("outputs/stock_automl/historical_st_8to9/exact_candidates_without_historical_st.csv.gz",
                    dtype={"symbol6": str})
prices = pd.read_csv(SECOND_HIGHS, dtype={"symbol6": str})
old = old.merge(prices[["next_date", "symbol6", "second_high"]],
                on=["next_date", "symbol6"], validate="one_to_one")
old["class"] = four_class(old.second_high / old.entry_1440 - 1)
clean_keys = set(zip(clean.signal_date, clean.symbol6))
old["kept"] = [(day, code) in clean_keys for day, code in
               zip(old.signal_date, old.symbol6)]
dates = sorted(old.signal_date.unique())
baseline = pd.read_csv(ROOT / "20261010_second_high_four_class_weighted/daily_top2.csv",
                       dtype={"symbol6": str})
baseline = baseline[(baseline.model.eq("unweighted")) &
                    (baseline.selection_rule.eq("class_priority_fallback"))]
old_predictions = pd.read_csv(
    ROOT / "20261010_second_high_four_class/predictions.csv.gz",
    dtype={"symbol6": str})
old_predictions = old_predictions[old_predictions.model.eq("small_boosted_tree")]
old_predictions = old_predictions[old_predictions.apply(
    lambda x: (x.signal_date, x.symbol6) in clean_keys, axis=1)]
exits = pd.read_csv(BASE, dtype={"symbol6": str})[[
    "signal_date", "symbol6", "close_40"]]
if exits.duplicated(["signal_date", "symbol6"]).any():
    raise ValueError("Duplicate minute exit price")

results = []
for seed in (11, 22, 33):
    scored = []
    for fold in range(20, len(dates)):
        train = old[old.signal_date.isin(dates[fold-20:fold])]
        test = old[old.signal_date.eq(dates[fold]) & old.kept]
        removed = int((~train.kept).sum())
        chosen = np.random.default_rng(seed + 1000*fold).choice(
            train.index.to_numpy(), size=removed, replace=False)
        randomized_train = train.drop(index=chosen)
        model = models()["small_boosted_tree"]
        model.fit(randomized_train[list(FEATURES)],
                  randomized_train["class"].astype(str))
        labels = list(model.classes_)
        probability = model.predict_proba(test[list(FEATURES)])
        frame = test[["signal_date", "next_date", "symbol6", "name",
                      "entry_1440"]].copy()
        for label in ("lt0", "0to1", "1to3", "ge3"):
            frame[f"p_{label}"] = probability[:, labels.index(label)]
        frame["predicted_class"] = np.asarray(labels)[probability.argmax(axis=1)]
        scored.append(frame)
    scored = pd.concat(scored, ignore_index=True)
    picks = class_priority_fallback(scored)
    picks = picks.merge(exits, on=["signal_date", "symbol6"],
                        validate="one_to_one")
    picks["return_0940_pct"] = (picks.close_40/picks.entry_1440 - 1)*100
    merged = scored.merge(old_predictions[["signal_date", "symbol6",
        "p_ge3", "predicted_class"]], on=["signal_date", "symbol6"],
        suffixes=("_random", "_old"), validate="one_to_one")
    result = {"seed": seed, "test_rows": len(scored),
              "selected_trades": len(picks),
              "selection_days": int(picks.signal_date.nunique()),
              "argmax_flips": int(merged.predicted_class_random.ne(
                  merged.predicted_class_old).sum()),
              "median_abs_p_ge3_change_pp": float(
                  (merged.p_ge3_random-merged.p_ge3_old).abs().median()*100),
              "top1_overlap_with_original": len(set(zip(
                  picks.loc[picks.selection_rank.eq(1), "signal_date"],
                  picks.loc[picks.selection_rank.eq(1), "symbol6"])) &
                  set(zip(baseline.loc[baseline.selection_rank.eq(1), "signal_date"],
                          baseline.loc[baseline.selection_rank.eq(1), "symbol6"]))),
              "top2_overlap_with_original": len(set(zip(
                  picks.signal_date, picks.symbol6)) &
                  set(zip(baseline.signal_date, baseline.symbol6))),
              "top1_mean_0940_pct": float(picks.loc[
                  picks.selection_rank.eq(1), "return_0940_pct"].mean()),
              "top2_mean_0940_pct": float(picks.return_0940_pct.mean())}
    results.append(result)
    print(result, flush=True)

actual_picks = pd.read_csv(
    ROOT / "20261010_second_high_four_class_weighted_historical_st/daily_top2.csv",
    dtype={"symbol6": str})
actual_picks = actual_picks[(actual_picks.model.eq("unweighted")) &
                            (actual_picks.selection_rule.eq("class_priority_fallback"))]
actual_trades = pd.read_csv(
    ROOT / "20261010_second_high_four_class_weighted_historical_st/exit_trades.csv",
    dtype={"symbol6": str})
actual_trades = actual_trades[(actual_trades.model.eq("unweighted")) &
                              (actual_trades.selection_rule.eq("class_priority_fallback")) &
                              (actual_trades.exit_strategy.eq("always_0940"))]
actual = {
    "top1_overlap_with_original": len(set(zip(
        actual_picks.loc[actual_picks.selection_rank.eq(1), "signal_date"],
        actual_picks.loc[actual_picks.selection_rank.eq(1), "symbol6"])) &
        set(zip(baseline.loc[baseline.selection_rank.eq(1), "signal_date"],
                baseline.loc[baseline.selection_rank.eq(1), "symbol6"]))),
    "top2_overlap_with_original": len(set(zip(
        actual_picks.signal_date, actual_picks.symbol6)) &
        set(zip(baseline.signal_date, baseline.symbol6))),
    "top1_mean_0940_pct": float(actual_trades.loc[
        actual_trades.selection_rank.eq(1), "exit_return"].mean()*100),
    "top2_mean_0940_pct": float(actual_trades.exit_return.mean()*100),
}
summary = {"method": "For each original 20-day training fold, delete the same number of randomly selected training rows as the historical-ST filter; score the common ST-free test candidates. Three fixed seeds. This is a sensitivity diagnostic, not a model evaluation or confidence interval.",
           "random_deletions": results, "actual_st_deletion": actual}
(OUT / "random_deletion_sensitivity.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

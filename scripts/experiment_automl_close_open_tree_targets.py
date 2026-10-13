"""Refit the original seven-factor tree architecture under four target definitions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits

from scripts.analyze_automl_cost_robustness import net_return
from scripts.build_automl_close_open_sequences import DATA
from scripts.experiment_automl_close_open_screen_targets import ROOT, TARGETS, as_target, metrics
from scripts.experiment_automl_second_high_four_class import four_class
from scripts.research_automl_close_open_objectives import FEATURES


PARAMS = dict(max_iter=60, learning_rate=.05, max_leaf_nodes=7,
              min_samples_leaf=100, l2_regularization=5,
              early_stopping=False, random_state=42)


def select_top1(model: HistGradientBoostingClassifier, candidates: pd.DataFrame) -> int | None:
    x = candidates[list(FEATURES)]
    probabilities = model.predict_proba(x)
    labels = model.classes_
    predicted = labels[probabilities.argmax(axis=1)]
    eligible = np.isin(predicted, ["ge3", "1to3"])
    if not eligible.any():
        return None
    p3 = probabilities[:, list(labels).index("ge3")] if "ge3" in labels else np.zeros(len(x))
    p1 = probabilities[:, list(labels).index("1to3")] if "1to3" in labels else np.zeros(len(x))
    priority = np.where(predicted == "ge3", 0, 1)
    within = np.where(predicted == "ge3", p3, p3+p1)
    order = np.lexsort((candidates.symbol6.to_numpy(), -within, priority))
    return next((int(i) for i in order if eligible[i]), None)


def run() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    source = DATA / "candidate_sequence_targets.csv.gz"
    rows = pd.read_csv(source, dtype={"symbol6": str, "period": str})
    rows["mean_openclose40"] = (rows.open_40 + rows.close_40_raw)/2
    picks, folds = [], []
    with threadpool_limits(limits=1):
        for period, group in rows.groupby("period", sort=False):
            dates = sorted(group.signal_date.unique())
            by_day = {day: part for day, part in group.groupby("signal_date", sort=False)}
            for i, day in enumerate(dates):
                if i < 24:
                    continue
                test = by_day[day]
                if not test.actual.notna().any():
                    continue
                train = pd.concat([by_day[d] for d in dates[i-20:i]], ignore_index=True)
                if train.next_date.max() > day:
                    raise ValueError("Immature training label")
                for target in TARGETS:
                    labeled = as_target(train, target)
                    labeled = labeled[labeled.actual.notna()]
                    if labeled.signal_date.nunique() != 20:
                        raise ValueError(f"Missing training labels for {target}")
                    y = four_class(labeled.actual).astype(str)
                    model = HistGradientBoostingClassifier(**PARAMS)
                    model.fit(labeled[list(FEATURES)], y)
                    k = select_top1(model, test)
                    chosen = test.iloc[k] if k is not None else None
                    price = (chosen.close_40_raw if target == "close" else
                             chosen.max_high if target == "max_high" else
                             chosen.second_high if target == "second_high" else
                             (chosen.open_40+chosen.close_40_raw)/2) if chosen is not None else None
                    picks.append({"period": period, "signal_date": day,
                                  "model": f"tree_{target}", "target": target,
                                  "symbol6": chosen.symbol6 if chosen is not None else "",
                                  "traded": chosen is not None,
                                  "return": chosen.actual if chosen is not None else 0.,
                                  "target_return": (price/chosen.signal_price-1)
                                  if chosen is not None else 0.})
                folds.append({"period": period, "signal_date": day,
                              "train_start": dates[i-20], "train_end": dates[i-1],
                              "last_label_date": train.next_date.max()})
                if i % 25 == 0:
                    print(f"Tree target fits {period} {day}: {i+1}/{len(dates)}", flush=True)
    results = pd.DataFrame(picks)
    summary, monthly = metrics(results)
    results["month"] = results.signal_date.str[:7]
    results["net"] = net_return(results["return"], 50_000, .00025)
    results.loc[~results.traded, "net"] = 0.
    results.to_csv(ROOT / "tree_target_picks.csv", index=False)
    summary.to_csv(ROOT / "tree_target_summary.csv", index=False)
    monthly.to_csv(ROOT / "tree_target_monthly.csv", index=False)
    pd.DataFrame(folds).to_csv(ROOT / "tree_target_folds.csv", index=False)
    (ROOT / "tree_target_manifest.json").write_text(json.dumps({
        "source_path": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "parameters": PARAMS,
        "original_saved_model": "untouched; this is a fresh refit with the same settings",
        "selection": "predicted >=3 class priority, else predicted 1-3 class, Top1 or cash",
        "realized_exit": "T+1 09:40 minute close for every target",
        "sklearn_version": sklearn.__version__,
    }, indent=2) + "\n")
    print(summary[["period", "model", "days", "trades", "unknown", "net_daily_pct",
                   "positive_months", "months"]].to_string(index=False))


if __name__ == "__main__":
    run()

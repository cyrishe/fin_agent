"""Evaluate continuous realized second-high returns by predicted rank."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


OUT = Path("docs/stock_automl_runs/20261009_second_high_regression")
PREDICTIONS = OUT / "rolling_predictions.csv.gz"


def main():
    scored = pd.read_csv(PREDICTIONS, dtype={"symbol6": str})
    rows = []
    daily_rows = []
    for name, frame in scored.groupby("model"):
        if name == "训练均值":
            # Its within-day predictions are tied; a top-k list would be arbitrary.
            continue
        daily_pool = frame.groupby("signal_date").second_high_return.mean()*100
        for k in (1, 2, 5, 10):
            selected = frame[frame["rank"].le(k)]
            daily_selected = selected.groupby("signal_date").second_high_return.mean()*100
            daily_uplift = daily_selected-daily_pool
            rows.append({"model": name, "daily_top_k": k,
                         "selected": len(selected),
                         "mean_actual_return_pct": float(selected.second_high_return.mean()*100),
                         "median_actual_return_pct": float(selected.second_high_return.median()*100),
                         "below_zero": int(selected.second_high_return.lt(0).sum()),
                         "daily_pool_mean_pct": float(daily_pool.mean()),
                         "daily_mean_uplift_pct_points": float(daily_uplift.mean()),
                         "days_above_pool": int(daily_uplift.gt(0).sum())})
            if k == 2:
                daily_rows.extend({"model": name, "signal_date": day,
                                   "top2_actual_mean_pct": float(daily_selected.loc[day]),
                                   "pool_actual_mean_pct": float(daily_pool.loc[day]),
                                   "difference_pct_points": float(daily_uplift.loc[day])}
                                  for day in daily_pool.index)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(OUT / "continuous_ranking_by_k.csv", index=False)
    pd.DataFrame(daily_rows).to_csv(OUT / "continuous_ranking_daily_top2.csv",
                                    index=False)
    tree = scored[scored.model.eq("小型提升树")].copy()
    tree["predicted_decile"] = pd.qcut(tree.predicted_pct, 10, labels=False,
                                        duplicates="drop")+1
    deciles = tree.groupby("predicted_decile").agg(
        count=("symbol6", "size"),
        mean_predicted_pct=("predicted_pct", "mean"),
        mean_actual_pct=("second_high_return", lambda x: x.mean()*100),
        median_actual_pct=("second_high_return", lambda x: x.median()*100))
    deciles.to_csv(OUT / "boosted_tree_score_deciles.csv")
    differences = (pd.DataFrame(daily_rows)
                   .query("model == '小型提升树'").difference_pct_points.to_numpy())
    rng = np.random.default_rng(20261009)
    bootstrap = differences[rng.integers(0, len(differences),
                                         size=(50_000, len(differences)))].mean(axis=1)
    (OUT / "continuous_ranking_summary.json").write_text(json.dumps({
        "evaluation": "realized second-high return / 14:40 price - 1, no binary threshold",
        "models": rows,
        "boosted_tree_top2_day_bootstrap_95pct_uplift_ci":
            np.quantile(bootstrap, [.025, .975]).tolist(),
        "bootstrap_seed": 20261009,
        "score_deciles": deciles.reset_index().to_dict("records"),
    }, ensure_ascii=False, indent=2) + "\n")
    print(metrics.to_string(index=False))
    print(deciles.to_string())


if __name__ == "__main__":
    main()

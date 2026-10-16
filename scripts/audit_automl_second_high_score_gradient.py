"""Check whether predicted second-high returns rank actual morning highs."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr


OUT = Path("docs/stock_automl_runs/20261009_second_high_regression")


def main():
    data = pd.read_csv(OUT / "rolling_predictions.csv.gz", dtype={"symbol6": str})
    data["actual_max_pct"] = (data.max_high/data.entry_1440-1)*100
    data["actual_second_pct"] = data.second_high_return*100
    rows, daily = [], []
    for model, frame in data.groupby("model"):
        if model == "训练均值":
            continue  # Every same-day prediction is tied.
        daily_spearman = {target: [float(spearmanr(day.predicted_pct,
                                                    day[target]).statistic)
                                    for _, day in frame.groupby("signal_date")]
                          for target in ("actual_max_pct", "actual_second_pct")}
        rows.append({"model": model,
                     "global_max_spearman": float(spearmanr(frame.predicted_pct,
                                                              frame.actual_max_pct).statistic),
                     "global_max_pearson": float(pearsonr(frame.predicted_pct,
                                                            frame.actual_max_pct).statistic),
                     "within_day_max_spearman_mean": float(np.mean(
                         daily_spearman["actual_max_pct"])),
                     "global_second_spearman": float(spearmanr(frame.predicted_pct,
                                                                 frame.actual_second_pct).statistic),
                     "within_day_second_spearman_mean": float(np.mean(
                         daily_spearman["actual_second_pct"]))})
        if model != "小型提升树":
            continue
        for day, group in frame.groupby("signal_date"):
            n = len(group)
            tail_n = int(np.ceil(n*.1))
            top = group[group["rank"].le(tail_n)]
            bottom = group[group["rank"].gt(n-tail_n)]
            for section, part in (("预测最高10%", top), ("预测最低10%", bottom),
                                  ("全体", group)):
                daily.append({"signal_date": day, "section": section,
                              "count": len(part),
                              "max_mean_pct": float(part.actual_max_pct.mean()),
                              "max_median_pct": float(part.actual_max_pct.median()),
                              "second_mean_pct": float(part.actual_second_pct.mean()),
                              "max_below_zero": int(part.actual_max_pct.lt(0).sum()),
                              "actual_max_top_tenth": int(part.actual_max_pct.ge(
                                  group.actual_max_pct.quantile(.9)).sum())})
    pd.DataFrame(rows).to_csv(OUT / "score_actual_correlations.csv", index=False)
    daily_frame = pd.DataFrame(daily)
    daily_frame.to_csv(OUT / "score_tail_daily.csv", index=False)
    # Weight each stock equally for tail return summaries; use day-level comparison
    # for inference, so large candidate days do not determine the confidence interval.
    combined = {}
    for section, group in daily_frame.groupby("section"):
        total = int(group["count"].sum())
        stock_frame = data[data.model.eq("小型提升树")].copy()
        if section != "全体":
            selected = []
            for day, part in stock_frame.groupby("signal_date"):
                tail_n = int(np.ceil(len(part)*.1))
                selected.append(part[part["rank"].le(tail_n) if section == "预测最高10%"
                                     else part["rank"].gt(len(part)-tail_n)])
            stock_frame = pd.concat(selected)
        combined[section] = {
            "stocks": total,
            "max_mean_pct": float(stock_frame.actual_max_pct.mean()),
            "max_median_pct": float(stock_frame.actual_max_pct.median()),
            "second_mean_pct": float(stock_frame.actual_second_pct.mean()),
            "max_below_zero_share": float(stock_frame.actual_max_pct.lt(0).mean()),
            "actual_max_top_tenth_share": float(group.actual_max_top_tenth.sum()/total),
        }
    top = daily_frame[daily_frame.section.eq("预测最高10%")].set_index("signal_date")
    bottom = daily_frame[daily_frame.section.eq("预测最低10%")].set_index("signal_date")
    difference = (top.max_mean_pct-bottom.max_mean_pct).to_numpy()
    rng = np.random.default_rng(20261009)
    bootstrap = difference[rng.integers(0, len(difference),
                                        size=(50_000, len(difference)))].mean(axis=1)
    report = {"methods": rows, "boosted_tree_daily_tails": combined,
              "top_minus_bottom_daily_mean_max_pct_points": float(difference.mean()),
              "days_top_above_bottom": int((difference > 0).sum()),
              "top_minus_bottom_day_bootstrap_95pct_ci":
                  np.quantile(bootstrap, [.025, .975]).tolist(),
              "bootstrap_seed": 20261009}
    (OUT / "score_gradient_summary.json").write_text(json.dumps(
        report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

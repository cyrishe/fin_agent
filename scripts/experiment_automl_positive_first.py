"""Positive-first, read-only history-vs-local factor check.

The T-day final limit-up flag affects the *outcome*, never the T 14:49 input
universe.  Dates in the later partitions have already been seen in earlier
research, so these results are diagnostic rather than a blind backtest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import dotenv_values
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.audit_automl_positive_cohort import daily_history
from scripts.experiment_automl_1449_asof import build_dataset
from scripts.experiment_automl_1450_grid import read_only_db


HISTORY = ("prior_return_5", "abs_prior_return_5", "prior_amount_log_ratio_5",
           "prior_avg_bias_3", "market_breadth")
WITH_LOCAL = (*HISTORY, "day_return", "tail_return_20m")


def prepare_model_frame(asof, daily):
    calendar = sorted(daily.date.unique())
    previous = dict(zip(calendar[1:], calendar[:-1]))
    # The existing replay includes this prior-day ratio; use the same dated
    # history source as the other exploratory factors below.
    rows = asof.drop(columns=["prior_amount_ratio_5"]).copy()
    rows["previous_date"] = rows.date.map(previous)
    prior = daily[["date", "symbol6", "prior_return_5", "prior_amount_ratio_5",
                   "prior_avg_bias_3", "history_contiguous6", "history_create_time",
                   "history_update_time"]].rename(
        columns={"date": "previous_date"})
    final = daily[["date", "symbol6", "is_limit_price"]].rename(
        columns={"is_limit_price": "t_final_limit_flag"})
    rows = rows.merge(prior, on=["previous_date", "symbol6"], how="left",
                      validate="one_to_one")
    rows = rows.merge(final, on=["date", "symbol6"], how="left",
                      validate="one_to_one")
    cutoff = pd.to_datetime(rows.date.astype(str) + " 14:49:59")
    rows["prior_history_asof"] = (rows.history_contiguous6.eq(True) &
                                  pd.to_datetime(rows.history_create_time).le(cutoff) &
                                  pd.to_datetime(rows.history_update_time).le(cutoff))
    rows["abs_prior_return_5"] = rows.prior_return_5.abs()
    rows["prior_amount_log_ratio_5"] = np.log(rows.prior_amount_ratio_5.where(
        rows.prior_amount_ratio_5 > 0))
    # Today's completed daily-bar flag is a future outcome.  A limit-up day
    # is conservatively counted as non-actionable, including when high5 hit.
    rows["actionable_hit5"] = rows.hit1 & rows.t_final_limit_flag.ne(1)
    rows["actionable_hit10"] = rows.hit1_10m & rows.t_final_limit_flag.ne(1)
    quality = (rows.observed & rows.prior_history_asof & rows.t_final_limit_flag.notna() &
               rows[list(WITH_LOCAL)].replace([np.inf, -np.inf], np.nan).notna().all(axis=1))
    return rows.loc[quality].copy(), {"asof_candidates": len(rows),
                                      "observed": int(rows.observed.sum()),
                                      "model_rows": int(quality.sum()),
                                      "t_final_limit_up_in_model_rows": int((quality &
                                          rows.t_final_limit_flag.eq(1)).sum())}


def top_fraction(rows, scores, fraction):
    ranked = rows.copy()
    ranked["score"] = scores
    ranked = ranked.sort_values(["date", "score", "symbol6"],
                                ascending=[True, False, True])
    ranked["rank"] = ranked.groupby("date").cumcount() + 1
    ranked["day_size"] = ranked.groupby("date").symbol6.transform("size")
    if fraction == 0:
        return ranked[ranked["rank"].eq(1)]
    return ranked[ranked["rank"].le(np.ceil(ranked.day_size * fraction))]


def pick_metrics(picked):
    return {"rows": len(picked), "hits": int(picked.actionable_hit5.sum()),
            "rate": round(float(picked.actionable_hit5.mean()), 5) if len(picked) else None,
            "touch10_hits": int(picked.actionable_hit10.sum()),
            "near_limit_at_entry": int(picked.near_limit_at_entry.sum())}


def evaluate(rows, scores):
    target = rows.actionable_hit5
    daily_aucs = []
    for _, group in rows.assign(score=scores).groupby("date"):
        if group.actionable_hit5.nunique() == 2:
            daily_aucs.append(roc_auc_score(group.actionable_hit5, group.score))
    out = {"rows": len(rows), "days": int(rows.date.nunique()),
           "base_hits": int(target.sum()),
           "base_rate": round(float(target.mean()), 5),
           "mean_daily_auc": round(float(np.mean(daily_aucs)), 5) if daily_aucs else None}
    for name, fraction in (("top10pct", 0.10), ("top1pct", 0.01), ("top1_per_day", 0)):
        out[name] = pick_metrics(top_fraction(rows, scores, fraction))
    return out


def experiment(rows):
    partitions = {"train": rows[rows.date.le("2026-09-14")],
                  "validation_seen": rows[rows.date.between("2026-09-15", "2026-09-21")],
                  "later_seen": rows[rows.date.ge("2026-09-22")]}
    train = partitions["train"]
    train_fit = train[train.t_final_limit_flag.ne(1)]
    result = {"target": "high5>=1% relative to 14:50 price AND T final limit flag != 1",
              "feature_timing": "T-1 daily history plus T 14:30 arrived bars",
              "train_final_limit_up_excluded": len(train) - len(train_fit),
              "models": {}}
    details = []
    for name, features in (("history", HISTORY), ("history_plus_local", WITH_LOCAL)):
        model = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=500))
        model.fit(train_fit[list(features)], train_fit.actionable_hit5.astype(int))
        train_scores = model.predict_proba(train[list(features)])[:, 1]
        train_daily_max = top_fraction(train, train_scores, 0).score
        # Fixed in advance as a frequency diagnostic: roughly one top-stock
        # opportunity per five training days.  Scoring never uses the label.
        score_gate = float(train_daily_max.quantile(0.8))
        block = {"features": list(features),
                 "train_daily_max_score_gate_80pct": round(score_gate, 6),
                 "standardized_coefficients": dict(zip(features,
                     [round(float(x), 6) for x in model[-1].coef_[0]])),
                 "partitions": {}}
        for part_name, part in partitions.items():
            score = model.predict_proba(part[list(features)])[:, 1]
            metrics = evaluate(part, score)
            top_one = top_fraction(part, score, 0)
            gated = top_one[top_one.score.ge(score_gate)]
            metrics["top1_per_day_train_gate"] = pick_metrics(gated)
            metrics["top1_per_day_train_gate"]["candidate_days"] = int(part.date.nunique())
            block["partitions"][part_name] = metrics
            view = part.assign(score=score).sort_values(
                ["date", "score", "symbol6"], ascending=[True, False, True]
            ).groupby("date").head(5).copy()
            view["model"] = name
            view["partition"] = part_name
            view["rank"] = view.groupby("date").cumcount() + 1
            view["passes_train_gate"] = view.score.ge(score_gate)
            details.append(view)
        result["models"][name] = block
    return result, pd.concat(details, ignore_index=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output", default="docs/stock_automl_runs/20261008_positive_cohort/model_check.json")
    parser.add_argument("--detail-output", default="outputs/stock_automl/cohorts/positive_first_model_top5.csv")
    args = parser.parse_args()
    if not os.environ.get("SIMPLE_BI_PLATFORM_DB_URL"):
        os.environ["SIMPLE_BI_PLATFORM_DB_URL"] = dotenv_values(args.env_file).get("PLATFORM_DB_URL", "")
    with read_only_db() as conn:
        daily = daily_history(conn)
        asof, _, arrivals = build_dataset(conn)
    rows, audit = prepare_model_frame(asof, daily)
    result, detail = experiment(rows)
    result["sample_audit"] = audit
    result["arrival_audit"] = arrivals
    result["generated_at"] = datetime.now().isoformat(timespec="seconds")
    result["provenance"] = {"source": "47.94.1.2:3312/kingdomai",
                            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                            "data_snapshot_version": None}
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    detail_path = Path(args.detail_output)
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    detail[["model", "partition", "date", "symbol6", "rank", "score",
            "passes_train_gate", "actionable_hit5", "actionable_hit10",
            "high5_return", "high10_return", "t_final_limit_flag",
            "near_limit_at_entry", *WITH_LOCAL]].to_csv(detail_path, index=False)
    print(f"wrote aggregate model check: {target}")
    print(f"wrote local top-five-per-day review: {detail_path}")


if __name__ == "__main__":
    main()

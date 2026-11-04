"""Read-only positive-first audit for the next morning's first 5/10 minutes.

The T-day final limit-up flag is used only to describe a retrospective cohort.
It cannot be used to screen a live 14:49 decision.  Detailed stock rows go to
an ignored local outputs/ file; the committed report contains aggregates only.
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
from sklearn.metrics import roc_auc_score

from scripts.experiment_automl_1449_asof import (
    END, START, build_dataset, morning_outcome,
)
from scripts.experiment_automl_1450_grid import board_limit, frame, read_only_db


HISTORY = ("prior_return_1", "prior_return_3", "prior_return_5",
           "prior_amount_ratio_5", "prior_avg_bias_1", "prior_avg_bias_3")
LOCAL = ("day_return", "tail_return_20m", "tail_amount_log_ratio", "market_breadth")


def daily_history(conn):
    daily = frame(conn, """
        SELECT trade_date AS date, LEFT(stk_code,6) AS symbol6,
          close, preclose, avg_price, amount, is_limit_price,
          create_time, update_time
        FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s
    """, ("2026-07-15", END))
    daily["date"] = pd.to_datetime(daily.date)
    daily = daily.drop_duplicates(["date", "symbol6"], keep=False)
    daily = daily.sort_values(["symbol6", "date"]).copy()
    for col in ("close", "preclose", "avg_price", "amount", "is_limit_price"):
        daily[col] = pd.to_numeric(daily[col], errors="coerce")
    for col in ("create_time", "update_time"):
        daily[col] = pd.to_datetime(daily[col], errors="coerce")
    calendar = {day: index for index, day in enumerate(sorted(daily.date.unique()))}
    daily["trading_index"] = daily.date.map(calendar)
    groups = daily.groupby("symbol6", sort=False)
    daily["history_contiguous6"] = daily.trading_index.sub(
        groups.trading_index.shift(5)).eq(5)
    daily["prior_return_1"] = daily.close / daily.preclose - 1
    log_return = np.log1p(daily.prior_return_1.where(daily.prior_return_1 > -1))
    for days in (3, 5):
        daily[f"prior_return_{days}"] = np.expm1(log_return.groupby(daily.symbol6).transform(
            lambda s: s.rolling(days, min_periods=days).sum()))
    older_amount = groups.amount.shift(1)
    old_mean = older_amount.groupby(daily.symbol6).transform(
        lambda s: s.rolling(5, min_periods=5).mean())
    daily["prior_amount_ratio_5"] = daily.amount / old_mean
    daily["prior_avg_bias_1"] = daily.close / daily.avg_price.where(daily.avg_price > 0) - 1
    daily["prior_avg_bias_3"] = daily.prior_avg_bias_1.groupby(daily.symbol6).transform(
        lambda s: s.rolling(3, min_periods=3).mean())
    for source in ("create_time", "update_time"):
        lookback = pd.concat([groups[source].shift(i) for i in range(6)], axis=1)
        daily[f"history_{source}"] = lookback.max(axis=1).where(lookback.notna().all(axis=1))
    return daily


def entry_bar(conn, day):
    entry = frame(conn, """
        SELECT LEFT(stk_code,6) AS symbol6, stk_name AS name, latest_price AS entry,
          is_fallback AS entry_fallback, is_finalized AS entry_finalized
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time=%s
    """, (day, f"{day} 14:50:00"))
    entry["date"] = pd.Timestamp(day)
    return entry


def retrospective_cohort(conn, daily):
    dates = sorted(daily[daily.date.between(START, END)].date.unique())
    calendar = sorted(daily.date.unique())
    previous = dict(zip(calendar[1:], calendar[:-1]))
    next_date = dict(zip(dates[:-1], dates[1:]))
    rows = []
    missing_days = []
    for index, day in enumerate(dates[:-1], 1):
        entry = entry_bar(conn, str(day)[:10])
        if entry.empty:
            missing_days.append(str(day)[:10])
            print(f"cohort {index}/{len(dates)-1} {str(day)[:10]}: no entry bar", flush=True)
            continue
        morning = morning_outcome(conn, str(next_date[day])[:10])
        if morning.empty:
            missing_days.append(str(day)[:10])
            print(f"cohort {index}/{len(dates)-1} {str(day)[:10]}: no next-morning bars", flush=True)
            continue
        entry["next_date"] = next_date[day]
        joined = entry.merge(morning, on=["next_date", "symbol6"], how="left",
                             validate="one_to_one")
        joined["previous_date"] = previous[day]
        rows.append(joined)
        print(f"cohort {index}/{len(dates)-1} {str(day)[:10]}: {len(joined)}", flush=True)
    data = pd.concat(rows, ignore_index=True)
    final = daily[["date", "symbol6", "close", "preclose", "is_limit_price"]].rename(
        columns={"close": "t_final_close", "preclose": "t_final_preclose",
                 "is_limit_price": "t_final_limit_flag"})
    prior = daily[["date", "symbol6", "close", "avg_price", "amount", "history_contiguous6",
                   "history_create_time",
                   "history_update_time", *HISTORY]].rename(
        columns={"date": "previous_date", "close": "prior_close",
                 "avg_price": "prior_avg_price", "amount": "prior_amount"})
    data = data.merge(final, on=["date", "symbol6"], how="left", validate="one_to_one")
    data = data.merge(prior, on=["previous_date", "symbol6"], how="left",
                      validate="one_to_one")
    for col in ("entry", "entry_fallback", "entry_finalized", "open931", "high5",
                "low5", "high10", "close935", "bars5", "bars10", "volume5",
                "fallback5", "finalized5", "fallback10", "finalized10"):
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data["observed5"] = (data.entry.gt(0) & data.entry_fallback.eq(0) &
                         data.entry_finalized.eq(1) & data.bars5.eq(5) &
                         data.volume5.gt(0) & data.fallback5.eq(0) &
                         data.finalized5.eq(1) & data.high5.gt(0) &
                         data.low5.gt(0) & data.open931.gt(0))
    data["observed10"] = (data.observed5 & data.bars10.eq(10) &
                          data.fallback10.eq(0) & data.finalized10.eq(1) &
                          data.high10.gt(0))
    data["high5_return"] = (data.high5 / data.entry - 1).where(data.observed5)
    data["high10_return"] = (data.high10 / data.entry - 1).where(data.observed10)
    data["hit5"] = data.high5_return.ge(0.01)
    data["hit10"] = data.high10_return.ge(0.01)
    data["t_limit_up"] = data.t_final_limit_flag.eq(1)
    data["entry_limit_buffer"] = [board_limit(s, n) for s, n in
                                  zip(data.symbol6, data.name)]
    data["near_limit_at_entry"] = (data.entry / data.t_final_preclose - 1).abs().ge(
        data.entry_limit_buffer)
    cutoff = pd.to_datetime(data.date.astype(str) + " 14:49:59")
    data["prior_history_asof"] = (data.history_contiguous6.eq(True) &
                                  pd.to_datetime(data.history_create_time).le(cutoff) &
                                  pd.to_datetime(data.history_update_time).le(cutoff))
    return data, missing_days


def feature_contrasts(rows, features):
    out = {}
    for feature in features:
        valid = rows[["date", feature, "hit5"]].replace([np.inf, -np.inf], np.nan).dropna()
        positive = valid[valid.hit5][feature]
        negative = valid[~valid.hit5][feature]
        day_medians = valid.groupby(["date", "hit5"])[feature].median().unstack()
        day_medians = day_medians.dropna()
        deltas = day_medians[True] - day_medians[False]
        daily_auc = [roc_auc_score(group.hit5, group[feature])
                     for _, group in valid.groupby("date") if group.hit5.nunique() == 2]
        out[feature] = {"positive_median": round(float(positive.median()), 6) if len(positive) else None,
                        "negative_median": round(float(negative.median()), 6) if len(negative) else None,
                        "daily_median_delta": round(float(deltas.median()), 6) if len(deltas) else None,
                        "positive_delta_days": int(deltas.gt(0).sum()),
                        "compared_days": len(deltas),
                        "mean_within_day_auc": round(float(np.mean(daily_auc)), 5) if daily_auc else None,
                        "auc_above_half_days": int(np.sum(np.asarray(daily_auc) > 0.5))}
    return out


def within_day_quintiles(rows, feature):
    valid = rows[["date", feature, "hit5"]].replace([np.inf, -np.inf], np.nan).dropna().copy()
    valid["quintile"] = np.minimum(5, np.ceil(valid.groupby("date")[feature]
        .rank(pct=True, method="first") * 5)).astype(int)
    grouped = valid.groupby("quintile").hit5.agg(["size", "sum", "mean"])
    return [{"quintile": int(index), "rows": int(row["size"]),
             "hit5": int(row["sum"]), "rate": round(float(row["mean"]), 5)}
            for index, row in grouped.iterrows()]


def summarize(data, asof):
    excluded = data[data.observed5 & data.t_limit_up]
    cohort = data[data.observed5 & data.t_final_limit_flag.notna() & ~data.t_limit_up].copy()
    valid_history = cohort[cohort.prior_history_asof & cohort[list(HISTORY)].notna().all(axis=1)]
    asof_keys = asof[["date", "symbol6", *LOCAL]].copy()
    asof_keys["asof_eligible"] = True
    cohort = cohort.merge(asof_keys, on=["date", "symbol6"], how="left",
                          validate="one_to_one")
    cohort["asof_eligible"] = cohort.asof_eligible.eq(True)
    asof_nonlimit = cohort[cohort.asof_eligible & cohort.prior_history_asof].copy()
    daily = cohort.groupby("date").agg(stocks=("symbol6", "size"), hit5=("hit5", "sum"),
                                       hit10=("hit10", "sum"),
                                       asof_eligible=("asof_eligible", "sum"))
    return cohort, {
        "source_signal_days": int(data.date.nunique()),
        "entry_rows": len(data),
        "observed_first5": int(data.observed5.sum()),
        "observed_first10": int(data.observed10.sum()),
        "excluded_t_final_limit_up": len(excluded),
        "excluded_t_final_limit_up_hit5": int(excluded.hit5.sum()),
        "non_limit_observed": len(cohort),
        "non_limit_hit5": int(cohort.hit5.sum()),
        "non_limit_hit10": int(cohort.hit10.sum()),
        "non_limit_near_limit_at_entry": int(cohort.near_limit_at_entry.sum()),
        "asof_non_limit_observed": len(asof_nonlimit),
        "asof_non_limit_hit5": int(asof_nonlimit.hit5.sum()),
        "asof_non_limit_hit10": int(asof_nonlimit.hit10.sum()),
        "daily": [{"date": str(day)[:10], **{k: int(v) for k, v in row.items()}}
                  for day, row in daily.iterrows()],
        "historical_feature_contrasts": feature_contrasts(valid_history, HISTORY),
        "prior_return_5_within_day_quintiles": within_day_quintiles(
            valid_history, "prior_return_5"),
        "asof_local_feature_contrasts": feature_contrasts(asof_nonlimit, LOCAL),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output", default="docs/stock_automl_runs/20261008_positive_cohort/summary.json")
    parser.add_argument("--positive-csv", default="outputs/stock_automl/cohorts/positive5_10_non_limit.csv")
    parser.add_argument("--cohort-csv", default="outputs/stock_automl/cohorts/cohort_non_limit.csv")
    args = parser.parse_args()
    if not os.environ.get("SIMPLE_BI_PLATFORM_DB_URL"):
        os.environ["SIMPLE_BI_PLATFORM_DB_URL"] = dotenv_values(args.env_file).get("PLATFORM_DB_URL", "")
    with read_only_db() as conn:
        daily = daily_history(conn)
        retrospective, missing_days = retrospective_cohort(conn, daily)
        asof, _, arrival = build_dataset(conn)
    cohort, result = summarize(retrospective, asof)
    result["missing_signal_days"] = missing_days
    result["arrival_audit"] = arrival
    result["generated_at"] = datetime.now().isoformat(timespec="seconds")
    result["provenance"] = {"source": "47.94.1.2:3312/kingdomai",
                            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                            "data_snapshot_version": None}
    positive = cohort[cohort.hit5 | cohort.hit10]
    columns = ["date", "next_date", "symbol6", "name", "entry", "high5", "high10",
               "hit5", "hit10", "high5_return", "high10_return",
               "prior_close", "prior_avg_price", "prior_amount",
               "near_limit_at_entry", "asof_eligible", "prior_history_asof", *HISTORY]
    csv_path = Path(args.positive_csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    positive[columns].sort_values(["date", "symbol6"]).to_csv(csv_path, index=False)
    result["positive_rows_written_locally"] = len(positive)
    cohort_path = Path(args.cohort_csv)
    cohort_path.parent.mkdir(parents=True, exist_ok=True)
    cohort_columns = ["date", "next_date", "symbol6", "name", "entry", "high5", "high10",
                      "hit5", "hit10", "high5_return", "high10_return",
                      "prior_close", "prior_avg_price", "prior_amount",
                      "near_limit_at_entry", "asof_eligible", "prior_history_asof",
                      *HISTORY, *LOCAL]
    cohort[cohort_columns].sort_values(["date", "symbol6"]).to_csv(cohort_path, index=False)
    result["cohort_rows_written_locally"] = len(cohort)
    json_path = Path(args.output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote local positive rows: {csv_path} ({len(positive)})")
    print(f"wrote local comparison cohort: {cohort_path} ({len(cohort)})")
    print(f"wrote aggregate report: {json_path}")


if __name__ == "__main__":
    main()

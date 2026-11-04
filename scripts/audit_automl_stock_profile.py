"""Read-only stock-profile audit for the positive-first morning cohort.

Profiles end at T-1 and require their current source versions to have arrived
by T 14:49:59.  Stock rows stay in ignored local outputs; reports are aggregate.
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

from scripts.experiment_automl_1450_grid import frame, read_only_db


PROFILE = ("total_mv_100m_cny", "free_float_mv_100m_cny", "avg_volume_5_million",
           "avg_volume_10_million", "avg_amount_5_million", "avg_amount_10_million",
           "avg_turn_5_pct", "avg_turn_10_pct", "volume_ratio_5_10")
BASE = ("prior_return_5", "abs_prior_return_5", "prior_amount_log_ratio_5",
        "prior_avg_bias_3", "market_breadth")
ADDED = ("log_total_mv", "log_avg_volume_10", "avg_turn_5_pct",
         "log_volume_ratio_5_10")


def load_sources(conn):
    price = frame(conn, """
        SELECT trade_date AS date, LEFT(stk_code,6) AS symbol6,
          volume, amount, turn_ratio, create_time, update_time
        FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s
    """, ("2026-07-15", "2026-09-30"))
    value = frame(conn, """
        SELECT trade_date AS date, LEFT(stk_code,6) AS symbol6,
          total_mv, free_float_mv, create_time, update_time
        FROM kcrp_stock_pricevaluate WHERE trade_date BETWEEN %s AND %s
    """, ("2026-07-15", "2026-09-29"))
    return price, value


def build_profile(price, value):
    price = price.copy()
    value = value.copy()
    for source in (price, value):
        source["date"] = pd.to_datetime(source.date)
        source["symbol6"] = source.symbol6.astype(str).str.zfill(6)
        source["create_time"] = pd.to_datetime(source.create_time, errors="coerce")
        source["update_time"] = pd.to_datetime(source.update_time, errors="coerce")
        source.drop_duplicates(["date", "symbol6"], keep=False, inplace=True)
    for column in ("volume", "amount", "turn_ratio"):
        price[column] = pd.to_numeric(price[column], errors="coerce")
    for column in ("total_mv", "free_float_mv"):
        value[column] = pd.to_numeric(value[column], errors="coerce")
    calendar = sorted(price.date.unique())
    next_date = dict(zip(calendar[:-1], calendar[1:]))
    trading_index = {day: index for index, day in enumerate(calendar)}
    price = price.sort_values(["symbol6", "date"]).copy()
    price["trading_index"] = price.date.map(trading_index)
    groups = price.groupby("symbol6", sort=False)
    traded = price.volume.gt(0) & price.amount.gt(0)
    price["all_traded_10"] = traded.groupby(price.symbol6).transform(
        lambda series: series.rolling(10, min_periods=10).sum()).eq(10)
    for days in (5, 10):
        price[f"contiguous_{days}"] = price.trading_index.sub(
            groups.trading_index.shift(days - 1)).eq(days - 1)
        for source, output in (("volume", "avg_volume"), ("amount", "avg_amount"),
                               ("turn_ratio", "avg_turn")):
            price[f"{output}_{days}"] = groups[source].transform(
                lambda series: series.rolling(days, min_periods=days).mean())
    for source in ("create_time", "update_time"):
        history = pd.concat([groups[source].shift(i) for i in range(10)], axis=1)
        price[f"history_{source}"] = history.max(axis=1).where(history.notna().all(axis=1))
    price["signal_date"] = pd.to_datetime(price.date.map(next_date)).astype("datetime64[ns]")
    cutoff = pd.to_datetime(price.signal_date.astype(str) + " 14:49:59", errors="coerce")
    price["price_profile_ready"] = (
        price.contiguous_10 & price.all_traded_10 & price.history_create_time.le(cutoff) &
        price.history_update_time.le(cutoff) &
        price.avg_volume_5.gt(0) & price.avg_volume_10.gt(0) &
        price.avg_amount_5.gt(0) & price.avg_amount_10.gt(0) &
        price.avg_turn_5.ge(0) & price.avg_turn_10.ge(0))
    price["avg_volume_5_million"] = price.avg_volume_5 / 1e6
    price["avg_volume_10_million"] = price.avg_volume_10 / 1e6
    price["avg_amount_5_million"] = price.avg_amount_5 / 1e6
    price["avg_amount_10_million"] = price.avg_amount_10 / 1e6
    price["avg_turn_5_pct"] = price.avg_turn_5
    price["avg_turn_10_pct"] = price.avg_turn_10
    price["volume_ratio_5_10"] = price.avg_volume_5 / price.avg_volume_10
    price.loc[~price.price_profile_ready, list(PROFILE[2:])] = np.nan
    prior = price[["signal_date", "symbol6", "date", "price_profile_ready",
                   *PROFILE[2:]]].rename(columns={"date": "profile_through_date"})

    value["signal_date"] = pd.to_datetime(value.date.map(next_date)).astype("datetime64[ns]")
    value_cutoff = pd.to_datetime(value.signal_date.astype(str) + " 14:49:59", errors="coerce")
    value["total_mv_ready"] = (value.total_mv.gt(0) &
                               value.create_time.le(value_cutoff) &
                               value.update_time.le(value_cutoff))
    value["free_float_mv_ready"] = value.total_mv_ready & value.free_float_mv.gt(0)
    value["total_mv_100m_cny"] = value.total_mv / 1e8
    value["free_float_mv_100m_cny"] = value.free_float_mv / 1e8
    value.loc[~value.total_mv_ready, "total_mv_100m_cny"] = np.nan
    value.loc[~value.free_float_mv_ready, "free_float_mv_100m_cny"] = np.nan
    value_prior = value[["signal_date", "symbol6", "date", "total_mv_ready",
                         "free_float_mv_ready", *PROFILE[:2]]].rename(
        columns={"date": "value_through_date"})
    return prior.merge(value_prior, on=["signal_date", "symbol6"], how="left",
                       validate="one_to_one")


def attach_profile(cohort, profile):
    rows = cohort.copy()
    rows["date"] = pd.to_datetime(rows.date)
    rows["symbol6"] = rows.symbol6.astype(str).str.zfill(6)
    rows = rows.merge(profile, left_on=["date", "symbol6"],
                      right_on=["signal_date", "symbol6"], how="left",
                      validate="one_to_one")
    rows["price_profile_ready"] = rows.price_profile_ready.eq(True)
    rows["total_mv_ready"] = rows.total_mv_ready.eq(True)
    rows["free_float_mv_ready"] = rows.free_float_mv_ready.eq(True)
    rows["profile_ready"] = rows.price_profile_ready & rows.total_mv_ready
    return rows


def feature_summary(rows, feature, ready):
    valid = rows.loc[rows[ready], ["date", "hit5", feature]].replace(
        [np.inf, -np.inf], np.nan).dropna()
    positive = valid[valid.hit5][feature]
    negative = valid[~valid.hit5][feature]
    aucs = [roc_auc_score(part.hit5, part[feature]) for _, part in valid.groupby("date")
            if part.hit5.nunique() == 2]
    valid = valid.copy()
    valid["quintile"] = np.minimum(5, np.ceil(valid.groupby("date")[feature].rank(
        pct=True, method="average") * 5)).astype(int)
    quintiles = valid.groupby("quintile").hit5.agg(["size", "sum", "mean"])
    return {"rows": len(valid), "positive_median": float(positive.median()),
            "negative_median": float(negative.median()),
            "positive_p10_p90": [float(positive.quantile(q)) for q in (.1, .9)],
            "mean_daily_auc": float(np.mean(aucs)),
            "days_auc_above_half": int(np.sum(np.asarray(aucs) > .5)),
            "days_compared": len(aucs),
            "within_day_quintiles": [{"quintile": int(q), "rows": int(row["size"]),
                                      "hit5": int(row["sum"]), "rate": float(row["mean"])}
                                     for q, row in quintiles.iterrows()]}


def model_frame(rows):
    data = rows[rows.asof_eligible & rows.prior_history_asof & rows.profile_ready].copy()
    data["abs_prior_return_5"] = data.prior_return_5.abs()
    data["prior_amount_log_ratio_5"] = np.log(data.prior_amount_ratio_5.where(
        data.prior_amount_ratio_5 > 0))
    data["log_total_mv"] = np.log(data.total_mv_100m_cny.where(
        data.total_mv_100m_cny > 0))
    data["log_avg_volume_10"] = np.log(data.avg_volume_10_million.where(
        data.avg_volume_10_million > 0))
    data["log_volume_ratio_5_10"] = np.log(data.volume_ratio_5_10.where(
        data.volume_ratio_5_10 > 0))
    data = data.replace([np.inf, -np.inf], np.nan)
    return data[data[[*BASE, *ADDED, "hit5"]].notna().all(axis=1)].copy()


def rank_metrics(part, scores):
    ranked = part[["date", "symbol6", "hit5", "hit10"]].copy()
    ranked["score"] = scores
    ranked = ranked.sort_values(["date", "score", "symbol6"],
                                ascending=[True, False, True])
    ranked["rank"] = ranked.groupby("date").cumcount() + 1
    ranked["daily_size"] = ranked.groupby("date").symbol6.transform("size")
    daily_base = part.groupby("date").hit5.mean()
    output = {"rows": len(part), "days": int(part.date.nunique()),
              "base_rate": float(part.hit5.mean()),
              "mean_daily_auc": float(np.mean([
                  roc_auc_score(group.hit5, group.score)
                  for _, group in ranked.groupby("date") if group.hit5.nunique() == 2]))}
    for name, chosen in (("top10pct", ranked[ranked["rank"].le(np.ceil(ranked.daily_size * .10))]),
                         ("top1pct", ranked[ranked["rank"].le(np.ceil(ranked.daily_size * .01))]),
                         ("top5_per_day", ranked[ranked["rank"].le(5)]),
                         ("top1_per_day", ranked[ranked["rank"].eq(1)])):
        output[name] = {"rows": len(chosen), "hits": int(chosen.hit5.sum()),
                        "rate": float(chosen.hit5.mean()),
                        "touch10_hits": int(chosen.hit10.sum()),
                        "matched_day_base_rate": float(chosen.date.map(daily_base).mean())}
    return output


def model_check(rows):
    partitions = {"train": rows[rows.date.le("2026-09-14")],
                  "validation_seen": rows[rows.date.between("2026-09-15", "2026-09-21")],
                  "later_seen": rows[rows.date.ge("2026-09-22")]}
    result = {"universe": "as-of eligible, observed, T final non-limit retrospective cohort, common profile coverage",
              "models": {}}
    for name, features in (("baseline", BASE), ("baseline_plus_profile", (*BASE, *ADDED))):
        model = make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=500))
        train = partitions["train"]
        model.fit(train[list(features)], train.hit5.astype(int))
        block = {"features": list(features),
                 "standardized_coefficients": dict(zip(features,
                     [float(coef) for coef in model[-1].coef_[0]])),
                 "partitions": {}}
        for key, part in partitions.items():
            scores = model.predict_proba(part[list(features)])[:, 1]
            block["partitions"][key] = rank_metrics(part, scores)
            if name == "baseline":
                ranked = part[["date", "hit5", "avg_turn_5_pct"]].copy()
                ranked["score"] = scores
                ranked["rank"] = ranked.groupby("date").score.rank(
                    ascending=False, method="first")
                ranked["daily_size"] = ranked.groupby("date").score.transform("size")
                ranked["turnover_percentile"] = ranked.groupby("date").avg_turn_5_pct.rank(
                    pct=True, method="average")
                for label, fraction in (("top10pct", .10), ("top1pct", .01)):
                    subset = ranked[ranked["rank"].le(np.ceil(ranked.daily_size * fraction))]
                    aucs = [roc_auc_score(group.hit5, group.avg_turn_5_pct)
                            for _, group in subset.groupby("date") if group.hit5.nunique() == 2]
                    block["partitions"][key][label]["turnover_mean_daily_auc_inside"] = (
                        float(np.mean(aucs)) if aucs else None)
                    if label == "top1pct":
                        retained = subset[subset.turnover_percentile.gt(.4)]
                        block["partitions"][key][label]["after_turnover_gate"] = {
                            "rows": len(retained), "hits": int(retained.hit5.sum())}
        result["models"][name] = block
    return result


def turnover_rule(rows):
    valid = rows[rows.asof_eligible & rows.prior_history_asof &
                 rows.price_profile_ready].copy()
    valid["turnover_quintile"] = np.minimum(5, np.ceil(valid.groupby("date")
        .avg_turn_5_pct.rank(pct=True, method="average") * 5)).astype(int)
    valid["board"] = np.select([
        valid.symbol6.str.startswith("68"), valid.symbol6.str.startswith("30"),
        valid.symbol6.str.startswith("60"), valid.symbol6.str.startswith("00")],
        ["STAR", "CHINEXT", "SH_MAIN", "SZ_MAIN"], default="OTHER")
    parts = {"train": valid[valid.date.le("2026-09-14")],
             "validation_seen": valid[valid.date.between("2026-09-15", "2026-09-21")],
             "later_seen": valid[valid.date.ge("2026-09-22")]}
    sections = {}
    for name, part in parts.items():
        retained = part[part.turnover_quintile.ge(3)]
        sections[name] = {"all_rows": len(part), "all_hit5": int(part.hit5.sum()),
                          "all_hit10": int(part.hit10.sum()),
                          "retained_rows": len(retained),
                          "retained_hit5": int(retained.hit5.sum()),
                          "retained_hit10": int(retained.hit10.sum())}
    board_auc = {}
    for board, part in valid.groupby("board"):
        aucs = [roc_auc_score(group.hit5, group.avg_turn_5_pct)
                for _, group in part.groupby("date")
                if len(group) >= 50 and group.hit5.nunique() == 2]
        board_auc[board] = {"rows": len(part), "days": len(aucs),
                            "mean_daily_auc": float(np.mean(aucs)) if aucs else None}
    cap = valid[valid.total_mv_ready].copy()
    cap["cap_quintile"] = np.minimum(5, np.ceil(cap.groupby("date")
        .total_mv_100m_cny.rank(pct=True, method="average") * 5)).astype(int)
    conditional_auc = [roc_auc_score(part.hit5, part.avg_turn_5_pct)
                       for _, part in cap.groupby(["date", "board", "cap_quintile"])
                       if len(part) >= 50 and part.hit5.nunique() == 2]
    return {"rule": "keep within-day top 60% by T-1 five-day mean turnover",
            "partitions": sections, "within_board_daily_auc": board_auc,
            "board_cap_quintile_conditional_auc": (
                float(np.mean(conditional_auc)) if conditional_auc else None),
            "board_cap_quintile_groups": len(conditional_auc)}


def summarize(rows):
    asof = rows[rows.asof_eligible & rows.prior_history_asof]
    return {"cohort_rows": len(rows), "cohort_hit5": int(rows.hit5.sum()),
            "asof_rows": len(asof),
            "price_profile_ready": int(asof.price_profile_ready.sum()),
            "total_mv_ready": int(asof.total_mv_ready.sum()),
            "free_float_mv_ready": int(asof.free_float_mv_ready.sum()),
            "common_profile_ready": int(asof.profile_ready.sum()),
            "common_profile_hit5": int(asof.loc[asof.profile_ready, "hit5"].sum()),
            "turnover_rule_check": turnover_rule(rows),
            "profile_features": {feature: feature_summary(
                asof, feature, "total_mv_ready" if feature == "total_mv_100m_cny" else
                "free_float_mv_ready" if feature == "free_float_mv_100m_cny" else
                "price_profile_ready") for feature in PROFILE}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--cohort", default="outputs/stock_automl/cohorts/cohort_non_limit.csv")
    parser.add_argument("--prior-top5", default="outputs/stock_automl/cohorts/positive_first_model_top5.csv")
    parser.add_argument("--output", default="docs/stock_automl_runs/20261008_stock_profile/summary.json")
    parser.add_argument("--detail-dir", default="outputs/stock_automl/cohorts")
    args = parser.parse_args()
    if not os.environ.get("SIMPLE_BI_PLATFORM_DB_URL"):
        os.environ["SIMPLE_BI_PLATFORM_DB_URL"] = dotenv_values(args.env_file).get("PLATFORM_DB_URL", "")
    with read_only_db() as conn:
        price, value = load_sources(conn)
    profile = build_profile(price, value)
    cohort = pd.read_csv(args.cohort, dtype={"symbol6": str}, parse_dates=["date"])
    rows = attach_profile(cohort, profile)
    result = summarize(rows)
    model_rows = model_frame(rows)
    result["model_rows"] = len(model_rows)
    result["model_check"] = model_check(model_rows)
    detail_dir = Path(args.detail_dir)
    detail_dir.mkdir(parents=True, exist_ok=True)
    detail_columns = ["date", "next_date", "symbol6", "name", "hit5", "hit10",
                      "high5_return", "high10_return", "asof_eligible",
                      "prior_history_asof", "price_profile_ready", "total_mv_ready",
                      "free_float_mv_ready", *PROFILE]
    rows[detail_columns].to_csv(detail_dir / "stock_profile_cohort.csv", index=False)
    rows.loc[rows.hit5 | rows.hit10, detail_columns].to_csv(
        detail_dir / "stock_profile_positives.csv", index=False)
    top5 = pd.read_csv(args.prior_top5, dtype={"symbol6": str}, parse_dates=["date"])
    top5 = top5.merge(profile, left_on=["date", "symbol6"],
                      right_on=["signal_date", "symbol6"], how="left",
                      validate="many_to_one")
    top5[["model", "partition", "date", "symbol6", "rank", "score",
          "actionable_hit5", "price_profile_ready", "total_mv_ready", *PROFILE]].to_csv(
              detail_dir / "stock_profile_prior_top5.csv", index=False)
    result["prior_top5_profile_ready"] = int((top5.price_profile_ready.eq(True) &
                                               top5.total_mv_ready.eq(True)).sum())
    result["prior_top5_rows"] = len(top5)
    result["generated_at"] = datetime.now().isoformat(timespec="seconds")
    result["provenance"] = {"script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                            "cohort_source": args.cohort, "database_snapshot_version": None}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote aggregate profile audit: {output}")
    print(f"wrote ignored local profile rows: {detail_dir}")


if __name__ == "__main__":
    main()

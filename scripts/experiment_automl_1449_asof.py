"""Read-only, arrival-verified 14:49:59 research replay.

The latest permitted input is the 14:30 one-minute bar.  Later minute bars
were not stored by the decision time in this historical database.  The 14:50
price is an *outcome/entry proxy* only; it never defines the candidate pool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from dotenv import dotenv_values
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.experiment_automl_1450_grid import board_limit, frame, read_only_db


START, END = "2026-08-24", "2026-09-30"
STAGE1 = ("prior_return_3", "prior_amount_ratio_5")
STAGE2 = ("day_return", "tail_return_20m", "tail_amount_log_ratio")
FEATURES = ("market_breadth", *STAGE1, *STAGE2)
DECISION_TIME = "14:49:59"
SCORE_GATE_FLOOR = 0.5
DAILY_MAX_SCORE_QUANTILE = 0.75  # Frequency target: roughly one train-day signal per week.
MIN_UNIVERSE = 4000  # Day-level coverage gate uses only decision-time candidate counts.
MIN_MARKET_BREADTH = 0.20  # Exploratory day rule derived from the prior market-only tree.


def minute_signal(conn, day):
    stamp = lambda time: f"{day} {time}:00"
    rows = frame(conn, """
        SELECT LEFT(stk_code, 6) AS symbol6,
          MAX(CASE WHEN bar_end_time=%s THEN stk_name END) AS name,
          MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS p1410,
          MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS p1430,
          SUM(CASE WHEN bar_end_time BETWEEN %s AND %s THEN amount ELSE 0 END) AS amount_prev10,
          SUM(CASE WHEN bar_end_time BETWEEN %s AND %s THEN amount ELSE 0 END) AS amount_tail10,
          COUNT(*) AS bars, MAX(is_fallback) AS fallback, MIN(is_finalized) AS finalized,
          MAX(source_snapshot_time) AS source_time,
          MAX(fetch_time) AS fetch_time, MAX(created_at) AS created_at,
          MAX(updated_at) AS updated_at
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND (bar_end_time=%s OR bar_end_time BETWEEN %s AND %s)
        GROUP BY LEFT(stk_code, 6)
    """, (stamp("14:30"), stamp("14:10"), stamp("14:30"),
          stamp("14:11"), stamp("14:20"), stamp("14:21"), stamp("14:30"),
          day, stamp("09:31"), stamp("14:10"), stamp("14:30")))
    rows["date"] = pd.Timestamp(day)
    return rows


def minute_entry(conn, day):
    rows = frame(conn, """
        SELECT LEFT(stk_code, 6) AS symbol6, latest_price AS entry,
          is_fallback AS entry_fallback, is_finalized AS entry_finalized
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time=%s
    """, (day, f"{day} 14:50:00"))
    rows["date"] = pd.Timestamp(day)
    return rows


def morning_outcome(conn, day):
    rows = frame(conn, """
        SELECT LEFT(stk_code, 6) AS symbol6,
          MAX(CASE WHEN bar_end_time=%s THEN open_price END) AS open931,
          MAX(CASE WHEN bar_end_time<=%s THEN high_price END) AS high5,
          MIN(CASE WHEN bar_end_time<=%s THEN low_price END) AS low5,
          MAX(high_price) AS high10,
          MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS close935,
          SUM(CASE WHEN bar_end_time<=%s THEN 1 ELSE 0 END) AS bars5,
          COUNT(*) AS bars10,
          SUM(CASE WHEN bar_end_time<=%s THEN volume ELSE 0 END) AS volume5,
          MAX(CASE WHEN bar_end_time<=%s THEN is_fallback END) AS fallback5,
          MIN(CASE WHEN bar_end_time<=%s THEN is_finalized END) AS finalized5,
          MAX(is_fallback) AS fallback10, MIN(is_finalized) AS finalized10
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time BETWEEN %s AND %s
        GROUP BY LEFT(stk_code, 6)
    """, (f"{day} 09:31:00", f"{day} 09:35:00", f"{day} 09:35:00",
          f"{day} 09:35:00",
          f"{day} 09:35:00", f"{day} 09:35:00",
          f"{day} 09:35:00", f"{day} 09:35:00", day,
          f"{day} 09:31:00", f"{day} 09:40:00"))
    rows["next_date"] = pd.Timestamp(day)
    return rows


def prior_daily(conn):
    rows = frame(conn, """
        SELECT trade_date AS prior_date, LEFT(stk_code, 6) AS symbol6,
          close AS prior_close, preclose, amount AS prior_amount,
          create_time AS daily_created, update_time AS daily_updated
        FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s
    """, ("2026-07-15", END))
    rows["prior_date"] = pd.to_datetime(rows.prior_date)
    rows = rows.drop_duplicates(["prior_date", "symbol6"], keep=False)
    rows = rows.sort_values(["symbol6", "prior_date"]).copy()
    for col in ("prior_close", "preclose", "prior_amount"):
        rows[col] = pd.to_numeric(rows[col], errors="coerce")
    rows["daily_created"] = pd.to_datetime(rows.daily_created, errors="coerce")
    rows["daily_updated"] = pd.to_datetime(rows.daily_updated, errors="coerce")
    groups = rows.groupby("symbol6", sort=False)
    returns = rows.prior_close / rows.preclose - 1
    log_returns = np.log1p(returns.where(returns > -1))
    rows["prior_return_3"] = np.expm1(log_returns.groupby(rows.symbol6).transform(
        lambda s: s.rolling(3, min_periods=3).sum()))
    older_amount = groups.prior_amount.shift(1)
    five_day_mean = older_amount.groupby(rows.symbol6).transform(
        lambda s: s.rolling(5, min_periods=5).mean())
    rows["prior_amount_ratio_5"] = rows.prior_amount / five_day_mean
    for col in ("daily_created", "daily_updated"):
        lookback = pd.concat([groups[col].shift(i) for i in range(6)], axis=1)
        rows[col] = lookback.max(axis=1).where(lookback.notna().all(axis=1))
    return rows.drop(columns="preclose")


def prepare_signal(points, prior, day, prior_date):
    """Only columns that existed by the decision timestamp influence eligibility."""
    cutoff = pd.Timestamp(f"{day} {DECISION_TIME}")
    signal = points.merge(prior[prior.prior_date.eq(pd.Timestamp(prior_date))],
                          on="symbol6", how="left", validate="one_to_one")
    for col in ("p1410", "p1430", "amount_prev10", "amount_tail10",
                "prior_close", "prior_amount", "prior_return_3", "prior_amount_ratio_5",
                "bars", "fallback", "finalized"):
        signal[col] = pd.to_numeric(signal[col], errors="coerce")
    for col in ("source_time", "fetch_time", "created_at", "updated_at",
                "daily_created", "daily_updated"):
        signal[col] = pd.to_datetime(signal[col], errors="coerce")
    times_ok = signal[["source_time", "fetch_time", "created_at", "updated_at",
                       "daily_created", "daily_updated"]].le(cutoff).all(axis=1)
    quality = (signal.bars.eq(22) & signal.fallback.eq(0) & signal.finalized.eq(1) &
               (signal[["p1410", "p1430", "prior_close"]] > 0).all(axis=1) &
               signal.prior_amount.ge(10_000_000) & signal.amount_tail10.ge(100_000) &
               signal.amount_prev10.gt(0) & signal.prior_amount_ratio_5.gt(0) &
               signal.prior_return_3.notna() & times_ok)
    signal = signal.loc[quality].copy()
    signal["day_return"] = signal.p1430 / signal.prior_close - 1
    signal["limit_buffer"] = [board_limit(s, n) for s, n in zip(signal.symbol6, signal.name)]
    signal = signal[signal.limit_buffer.notna() &
                    signal.day_return.abs().lt(signal.limit_buffer)].copy()
    signal["tail_return_20m"] = signal.p1430 / signal.p1410 - 1
    signal["tail_amount_log_ratio"] = np.log(signal.amount_tail10 / signal.amount_prev10)
    # This universe is fully determined before joining entry prices and labels.
    signal["market_breadth"] = signal.day_return.gt(0).groupby(signal.date).transform("mean")
    return signal[["date", "symbol6", "prior_close", "limit_buffer", *FEATURES]].replace(
        [np.inf, -np.inf], np.nan).dropna()


def attach_outcome(signal, entry, morning):
    result = signal.merge(entry, on=["date", "symbol6"], how="left", validate="one_to_one")
    result = result.merge(morning, on=["next_date", "symbol6"], how="left", validate="one_to_one")
    for col in ("entry", "entry_fallback", "entry_finalized", "open931", "high5", "low5", "high10",
                "close935", "bars5", "bars10", "volume5", "fallback5", "finalized5",
                "fallback10", "finalized10"):
        result[col] = pd.to_numeric(result[col], errors="coerce")
    entry_ok = result.entry.gt(0) & result.entry_fallback.eq(0) & result.entry_finalized.eq(1)
    result["observed"] = (entry_ok & result.open931.gt(0) & result.high5.gt(0) &
                          result.low5.gt(0) & result.close935.gt(0) &
                          result.bars5.eq(5) & result.volume5.gt(0) &
                          result.fallback5.eq(0) & result.finalized5.eq(1))
    result["observed10"] = (result.observed & result.high10.gt(0) & result.bars10.eq(10) &
                            result.fallback10.eq(0) & result.finalized10.eq(1))
    result["high5_return"] = (result.high5 / result.entry - 1).where(result.observed)
    result["low5_return"] = (result.low5 / result.entry - 1).where(result.observed)
    result["open_return"] = (result.open931 / result.entry - 1).where(result.observed)
    result["high10_return"] = (result.high10 / result.entry - 1).where(result.observed10)
    result["exit_return"] = (result.close935 / result.entry - 1).where(result.observed)
    result["hit1"] = result.high5_return.ge(0.01)
    result["hit1_10m"] = result.high10_return.ge(0.01)
    result["near_limit_at_entry"] = (result.entry / result.prior_close - 1).abs().ge(
        result.limit_buffer).where(entry_ok, False)
    return result


def coverage_gate(signal, minimum=MIN_UNIVERSE):
    return signal if len(signal) >= minimum else signal.iloc[0:0].copy()


def arrival_audit(conn, dates):
    totals = {bar: {"rows": 0, "current_rows_present_by_decision": 0,
                    "source_time_equals_bar_end": 0}
              for bar in ("14:30", "14:40", "14:49")}
    for day in dates:
        cutoff = f"{day} {DECISION_TIME}"
        for bar, total in totals.items():
            row = frame(conn, """
                SELECT COUNT(*) AS n,
                  COALESCE(SUM(fetch_time<=%s AND created_at<=%s AND updated_at<=%s),0) AS present,
                  COALESCE(SUM(source_snapshot_time=bar_end_time),0) AS source_equals_end
                FROM aiia_stock_realtime_minute_snapshot_full
                WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                  AND bar_end_time=%s
            """, (cutoff, cutoff, cutoff, day, f"{day} {bar}:00")).iloc[0]
            total["rows"] += int(row.n)
            total["current_rows_present_by_decision"] += int(row.present)
            total["source_time_equals_bar_end"] += int(row.source_equals_end)
    return totals


def build_dataset(conn):
    dates = frame(conn, """SELECT DISTINCT trade_date AS date FROM kcrp_stock_price
        WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date""", (START, END)).date.astype(str).tolist()
    arrivals = arrival_audit(conn, dates)
    daily = prior_daily(conn)
    samples = []
    entries = []
    mornings = []
    audit = []
    for previous, day in zip(dates[:-1], dates[1:]):
        points = minute_signal(conn, day)
        prepared = prepare_signal(points, daily, day, previous)
        before_coverage = len(prepared)
        prepared = coverage_gate(prepared)
        samples.append(prepared)
        entries.append(minute_entry(conn, day))
        mornings.append(morning_outcome(conn, day))
        audit.append({"date": day, "minute_rows": len(points),
                      "asof_eligible_before_coverage": before_coverage,
                      "asof_eligible": len(prepared)})
        print(f"as-of {day}: {len(prepared)} / {len(points)}", flush=True)
    signals = pd.concat(samples, ignore_index=True)
    next_date = dict(zip(pd.to_datetime(dates[:-1]), pd.to_datetime(dates[1:])))
    signals["next_date"] = signals.date.map(next_date)
    signals = attach_outcome(signals, pd.concat(entries, ignore_index=True),
                             pd.concat(mornings, ignore_index=True))
    return signals[signals.next_date.notna()].copy(), audit, arrivals


def metrics(rows):
    observed = rows[rows.observed]
    count = len(rows)
    return {"signals": count, "days": int(rows.date.nunique()) if count else 0,
            "unobserved": count - len(observed),
            "high5_up_1pct": int(rows.hit1.sum()),
            "precision_high5_up_1pct": round(float(rows.hit1.mean()), 5) if count else None,
            "high5_up_1_3pct": int(rows.high5_return.ge(0.013).sum()),
            "opened_up_1pct": int(rows.open_return.ge(0.01).sum()),
            "low5_below_minus_1pct": int(rows.low5_return.lt(-0.01).sum()),
            "high10_up_1pct": int(rows.hit1_10m.sum()),
            "precision_high10_up_1pct": round(float(rows.hit1_10m.mean()), 5) if count else None,
            "near_limit_at_entry": int(rows.near_limit_at_entry.sum()),
            "935_net_positive": int(rows.exit_return.gt(0.003).sum()),
            "mean_935_return_net_30bp_observed": round(float(observed.exit_return.mean() - 0.003), 6)
            if len(observed) else None}


def select_top_one(rows, scores, gate):
    ranked = rows.copy()
    ranked["score"] = scores
    return (ranked[ranked.score.ge(gate)]
            .sort_values(["date", "score", "symbol6"], ascending=[True, False, True])
            .groupby("date").head(1))


def training_score_gate(rows, scores):
    eligible = rows.copy()
    eligible["score"] = scores
    daily_max = eligible.groupby("date").score.max()
    if daily_max.empty:
        return None
    return max(SCORE_GATE_FLOOR, float(daily_max.quantile(DAILY_MAX_SCORE_QUANTILE)))


def leaf_rule(tree, leaf, features):
    paths = {}

    def visit(node, conditions):
        if tree.tree_.children_left[node] == -1:
            paths[node] = conditions
            return
        feature = features[tree.tree_.feature[node]]
        threshold = tree.tree_.threshold[node]
        visit(tree.tree_.children_left[node], [*conditions, f"{feature} <= {threshold:.6g}"])
        visit(tree.tree_.children_right[node], [*conditions, f"{feature} > {threshold:.6g}"])

    visit(0, [])
    return " and ".join(paths[leaf])


def experiment(data, audit):
    train = data[data.date.le("2026-09-14")]
    validation = data[data.date.between("2026-09-15", "2026-09-21")]
    later = data[data.date.ge("2026-09-22")]
    fit = train[train.observed]
    if fit.hit1.nunique() < 2:
        raise ValueError("training labels lack both classes")
    market_fit = fit[fit.market_breadth.ge(MIN_MARKET_BREADTH)]
    coarse = DecisionTreeClassifier(max_depth=2, min_samples_leaf=1500, random_state=42)
    coarse.fit(market_fit[list(STAGE1)], market_fit.hit1.astype(int))
    leaves = market_fit.assign(leaf=coarse.apply(market_fit[list(STAGE1)])).groupby("leaf").hit1.agg(
        ["count", "sum", "mean"])
    qualifying = leaves[leaves["mean"].ge(0.20)]
    selected_leaf = int(qualifying.sort_values(["mean", "count"], ascending=False).index[0]) \
        if not qualifying.empty else None
    stage2_fit = market_fit[coarse.apply(market_fit[list(STAGE1)]) == selected_leaf] \
        if selected_leaf is not None else fit.iloc[0:0]
    model = None
    if stage2_fit.hit1.nunique() == 2:
        model = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=500))
        model.fit(stage2_fit[list(STAGE2)], stage2_fit.hit1.astype(int))
    train_stage1 = train[train.market_breadth.ge(MIN_MARKET_BREADTH)]
    train_stage1 = train_stage1[coarse.apply(train_stage1[list(STAGE1)]) == selected_leaf] \
        if selected_leaf is not None else train.iloc[0:0]
    score_gate = training_score_gate(train_stage1, model.predict_proba(train_stage1[list(STAGE2)])[:, 1]) \
        if model is not None and not train_stage1.empty else None
    out = {"decision": DECISION_TIME, "latest_input_bar": "14:30:00",
           "target": "next 09:31-09:35 max(high_price) / 14:50 latest_price proxy >= 1.01",
           "secondary_target": "next 09:31-09:40 max(high_price) / 14:50 latest_price proxy >= 1.01",
           "market_rule": {"minimum_breadth": MIN_MARKET_BREADTH,
                           "chosen_after_market_only_diagnostic": True},
           "stage1": {"features": list(STAGE1), "model": "depth-2 decision tree, min leaf 1500",
                      "rule_tree": export_text(coarse, feature_names=list(STAGE1), decimals=5),
                      "selected_leaf": selected_leaf,
                      "selected_rule": leaf_rule(coarse, selected_leaf, STAGE1) if selected_leaf is not None else None,
                      "leaf_train_rates": [{"leaf": int(idx), "train_observed": int(row["count"]),
                                            "train_hits": int(row["sum"]), "train_hit_rate": round(float(row["mean"]), 5)}
                                           for idx, row in leaves.iterrows()],
                      "minimum_train_leaf_hit_rate": 0.20,
                      "selection": "highest train hit-rate leaf; no leaf -> abstain"},
           "stage2": {"features": list(STAGE2), "model": "standardized logistic regression C=0.1",
                      "training_labeled_rows": len(stage2_fit),
                      "coefficients_standardized": dict(zip(STAGE2,
                          [round(float(v), 6) for v in model[-1].coef_[0]])) if model else None,
                      "intercept": round(float(model[-1].intercept_[0]), 6) if model else None},
           "policy": {"minimum_stage2_score": round(score_gate, 6) if score_gate is not None else None,
                      "gate_calibration": "max(0.5, train daily-max score 75th percentile)",
                      "max_per_day": 1, "may_abstain": True},
           "train_labeled_rows": len(fit), "daily_asof_counts": audit, "splits": {}}
    for name, split in (("train", train), ("validation_seen", validation), ("later_seen", later)):
        if split.empty:
            out["splits"][name] = {"status": "no_asof_candidates"}
            continue
        market_rows = split[split.market_breadth.ge(MIN_MARKET_BREADTH)]
        stage1_rows = market_rows[coarse.apply(market_rows[list(STAGE1)]) == selected_leaf] \
            if selected_leaf is not None and not market_rows.empty else split.iloc[0:0]
        block = {"all_candidates": metrics(split), "after_market_rule": metrics(market_rows),
                 "after_stage1": metrics(stage1_rows)}
        if model is not None and not stage1_rows.empty:
            score = model.predict_proba(stage1_rows[list(STAGE2)])[:, 1]
            picked = select_top_one(stage1_rows, score, score_gate) \
                if score_gate is not None else stage1_rows.iloc[0:0]
            top_one = select_top_one(stage1_rows, score, 0.0)
            block.update({"policy": metrics(picked), "ranking_top_one_every_day": metrics(top_one),
                          "score_range": [round(float(score.min()), 6),
                                          round(float(score.max()), 6)]})
        out["splits"][name] = block
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/stock_automl_runs/20261008_1449_asof/summary.json")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    if not os.environ.get("SIMPLE_BI_PLATFORM_DB_URL"):
        os.environ["SIMPLE_BI_PLATFORM_DB_URL"] = dotenv_values(args.env_file).get("PLATFORM_DB_URL", "")
    with read_only_db() as conn:
        data, audit, arrivals = build_dataset(conn)
    result = experiment(data, audit)
    result["arrival_audit"] = arrivals
    result["generated_at"] = datetime.now().isoformat(timespec="seconds")
    result["provenance"] = {
        "source": "47.94.1.2:3312/kingdomai",
        "tables": ["aiia_stock_realtime_minute_snapshot_full", "kcrp_stock_price"],
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python_version": platform.python_version(), "sklearn_version": sklearn.__version__,
        "data_snapshot_version": None,
    }
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {destination}")


if __name__ == "__main__":
    main()

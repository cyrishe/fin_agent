"""Read-only, short-window 14:45 next-open research experiment.

This is an exploratory script, separate from the production AutoML protocol.
It saves aggregate evidence only; market rows and model binaries stay in memory.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import HistGradientBoostingClassifier

from src.quant_research.automl.data import kingdom_connection


START_HISTORY = "2026-07-15"
FIRST_SIGNAL = "2026-08-25"
LAST_SIGNAL = "2026-09-30"
TOP_K = 10
THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
FEATURES = (
    "prior_return_1", "prior_return_3", "prior_return_5", "prior_return_20",
    "prior_volatility_5", "prior_amount_ratio_5", "prior_turnover",
    "prior_flow_ratio", "prior_flow_ratio_3", "prior_log_market_cap", "prior_pe",
    "day_return_1445", "tail_return_14m", "tail_return_5m", "tail_amount_vs_prior",
    "market_day_return_1445", "market_breadth_1445", "sector_day_return_1445",
)


def fetch_frame(conn, sql, params=()):
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return pd.DataFrame(cursor.fetchall())


@contextmanager
def minute_connection():
    credential_key = os.environ.get("KINGDOMAI_DB_CREDENTIAL_SOURCE", "")
    if credential_key not in {"PLATFORM_DB_URL", "REPORT_DB_URL", "BUSINESS_DB_URL", "KINGDOMAI_DB_URL"}:
        raise ValueError("configure an explicit KingdomAI minute credential source")
    parsed = urlparse(os.environ[credential_key].replace("mysql+pymysql://", "mysql://", 1))
    host = os.environ.get("KINGDOMAI_DB_HOST")
    if not host or parsed.scheme != "mysql":
        raise ValueError("configure KINGDOMAI_DB_HOST and a MySQL credential source")
    conn = pymysql.connect(
        host=host, port=int(os.environ.get("KINGDOMAI_DB_PORT") or 3306),
        user=unquote(parsed.username or ""), password=unquote(parsed.password or ""),
        database="kingdomai", charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=8, read_timeout=45, autocommit=False,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=35000")
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        yield conn
    finally:
        conn.rollback()
        conn.close()


def read_daily(start, end):
    with kingdom_connection() as conn:
        dates = fetch_frame(conn, "SELECT DISTINCT trade_date AS date FROM kcrp_stock_price "
                            "WHERE trade_date >= %s AND trade_date <= %s ORDER BY trade_date", (start, end))
        chunks = []
        for left in pd.date_range(start, end, freq="14D"):
            right = min(left + pd.Timedelta(days=14), pd.Timestamp(end) + pd.Timedelta(days=1))
            chunks.append(fetch_frame(conn, "SELECT trade_date AS date, stk_code AS symbol, "
                "open, close, adjopen, adjclose, volume, amount, turn_ratio, update_time "
                "FROM kcrp_stock_price WHERE trade_date >= %s AND trade_date < %s",
                (left.date(), right.date())))
        daily = pd.concat(chunks, ignore_index=True)
        flows = fetch_frame(conn, "SELECT trade_date AS date, stk_code AS symbol, "
            "main_net_buy_value_ratio AS flow_ratio, update_time AS flow_updated "
            "FROM kcrp_stock_moneyflow WHERE trade_date >= %s AND trade_date <= %s",
            (start, end))
        values = fetch_frame(conn, "SELECT trade_date AS date, stk_code AS symbol, "
            "total_mv, pe_ttm, update_time AS value_updated FROM kcrp_stock_pricevaluate "
            "WHERE trade_date >= %s AND trade_date <= %s", (start, end))
        industries = fetch_frame(conn, "SELECT LEFT(stk_code, 6) AS symbol6, industry_name AS industry, "
            "begin_date, end_date FROM kcrp_stock_industry WHERE industry_type='SW2021' AND level=1 "
            "AND begin_date<=%s AND (end_date IS NULL OR end_date>%s)", (end, start))
    for frame in (dates, daily, flows, values):
        frame["date"] = pd.to_datetime(frame["date"])
    daily = daily.merge(flows, on=["date", "symbol"], how="left", validate="one_to_one")
    daily = daily.merge(values, on=["date", "symbol"], how="left", validate="one_to_one")
    industries["begin_date"] = pd.to_datetime(industries.begin_date)
    # The source uses 2999-12-31 as an open-ended sentinel, beyond pandas ns range.
    industries["end_date"] = pd.to_datetime(industries.end_date, errors="coerce")
    return dates.date.sort_values().tolist(), daily, industries


def read_minutes(conn, date, industries):
    day = pd.Timestamp(date).strftime("%Y-%m-%d")
    stamp = lambda time: f"{day} {time}:00"
    frame = fetch_frame(conn, """
        SELECT stk_code AS symbol6,
          MAX(CASE WHEN bar_end_time=%s THEN open_price END) AS day_open,
          MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS p1431,
          MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS p1440,
          MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS p1445,
          SUM(CASE WHEN bar_end_time>%s THEN amount ELSE 0 END) AS tail_amount,
          SUM(CASE WHEN bar_end_time>%s THEN 1 ELSE 0 END) AS tail_bars,
          MAX(CASE WHEN bar_end_time>%s THEN is_fallback ELSE 0 END) AS fallback,
          MAX(source_snapshot_time) AS latest_source_snapshot
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 AND is_finalized=1
          AND (bar_end_time=%s OR (bar_end_time>%s AND bar_end_time<=%s))
        GROUP BY stk_code
    """, (stamp("09:31"), stamp("14:31"), stamp("14:40"), stamp("14:45"),
          stamp("14:30"), stamp("14:30"), stamp("14:30"), day,
          stamp("09:31"), stamp("14:30"), stamp("14:45")))
    frame["date"] = pd.Timestamp(date)
    industry = industries[(industries.begin_date <= pd.Timestamp(date)) &
                          (industries.end_date.isna() | (industries.end_date > pd.Timestamp(date)))]
    industry = industry.sort_values("begin_date").drop_duplicates("symbol6", keep="last")
    return frame.merge(industry, on="symbol6", how="left", validate="many_to_one")


def prepare_dataset(market_dates, daily, minute):
    """Use t-1 daily facts and bars ending by 14:45; t close is label-only."""
    dates = pd.DatetimeIndex(market_dates)
    next_day = dict(zip(dates[:-1], dates[1:]))
    daily = daily.sort_values(["symbol", "date"]).copy()
    group = daily.groupby("symbol", sort=False)
    for col in ("open", "close", "adjopen", "adjclose", "volume", "amount", "turn_ratio",
                "flow_ratio", "total_mv", "pe_ttm"):
        daily[col] = pd.to_numeric(daily[col], errors="coerce")
    daily["prior_return_1"] = group.adjclose.pct_change(1, fill_method=None)
    daily["prior_return_3"] = group.adjclose.pct_change(3, fill_method=None)
    daily["prior_return_5"] = group.adjclose.pct_change(5, fill_method=None)
    daily["prior_return_20"] = group.adjclose.pct_change(20, fill_method=None)
    daily["prior_volatility_5"] = daily.groupby("symbol").prior_return_1.transform(
        lambda series: series.rolling(5, min_periods=5).std())
    daily["prior_amount_ratio_5"] = daily.amount / daily.groupby("symbol").amount.transform(
        lambda series: series.rolling(5, min_periods=5).mean())
    daily["prior_turnover"] = daily.turn_ratio
    daily["prior_flow_ratio"] = daily.flow_ratio
    daily["prior_flow_ratio_3"] = daily.groupby("symbol").flow_ratio.transform(
        lambda series: series.rolling(3, min_periods=3).mean())
    flow_update_ns = pd.to_datetime(daily.flow_updated).astype("int64")
    daily["flow_updated_3_max"] = flow_update_ns.groupby(daily.symbol).transform(
        lambda series: series.rolling(3, min_periods=3).max())
    daily["prior_log_market_cap"] = np.log1p(daily.total_mv.clip(lower=0))
    daily["prior_pe"] = daily.pe_ttm
    daily["signal_date"] = daily.date.map(next_day)
    cutoff = daily.signal_date + pd.Timedelta(hours=14, minutes=45)
    for source, fields in (("flow_updated", ("prior_flow_ratio", "prior_flow_ratio_3")),
                           ("value_updated", ("prior_log_market_cap", "prior_pe"))):
        late = pd.to_datetime(daily[source]).gt(cutoff)
        daily.loc[late, list(fields)] = np.nan
    daily.loc[daily.flow_updated_3_max.gt(cutoff.astype("int64")), "prior_flow_ratio_3"] = np.nan
    prior = daily.rename(columns={"date": "prior_date", "amount": "prior_amount"})
    prior = prior[["symbol", "signal_date", "prior_date", "prior_amount", *FEATURES[:11]]]
    current = daily[["symbol", "date", "adjclose"]].rename(columns={"adjclose": "label_close"})
    current["next_date"] = current.date.map(next_day)
    next_open = daily[["symbol", "date", "adjopen"]].rename(
        columns={"date": "next_date", "adjopen": "label_next_open"})
    labels = current.merge(next_open, on=["symbol", "next_date"], how="left", validate="one_to_one")
    labels["gap"] = labels.label_next_open / labels.label_close - 1
    minute = minute.copy()
    minute["symbol6"] = minute.symbol6.astype(str).str.zfill(6)
    daily["symbol6"] = daily.symbol.str.slice(0, 6)
    identity = daily[["date", "symbol6", "symbol"]].drop_duplicates()
    if identity.duplicated(["date", "symbol6"]).any():
        raise ValueError("ambiguous six-digit minute stock code")
    minute = minute.merge(identity, on=["date", "symbol6"], how="inner", validate="many_to_one")
    for col in ("day_open", "p1431", "p1440", "p1445", "tail_amount", "tail_bars", "fallback"):
        minute[col] = pd.to_numeric(minute[col], errors="coerce")
    minute = minute[(minute.tail_bars.eq(15)) & (minute.fallback.eq(0)) &
                    (minute[["day_open", "p1431", "p1440", "p1445"]] > 0).all(axis=1) &
                    (minute.tail_amount > 0) &
                    (pd.to_datetime(minute.latest_source_snapshot) <= minute.date + pd.Timedelta(hours=14, minutes=45))]
    minute["day_return_1445"] = minute.p1445 / minute.day_open - 1
    minute["tail_return_14m"] = minute.p1445 / minute.p1431 - 1
    minute["tail_return_5m"] = minute.p1445 / minute.p1440 - 1
    minute["market_day_return_1445"] = minute.groupby("date").day_return_1445.transform("mean")
    minute["market_breadth_1445"] = minute.day_return_1445.gt(0).groupby(minute.date).transform("mean")
    minute["sector_day_return_1445"] = minute.groupby(["date", "industry"]).day_return_1445.transform("mean")
    data = minute.merge(prior, left_on=["symbol", "date"], right_on=["symbol", "signal_date"],
                        how="left", validate="one_to_one")
    data = data.merge(labels[["symbol", "date", "gap"]], on=["symbol", "date"],
                      how="left", validate="one_to_one")
    data["tail_amount_vs_prior"] = data.tail_amount / data.prior_amount
    data["positive"] = data.gap.gt(0)
    data = data[(data.prior_amount > 1e7) & data.prior_return_20.notna() & data.gap.notna() &
                np.isfinite(data.gap) & data.tail_amount_vs_prior.replace([np.inf, -np.inf], np.nan).notna()]
    return data.sort_values(["date", "symbol"]).reset_index(drop=True)


def picked(frame, score, threshold):
    candidates = frame.loc[np.asarray(score) >= threshold].copy()
    candidates["score"] = np.asarray(score)[np.asarray(score) >= threshold]
    return candidates.sort_values(["date", "score", "symbol"], ascending=[True, False, True]).groupby("date").head(TOP_K)


def measures(frame, score=None, threshold=None):
    subset = frame if score is None else picked(frame, score, threshold)
    gap = subset.gap
    return {"signals": int(len(subset)), "days": int(subset.date.nunique()),
            "precision": float(gap.gt(0).mean()) if len(subset) else None,
            "down_rate": float(gap.lt(0).mean()) if len(subset) else None,
            "mean_gap": float(gap.mean()) if len(subset) else None,
            "severe_down_rate": float(gap.lt(-0.01).mean()) if len(subset) else None}


def run(output):
    load_dotenv(".env")
    market_dates, daily, industries = read_daily(START_HISTORY, LAST_SIGNAL)
    signal_dates = [day for day in market_dates if FIRST_SIGNAL <= day.strftime("%Y-%m-%d") <= LAST_SIGNAL]
    minute_chunks = []
    with minute_connection() as conn:
        for index, day in enumerate(signal_dates, 1):
            frame = read_minutes(conn, day, industries)
            minute_chunks.append(frame)
            print(f"minute {index}/{len(signal_dates)} {day.date()} {len(frame)}", flush=True)
    minute = pd.concat(minute_chunks, ignore_index=True)
    data = prepare_dataset(market_dates, daily, minute)
    valid_dates = sorted(data.date.unique())
    if len(valid_dates) < 18:
        raise ValueError(f"only {len(valid_dates)} labeled dates; no meaningful chronological split")
    train_days, validation_days, test_days = valid_dates[:-10], valid_dates[-10:-5], valid_dates[-5:]
    train = data[data.date.isin(train_days)]
    validation = data[data.date.isin(validation_days)]
    test = data[data.date.isin(test_days)]
    print("labeled rows",len(data),"split days",len(train_days),len(validation_days),len(test_days),flush=True)
    models = {
        "logistic_prior_only": (Pipeline([("impute", SimpleImputer(strategy="median")),
                              ("scale", StandardScaler()),
                              ("model", LogisticRegression(C=0.2, max_iter=300))]), FEATURES[:11]),
        "logistic": (Pipeline([("impute", SimpleImputer(strategy="median")),
                              ("scale", StandardScaler()),
                              ("model", LogisticRegression(C=0.2, max_iter=300))]), FEATURES),
        "shallow_tree": (Pipeline([("impute", SimpleImputer(strategy="median")),
                                  ("model", DecisionTreeClassifier(max_depth=3, min_samples_leaf=500, random_state=42))]), FEATURES),
        "hist_gradient_boosting": (HistGradientBoostingClassifier(max_iter=80, max_leaf_nodes=7,
            min_samples_leaf=300, learning_rate=0.05, l2_regularization=2.0, random_state=42), FEATURES),
    }
    rows = []
    for name, (model, columns) in models.items():
        model.fit(train[list(columns)].replace([np.inf, -np.inf], np.nan), train.positive.astype(int))
        val_score = model.predict_proba(validation[list(columns)].replace([np.inf, -np.inf], np.nan))[:, 1]
        candidates = [(threshold, measures(validation, val_score, threshold)) for threshold in THRESHOLDS]
        qualified = [(threshold, metric) for threshold, metric in candidates
                     if metric["signals"] >= 30 and metric["days"] >= 3]
        if not qualified:
            rows.append({"model": name, "features": columns, "validation": candidates, "selected": None,
                         "reason": "no threshold had at least 30 signals across 3 dates"})
            continue
        threshold, val_metric = max(qualified, key=lambda x: (x[1]["precision"], x[1]["days"], x[1]["signals"]))
        test_score = model.predict_proba(test[list(columns)].replace([np.inf, -np.inf], np.nan))[:, 1]
        rows.append({"model": name, "features": columns, "validation": candidates,
                     "selected": {"threshold": threshold, "validation": val_metric,
                                  "test": measures(test, test_score, threshold)}})
        print(name,"threshold",threshold,"val",val_metric,"test",rows[-1]["selected"]["test"],flush=True)
    momentum_score = test.day_return_1445.to_numpy()
    momentum = picked(test, momentum_score, float("-inf"))
    report = {
        "as_of": datetime.now().astimezone().isoformat(),
        "experiment": "14:45 completed 1m bars to next market-session adjusted open versus current adjusted close",
        "source": "read-only kingdomai; minute full table from configured alternate host",
        "signal_dates_with_rows": len(valid_dates),
        "split": {"train": [str(train_days[0])[:10], str(train_days[-1])[:10], int(len(train))],
                  "validation": [str(validation_days[0])[:10], str(validation_days[-1])[:10], int(len(validation))],
                  "test": [str(test_days[0])[:10], str(test_days[-1])[:10], int(len(test))]},
        "feature_names": FEATURES,
        "train_feature_nonmissing": {name: round(float(train[name].notna().mean()), 4) for name in FEATURES},
        "liquidity_filter": "previous session amount > 10 million; 20-day prior history; complete 15 tail bars; positive minute amount",
        "test_all_eligible": measures(test), "test_top10_intraday_momentum": measures(momentum),
        "models": rows,
        "limits": ["About one month of intraday history; stock rows on the same date are correlated, so row count is not independent test size.",
                   "Validation chooses model-specific threshold from seven fixed values; final dates were not used for choice.",
                   "No historical revision log for money flow, valuation or industry; facts updated after the decision cutoff are excluded where timestamps exist.",
                   "Next-open direction is not the return from a 14:45 executable purchase; trading costs and fill constraints are not modeled.",
                   "Raw market observations, predictions and fitted models are not saved."],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False, default=str))
    print("saved", output, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="outputs/stock_automl/next_open_1445/exploration.json")
    args = parser.parse_args()
    run(Path(args.output))

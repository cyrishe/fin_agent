"""Long-history, read-only next-open ranking experiment with a weekly signal budget.

Run from the repository root with PYTHONPATH=.  Market rows and predictions stay
in memory; the output contains aggregate evidence only.  Every feature for a
decision date uses the preceding completed trading session or earlier.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC
from sklearn.tree import DecisionTreeClassifier
import sklearn

from src.quant_research.automl.config import ResearchSpec
from src.quant_research.automl.data import _market_calendar, _source_batches, kingdom_connection, query, select_symbols


START = "2024-04-01"
END = "2026-09-30"
TRAIN_START, TRAIN_END = "2024-05-29", "2025-06-30"
VALID_START, VALID_END = "2025-07-01", "2025-12-31"
TEST_START, TEST_END = "2026-01-01", "2026-07-31"
SAMPLE_SYMBOLS = 1000
WEEKLY_LIMIT = 5
WEEKLY_ACTIVE_DAYS = 2
DAILY_LIMIT = 3
QUANTILES = (0.95, 0.975, 0.99, 0.995, 0.9975, 0.999, 0.9995, 0.9998)

PRICE = (
    "price_return_1", "price_return_3", "price_return_5", "price_return_20",
    "price_volatility_20", "price_amount_ratio_5", "price_amount_ratio_20",
    "price_turnover", "price_body", "price_range", "price_gap",
)
FLOW = ("flow_main_ratio_1", "flow_main_ratio_3", "flow_main_ratio_5", "flow_huge_ratio_1")
MARKET = (
    "csi300_return_1", "csi300_return_5", "csi300_return_20",
    "csi300_amount_ratio_5", "csi1000_return_1", "csi1000_return_5",
    "sector_return_1", "sector_return_5", "sector_relative_5",
)
VALUE = ("log_market_cap", "pe_ttm")
ALL_FEATURES = PRICE + FLOW + MARKET + VALUE


def _load_batches(conn, columns, table, symbols):
    frames = []
    for index, frame in enumerate(_source_batches(conn, columns, table, symbols, START, END,
                                                    symbol_batch_size=64, days=366, row_limit=50000), 1):
        if not frame.empty:
            frames.append(frame)
        if index % 12 == 0:
            print(f"{table}: {index} batches, {sum(len(item) for item in frames)} rows", flush=True)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_sources(sample_symbols):
    with kingdom_connection() as conn:
        spec = ResearchSpec(start=TRAIN_START, end=END, max_symbols=sample_symbols, seed=20261007)
        symbols = select_symbols(conn, {}, spec)
        calendar = pd.DatetimeIndex(pd.to_datetime(_market_calendar(conn, START, END)))
        price = _load_batches(conn, "stk_code AS symbol, trade_date AS date, open, high, low, close, "
            "adjopen, adjclose, volume, amount, turn_ratio, update_time", "kcrp_stock_price", symbols)
        flow = _load_batches(conn, "stk_code AS symbol, trade_date AS date, "
            "main_net_buy_value_ratio AS main_ratio, huge_net_buy_value_ratio AS huge_ratio, "
            "update_time AS flow_updated", "kcrp_stock_moneyflow", symbols)
        value = _load_batches(conn, "stk_code AS symbol, trade_date AS date, "
            "total_mv, pe_ttm, update_time AS value_updated", "kcrp_stock_pricevaluate", symbols)
        industry = pd.DataFrame(query(conn, "SELECT stk_code AS symbol, industry_name, begin_date, end_date "
            "FROM kcrp_stock_industry WHERE industry_type='SW2021' AND level=1 "
            "AND begin_date<=%s AND (end_date IS NULL OR end_date>%s)", (END, START)))
        codes = ("000300.SH", "000852.SH")
        index_rows = pd.DataFrame(query(conn, "SELECT idx_code, index_short_name, trade_date AS date, "
            "close, amount, update_time FROM kcrp_index_price WHERE trade_date>=%s AND trade_date<=%s "
            "AND (idx_code IN (%s,%s) OR idx_code LIKE '801%%.SL')",
            (START, END, *codes)))
    for frame in (price, flow, value, index_rows):
        frame["date"] = pd.to_datetime(frame.date)
    industry["begin_date"] = pd.to_datetime(industry.begin_date)
    industry["end_date"] = pd.to_datetime(industry.end_date, errors="coerce")
    print("sources", {"symbols": len(symbols), "price": len(price), "flow": len(flow),
                       "value": len(value), "indices": len(index_rows), "industry_intervals": len(industry)}, flush=True)
    return symbols, calendar, price, flow, value, industry, index_rows


def weekly_policy(frame, scores, cutoff):
    """Online rule: act on at most two days and five stocks per ISO week."""
    eligible = frame.loc[np.asarray(scores) >= cutoff, ["signal_date", "symbol", "gap"]].copy()
    eligible["score"] = np.asarray(scores)[np.asarray(scores) >= cutoff]
    if eligible.empty:
        return eligible
    eligible["week"] = eligible.signal_date.dt.strftime("%G-W%V")
    chosen = []
    for _, week in eligible.groupby("week", sort=True):
        remaining, active_days = WEEKLY_LIMIT, 0
        for _, day in week.groupby("signal_date", sort=True):
            if remaining == 0 or active_days == WEEKLY_ACTIVE_DAYS:
                break
            current = day.sort_values(["score", "symbol"], ascending=[False, True]).head(
                min(DAILY_LIMIT, remaining))
            if not current.empty:
                chosen.append(current)
                remaining -= len(current)
                active_days += 1
    return pd.concat(chosen, ignore_index=True) if chosen else eligible.iloc[:0]


def metrics(frame, total_weeks=None):
    if frame.empty:
        return {"signals": 0, "active_days": 0, "active_weeks": 0, "precision": None,
                "down_rate": None, "severe_down_rate": None, "mean_gap": None,
                "median_gap": None, "gap_p05": None, "gap_p95": None}
    gap = frame.gap
    result = {"signals": int(len(frame)), "active_days": int(frame.signal_date.nunique()),
              "active_weeks": int(frame.signal_date.dt.strftime("%G-W%V").nunique()),
              "precision": float(gap.gt(0).mean()), "down_rate": float(gap.lt(0).mean()),
              "severe_down_rate": float(gap.lt(-0.01).mean()), "mean_gap": float(gap.mean()),
              "median_gap": float(gap.median()), "gap_p05": float(gap.quantile(.05)),
              "gap_p95": float(gap.quantile(.95))}
    if total_weeks:
        result["weeks_available"] = int(total_weeks)
    return result


def weekly_bootstrap_lower(frame, all_weeks, seed=42):
    """Descriptive week-block lower decile, not an independent statistical guarantee."""
    if frame.empty:
        return None
    grouped = frame.assign(week=frame.signal_date.dt.strftime("%G-W%V")).groupby("week")
    counts = grouped.gap.agg(n="size", wins=lambda values: values.gt(0).sum())
    counts = counts.reindex(all_weeks, fill_value=0)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(all_weeks), size=(400, len(all_weeks)))
    n = counts.n.to_numpy()[draws].sum(axis=1)
    wins = counts.wins.to_numpy()[draws].sum(axis=1)
    return float(np.quantile(wins[n > 0] / n[n > 0], .10)) if np.any(n > 0) else None


def rank_buckets(frame, scores):
    ranked = frame[["signal_date", "symbol", "gap"]].copy()
    ranked["score"] = scores
    ranked = ranked.sort_values(["signal_date", "score", "symbol"], ascending=[True, False, True])
    ranked["rank"] = ranked.groupby("signal_date").cumcount() + 1
    groups = ((1, 1), (2, 5), (6, 20), (21, 100), (101, 500))
    return [{"rank": f"{lo}-{hi}", **metrics(ranked[ranked["rank"].between(lo, hi)])}
            for lo, hi in groups]


def _date_map(calendar):
    return dict(zip(calendar[:-1], calendar[1:]))


def prepare_dataset(calendar, price, flow, value, industry, index_rows):
    """Construct one decision row per stock/date without using same-day close as input."""
    price = price.sort_values(["symbol", "date"]).copy()
    for col in ("open", "high", "low", "close", "adjopen", "adjclose", "volume", "amount", "turn_ratio"):
        price[col] = pd.to_numeric(price[col], errors="coerce")
    flow[["main_ratio", "huge_ratio"]] = flow[["main_ratio", "huge_ratio"]].apply(pd.to_numeric, errors="coerce")
    value[["total_mv", "pe_ttm"]] = value[["total_mv", "pe_ttm"]].apply(pd.to_numeric, errors="coerce")
    price = price.merge(flow, on=["symbol", "date"], how="left", validate="one_to_one")
    price = price.merge(value, on=["symbol", "date"], how="left", validate="one_to_one")
    group = price.groupby("symbol", sort=False)
    for horizon in (1, 3, 5, 20):
        price[f"price_return_{horizon}"] = group.adjclose.pct_change(horizon, fill_method=None)
    price["price_volatility_20"] = price.groupby("symbol").price_return_1.transform(
        lambda s: s.rolling(20, min_periods=20).std())
    for n in (5, 20):
        price[f"price_amount_ratio_{n}"] = price.amount / price.groupby("symbol").amount.transform(
            lambda s: s.rolling(n, min_periods=n).mean())
    price["price_turnover"] = price.turn_ratio
    price["price_body"] = price.close / price.open - 1
    price["price_range"] = (price.high - price.low) / price.open
    price["price_gap"] = price.open / price.groupby("symbol").close.shift(1) - 1
    price["flow_main_ratio_1"] = price.main_ratio
    price["flow_huge_ratio_1"] = price.huge_ratio
    for n in (3, 5):
        price[f"flow_main_ratio_{n}"] = price.groupby("symbol").main_ratio.transform(
            lambda s: s.rolling(n, min_periods=n).mean())
    price["log_market_cap"] = np.log1p(price.total_mv.clip(lower=0))
    next_day = _date_map(calendar)
    price["signal_date"] = pd.to_datetime(price.date.map(next_day))
    cutoff = price.signal_date + pd.Timedelta(hours=14, minutes=45)
    price_updated = pd.to_datetime(price.update_time)
    price["previous_price_ready"] = price_updated.le(cutoff)
    price_history_updated = price_updated.astype("int64").groupby(price.symbol).transform(
        lambda s: s.rolling(21, min_periods=21).max())
    price.loc[price_history_updated.gt(cutoff.astype("int64")), list(PRICE)] = np.nan
    price.loc[pd.to_datetime(price.flow_updated).gt(cutoff), list(FLOW)] = np.nan
    flow_update_ns = pd.to_datetime(price.flow_updated).astype("int64")
    for n in (3, 5):
        late_history = flow_update_ns.groupby(price.symbol).transform(
            lambda s: s.rolling(n, min_periods=n).max()).gt(cutoff.astype("int64"))
        price.loc[late_history, f"flow_main_ratio_{n}"] = np.nan
    price.loc[pd.to_datetime(price.value_updated).gt(cutoff), list(VALUE)] = np.nan
    price.loc[~price.previous_price_ready, list(PRICE)] = np.nan
    previous = price[["symbol", "signal_date", "date", "amount", *PRICE, *FLOW, *VALUE]].rename(
        columns={"date": "previous_date", "amount": "previous_amount"})
    previous["previous_price_ready"] = price.previous_price_ready
    current = price[["symbol", "date", "adjclose"]].rename(
        columns={"date": "signal_date", "adjclose": "label_close"})
    current["next_date"] = current.signal_date.map(next_day)
    next_open = price[["symbol", "date", "adjopen"]].rename(
        columns={"date": "next_date", "adjopen": "label_next_open"})
    labels = current.merge(next_open, on=["symbol", "next_date"], how="left", validate="one_to_one")
    labels["gap"] = labels.label_next_open / labels.label_close - 1
    data = previous.merge(labels[["symbol", "signal_date", "gap"]], on=["symbol", "signal_date"],
                          how="left", validate="one_to_one")
    data = data[data.signal_date.notna()].copy()

    industry = industry.sort_values("begin_date")
    data = pd.merge_asof(data.sort_values("signal_date"), industry.sort_values("begin_date"),
        left_on="signal_date", right_on="begin_date", by="symbol", direction="backward")
    data.loc[data.end_date.notna() & data.signal_date.ge(data.end_date), "industry_name"] = np.nan

    index_rows = index_rows.sort_values(["idx_code", "date"]).copy()
    index_rows["close"] = pd.to_numeric(index_rows.close, errors="coerce")
    index_rows["amount"] = pd.to_numeric(index_rows.amount, errors="coerce")
    index_group = index_rows.groupby("idx_code", sort=False)
    for n in (1, 5, 20):
        index_rows[f"return_{n}"] = index_group.close.pct_change(n, fill_method=None)
    index_rows["amount_ratio_5"] = index_rows.amount / index_rows.groupby("idx_code").amount.transform(
        lambda s: s.rolling(5, min_periods=5).mean())
    index_rows["signal_date"] = pd.to_datetime(index_rows.date.map(next_day))
    index_cutoff = index_rows.signal_date + pd.Timedelta(hours=14, minutes=45)
    index_history_updated = pd.to_datetime(index_rows.update_time).astype("int64").groupby(
        index_rows.idx_code).transform(lambda s: s.rolling(21, min_periods=21).max())
    index_rows.loc[index_history_updated.gt(index_cutoff.astype("int64")),
                   ["return_1", "return_5", "return_20", "amount_ratio_5"]] = np.nan
    for code, prefix, fields in (("000300.SH", "csi300", ("return_1", "return_5", "return_20", "amount_ratio_5")),
                                 ("000852.SH", "csi1000", ("return_1", "return_5"))):
        one = index_rows[index_rows.idx_code.eq(code)][["signal_date", *fields]].copy()
        one = one.rename(columns={field: f"{prefix}_{field}" for field in fields})
        data = data.merge(one, on="signal_date", how="left", validate="many_to_one")
    sector = index_rows[index_rows.idx_code.str.startswith("801")][
        ["signal_date", "index_short_name", "return_1", "return_5"]].rename(columns={
            "index_short_name": "industry_name", "return_1": "sector_return_1", "return_5": "sector_return_5"})
    sector = sector.drop_duplicates(["signal_date", "industry_name"])
    data = data.merge(sector, on=["signal_date", "industry_name"], how="left", validate="many_to_one")
    data["sector_relative_5"] = data.sector_return_5 - data.csi300_return_5
    audit = data.assign(month=data.signal_date.dt.to_period("M").astype(str),
                        amount_ok=data.previous_amount.gt(1e7),
                        history_ok=data.price_return_20.notna(),
                        label_ok=data.gap.notna() & np.isfinite(data.gap)).groupby("month").agg(
                            rows=("symbol", "size"), amount_ok=("amount_ok", "sum"),
                            history_ok=("history_ok", "sum"), label_ok=("label_ok", "sum"))
    print("first-month coverage", audit.head(8).to_dict("index"), flush=True)
    data = data[data.previous_price_ready & (data.previous_amount > 1e7) &
                data.price_return_20.notna() &
                data.gap.notna() & np.isfinite(data.gap)]
    data["positive"] = data.gap.gt(0)
    return data[["symbol", "signal_date", "gap", "positive", *ALL_FEATURES]].sort_values(
        ["signal_date", "symbol"]).reset_index(drop=True)


def _score(model, frame, columns):
    matrix = frame[list(columns)].replace([np.inf, -np.inf], np.nan)
    if hasattr(model, "predict_proba"):
        return model.predict_proba(matrix)[:, 1]
    return model.decision_function(matrix)


def evaluate_models(data):
    train = data[data.signal_date.between(TRAIN_START, TRAIN_END)]
    valid = data[data.signal_date.between(VALID_START, VALID_END)]
    test = data[data.signal_date.between(TEST_START, TEST_END)]
    if min(train.signal_date.nunique(), valid.signal_date.nunique(), test.signal_date.nunique()) < 100:
        raise ValueError("chronological periods require at least 100 decision dates each")
    print("split", [(name, len(frame), frame.signal_date.nunique())
                    for name, frame in (("train", train), ("valid", valid), ("test", test))], flush=True)
    configurations = {
        "logistic_price": (PRICE, Pipeline([("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()), ("model", LogisticRegression(C=.2, max_iter=300))])),
        "logistic_price_flow": (PRICE + FLOW, Pipeline([("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()), ("model", LogisticRegression(C=.2, max_iter=300))])),
        "logistic_price_flow_market": (PRICE + FLOW + MARKET, Pipeline([("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()), ("model", LogisticRegression(C=.2, max_iter=300))])),
        "logistic_all": (ALL_FEATURES, Pipeline([("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()), ("model", LogisticRegression(C=.2, max_iter=300))])),
        "linear_svm_all": (ALL_FEATURES, Pipeline([("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()), ("model", LinearSVC(C=.1, dual=False, max_iter=3000, random_state=42))])),
        "shallow_tree_all": (ALL_FEATURES, Pipeline([("impute", SimpleImputer(strategy="median")),
            ("model", DecisionTreeClassifier(max_depth=3, min_samples_leaf=500, random_state=42))])),
        "hist_boost_all": (ALL_FEATURES, HistGradientBoostingClassifier(max_iter=80, max_leaf_nodes=7,
            min_samples_leaf=300, learning_rate=.05, l2_regularization=2, random_state=42)),
    }
    all_valid_weeks = sorted(valid.signal_date.dt.strftime("%G-W%V").unique())
    all_test_weeks = sorted(test.signal_date.dt.strftime("%G-W%V").unique())
    results = []
    for name, (columns, model) in configurations.items():
        model.fit(train[list(columns)].replace([np.inf, -np.inf], np.nan), train.positive.astype(int))
        val_score = _score(model, valid, columns)
        trial_rows = []
        for q in QUANTILES:
            threshold = float(np.quantile(val_score, q))
            picks = weekly_policy(valid, val_score, threshold)
            evidence = metrics(picks, len(all_valid_weeks))
            evidence["week_bootstrap_lower_decile"] = weekly_bootstrap_lower(picks, all_valid_weeks)
            trial_rows.append({"quantile": q, "threshold": threshold, "validation": evidence})
        supported = [row for row in trial_rows if row["validation"]["signals"] >= 40
                     and row["validation"]["active_weeks"] >= 10]
        chosen = max(supported, key=lambda row: (
            row["validation"]["week_bootstrap_lower_decile"], row["validation"]["precision"],
            row["validation"]["active_weeks"])) if supported else None
        result = {"model": name, "features": columns, "validation_trials": trial_rows,
                  "chosen": None, "test_rank_buckets": None}
        if chosen:
            test_score = _score(model, test, columns)
            test_picks = weekly_policy(test, test_score, chosen["threshold"])
            validation_picks = weekly_policy(valid, val_score, chosen["threshold"])
            validation_days = valid[valid.signal_date.isin(validation_picks.signal_date)]
            test_days = test[test.signal_date.isin(test_picks.signal_date)]
            result["chosen"] = {**chosen, "test": metrics(test_picks, len(all_test_weeks)),
                                "validation_selected_day_pool_rate": float(validation_days.positive.mean()),
                                "test_selected_day_pool_rate": float(test_days.positive.mean())
                                    if not test_days.empty else None,
                                "test_week_bootstrap_lower_decile": weekly_bootstrap_lower(test_picks, all_test_weeks),
                                "test_by_month": [{"month": month, **metrics(part)} for month, part in
                                  test_picks.groupby(test_picks.signal_date.dt.to_period("M").astype(str))]}
            result["test_rank_buckets"] = rank_buckets(test, test_score)
            print(name, "val", chosen["validation"], "test", result["chosen"]["test"], flush=True)
        else:
            print(name, "no supported validation policy", flush=True)
        results.append(result)
    eligible_models = [row for row in results if row["chosen"]]
    validation_choice = max(eligible_models, key=lambda row: (
        row["chosen"]["validation"]["week_bootstrap_lower_decile"],
        row["chosen"]["validation"]["precision"]))["model"] if eligible_models else None
    return {"split": {name: {"rows": len(frame), "dates": frame.signal_date.nunique(),
                              "first": str(frame.signal_date.min())[:10], "last": str(frame.signal_date.max())[:10],
                              "base_high_open_rate": float(frame.positive.mean())}
                      for name, frame in (("train", train), ("validation", valid), ("test", test))},
            "feature_nonmissing_train": {column: float(train[column].notna().mean()) for column in ALL_FEATURES},
            "model_selected_on_validation": validation_choice,
            "models": results}


def main(output, sample_symbols):
    load_dotenv(".env")
    symbols, calendar, price, flow, value, industry, indices = load_sources(sample_symbols)
    data = prepare_dataset(calendar, price, flow, value, industry, indices)
    result = evaluate_models(data)
    script_path = Path(__file__)
    result.update({"as_of": datetime.now().astimezone().isoformat(),
                   "objective": "At 14:45, rank next-session adjusted open above current adjusted close, using only preceding completed sessions",
                   "source": "read-only kingdomai; sampled historical A-share universe",
                   "sample_symbols": len(symbols), "sample_seed": 20261007,
                   "labelled_rows": len(data), "labelled_dates": data.signal_date.nunique(),
                   "weekly_policy": {"stocks_per_week": WEEKLY_LIMIT, "active_days_per_week": WEEKLY_ACTIVE_DAYS,
                                     "stocks_per_active_day": DAILY_LIMIT, "minimum_validation_signals": 40,
                                     "minimum_validation_weeks": 10},
                   "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
                   "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                   "environment": {"pandas": pd.__version__, "numpy": np.__version__, "scikit_learn": sklearn.__version__},
                   "limitations": ["The 1000-company seeded sample is not a full-market daily ranking.",
                       "Same-day 14:45 minute variables are absent from this long-history baseline; adding a completed daily bar would leak future information.",
                       "Money-flow and valuation tables do not retain complete historical revisions; last updates later than the cutoff are excluded.",
                       "Signals in the same week and market regime are correlated; bootstrap lower decile is descriptive, not a guaranteed confidence bound.",
                       "A higher model score is not necessarily a calibrated probability; ranking and weekly precision are evaluated separately.",
                       "High-open direction is distinct from executable 14:45-to-next-open net return."]})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str, allow_nan=False))
    print("saved", output, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-symbols", type=int, default=SAMPLE_SYMBOLS)
    parser.add_argument("--output", default="outputs/stock_automl/next_open_weekly/long_history.json")
    args = parser.parse_args()
    main(Path(args.output), args.sample_symbols)

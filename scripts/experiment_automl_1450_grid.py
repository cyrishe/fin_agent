"""Exploratory, read-only 14:50 -> next 09:35 stock-selection grid.

All features end by the 14:49 minute bar.  The 14:50 minute close is only an
entry-price proxy; minute OHLCV cannot establish an actual order fill.  Only
aggregate results are saved, never source market rows or fitted models.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


START = "2026-08-24"
END = "2026-09-30"
MARKET = ("market_return", "market_breadth", "market_dispersion",
          "sector_return", "sector_breadth", "sector_relative")
RECENT = ("prior_return_1", "prior_return_3", "prior_return_5",
          "prior_volatility_5", "prior_amount_ratio_5", "prior_turnover")
INTRADAY = ("day_return", "open_return", "tail_return_19m",
            "tail_return_9m", "tail_return_4m", "tail_amount_ratio")
GROUPS = {"market": MARKET, "recent": RECENT, "intraday": INTRADAY}
COMBINATIONS = ("market", "recent", "intraday", "market+recent",
                "market+intraday", "recent+intraday", "market+recent+intraday")


def frame(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return pd.DataFrame(cur.fetchall())


@contextmanager
def read_only_db():
    load_dotenv(".env")
    import os
    raw = os.environ.get("SIMPLE_BI_PLATFORM_DB_URL", "")
    parsed = urlparse(raw.replace("mysql+pymysql://", "mysql://", 1))
    if parsed.hostname != "47.94.1.2" or parsed.port != 3312:
        raise ValueError("SIMPLE_BI_PLATFORM_DB_URL must identify the confirmed new host")
    conn = pymysql.connect(
        host=parsed.hostname, port=parsed.port, user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""), database="kingdomai",
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=8, read_timeout=60, autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION MAX_EXECUTION_TIME=50000")
            cur.execute("SET TRANSACTION READ ONLY")
        yield conn
    finally:
        conn.rollback()
        conn.close()


def minute_points(conn, day):
    stamp = lambda t: f"{day} {t}:00"
    rows = frame(conn, """
        SELECT stk_code AS symbol6,
          MAX(CASE WHEN bar_end_time=%s THEN stk_name END) AS name,
          MAX(CASE WHEN bar_end_time=%s THEN open_price END) AS day_open,
          MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS p1430,
          MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS p1440,
          MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS p1445,
          MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS p1449,
          MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS entry,
          MAX(CASE WHEN bar_end_time=%s THEN amount END) AS entry_amount,
          SUM(CASE WHEN bar_end_time BETWEEN %s AND %s THEN amount ELSE 0 END) AS amount_prev10,
          SUM(CASE WHEN bar_end_time BETWEEN %s AND %s THEN amount ELSE 0 END) AS amount_tail9,
          COUNT(*) AS bars, MAX(is_fallback) AS fallback,
          MIN(is_finalized) AS finalized
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND (bar_end_time=%s OR bar_end_time BETWEEN %s AND %s)
        GROUP BY stk_code
    """, (stamp("14:49"), stamp("09:31"), stamp("14:30"), stamp("14:40"),
          stamp("14:45"), stamp("14:49"), stamp("14:50"),
          stamp("14:50"), stamp("14:31"), stamp("14:40"), stamp("14:41"),
          stamp("14:49"), day, stamp("09:31"), stamp("14:30"), stamp("14:50")))
    rows["date"] = pd.Timestamp(day)
    return rows


def morning_label(conn, day):
    rows = frame(conn, """
        SELECT stk_code AS symbol6, MAX(high_price) AS high5,
          MIN(low_price) AS low5,
          MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS close935,
          SUM(volume) AS morning_volume, COUNT(*) AS bars5,
          MAX(is_fallback) AS fallback5, MIN(is_finalized) AS finalized5
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time BETWEEN %s AND %s
        GROUP BY stk_code
    """, (f"{day} 09:35:00", day, f"{day} 09:31:00", f"{day} 09:35:00"))
    rows["next_date"] = pd.Timestamp(day)
    return rows


def read_daily(conn):
    daily = frame(conn, """
        SELECT trade_date AS date, LEFT(stk_code, 6) AS symbol6,
          close, preclose, amount, turn_ratio
        FROM kcrp_stock_price
        WHERE trade_date BETWEEN %s AND %s
    """, ("2026-07-15", END))
    daily["date"] = pd.to_datetime(daily.date)
    daily = daily.sort_values(["symbol6", "date"])
    daily = daily.drop_duplicates(["symbol6", "date"], keep=False)
    for col in ("close", "preclose", "amount", "turn_ratio"):
        daily[col] = pd.to_numeric(daily[col], errors="coerce")
    daily["daily_return"] = daily.close / daily.preclose - 1
    grp = daily.groupby("symbol6", sort=False)
    shifted_return = grp.daily_return.shift(1)
    daily["prior_return_1"] = shifted_return
    logged = np.log1p(shifted_return.where(shifted_return > -1))
    for n in (3, 5):
        daily[f"prior_return_{n}"] = np.expm1(logged.groupby(daily.symbol6).transform(
            lambda s: s.rolling(n, min_periods=n).sum()))
    daily["prior_volatility_5"] = shifted_return.groupby(daily.symbol6).transform(
        lambda s: s.rolling(5, min_periods=5).std())
    prior_amount = grp.amount.shift(1)
    older_amount = grp.amount.shift(2)
    daily["prior_amount_ratio_5"] = prior_amount / older_amount.groupby(daily.symbol6).transform(
        lambda s: s.rolling(5, min_periods=5).mean())
    daily["prior_turnover"] = grp.turn_ratio.shift(1)
    daily["prior_amount"] = prior_amount
    daily["today_preclose"] = daily.preclose
    daily["today_close"] = daily.close
    daily["observed_history"] = grp.cumcount()
    return daily[["date", "symbol6", "today_preclose", "today_close", "prior_amount", "observed_history", *RECENT]]


def read_industries(conn):
    industry = frame(conn, """
        SELECT LEFT(stk_code, 6) AS symbol6, industry_name AS industry,
          begin_date, end_date
        FROM kcrp_stock_industry
        WHERE industry_type='SW2021' AND level=1 AND begin_date<=%s
          AND (end_date IS NULL OR end_date>=%s)
    """, (END, START))
    industry["begin_date"] = pd.to_datetime(industry.begin_date)
    industry["end_date"] = pd.to_datetime(industry.end_date, errors="coerce")
    return industry


def board_limit(symbol, name):
    """Conservative near-limit exclusion, not a substitute for a limit-price feed."""
    symbol, name = str(symbol), str(name).upper()
    if "ST" in name:
        return np.nan  # Exclude all risk-warning stocks rather than guess their current rule.
    if symbol.startswith(("300", "301", "688", "689")):
        return 0.195
    if symbol.startswith(("4", "8", "92")):
        return 0.295
    if symbol.startswith(("000", "001", "002", "003", "600", "601", "603", "605")):
        return 0.095
    return np.nan


def build_dataset(conn):
    dates = frame(conn, """
        SELECT DISTINCT trade_date AS date FROM kcrp_stock_price
        WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date
    """, (START, END)).date.astype(str).tolist()
    daily = read_daily(conn)
    industry = read_industries(conn)
    minute = []
    labels = []
    for index, day in enumerate(dates, 1):
        points = minute_points(conn, day)
        valid = industry[(industry.begin_date <= pd.Timestamp(day)) &
                         (industry.end_date.isna() | (industry.end_date >= pd.Timestamp(day)))]
        valid = valid.sort_values("begin_date").drop_duplicates("symbol6", keep="last")
        minute.append(points.merge(valid[["symbol6", "industry"]], on="symbol6", how="left"))
        labels.append(morning_label(conn, day))
        print(f"minute {index}/{len(dates)} {day}: {len(points)}", flush=True)
    signal = pd.concat(minute, ignore_index=True)
    morning = pd.concat(labels, ignore_index=True)
    next_date = dict(zip(pd.to_datetime(dates[:-1]), pd.to_datetime(dates[1:])))
    signal["next_date"] = signal.date.map(next_date)
    signal = signal.merge(daily, on=["date", "symbol6"], how="left", validate="one_to_one")
    signal["preclose"] = signal.today_preclose
    numeric = ("day_open", "p1430", "p1440", "p1445", "p1449", "preclose",
               "entry", "entry_amount", "amount_prev10", "amount_tail9", "bars",
               "fallback", "finalized", "prior_amount", "observed_history")
    for col in numeric:
        signal[col] = pd.to_numeric(signal[col], errors="coerce")
    print("filter diagnostic", {"joined": len(signal), "bars22": int(signal.bars.eq(22).sum()),
          "fallback0": int(signal.fallback.eq(0).sum()),
          "finalized1": int(signal.finalized.eq(1).sum()),
          "prices_positive": int((signal[["day_open", "p1430", "p1440", "p1445", "p1449", "preclose", "entry"]] > 0).all(axis=1).sum()),
          "individual_prices_positive": {col: int(signal[col].gt(0).sum()) for col in
              ("day_open", "p1430", "p1440", "p1445", "p1449", "preclose", "entry")},
          "prior_amount_10m": int(signal.prior_amount.ge(10_000_000).sum()),
          "entry_amount_100k": int(signal.entry_amount.ge(100_000).sum()),
          "history6": int(signal.observed_history.ge(6).sum()),
          "next_date": int(signal.next_date.notna().sum())}, flush=True)
    valid = (signal.bars.eq(22) & signal.fallback.eq(0) & signal.finalized.eq(1) &
             (signal[["day_open", "p1430", "p1440", "p1445", "p1449", "preclose", "entry"]] > 0).all(axis=1) &
             signal.entry_amount.ge(100_000) & signal.prior_amount.ge(10_000_000) &
             signal.observed_history.ge(6) & signal.next_date.notna())
    signal = signal.loc[valid].copy()
    print("after basic filters", len(signal), flush=True)
    signal["limit_buffer"] = [board_limit(s, n) for s, n in zip(signal.symbol6, signal.name)]
    signal["day_return"] = signal.p1449 / signal.preclose - 1
    signal = signal[signal.limit_buffer.notna() &
                    signal.day_return.abs().lt(signal.limit_buffer)].copy()
    print("after near-limit filter", len(signal), flush=True)
    signal["open_return"] = signal.p1449 / signal.day_open - 1
    signal["tail_return_19m"] = signal.p1449 / signal.p1430 - 1
    signal["tail_return_9m"] = signal.p1449 / signal.p1440 - 1
    signal["tail_return_4m"] = signal.p1449 / signal.p1445 - 1
    signal["tail_amount_ratio"] = signal.amount_tail9 / signal.amount_prev10.replace(0, np.nan)
    # Breadth is computed from the as-of eligible universe, never from next-day outcomes.
    by_day = signal.groupby("date")
    signal["market_return"] = by_day.day_return.transform("mean")
    signal["market_breadth"] = signal.day_return.gt(0).groupby(signal.date).transform("mean")
    signal["market_dispersion"] = by_day.day_return.transform("std")
    by_sector = signal.groupby(["date", "industry"])
    signal["sector_return"] = by_sector.day_return.transform("mean")
    signal["sector_breadth"] = signal.day_return.gt(0).groupby([signal.date, signal.industry]).transform("mean")
    signal["sector_relative"] = signal.sector_return - signal.market_return
    signal = signal.merge(morning, on=["symbol6", "next_date"], how="left", validate="one_to_one")
    print("after label join", len(signal), flush=True)
    next_reference = daily[["date", "symbol6", "today_preclose"]].rename(
        columns={"date": "next_date", "today_preclose": "next_preclose"})
    signal = signal.merge(next_reference, on=["next_date", "symbol6"], how="left", validate="one_to_one")
    signal["reference_gap"] = (signal.next_preclose / signal.today_close - 1).abs()
    print("cross-date reference gap >0.5% (audit only)",
          int(signal.reference_gap.gt(0.005).sum()), flush=True)
    for col in ("high5", "low5", "close935", "morning_volume", "bars5", "fallback5", "finalized5"):
        signal[col] = pd.to_numeric(signal[col], errors="coerce")
    signal["label_observed"] = ((signal.bars5.eq(5)) & (signal.fallback5.eq(0)) &
                                 (signal.finalized5.eq(1)) & (signal.morning_volume > 0) &
                                 (signal[["high5", "low5", "close935"]] > 0).all(axis=1))
    print("unobserved next morning", int((~signal.label_observed).sum()), flush=True)
    signal.loc[~signal.label_observed, ["high5", "low5", "close935"]] = np.nan
    signal["high_return"] = signal.high5 / signal.entry - 1
    signal["low_return"] = signal.low5 / signal.entry - 1
    signal["exit_return"] = signal.close935 / signal.entry - 1
    signal["hit1"] = signal.high_return.ge(0.01)
    signal["hit2"] = signal.high_return.ge(0.02)
    return signal.sort_values(["date", "symbol6"]).reset_index(drop=True), dates


def select(frame, scores, k, gate):
    chosen = frame.copy()
    chosen["score"] = scores
    chosen = chosen[chosen.score >= gate]
    return chosen.sort_values(["date", "score", "symbol6"], ascending=[True, False, True]).groupby("date").head(k)


def metrics(rows, target):
    if not len(rows):
        return {"signals": 0, "days": 0, "unobserved": 0, "hit_rate": None, "safe_rate": None,
                "hit_after_30bp": None,
                "severe_down_rate": None, "exit_safe_rate": None,
                "exit_nonnegative_rate": None, "exit_severe_down_rate": None,
                "exit_severe_or_unobserved_rate": None,
                "mean_exit_return": None,
                "mean_exit_return_net_30bp": None}
    mean_exit = rows.exit_return.mean()
    return {"signals": int(len(rows)), "days": int(rows.date.nunique()),
            "unobserved": int((~rows.label_observed).sum()),
            "hit_rate": round(float(rows[target].mean()), 5),
            "hit_after_30bp": round(float(rows.high_return.ge(
                (0.01 if target == "hit1" else 0.02) + 0.003).mean()), 5),
            "safe_rate": round(float(rows.low_return.ge(-0.005).mean()), 5),
            "severe_down_rate": round(float(rows.low_return.lt(-0.01).mean()), 5),
            "exit_safe_rate": round(float(rows.exit_return.ge(-0.005).mean()), 5),
            "exit_nonnegative_rate": round(float(rows.exit_return.ge(0).mean()), 5),
            "exit_severe_down_rate": round(float(rows.exit_return.lt(-0.01).mean()), 5),
            "exit_severe_or_unobserved_rate": round(float((rows.exit_return.lt(-0.01) |
                                                            ~rows.label_observed).mean()), 5),
            "mean_exit_return": round(float(mean_exit), 6) if pd.notna(mean_exit) else None,
            "mean_exit_return_net_30bp": round(float(mean_exit - 0.003), 6) if pd.notna(mean_exit) else None}


def model_grid():
    return {
        "logit_c0.1": lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                              LogisticRegression(C=0.1, max_iter=300)),
        "logit_c1": lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                          LogisticRegression(C=1.0, max_iter=300)),
        "hgb_leaf7": lambda: HistGradientBoostingClassifier(max_iter=70, max_leaf_nodes=7,
            min_samples_leaf=200, learning_rate=0.06, l2_regularization=3, random_state=42),
        "hgb_leaf15": lambda: HistGradientBoostingClassifier(max_iter=70, max_leaf_nodes=15,
            min_samples_leaf=200, learning_rate=0.06, l2_regularization=3, random_state=42),
    }


def experiment(data, dates):
    usable = sorted(data.date.unique())
    if len(usable) < 20:
        raise ValueError(f"only {len(usable)} labeled days: too few for this split")
    train_days, val_days, test_days = usable[:-10], usable[-10:-5], usable[-5:]
    splits = [data[data.date.isin(days)].copy() for days in (train_days, val_days, test_days)]
    train, val, test = splits
    train_fit = train[train.label_observed].copy()
    out = {"source": "47.94.1.2:3312/kingdomai", "table": "aiia_stock_realtime_minute_snapshot_full",
           "date_range": [dates[0], dates[-1]], "split": {"train": [str(train_days[0])[:10], str(train_days[-1])[:10]],
           "validation": [str(val_days[0])[:10], str(val_days[-1])[:10]],
           "test": [str(test_days[0])[:10], str(test_days[-1])[:10]]},
           "rows": {"train": len(train), "train_labeled": len(train_fit),
                    "validation": len(val), "test": len(test)},
           "data_quality": {"unobserved_next_morning": int((~data.label_observed).sum()),
                            "cross_date_reference_gap_gt_0.5pct": int(data.reference_gap.gt(0.005).sum())},
           "baseline": {}, "daily_baseline": {}, "results": [], "risk_adjusted_results": []}
    for target in ("hit1", "hit2"):
        out["baseline"][target] = {"train_all_eligible": metrics(train, target),
                                    "validation_all_eligible": metrics(val, target),
                                    "all_eligible": metrics(test, target)}
        out["daily_baseline"][target] = {
            str(day)[:10]: metrics(chunk, target)
            for day, chunk in data.groupby("date")
        }
        for col in ("prior_return_5", "day_return"):
            base = test.sort_values(["date", col], ascending=[True, False]).groupby("date").head(3)
            out["baseline"][target][f"top3_{col}"] = metrics(base, target)
        for combo in COMBINATIONS:
            features = [col for name in combo.split("+") for col in GROUPS[name]]
            valid_candidates = []
            for model_name, factory in model_grid().items():
                model = factory()
                model.fit(train_fit[features].replace([np.inf, -np.inf], np.nan),
                          train_fit[target].astype(int))
                val_scores = model.predict_proba(val[features].replace([np.inf, -np.inf], np.nan))[:, 1]
                test_scores = model.predict_proba(test[features].replace([np.inf, -np.inf], np.nan))[:, 1]
                val_max = pd.Series(val_scores, index=val.index).groupby(val.date).max().to_numpy()
                for quantile in (0.0, 0.5):
                    gate = float(np.quantile(val_max, quantile))
                    for top_k in (1, 3, 5):
                        val_picked = select(val, val_scores, top_k, gate)
                        vm = metrics(val_picked, target)
                        if vm["signals"] < 8 or vm["days"] < 3:
                            continue
                        test_picked = select(test, test_scores, top_k, gate)
                        score = (vm["hit_rate"] + 0.25 * vm["safe_rate"] -
                                 0.5 * vm["severe_down_rate"] - vm["unobserved"] / vm["signals"])
                        valid_candidates.append((score, vm["signals"], model_name, quantile,
                                                 top_k, gate, vm, metrics(test_picked, target)))
                print(f"grid {target} {combo} {model_name}", flush=True)
            if not valid_candidates:
                out["results"].append({"target": target, "groups": combo, "status": "no_valid_policy"})
                continue
            best = max(valid_candidates, key=lambda x: (x[0], x[1]))
            out["results"].append({"target": target, "groups": combo, "model": best[2],
                "gate_quantile_of_validation_daily_max": best[3], "top_k_per_day": best[4],
                "gate": round(best[5], 6), "validation": best[6], "test": best[7],
                "trials_considered": len(valid_candidates)})
            # A separate two-head grid reflects the user's asymmetric utility:
            # missing an upside target is tolerable; a material morning loss is not.
            risk_candidates = []
            loss_train = train_fit.exit_return.lt(-0.01).astype(int)
            for risk_model_name in ("logit_c0.1", "hgb_leaf7"):
                factory = model_grid()[risk_model_name]
                hit_model, loss_model = factory(), factory()
                x_train = train_fit[features].replace([np.inf, -np.inf], np.nan)
                x_val = val[features].replace([np.inf, -np.inf], np.nan)
                x_test = test[features].replace([np.inf, -np.inf], np.nan)
                hit_model.fit(x_train, train_fit[target].astype(int))
                loss_model.fit(x_train, loss_train)
                hit_val = hit_model.predict_proba(x_val)[:, 1]
                hit_test = hit_model.predict_proba(x_test)[:, 1]
                loss_val = loss_model.predict_proba(x_val)[:, 1]
                loss_test = loss_model.predict_proba(x_test)[:, 1]
                for penalty in (0.5, 1.0, 2.0, 4.0):
                    val_score = hit_val - penalty * loss_val
                    test_score = hit_test - penalty * loss_test
                    val_max = pd.Series(val_score, index=val.index).groupby(val.date).max().to_numpy()
                    for quantile in (0.0, 0.5):
                        gate = float(np.quantile(val_max, quantile))
                        for top_k in (1, 3, 5):
                            val_picked = select(val, val_score, top_k, gate)
                            vm = metrics(val_picked, target)
                            if vm["signals"] < 8 or vm["days"] < 3:
                                continue
                            test_picked = select(test, test_score, top_k, gate)
                            utility = (vm["hit_rate"] + 0.5 * vm["exit_safe_rate"] -
                                       1.5 * vm["exit_severe_or_unobserved_rate"])
                            risk_candidates.append((utility, vm["signals"], risk_model_name,
                                                    penalty, quantile, top_k, gate, vm,
                                                    metrics(test_picked, target)))
                print(f"risk grid {target} {combo} {risk_model_name}", flush=True)
            if risk_candidates:
                best_risk = max(risk_candidates, key=lambda x: (x[0], x[1]))
                out["risk_adjusted_results"].append({
                    "target": target, "groups": combo, "model": best_risk[2],
                    "loss_probability_penalty": best_risk[3],
                    "gate_quantile_of_validation_daily_max": best_risk[4],
                    "top_k_per_day": best_risk[5], "gate": round(best_risk[6], 6),
                    "validation": best_risk[7], "test": best_risk[8],
                    "trials_considered": len(risk_candidates)})
    return out


def export_research_model(data, result, model_path, summary_path):
    """Persist the exact train-window two-head research model, not a live signal."""
    import joblib
    import sklearn

    selected = next(row for row in result["risk_adjusted_results"]
                    if row["target"] == "hit1" and row["groups"] == "market+intraday")
    train_days = sorted(data.date.unique())[:-10]
    train = data[data.date.isin(train_days) & data.label_observed]
    features = [*MARKET, *INTRADAY]
    x_train = train[features].replace([np.inf, -np.inf], np.nan)
    factory = model_grid()[selected["model"]]
    hit_model, loss_model = factory(), factory()
    hit_model.fit(x_train, train.hit1.astype(int))
    loss_model.fit(x_train, train.exit_return.lt(-0.01).astype(int))
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"hit_model": hit_model, "loss_model": loss_model,
                 "features": features, "research_only": True}, model_path, compress=3)
    checksum = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "purpose": "research_only_not_live_trading", "model_file": model_path.name,
        "model_sha256": checksum(model_path), "script_sha256": checksum(Path(__file__)),
        "summary_sha256": checksum(summary_path), "source_table": result["table"],
        "source_host_schema": result["source"], "split": result["split"],
        "training_labeled_rows": len(train), "features": features,
        "selected_by_validation": selected, "python_sklearn_version": sklearn.__version__,
        "serialization": "joblib/pickle; load only from trusted repository artifacts",
        "data_snapshot_version": None,
    }
    manifest_path = model_path.with_name(model_path.stem + "_manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n")
    print(f"wrote research model: {model_path} ({model_path.stat().st_size} bytes)")
    print(f"wrote model manifest: {manifest_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/stock_automl_runs/20261007_1450_grid/summary.json")
    parser.add_argument("--export-research-model", type=Path)
    args = parser.parse_args()
    with read_only_db() as conn:
        data, dates = build_dataset(conn)
    print(f"eligible rows={len(data)} labeled days={data.date.nunique()}", flush=True)
    result = experiment(data, dates)
    result["generated_at"] = datetime.now().isoformat(timespec="seconds")
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    print(f"wrote aggregate results: {target}")
    if args.export_research_model:
        export_research_model(data, result, args.export_research_model, target)


if __name__ == "__main__":
    main()

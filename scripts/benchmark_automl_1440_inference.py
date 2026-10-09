"""Time one read-only 14:40 inference pass using a full A-share batch quote API.

The batch API has no exchange timestamp. Run near 14:40 for a live decision;
an after-hours run measures latency only, not 14:40 signal quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from itertools import combinations
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
import pymysql
import requests
import sklearn
from dotenv import load_dotenv
from scipy.special import expit


QUOTE_URL = "http://jzyzwup.upoem1.com/json/hq_basichq/stockHq"
MODEL = Path("docs/stock_automl_runs/20261008_sina_15m_july/exact_trained_seven_factor.joblib")
MANIFEST = MODEL.with_name("frozen_models_manifest.json")
REFERENCE = MODEL.with_name("exact_top5_two_models.csv")
FEATURES = ("signal_return", "volume_ratio", "turnover_so_far_pct",
            "float_mv_100m_cny", "volume_4of5_increasing",
            "ma_bull_5_10_20", "all_intraday_lows_above_ma")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def db_connection(env_file=Path(".env")):
    load_dotenv(env_file)
    raw = (os.environ.get("SIMPLE_BI_PLATFORM_DB_URL") or
           os.environ.get("PLATFORM_DB_URL", ""))
    parsed = urlparse(raw.replace("mysql+pymysql://", "mysql://", 1))
    if parsed.hostname != "47.94.1.2" or parsed.port != 3312:
        raise ValueError("Read-only benchmark requires the configured kingdomai host")
    conn = pymysql.connect(
        host=parsed.hostname, port=parsed.port, user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""), database="kingdomai",
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=8, read_timeout=60, autocommit=False,
    )
    with conn.cursor() as cur:
        cur.execute("SET SESSION MAX_EXECUTION_TIME=50000")
        cur.execute("SET TRANSACTION READ ONLY")
    return conn


def query(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return pd.DataFrame(cur.fetchall())


def exchange(code):
    return 1 if code.startswith("6") else 7 if code.startswith(("4", "8", "9")) else 0


def fetch_batch(codes, timeout):
    payload = {"stReq": {"vStock": [
        {"shtSetcode": exchange(code), "sCode": code} for code in codes], "eHqData": 1}}
    start = time.perf_counter()
    response = requests.post(QUOTE_URL, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    raw = data.get("stRsp", {}).get("vStockHq")
    if not isinstance(raw, list):
        raise ValueError("Quote batch has no vStockHq")
    rows = []
    for item in raw:
        q = item.get("stSimHq") or {}
        rows.append({"symbol6": str(item.get("sCode", ""))[:6],
                     "name": item.get("sName", ""),
                     "signal_price": q.get("fNowPrice"),
                     "preclose": q.get("fClose"),
                     "volume_hands": q.get("lVolume"),
                     "low_so_far": q.get("fLow")})
    return rows, time.perf_counter() - start, len(response.content)


def fetch_full_market(codes, batch_size=200, workers=6, timeout=12):
    started_at = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    start = time.perf_counter()
    batches = [codes[i:i + batch_size] for i in range(0, len(codes), batch_size)]
    collected, latencies, byte_counts = [], [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_batch, batch, timeout) for batch in batches]
        for future in as_completed(futures):
            rows, seconds, nbytes = future.result()
            collected.extend(rows)
            latencies.append(seconds)
            byte_counts.append(nbytes)
    elapsed = time.perf_counter() - start
    ended_at = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    quotes = pd.DataFrame(collected)
    quotes = quotes[quotes.symbol6.isin(codes)].drop_duplicates("symbol6", keep=False)
    for col in ("signal_price", "preclose", "volume_hands", "low_so_far"):
        quotes[col] = pd.to_numeric(quotes[col], errors="coerce")
    metadata = {"source": QUOTE_URL, "requested_symbols": len(codes),
                "returned_symbols": len(quotes), "batches": len(batches),
                "workers": workers, "response_megabytes": round(sum(byte_counts) / 1e6, 2),
                "batch_max_seconds": round(max(latencies, default=0), 3),
                "request_started_at": started_at, "request_ended_at": ended_at,
                "source_exchange_timestamp": None, "seconds": round(elapsed, 3)}
    return quotes, metadata


def four_of_five_increasing(volumes):
    return int(any(all(volumes[a] < volumes[b] for a, b in zip(ix, ix[1:]))
                   for ix in combinations(range(5), 4)))


def select_candidates(quotes):
    quotes = quotes.copy()
    quotes["signal_return"] = quotes.signal_price / quotes.preclose - 1
    return quotes[quotes.signal_return.between(.03 - 1e-10, .06 + 1e-10,
                                              inclusive="both")].copy()


def model_universe_eligible(symbol, name):
    """Match the frozen model's stock universe before calculating factors."""
    if "ST" in str(name).upper():
        return False
    return str(symbol).startswith(("300", "301", "688", "689", "4", "8", "92",
                                   "000", "001", "002", "003", "600", "601",
                                   "603", "605"))


def make_features(candidates, history, value, expected_dates):
    history = history.copy()
    history["symbol6"] = history.stk_code.str[:6]
    history["trade_date"] = pd.to_datetime(history.trade_date)
    value = value.copy()
    value["symbol6"] = value.stk_code.str[:6]
    value = value.set_index("symbol6")
    expected = list(pd.to_datetime(expected_dates[-20:]))
    features, exclusions = [], {"incomplete_history": 0, "adjustment_break": 0,
                                "missing_valuation": 0, "invalid_quote": 0}
    grouped = {code: h.sort_values("trade_date") for code, h in history.groupby("symbol6")}
    for q in candidates.itertuples(index=False):
        h = grouped.get(q.symbol6)
        if h is None or h.trade_date.iloc[-20:].tolist() != expected:
            exclusions["incomplete_history"] += 1
            continue
        h = h.tail(21)
        numeric = h[["close", "adjclose", "adjpreclose", "volume"]].apply(pd.to_numeric,
                                                                          errors="coerce")
        if not np.isfinite(numeric.to_numpy()).all() or (numeric[["close", "adjclose", "volume"]] <= 0).any().any():
            exclusions["incomplete_history"] += 1
            continue
        adj_break = (numeric.adjpreclose.iloc[1:].to_numpy() /
                     numeric.adjclose.iloc[:-1].to_numpy() - 1)
        if (np.abs(adj_break) > .01).any():
            exclusions["adjustment_break"] += 1
            continue
        if q.symbol6 not in value.index:
            exclusions["missing_valuation"] += 1
            continue
        v = value.loc[q.symbol6]
        float_mv, float_share = float(v.float_mv), float(v.float_share)
        if not (np.isfinite(float_mv) and np.isfinite(float_share) and
                float_mv > 0 and float_share > 0):
            exclusions["missing_valuation"] += 1
            continue
        if not (q.signal_price > 0 and q.preclose > 0 and
                q.volume_hands > 0 and q.low_so_far > 0):
            exclusions["invalid_quote"] += 1
            continue
        last20 = numeric.tail(20)
        ma = [last20.adjclose.tail(n).mean() * q.preclose / last20.adjclose.iloc[-1]
              for n in (5, 10, 20)]
        volume5 = numeric.volume.tail(5).to_numpy()
        volume_shares = q.volume_hands * 100
        features.append({"symbol6": q.symbol6, "name": q.name,
                         "signal_return": q.signal_return,
                         "volume_ratio": volume_shares * 240 / (220 * volume5.mean()),
                         "turnover_so_far_pct": volume_shares / float_share * 100,
                         "float_mv_100m_cny": float_mv / 1e8,
                         "volume_4of5_increasing": four_of_five_increasing(volume5),
                         "ma_bull_5_10_20": int(ma[0] > ma[1] > ma[2]),
                         "all_intraday_lows_above_ma": int(q.low_so_far > max(ma))})
    return pd.DataFrame(features), exclusions


def verify_model(model_path=MODEL, manifest_path=MANIFEST):
    manifest = json.loads(manifest_path.read_text())
    expected_hash = manifest["artifacts_sha256"][model_path.name]
    actual_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if expected_hash != actual_hash or tuple(manifest["seven_features"]) != FEATURES:
        raise ValueError("Frozen model hash or feature contract changed")
    with warnings.catch_warnings(record=True) as caught:
        model = joblib.load(model_path)
    reference = pd.read_csv(REFERENCE)
    reference = reference[reference.model.eq("精确训练七因子_去当前价高于均线")].head(5)
    scores = frozen_score(model, reference[list(FEATURES)])
    max_difference = float(np.max(np.abs(scores - reference.score.to_numpy())))
    if max_difference > 1e-12:
        raise ValueError("Frozen model no longer reproduces archived reference scores")
    return model, {"saved_sklearn": manifest["sklearn_version"],
                   "runtime_sklearn": sklearn.__version__,
                   "load_warnings": len(caught),
                   "archived_score_max_difference": max_difference}


def frozen_score(model, features):
    """Use the frozen logistic coefficients without sklearn's versioned predict API."""
    transformed = model.named_steps["columntransformer"].transform(features)
    logistic = model.named_steps["logisticregression"]
    return expit(transformed @ logistic.coef_[0] + logistic.intercept_[0])


def run(day, batch_size=200, workers=6, env_file=Path(".env")):
    if day != datetime.now(SHANGHAI).date().isoformat():
        raise ValueError("Live quote date must be today's Shanghai date")
    started = time.perf_counter()
    stages = {}
    conn = db_connection(env_file)
    try:
        t = time.perf_counter()
        dates = query(conn, "SELECT DISTINCT trade_date FROM kcrp_stock_price "
                      "WHERE trade_date < %s ORDER BY trade_date DESC LIMIT 21", (day,))
        if len(dates) < 21:
            raise ValueError("Fewer than 21 prior trading dates")
        day_before = str(dates.trade_date.iloc[0])[:10]
        universe = query(conn, "SELECT stk_code FROM kcrp_stock_price WHERE trade_date=%s",
                         (day_before,))
        codes = sorted(universe.stk_code.astype(str).str[:6].unique().tolist())
        stages["prior_calendar_and_universe_seconds"] = round(time.perf_counter() - t, 3)

        quotes, quote_meta = fetch_full_market(codes, batch_size, workers)
        stages["full_market_quote_seconds"] = quote_meta["seconds"]
        coverage = len(quotes) / len(codes)
        if len(quotes) != len(codes):
            raise ValueError(f"Batch quote covered {len(quotes)} of {len(codes)} symbols")
        t = time.perf_counter()
        candidates = select_candidates(quotes)
        stages["three_to_six_percent_filter_seconds"] = round(time.perf_counter() - t, 3)
        initial_candidates = len(candidates)
        candidates = candidates[[model_universe_eligible(s, n) for s, n in
                                 zip(candidates.symbol6, candidates.name)]].copy()

        t = time.perf_counter()
        stock_codes = universe[universe.stk_code.str[:6].isin(candidates.symbol6)].stk_code.tolist()
        if not stock_codes:
            raise ValueError("No 3–6% candidates")
        placeholders = ",".join(["%s"] * len(stock_codes))
        first_day = str(dates.trade_date.iloc[-1])[:10]
        history = query(conn, "SELECT trade_date, stk_code, close, adjclose, adjpreclose, volume "
                        "FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s "
                        f"AND stk_code IN ({placeholders})",
                        (first_day, day_before, *stock_codes))
        stages["prior_21d_prices_seconds"] = round(time.perf_counter() - t, 3)
        t = time.perf_counter()
        value = query(conn, "SELECT stk_code, float_mv, float_share "
                      "FROM kcrp_stock_pricevaluate WHERE trade_date=%s "
                      f"AND stk_code IN ({placeholders})", (day_before, *stock_codes))
        stages["prior_float_valuation_seconds"] = round(time.perf_counter() - t, 3)
        t = time.perf_counter()
        rows, exclusions = make_features(candidates, history, value,
                                         list(reversed(dates.trade_date.tolist())))
        stages["factor_calculation_seconds"] = round(time.perf_counter() - t, 3)

        t = time.perf_counter()
        model, model_validation = verify_model()
        if not rows.empty:
            rows["score"] = frozen_score(model, rows[list(FEATURES)])
            rows = rows.sort_values(["score", "symbol6"], ascending=[False, True])
        stages["frozen_model_load_and_score_seconds"] = round(time.perf_counter() - t, 3)
        request_start = datetime.fromisoformat(quote_meta["request_started_at"])
        request_end = datetime.fromisoformat(quote_meta["request_ended_at"])
        window_start = datetime.fromisoformat(f"{day}T14:40:00+08:00")
        window_end = datetime.fromisoformat(f"{day}T14:41:00+08:00")
        within_1440_window = window_start <= request_start and request_end <= window_end
        return {"requested_day": day, "prior_trading_day": day_before,
                "quote": quote_meta, "quote_coverage": round(coverage, 4),
                "within_1440_wall_clock_window": within_1440_window,
                "benchmark_only": not within_1440_window,
                "filter_3_to_6_count": initial_candidates,
                "model_universe_eligible_count": len(candidates),
                "feature_complete_count": len(rows),
                "exclusions": exclusions, "history_rows": len(history),
                "valuation_rows": len(value), "model": str(MODEL),
                "model_validation": model_validation, "stages": stages,
                "total_seconds": round(time.perf_counter() - started, 3),
                "top5": rows[["symbol6", "name", "score"]].head(5).to_dict("records")
                if within_1440_window and not rows.empty else []}
    finally:
        conn.rollback()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", default=datetime.now(SHANGHAI).date().isoformat())
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.day, args.batch_size, args.workers, args.env_file)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()

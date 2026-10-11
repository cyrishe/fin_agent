"""One-day read-only pilot for filling a missing historical 1-minute cohort.

Daily high/low is used solely to limit HTTP requests to a superset of the
14:40 3%-6% cohort. It never becomes a feature or a candidate decision.
The source API pages backwards from its current latest bar, so offset is an
exploratory hint and every stock-day is accepted only after exact bar checks.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd
from dotenv import dotenv_values

from scripts.experiment_automl_1450_grid import frame, read_only_db


URL = "http://jzyzwup.upoem1.com/json/hq_marketdata/kLineData"
MORNING = set(range(9 * 60 + 31, 9 * 60 + 41))
THROUGH_1440 = (set(range(9 * 60 + 31, 11 * 60 + 31)) |
                set(range(13 * 60 + 1, 14 * 60 + 41)))


def request_bars(symbol: str, offset: int, want: int) -> list[dict]:
    market = 1 if symbol.startswith(("6", "900")) else (
        7 if symbol.startswith(("92", "4", "8")) else 0)
    payload = {"stReq": {"stHeader": {"shtMarket": market}, "sCode": symbol,
                         "eLineType": 1, "shtStartxh": offset, "shtWantNum": want}}
    request = Request(URL, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response).get("stRsp", {}).get("vAnalyData", [])
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise AssertionError("unreachable")


def extract(symbol: str, signal_date: str, next_date: str, initial_offset: int,
            want: int) -> dict:
    t = int(signal_date.replace("-", ""))
    t1 = int(next_date.replace("-", ""))
    offset = initial_offset
    for attempt in range(8):
        bars = request_bars(symbol, offset, want)
        if not bars:
            if offset:
                offset = max(0, offset - 1000)
                continue
            return {"symbol6": symbol, "status": "api_empty", "offset": offset}
        by_key = {(int(row["sttDateTime"]["iDate"]),
                   int(row["sttDateTime"]["shtTime"])): row for row in bars}
        times_t = {minute for day, minute in by_key if day == t}
        times_t1 = {minute for day, minute in by_key if day == t1}
        required_t = THROUGH_1440 | {14 * 60 + 50}
        if required_t <= times_t and MORNING <= times_t1:
            signal = by_key[t, 14 * 60 + 40]
            entry = by_key[t, 14 * 60 + 50]
            morning = [by_key[t1, minute] for minute in sorted(MORNING)]
            return {"symbol6": symbol, "status": "complete", "offset": offset,
                    "minute_bars_1440": len(THROUGH_1440),
                    "signal_price": float(signal["fClose"]),
                    "entry_1450": float(entry["fClose"]),
                    "minute_volume_hands": sum(float(by_key[t, minute]["lVolume"])
                                               for minute in THROUGH_1440),
                    "min_low_so_far": min(float(by_key[t, minute]["fLow"])
                                          for minute in THROUGH_1440),
                    "next_open": float(morning[0]["fOpen"]),
                    "next_high5": max(float(row["fHigh"]) for row in morning[:5]),
                    "next_high10": max(float(row["fHigh"]) for row in morning),
                    "next_0940": float(morning[-1]["fClose"]),
                    "next_volume5_hands": sum(float(row["lVolume"])
                                              for row in morning[:5])}
        dates = sorted({day for day, _ in by_key})
        if dates[-1] < t:
            missed_days = len(pd.bdate_range(str(dates[-1]), signal_date)) - 1
            offset = max(0, offset - max(240, missed_days * 240))
        elif dates[0] > t1:
            missed_days = len(pd.bdate_range(next_date, str(dates[0]))) - 1
            offset += max(240, missed_days * 240)
        elif t1 not in dates and dates[-1] == t:
            offset = max(0, offset - 240)
        elif t not in dates and dates[0] == t1:
            offset += 240
        elif t not in dates or t1 not in dates:
            return {"symbol6": symbol, "status": "missing_trading_day", "offset": offset}
        elif min(times_t) > min(THROUGH_1440):
            offset += 240
        elif max(times_t1) < max(MORNING):
            offset = max(0, offset - 240)
        else:
            return {"symbol6": symbol, "status": "incomplete_bars", "offset": offset,
                    "bars_signal": len(times_t & required_t),
                    "bars_next_morning": len(times_t1 & MORNING)}
    return {"symbol6": symbol, "status": "outside_window", "offset": offset}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-date", default="2026-08-19")
    parser.add_argument("--initial-offset", type=int, default=6900)
    parser.add_argument("--want", type=int, default=600)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    output = args.output or Path(f"outputs/stock_automl/minute_api_backfill/{args.signal_date}.csv")
    summary = args.summary or Path(
        f"docs/stock_automl_runs/20261008_minute_api_backfill/{args.signal_date}.json")
    config = dotenv_values(args.env_file)
    url = (config.get("SIMPLE_BI_PLATFORM_DB_URL") or config.get("PLATFORM_DB_URL") or
           os.environ.get("SIMPLE_BI_PLATFORM_DB_URL") or "")
    if url:
        os.environ["SIMPLE_BI_PLATFORM_DB_URL"] = url
    with read_only_db() as conn:
        next_date = frame(conn, "SELECT MIN(trade_date) AS next_date FROM kcrp_stock_price "
                          "WHERE trade_date > %s", (args.signal_date,)).iloc[0].next_date
        if next_date is None:
            raise ValueError("No next trading date in daily source")
        next_date = str(next_date)[:10]
        superset = frame(conn, """SELECT LEFT(stk_code,6) AS symbol6, preclose
            FROM kcrp_stock_price WHERE trade_date=%s AND preclose>0
              AND high>=preclose*1.03 AND low<=preclose*1.06""",
                         (args.signal_date,)).drop_duplicates("symbol6")
    day_results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(extract, row.symbol6, args.signal_date, next_date,
                               args.initial_offset, args.want): row
                   for row in superset.itertuples(index=False)}
        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"symbol6": source.symbol6, "status": "api_error",
                          "error": type(exc).__name__}
            result["preclose"] = float(source.preclose)
            day_results.append(result)
    result = pd.DataFrame(day_results).sort_values("symbol6")
    for column in ("signal_price", "entry_1450", "next_high10"):
        if column not in result:
            result[column] = pd.NA
    result["signal_return"] = result.signal_price / result.preclose - 1
    result["candidate_1440"] = (result.status.eq("complete") &
                                result.signal_return.between(.03, .06, inclusive="both"))
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    candidates = result[result.candidate_1440]
    report = {"signal_date": args.signal_date, "next_date": next_date,
              "generated_at": datetime.now().isoformat(timespec="seconds"),
              "source": "Upchina kLineData 1-minute HTTP API, eLineType=1",
              "historical_daily_prefilter_only": "T high >= T preclose * 1.03 and T low <= T preclose * 1.06",
              "daily_prefilter_stocks": len(superset),
              "api_status": result.status.value_counts().to_dict(),
              "complete_1440_candidates": len(candidates),
              "target_1pct_high10_count": int((candidates.next_high10 /
                                               candidates.entry_1450 - 1 > .01).sum()),
              "output": str(output), "initial_offset": args.initial_offset,
              "want": args.want,
              "limitation": "API completeness and one-day cohort audit only; no model training or out-of-sample return claim."}
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

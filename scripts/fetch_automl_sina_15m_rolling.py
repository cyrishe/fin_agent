"""Fetch one consistent Sina 15-minute bar source for June-August rolling tests."""
from __future__ import annotations

import argparse
import gzip
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from scripts.benchmark_automl_1440_inference import db_connection
from scripts.fetch_automl_sina_15m_july import URL, prefix


START, END = "2026-06-01", "2026-08-11"
_lock = threading.Lock()
_next_request_at = 0.0


def universe(env_file: Path) -> list[str]:
    with db_connection(env_file) as db:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT LEFT(stk_code,6) AS symbol6 FROM kcrp_stock_price "
                "WHERE trade_date BETWEEN %s AND %s ORDER BY symbol6", (START, END))
            return [row["symbol6"] for row in cursor.fetchall()]


def throttle(interval: float) -> None:
    global _next_request_at
    with _lock:
        now = time.monotonic()
        if _next_request_at > now:
            time.sleep(_next_request_at - now)
        _next_request_at = time.monotonic() + interval


def fetch(symbol: str, interval: float) -> list[dict]:
    for attempt in range(3):
        try:
            throttle(interval)
            response = requests.get(URL, params={
                "symbol": prefix(symbol) + symbol, "scale": "15", "ma": "no",
                "datalen": "1970"}, timeout=30)
            response.raise_for_status()
            match = re.search(r"=\((.*)\);?\s*$", response.text, re.S)
            if match is None:
                raise ValueError("Sina JSONP wrapper missing")
            bars = json.loads(match.group(1))
            if not isinstance(bars, list):
                raise ValueError("Sina returned null/non-list")
            selected = [bar for bar in bars
                        if START <= str(bar.get("day", ""))[:10] <= END]
            if not selected:
                raise ValueError("No bars in requested date range")
            return selected
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("/Volumes/ext/fin_agent/.env"))
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/stock_automl/sina_15m_rolling"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--interval", type=float, default=.25)
    parser.add_argument("--max-symbols", type=int)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or args.interval < .1:
        parser.error("workers must be 1..8 and interval must be >= 0.1 seconds")
    symbols = universe(args.env_file)
    if args.max_symbols is not None:
        symbols = symbols[:args.max_symbols]
    cache = args.output / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    journal = args.output / "fetch_journal.jsonl"
    todo = [symbol for symbol in symbols if not (cache / f"{symbol}.json.gz").exists()]
    print(json.dumps({"universe": len(symbols), "cached": len(symbols)-len(todo),
                      "remaining": len(todo)}), flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool, journal.open("a", buffering=1) as sink:
        futures = {pool.submit(fetch, symbol, args.interval): symbol for symbol in todo}
        for number, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            record = {"symbol6": symbol, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            try:
                bars = future.result()
                temp = cache / f".{symbol}.json.gz.tmp"
                with gzip.open(temp, "wt", encoding="utf8") as file:
                    json.dump(bars, file, ensure_ascii=False)
                temp.replace(cache / f"{symbol}.json.gz")
                dates = sorted({str(bar["day"])[:10] for bar in bars})
                record.update(status="ok", bars=len(bars), dates=len(dates),
                              first=dates[0], last=dates[-1])
            except Exception as exc:
                record.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:150]}")
            sink.write(json.dumps(record, ensure_ascii=False) + "\n")
            if number % 100 == 0 or number == len(todo):
                print(json.dumps({"processed": number, "remaining": len(todo)-number,
                                  "latest": symbol, "status": record["status"]}), flush=True)


if __name__ == "__main__":
    main()

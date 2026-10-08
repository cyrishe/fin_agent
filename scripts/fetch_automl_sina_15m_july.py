"""Resume-safe, rate-limited read-only fetch of July 2026 Sina 15m bars."""
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

from scripts.backfill_automl_august_minute_full import connection


URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData"
START, END = "2026-07-01", "2026-08-03"
rate_lock = threading.Lock()
next_at = 0.0


def universe(env_file: Path) -> list[str]:
    with connection(env_file) as db:
        with db.cursor() as cursor:
            cursor.execute("""SELECT DISTINCT LEFT(stk_code,6) AS symbol6
                FROM kcrp_stock_price
                WHERE trade_date BETWEEN '2026-07-01' AND '2026-07-31'
                ORDER BY symbol6""")
            return [r["symbol6"] for r in cursor.fetchall()]


def prefix(symbol: str) -> str:
    if symbol.startswith("6") or symbol.startswith("900"):
        return "sh"
    if symbol.startswith(("4", "8", "92")):
        return "bj"
    return "sz"


def throttle(interval: float) -> None:
    global next_at
    with rate_lock:
        now = time.monotonic()
        if next_at > now:
            time.sleep(next_at - now)
        next_at = time.monotonic() + interval


def fetch(symbol: str, interval: float) -> tuple[str, list[dict]]:
    for attempt in range(3):
        try:
            throttle(interval)
            response = requests.get(URL, params={"symbol": prefix(symbol) + symbol,
                                    "scale": "15", "ma": "no", "datalen": "1970"},
                                    timeout=25)
            response.raise_for_status()
            match = re.search(r"=\((.*)\);?\s*$", response.text, re.S)
            if not match:
                raise ValueError("Sina JSONP wrapper missing")
            bars = json.loads(match.group(1))
            if not isinstance(bars, list):
                raise ValueError("Sina returned null/non-list")
            filtered = [bar for bar in bars if START <= str(bar.get("day", ""))[:10] <= END]
            return symbol, filtered
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-file", type=Path, default=Path("/Volumes/ext/fin_agent/.env"))
    p.add_argument("--output", type=Path, default=Path("outputs/stock_automl/sina_15m_july"))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--interval", type=float, default=.25,
                   help="Minimum seconds between request starts across all workers")
    p.add_argument("--max-symbols", type=int)
    args = p.parse_args()
    if not 1 <= args.workers <= 8 or args.interval < .1:
        p.error("workers must be 1..8 and interval at least 0.1 seconds")
    symbols = universe(args.env_file)
    if args.max_symbols is not None:
        symbols = symbols[:args.max_symbols]
    cache = args.output / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    journal_path = args.output / "fetch_journal.jsonl"
    todo = [s for s in symbols if not (cache / f"{s}.json.gz").exists()]
    print(json.dumps({"universe": len(symbols), "cached": len(symbols)-len(todo),
                      "remaining": len(todo)}), flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool, journal_path.open("a", buffering=1) as journal:
        futures = {pool.submit(fetch, s, args.interval): s for s in todo}
        for number, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            record = {"symbol6": symbol, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            try:
                _, bars = future.result()
                dates = sorted({bar["day"][:10] for bar in bars})
                if not bars:
                    raise ValueError("No July/August bars returned")
                path = cache / f"{symbol}.json.gz"
                tmp = cache / f".{symbol}.json.gz.tmp"
                with gzip.open(tmp, "wt", encoding="utf-8") as out:
                    json.dump(bars, out, ensure_ascii=False)
                tmp.replace(path)
                record.update(status="ok", bars=len(bars), dates=len(dates),
                              first=dates[0], last=dates[-1])
            except Exception as exc:
                record.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:150]}")
            journal.write(json.dumps(record, ensure_ascii=False) + "\n")
            if number % 100 == 0 or number == len(todo):
                print(json.dumps({"processed": number, "remaining": len(todo)-number,
                                  "latest": symbol, "status": record["status"]}), flush=True)


if __name__ == "__main__":
    main()

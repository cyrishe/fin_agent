"""Fill incomplete 2026-09-14–16 one-minute bars from the existing quote API."""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from scripts.audit_automl_minute_api_backfill import request_bars
from scripts.backfill_automl_august_minute_full import (
    INSERT_SQL, connection, pause_during_market, prepared_rows,
)


DAYS = ("2026-09-14", "2026-09-15", "2026-09-16")


def missing_symbols(db) -> dict[str, set[str]]:
    expected = {}
    with db.cursor() as cursor:
        for day in DAYS:
            cursor.execute("""SELECT LEFT(stk_code,6) AS symbol6
                FROM kcrp_stock_price WHERE trade_date=%s AND volume>0
                AND LEFT(stk_code,1) IN ('0','3','6')""", (day,))
            listed = {row["symbol6"] for row in cursor.fetchall()}
            cursor.execute("""SELECT stk_code AS symbol6, COUNT(*) AS bars
                FROM aiia_stock_realtime_minute_snapshot_full
                WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                AND bar_end_time BETWEEN %s AND %s
                AND LEFT(stk_code,1) IN ('0','3','6')
                GROUP BY stk_code""",
                (day, day + " 09:31:00", day + " 14:40:00"))
            have = {row["symbol6"] for row in cursor.fetchall()
                    if row["bars"] == 220}
            for symbol in listed - have:
                expected.setdefault(symbol, set()).add(day)
    return expected


def fetch(symbol: str, days: set[str]):
    best = ([], {})
    for offset in (2800, 2600, 3000):
        raw = request_bars(symbol, offset, 1000)
        bars, counts = prepared_rows(symbol, "", raw, min(days), max(days))
        if sum(counts.get(day.replace("-", ""), 0) == 240 for day in days) > sum(
                best[1].get(day.replace("-", ""), 0) == 240 for day in days):
            best = bars, counts
        if all(counts.get(day.replace("-", ""), 0) == 240 for day in days):
            break
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--journal", type=Path, default=Path(
        "outputs/stock_automl/september_minute_gaps_journal.jsonl"))
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("workers must be 1..8")
    db = connection(args.env_file)
    try:
        expected = missing_symbols(db)
        selected = sorted(expected)[:args.max_symbols]
        if args.max_symbols is None:
            selected = sorted(expected)
        print(json.dumps({"missing_symbols": len(expected),
                          "selected": len(selected), "execute": args.execute}), flush=True)
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool, \
                args.journal.open("a", buffering=1) as journal:
            futures = {pool.submit(fetch, symbol, expected[symbol]): symbol
                       for symbol in selected}
            for index, future in enumerate(as_completed(futures), 1):
                symbol = futures[future]
                record = {"symbol6": symbol, "wanted_days": sorted(expected[symbol]),
                          "time": datetime.now().isoformat(timespec="seconds")}
                try:
                    bars, counts = future.result()
                    record["api_counts"] = counts
                    record["complete_days"] = [day for day in expected[symbol]
                        if counts.get(day.replace("-", ""), 0) == 240]
                    if args.execute and bars:
                        pause_during_market()
                        db.ping(reconnect=True)
                        with db.cursor() as cursor:
                            for start in range(0, len(bars), 500):
                                cursor.executemany(INSERT_SQL, bars[start:start+500])
                        db.commit()
                    record["status"] = "complete" if len(record["complete_days"]) == len(
                        expected[symbol]) else "partial"
                except Exception as exc:
                    db.rollback()
                    record.update(status="error", error=f"{type(exc).__name__}: {exc}"[:200])
                journal.write(json.dumps(record, ensure_ascii=False) + "\n")
                if index % 50 == 0 or index == len(selected):
                    print(json.dumps({"processed": index, "selected": len(selected),
                                      "status": record["status"]}), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()

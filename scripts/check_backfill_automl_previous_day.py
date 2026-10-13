"""Audit yesterday's traded Shanghai/Shenzhen 1m bars through 14:40; fill missing slots.

Default mode is read-only. --execute obtains complete raw API sessions and inserts
only absent minute keys; existing database bars are never replaced. This can be
run after the usual minute-ingest job and before 14:40 model preparation.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.backfill_automl_august_minute_full import (
    INSERT_SQL, connection, pause_during_market,
)
from scripts.backfill_automl_deep_v_minute_gaps import fetch_day


SHANGHAI = ZoneInfo("Asia/Shanghai")
EXPECTED = (set(range(9 * 60 + 31, 11 * 60 + 31)) |
            set(range(13 * 60 + 1, 14 * 60 + 41)))


def previous_day(db) -> str:
    today = datetime.now(SHANGHAI).date().isoformat()
    with db.cursor() as cursor:
        cursor.execute("SELECT MAX(trade_date) AS day FROM kcrp_stock_price "
                       "WHERE trade_date < %s", (today,))
        row = cursor.fetchone()
    if row["day"] is None:
        raise ValueError("No previous trading day in daily source")
    return str(row["day"])


def coverage(db, day: str) -> dict[str, dict]:
    with db.cursor() as cursor:
        cursor.execute("""SELECT LEFT(stk_code,6) AS symbol6
            FROM kcrp_stock_price WHERE trade_date=%s AND volume>0
              AND LEFT(stk_code,1) IN ('0','3','6')""", (day,))
        active = {row["symbol6"] for row in cursor.fetchall()}
        cursor.execute("""SELECT LEFT(stk_code,6) AS symbol6,
            COUNT(*) AS bars,COUNT(DISTINCT bar_end_time) AS slots,
            SUM(is_finalized=1 AND is_fallback=0 AND
                source_snapshot_time=bar_end_time) AS exact
            FROM aiia_stock_realtime_minute_snapshot_full
            WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
              AND bar_end_time BETWEEN %s AND %s
              AND LEFT(stk_code,1) IN ('0','3','6')
            GROUP BY LEFT(stk_code,6)""",
                       (day, day + " 09:31:00", day + " 14:40:00"))
        observed = {row["symbol6"]: row for row in cursor.fetchall()}
    return {code: {"bars": int(observed.get(code, {}).get("bars") or 0),
                   "slots": int(observed.get(code, {}).get("slots") or 0),
                   "exact": int(observed.get(code, {}).get("exact") or 0)}
            for code in sorted(active)}


def incomplete(state: dict[str, dict]) -> list[str]:
    return [code for code, row in state.items()
            if (row["bars"], row["slots"], row["exact"]) != (220, 220, 220)]


def existing_slots(db, day: str, code: str) -> set[int]:
    with db.cursor() as cursor:
        cursor.execute("""SELECT bar_end_time
            FROM aiia_stock_realtime_minute_snapshot_full
            WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
              AND LEFT(stk_code,6)=%s AND bar_end_time BETWEEN %s AND %s""",
                       (day, code, day + " 09:31:00", day + " 14:40:00"))
        return {row["bar_end_time"].hour * 60 + row["bar_end_time"].minute
                for row in cursor.fetchall()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", help="Trading day to audit; default is previous trading day")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("workers must be 1..8")
    db = connection(args.env_file)
    try:
        day = args.day or previous_day(db)
        before = coverage(db, day)
        needed = incomplete(before)
        result = {"day": day, "checked_at": datetime.now(SHANGHAI).isoformat(),
                  "expected_bars_per_active_stock": 220,
                  "active_stocks": len(before), "complete_before": len(before)-len(needed),
                  "incomplete_before": len(needed), "execute": args.execute,
                  "api_complete": 0, "api_incomplete": 0,
                  "inserted_missing_bars": 0}
        if args.execute and needed:
            fetched = {}
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(fetch_day, code, day, 0): code for code in needed}
                for future in as_completed(futures):
                    code = futures[future]
                    try:
                        rows, audit = future.result()
                        fetched[code] = (rows, audit)
                    except Exception as exc:
                        fetched[code] = ([], {"error": type(exc).__name__})
            result["api_complete"] = sum(bool(rows) for rows, _ in fetched.values())
            result["api_incomplete"] = len(needed)-result["api_complete"]
            for index, code in enumerate(needed, 1):
                rows, _ = fetched[code]
                if not rows:
                    continue
                pause_during_market()
                db.ping(reconnect=True)
                have = existing_slots(db, day, code)
                missing = [row for row in rows
                           if row[0].strftime("%Y-%m-%d") == day and
                           row[5] in EXPECTED and row[5] not in have]
                if missing:
                    with db.cursor() as cursor:
                        cursor.executemany(INSERT_SQL, missing)
                        result["inserted_missing_bars"] += max(0, cursor.rowcount)
                    db.commit()
                if index % 100 == 0 or index == len(needed):
                    print(f"{day}: {index}/{len(needed)} checked", flush=True)
        after = coverage(db, day)
        remaining = incomplete(after)
        result.update(complete_after=len(after)-len(remaining),
                      incomplete_after=len(remaining),
                      remaining_symbols=remaining)
        output = args.output or Path("outputs/stock_automl/previous_day_minute_check") / f"{day}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({key: value for key, value in result.items()
                          if key != "remaining_symbols"}, ensure_ascii=False, indent=2))
        print(f"evidence: {output}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

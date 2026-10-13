"""Fill traded-stock 1m gaps for deep-V rolling-window dates, with a journal.

Only API days with all 240 valid session bars are accepted. Existing minute
rows are left intact by the insert's no-op duplicate-key clause. Run once in
preview mode before --execute; both modes append stock-day results to journal.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

from scripts.backfill_automl_august_minute_full import INSERT_SQL, connection, pause_during_market
from scripts.backfill_automl_deep_v_minute_gaps import fetch_day


def read_day(db, day: str) -> tuple[dict[str, dict], dict[str, dict]]:
    with db.cursor() as cur:
        cur.execute("""SELECT LEFT(stk_code,6) AS symbol6, volume
            FROM kcrp_stock_price WHERE trade_date=%s AND volume>0
            AND LEFT(stk_code,1) IN ('0','3','6')""", (day,))
        daily = {row["symbol6"]: row for row in cur.fetchall()}
        cur.execute("""SELECT stk_code AS symbol6,COUNT(*) AS bars,
            SUM(is_finalized=1 AND is_fallback=0 AND
                source_snapshot_time=bar_end_time) AS exact_bars,
            MIN(bar_end_time) AS first_bar,MAX(bar_end_time) AS last_bar
            FROM aiia_stock_realtime_minute_snapshot_full
            WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
            AND bar_end_time BETWEEN %s AND %s GROUP BY stk_code""",
            (day, f"{day} 09:31:00", f"{day} 15:00:00"))
        existing = {row["symbol6"]: row for row in cur.fetchall()}
    return daily, existing


def is_complete(row: dict | None) -> bool:
    return bool(row and row["bars"] == 240 and row["exact_bars"] == 240 and
                str(row["first_bar"]).endswith("09:31:00") and
                str(row["last_bar"]).endswith("15:00:00"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", default="2026-08-25,2026-08-28")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--env-file", type=Path, default=Path(".env"))
    p.add_argument("--journal", type=Path, default=Path(
        "outputs/stock_automl/deep_v_20261013/universe_minute_backfill.jsonl"))
    p.add_argument("--max-symbols", type=int)
    args = p.parse_args()
    if not 1 <= args.workers <= 8:
        p.error("workers must be 1..8")
    db = connection(args.env_file)
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    try:
        with db.cursor() as cur:
            cur.execute("SELECT DISTINCT trade_date FROM kcrp_stock_price "
                        "WHERE trade_date BETWEEN '2026-08-01' AND '2026-10-13' "
                        "ORDER BY trade_date")
            calendar = [str(row["trade_date"]) for row in cur.fetchall()]
            cur.execute("SELECT LEFT(stk_code,6) AS symbol6,stk_name "
                        "FROM kcrp_stock_baseinfo")
            names = {row["symbol6"]: row["stk_name"] or "" for row in cur.fetchall()}
        if args.start or args.end:
            if not args.start or not args.end or args.start > args.end:
                p.error("--start and --end must both be set in ascending order")
            days = [day for day in calendar if args.start <= day <= args.end]
        else:
            days = sorted(set(args.days.split(",")))
        for day in days:
            daily, existing = read_day(db, day)
            missing = sorted(symbol for symbol in daily if not is_complete(existing.get(symbol)))
            missing_total = len(missing)
            if args.max_symbols is not None:
                missing = missing[:args.max_symbols]
            offset = max(0, (len(calendar) - 1 - calendar.index(day)) * 240 - 450)
            print(json.dumps({"day": day, "traded": len(daily),
                              "db_complete": len(daily) - missing_total,
                              "api_needed": missing_total,
                              "selected": len(missing), "execute": args.execute}), flush=True)
            counts = {"complete": 0, "api_incomplete": 0, "error": 0}
            with ThreadPoolExecutor(max_workers=args.workers) as pool, \
                    args.journal.open("a", buffering=1) as journal:
                iterator = iter(missing)
                futures = {}

                def submit_next() -> bool:
                    try:
                        symbol = next(iterator)
                    except StopIteration:
                        return False
                    futures[pool.submit(fetch_day, symbol, day, offset)] = symbol
                    return True

                for _ in range(min(args.workers * 2, len(missing))):
                    submit_next()
                index = 0
                while futures:
                    finished, _ = wait(futures, return_when=FIRST_COMPLETED)
                    for future in finished:
                        symbol = futures.pop(future)
                        index += 1
                        record = {"time": datetime.now().isoformat(timespec="seconds"),
                                  "day": day, "symbol6": symbol,
                                  "db_bars_before": int(existing.get(symbol, {}).get("bars") or 0)}
                        try:
                            rows, audit = future.result()
                            record.update(audit)
                            if len(rows) != 240:
                                record["status"] = "api_incomplete"
                                counts["api_incomplete"] += 1
                            elif not args.execute:
                                record["status"] = "ready"
                                counts["complete"] += 1
                            else:
                                pause_during_market()
                                db.ping(reconnect=True)
                                named_rows = [tuple(list(row[:9]) + [names.get(symbol, "")] +
                                                    list(row[10:])) for row in rows]
                                if any(len(row) != 26 or not isinstance(row[10], float)
                                       for row in named_rows):
                                    raise ValueError("API row does not match insert schema")
                                with db.cursor() as cur:
                                    cur.executemany(INSERT_SQL, named_rows)
                                db.commit()
                                record["status"] = "inserted_or_preserved"
                                counts["complete"] += 1
                        except Exception as exc:
                            db.rollback()
                            record["status"] = "error"
                            record["error"] = f"{type(exc).__name__}: {exc}"[:180]
                            counts["error"] += 1
                        journal.write(json.dumps(record, ensure_ascii=False) + "\n")
                        if index % 100 == 0 or index == len(missing):
                            print(json.dumps({"day": day, "processed": index,
                                              "total": len(missing), **counts}), flush=True)
                        submit_next()
            if args.execute:
                _, after = read_day(db, day)
                still = sorted(symbol for symbol in daily if not is_complete(after.get(symbol)))
                print(json.dumps({"day": day, "traded": len(daily),
                                  "db_complete_after": len(daily) - len(still),
                                  "still_incomplete": len(still),
                                  "examples": still[:12]}), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()

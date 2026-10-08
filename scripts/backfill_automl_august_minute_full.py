"""Resume-safe, missing-only August 2026 1m backfill into kingdomai's full table.

The confirmed vendor 1m window starts with a partial 2026-08-06 session;
2026-08-07..21 are the missing full-table dates. July is not synthesized.
Only finalized raw 1m bars are inserted. Existing rows are never updated.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymysql
from dotenv import dotenv_values

from scripts.audit_automl_minute_api_backfill import request_bars


START, END = "2026-08-07", "2026-08-21"
TABLE = "aiia_stock_realtime_minute_snapshot_full"
INSERT_SQL = f"""INSERT INTO {TABLE} (
  trade_date,kline_type,period_minutes,bar_start_time,bar_end_time,
  minute_index,snapshot_time,snapshot_slot,stk_code,stk_name,
  latest_price,open_price,high_price,low_price,preclose_price,amount,volume,
  source_bar_count,is_finalized,source,fetch_attempts,is_fallback,ingest_tag,
  source_snapshot_time,fetch_time,error_message
) VALUES ({','.join(['%s'] * 26)})
ON DUPLICATE KEY UPDATE stk_code=VALUES(stk_code)"""


def connection(env_file: Path):
    cfg = dotenv_values(env_file)
    raw = (cfg.get("SIMPLE_BI_PLATFORM_DB_URL") or cfg.get("PLATFORM_DB_URL") or
           os.environ.get("SIMPLE_BI_PLATFORM_DB_URL") or "")
    parsed = urlparse(raw.replace("mysql+pymysql://", "mysql://", 1))
    if parsed.hostname != "47.94.1.2" or parsed.port != 3312:
        raise ValueError("Expected the confirmed kingdomai host at 47.94.1.2:3312")
    return pymysql.connect(
        host=parsed.hostname, port=parsed.port, user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""), database="kingdomai",
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=8, read_timeout=60, write_timeout=60, autocommit=False)


def load_universe(db, start: str, end: str):
    with db.cursor() as cur:
        cur.execute("""SELECT trade_date,LEFT(stk_code,6) AS symbol6
            FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s""", (start, end))
        days = cur.fetchall()
        cur.execute("SELECT LEFT(stk_code,6) AS symbol6,stk_name FROM kcrp_stock_baseinfo")
        names = {row["symbol6"]: row["stk_name"] or "" for row in cur.fetchall()}
    expected = {}
    for row in days:
        expected.setdefault(row["symbol6"], set()).add(str(row["trade_date"]))
    return expected, names


def source_window(symbol: str, start: str, end: str):
    """Seek an API page containing the requested August window, if retained."""
    offset = 6500
    best = []
    for _ in range(8):
        rows = request_bars(symbol, offset, 3000)
        if not rows:
            if offset == 0:
                break
            offset = max(0, offset - 1000)
            continue
        best = rows
        stamps = [(str(row["sttDateTime"]["iDate"]),
                   int(row["sttDateTime"]["shtTime"])) for row in rows]
        newest, oldest = max(stamps), min(stamps)
        if newest < (end.replace("-", ""), 15 * 60):
            offset = max(0, offset - 200)
        elif oldest > (start.replace("-", ""), 9 * 60 + 31) and offset + 3000 < 9500:
            offset += 200
        else:
            break
    return best, offset


def prepared_rows(symbol: str, name: str, raw: list[dict], start: str, end: str):
    now = datetime.now()
    output = []
    by_day = {}
    seen = set()
    for item in raw:
        stamp = item["sttDateTime"]
        date_text = str(stamp["iDate"])
        if not start.replace("-", "") <= date_text <= end.replace("-", ""):
            continue
        minute = int(stamp["shtTime"])
        if not (9 * 60 + 31 <= minute <= 11 * 60 + 30 or
                13 * 60 + 1 <= minute <= 15 * 60):
            continue
        day = datetime.strptime(date_text, "%Y%m%d")
        end_time = day + timedelta(minutes=minute)
        if end_time in seen:
            raise ValueError(f"Duplicate API minute for {symbol}: {end_time}")
        seen.add(end_time)
        prices = [float(item[key]) for key in ("fOpen", "fHigh", "fLow", "fClose")]
        if min(prices) <= 0 or prices[1] < max(prices[0], prices[3]) or \
                prices[2] > min(prices[0], prices[3]):
            raise ValueError(f"Invalid price bar for {symbol}: {end_time}")
        volume = float(item.get("lVolume") or 0)
        if volume < 0:
            raise ValueError(f"Negative volume for {symbol}: {end_time}")
        preclose = float(item.get("dPreClose") or 0) or None
        amount = item.get("fAmount")
        by_day[date_text] = by_day.get(date_text, 0) + 1
        output.append((
            day.date(), "1m", 1, end_time - timedelta(minutes=1), end_time,
            minute, end_time, f"{minute // 60:02d}:{minute % 60:02d}", symbol, name,
            prices[3], prices[0], prices[1], prices[2], preclose,
            float(amount) if amount is not None else None, volume,
            1, 1, "upchina_kline_1m", 1, 0, "historical_backfill",
            end_time, now, ""))
    # A truncated API page must not masquerade as a complete trading session.
    complete = [row for row in output if by_day[row[0].strftime("%Y%m%d")] == 240]
    return complete, by_day


def pause_during_market():
    while True:
        now = datetime.now()
        if now.weekday() >= 5 or not (9 * 60 + 10 <= now.hour * 60 + now.minute < 15 * 60 + 20):
            return
        time.sleep(30)


def read_done(journal: Path, start: str, end: str):
    done = set()
    if journal.exists():
        for line in journal.read_text().splitlines():
            record = json.loads(line)
            if record.get("start") == start and record.get("end") == end and \
                    record.get("status") == "ok":
                done.add(record["symbol6"])
    return done


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", default=END)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--journal", type=Path, default=Path(
        "outputs/stock_automl/minute_full_backfill/journal_20260807_20260821.jsonl"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.start < START or args.end > END or args.start > args.end:
        parser.error("Only the confirmed 2026-08-07..21 1m backfill window is supported")
    if args.workers < 1 or args.workers > 8:
        parser.error("--workers must be between 1 and 8")
    db = connection(args.env_file)
    try:
        with db.cursor() as cur:
            cur.execute("SET SESSION innodb_lock_wait_timeout=2")
            cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        expected, names = load_universe(db, args.start, args.end)
        requested = set(args.symbols.split(",")) if args.symbols else None
        done = read_done(args.journal, args.start, args.end)
        symbols = [s for s in sorted(expected) if s not in done and
                   (requested is None or s in requested)]
        if args.max_symbols is not None:
            symbols = symbols[:args.max_symbols]
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        print(json.dumps({"start": args.start, "end": args.end,
                          "universe": len(expected), "remaining": len(symbols),
                          "execute": args.execute}), flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool, \
                args.journal.open("a", buffering=1) as journal:
            pending = {}
            iterator = iter(symbols)

            def submit_next():
                try:
                    symbol = next(iterator)
                except StopIteration:
                    return False
                pending[pool.submit(source_window, symbol, args.start, args.end)] = symbol
                return True

            for _ in range(min(args.workers * 2, len(symbols))):
                submit_next()
            processed = 0
            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    symbol = pending.pop(future)
                    record = {"start": args.start, "end": args.end,
                              "symbol6": symbol, "time": datetime.now().isoformat(timespec="seconds")}
                    try:
                        raw, offset = future.result()
                        bars, counts = prepared_rows(symbol, names.get(symbol, ""),
                                                      raw, args.start, args.end)
                        expected_days = expected[symbol]
                        incomplete = {day: counts.get(day.replace("-", ""), 0)
                                      for day in expected_days
                                      if counts.get(day.replace("-", ""), 0) != 240}
                        record.update(source_rows=len(bars), api_offset=offset,
                                      day_counts=counts, incomplete_days=incomplete)
                        if args.execute and bars:
                            pause_during_market()
                            db.ping(reconnect=True)
                            inserted = 0
                            with db.cursor() as cur:
                                for pos in range(0, len(bars), 500):
                                    cur.executemany(INSERT_SQL, bars[pos:pos + 500])
                                    inserted += max(0, cur.rowcount)
                            db.commit()
                            record["inserted"] = inserted
                        record["status"] = ("partial" if incomplete else "ok") if args.execute else "preview"
                    except Exception as exc:
                        db.rollback()
                        record.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:180]}")
                    journal.write(json.dumps(record, ensure_ascii=False) + "\n")
                    processed += 1
                    if processed % 50 == 0 or processed == len(symbols):
                        print(json.dumps({"processed": processed, "remaining": len(symbols)-processed,
                                          "latest": symbol, "status": record["status"]}), flush=True)
                    submit_next()
    finally:
        db.close()


if __name__ == "__main__":
    main()

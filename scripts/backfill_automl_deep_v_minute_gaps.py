"""Backfill missing deep-V candidate 1m bars from the existing Upchina API.

The old D3 Top20 file is only a target list. The database is rechecked before
each write. Only traded, finalized 240-bar API days are accepted, and existing
database bars are preserved. The journal records every stock-day outcome.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from scripts.audit_automl_minute_api_backfill import request_bars
from scripts.backfill_automl_august_minute_full import (
    INSERT_SQL, connection, pause_during_market, prepared_rows,
)


EXPECTED = (set(range(9 * 60 + 31, 11 * 60 + 31)) |
            set(range(13 * 60 + 1, 15 * 60 + 1)))


def get_targets(top_file: Path, model: str) -> list[tuple[str, str]]:
    top = pd.read_csv(top_file, dtype={"symbol6": str})
    top = top[top.model.eq(model) & ~top.full240.astype(bool)]
    return sorted({(str(row.signal_date), row.symbol6.zfill(6))
                   for row in top.itertuples(index=False)})


def fetch_day(symbol: str, day: str, initial_offset: int) -> tuple[list[tuple], dict]:
    """Seek the target day in backwards API pagination, then require 240 slots."""
    target = int(day.replace("-", ""))
    offset = max(0, initial_offset)
    want = 1000
    audit = {"api_offsets": [], "api_day_bars": 0}
    collected: dict[int, dict] = {}
    for _ in range(16):
        raw = request_bars(symbol, offset, want)
        audit["api_offsets"].append(offset)
        dates = [int(x["sttDateTime"]["iDate"]) for x in raw]
        day_raw = [x for x in raw if int(x["sttDateTime"]["iDate"]) == target]
        for item in day_raw:
            collected[int(item["sttDateTime"]["shtTime"])] = item
        audit["api_day_bars"] = len(collected)
        if set(collected) == EXPECTED:
            bars, _ = prepared_rows(symbol, "", list(collected.values()), day, day)
            if len(bars) == 240:
                audit["api_complete"] = True
                return bars, audit
        if day_raw:
            times = {int(x["sttDateTime"]["shtTime"]) for x in day_raw}
            audit["api_complete"] = False
            audit["api_minute_count"] = len(set(collected) & EXPECTED)
            # A page edge can truncate the target; center another page on it.
            if dates and min(dates) == target and max(dates) > target:
                offset += min(200, want // 2)
            elif dates and max(dates) == target and min(dates) < target:
                offset = max(0, offset - min(200, want // 2))
            else:
                return [], audit
        elif not raw:
            # The endpoint returns [] when offset + requested bars crosses its
            # retention limit; a smaller page may still contain the day.
            if offset >= 8000 and want > 300:
                want = max(300, want // 2)
                continue
            if offset == 0:
                break
            offset = max(0, offset - 400 if offset >= 8000 else offset - 700)
        elif max(dates) < target:
            offset = max(0, offset - 700)
        elif min(dates) > target:
            offset += 700
        else:
            # The day is inside the page's dates, but has no traded bars.
            break
    audit["api_complete"] = False
    return [], audit


def db_state(db, day: str, symbol: str) -> dict:
    stock_code = symbol + (".SH" if symbol.startswith("6") else ".SZ")
    keys = [(day, "1m", datetime.strptime(day, "%Y-%m-%d") +
             timedelta(minutes=minute), symbol) for minute in sorted(EXPECTED)]
    placeholders = ",".join(["(%s,%s,%s,%s)"] * len(keys))
    with db.cursor() as cur:
        cur.execute("""SELECT volume FROM kcrp_stock_price
            WHERE trade_date=%s AND stk_code=%s LIMIT 1""", (day, stock_code))
        daily = cur.fetchone()
        cur.execute(f"""SELECT bar_end_time,is_finalized,is_fallback,source_snapshot_time
            FROM aiia_stock_realtime_minute_snapshot_full
            WHERE (trade_date,kline_type,bar_end_time,stk_code) IN ({placeholders})
            AND period_minutes=1""", tuple(item for key in keys for item in key))
        rows = cur.fetchall()
    times = {x["bar_end_time"].hour * 60 + x["bar_end_time"].minute for x in rows}
    exact = sum(x["is_finalized"] == 1 and x["is_fallback"] == 0 and
                x["source_snapshot_time"] == x["bar_end_time"] for x in rows)
    return {"daily": daily, "bars": len(rows), "slots": times,
            "exact": exact}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-file", type=Path, default=Path(
        "outputs/stock_automl/deep_v_20261013/model_v1/top20_universe.csv"))
    parser.add_argument("--model", default="direct_prior")
    parser.add_argument("--targets-file", type=Path,
                        help="CSV with day,symbol6 to backfill instead of old Top20 targets")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--journal", type=Path, default=Path(
        "outputs/stock_automl/deep_v_20261013/minute_api_backfill.jsonl"))
    args = parser.parse_args()
    if not 1 <= args.workers <= 6:
        parser.error("workers must be 1..6")
    if args.targets_file:
        target_frame = pd.read_csv(args.targets_file, dtype={"symbol6": str})
        targets = sorted({(str(row.day), row.symbol6.zfill(6))
                          for row in target_frame.itertuples(index=False)})
    else:
        targets = get_targets(args.top_file, args.model)
    db = connection(args.env_file)
    try:
        with db.cursor() as cur:
            cur.execute("SELECT DISTINCT trade_date FROM kcrp_stock_price "
                        "WHERE trade_date BETWEEN '2026-08-01' AND '2026-10-13' "
                        "ORDER BY trade_date")
            calendar = [str(x["trade_date"]) for x in cur.fetchall()]
        latest = len(calendar) - 1
        offsets = {day: max(0, (latest - calendar.index(day)) * 240 - 450)
                   for day, _ in targets}
        current = {(day, symbol): db_state(db, day, symbol)
                   for day, symbol in targets}
        needed = [(day, symbol) for day, symbol in targets
                  if current[day, symbol]["daily"] and
                  float(current[day, symbol]["daily"]["volume"] or 0) > 0 and
                  (current[day, symbol]["bars"] != 240 or
                   current[day, symbol]["slots"] != EXPECTED or
                   current[day, symbol]["exact"] != 240)]
        print(json.dumps({"target_stock_days": len(targets),
                          "api_needed": len(needed), "execute": args.execute}), flush=True)
        results = {}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(fetch_day, symbol, day, offsets[day]): (day, symbol)
                       for day, symbol in needed}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as exc:
                    results[key] = ([], {"api_error": f"{type(exc).__name__}: {exc}"[:180]})
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        with args.journal.open("a", buffering=1) as journal:
            for day, symbol in targets:
                before = current[day, symbol]
                daily = before["daily"]
                record = {"time": datetime.now().isoformat(timespec="seconds"),
                          "day": day, "symbol6": symbol, "db_bars_before": before["bars"],
                          "db_exact_before": before["exact"],
                          "daily_volume": float(daily["volume"] or 0) if daily else None}
                if not daily:
                    record["status"] = "daily_missing"
                elif not record["daily_volume"]:
                    record["status"] = "no_trade"
                elif (before["bars"] == 240 and before["slots"] == EXPECTED and
                      before["exact"] == 240):
                    record["status"] = "already_complete"
                else:
                    rows, api_audit = results[day, symbol]
                    record.update(api_audit)
                    if not rows:
                        record["status"] = "api_incomplete"
                    elif not args.execute:
                        record["status"] = "ready"
                    else:
                        pause_during_market()
                        db.ping(reconnect=True)
                        now = db_state(db, day, symbol)
                        existing = now["slots"]
                        missing = [tuple(list(row[:9]) + [""] +
                                         list(row[10:])) for row in rows
                                   if row[5] not in existing]
                        if any(len(row) != 26 or not isinstance(row[10], float)
                               for row in missing):
                            raise ValueError("Prepared API row does not match the 26-column insert")
                        try:
                            with db.cursor() as cur:
                                if missing:
                                    cur.executemany(INSERT_SQL, missing)
                            db.commit()
                            after = db_state(db, day, symbol)
                            record["inserted"] = len(missing)
                            record["db_bars_after"] = after["bars"]
                            record["db_exact_after"] = after["exact"]
                            record["status"] = ("complete" if after["bars"] == 240 and
                                                after["slots"] == EXPECTED and
                                                after["exact"] == 240 else "still_incomplete")
                        except Exception as exc:
                            db.rollback()
                            record["status"] = "write_error"
                            record["error"] = f"{type(exc).__name__}: {exc}"[:180]
                journal.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(json.dumps(record, ensure_ascii=False), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()

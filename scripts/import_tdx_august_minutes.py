"""Insert audited August 3–4 TDX 1m bars, preserving existing database rows."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from scripts.backfill_automl_august_minute_full import connection


DAYS = ("2026-08-03", "2026-08-04")
ROOT = Path("outputs/stock_automl/mk_1m_compact")
OUT = Path("outputs/stock_automl/tdx_august_20261014")
SQL = """INSERT IGNORE INTO aiia_stock_realtime_minute_snapshot_full (
  trade_date,kline_type,period_minutes,bar_start_time,bar_end_time,
  minute_index,snapshot_time,snapshot_slot,stk_code,stk_name,
  latest_price,open_price,high_price,low_price,preclose_price,amount,volume,
  source_bar_count,is_finalized,source,fetch_attempts,is_fallback,ingest_tag,
  source_snapshot_time,fetch_time,error_message
) VALUES (""" + ",".join(["%s"] * 26) + ")"


def main() -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-batches", type=int)
    args = parser.parse_args()
    manifest = json.loads((OUT / "prepared_manifest.json").read_text())
    db = connection(Path(".env"))
    summary = {"execute": args.execute, "days": {}, "source_zip_sha256": manifest["zip_sha256"]}
    try:
        with db.cursor() as cur:
            cur.execute("SELECT trade_date,LEFT(stk_code,6) symbol,preclose FROM kcrp_stock_price "
                        "WHERE trade_date IN ('2026-08-03','2026-08-04')")
            preclose = {(str(r["trade_date"]), r["symbol"]): float(r["preclose"])
                        for r in cur.fetchall()}
            cur.execute("SELECT LEFT(stk_code,6) symbol,stk_name FROM kcrp_stock_baseinfo")
            names = {r["symbol"]: r["stk_name"] or "" for r in cur.fetchall()}
        now = datetime.now()
        batch_no = 0
        for day in DAYS:
            path = ROOT / f"{day}.csv.zst"
            if not path.exists():
                raise FileNotFoundError(path)
            attempted = inserted = 0
            for frame in pd.read_csv(path, dtype={"symbol": str}, chunksize=20_000):
                frame["symbol6"] = frame.symbol.str[:6]
                if not frame.symbol6.map(lambda s: (day, s) in preclose).all():
                    raise ValueError(f"No daily preclose for {day}")
                rows = []
                for row in frame.itertuples(index=False):
                    end = datetime.fromisoformat(row.time)
                    minute = end.hour * 60 + end.minute
                    symbol = row.symbol6
                    rows.append((
                        day, "1m", 1, end - timedelta(minutes=1), end,
                        minute, end, end.strftime("%H:%M"), symbol, names.get(symbol, ""),
                        row.close, row.open, row.high, row.low, preclose[(day, symbol)],
                        row.turnover, row.volume / 100.0,
                        1, 1, "tdx_zip_1m", 1, 0, "historical_backfill", end, now, ""))
                if args.execute:
                    try:
                        with db.cursor() as cur:
                            for offset in range(0, len(rows), 1000):
                                cur.executemany(SQL, rows[offset:offset + 1000])
                                inserted += cur.rowcount
                        db.commit()
                    except Exception:
                        db.rollback()
                        raise
                attempted += len(rows)
                batch_no += 1
                print(json.dumps({"day": day, "attempted": attempted, "inserted": inserted}), flush=True)
                if args.max_batches is not None and batch_no >= args.max_batches:
                    summary["stopped_after_batches"] = batch_no
                    break
            summary["days"][day] = {"attempted": attempted, "inserted": inserted}
            if summary.get("stopped_after_batches"):
                break
        (OUT / "db_import_progress.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    finally:
        db.close()
    return summary


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

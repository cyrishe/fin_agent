"""Read-only audit of missing next-morning labels against the 1m quote API."""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.audit_automl_minute_api_backfill import MORNING, request_bars
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.run_automl_four_class_pipeline import PREPARED


OUT = Path("outputs/stock_automl/missing_morning_api")


def find_morning(symbol: str, day: str, initial_offset: int) -> dict:
    target = int(day.replace("-", ""))
    offset = initial_offset
    for _ in range(10):
        bars = request_bars(symbol, offset, 1200)
        if not bars:
            if offset == 0:
                return {"status": "api_empty", "offset": offset}
            offset = max(0, offset - 480)
            continue
        date_numbers = [int(bar["sttDateTime"]["iDate"]) for bar in bars]
        newest, oldest = max(date_numbers), min(date_numbers)
        if target > newest:
            if offset == 0:
                return {"status": "no_target_day", "offset": offset,
                        "api_latest_date": newest}
            offset = max(0, offset - 480)
            continue
        if target < oldest:
            offset += 480
            continue
        day_bars = [bar for bar in bars if int(bar["sttDateTime"]["iDate"]) == target]
        by_minute = {int(bar["sttDateTime"]["shtTime"]): bar for bar in day_bars}
        if MORNING.issubset(by_minute):
            morning = [by_minute[minute] for minute in sorted(MORNING)]
            highs = np.array([float(bar["fHigh"]) for bar in morning])
            closes = np.array([float(bar["fClose"]) for bar in morning])
            if (not np.isfinite(highs).all() or not np.isfinite(closes).all() or
                    min(highs.min(), closes.min()) <= 0):
                return {"status": "invalid_prices", "offset": offset}
            return {"status": "complete", "offset": offset,
                    "api_day_bars_on_page": len(by_minute),
                    "morning_bars": len(MORNING),
                    "second_high": float(np.sort(highs)[-2]),
                    "close_40": float(closes[-1]),
                    "open_31": float(morning[0]["fOpen"]),
                    "morning_volume_hands": float(sum(float(bar.get("lVolume") or 0)
                                                      for bar in morning))}
        if day_bars and min(by_minute) > min(MORNING):
            offset += 240
            continue
        if day_bars and max(by_minute) < max(MORNING) and offset:
            offset = max(0, offset - 240)
            continue
        return {"status": "no_target_day" if not day_bars else "incomplete_morning",
                "offset": offset, "api_day_bars_on_page": len(day_bars),
                "morning_bars": len(MORNING.intersection(by_minute))}
    return {"status": "outside_api_window", "offset": offset}


def run(prepared: Path, env_file: Path, output: Path, workers: int) -> pd.DataFrame:
    rows = pd.read_csv(prepared, dtype={"symbol6": str})
    missing = rows[rows.second_high.isna()][["signal_date", "next_date", "symbol6", "name",
                                             "signal_price"]].copy()
    conn = db_connection(env_file)
    try:
        calendar = query(conn, "SELECT DISTINCT trade_date FROM kcrp_stock_price "
                         "WHERE trade_date >= %s ORDER BY trade_date",
                         (missing.next_date.min(),)).trade_date.astype(str).tolist()
        daily = []
        for day, group in missing.groupby("next_date"):
            codes = group.symbol6.tolist()
            placeholders = ",".join(["%s"] * len(codes))
            daily.append(query(conn, "SELECT LEFT(stk_code,6) AS symbol6, "
                               "trade_date AS next_date, volume AS daily_volume "
                               "FROM kcrp_stock_price WHERE trade_date=%s "
                               f"AND LEFT(stk_code,6) IN ({placeholders})",
                               (day, *codes)))
    finally:
        conn.rollback()
        conn.close()
    daily = pd.concat(daily, ignore_index=True)
    daily["next_date"] = daily.next_date.astype(str)
    missing = missing.merge(daily, on=["next_date", "symbol6"], how="left",
                            validate="one_to_one")
    offsets = {day: max(0, (sum(x > day for x in calendar)-2)*240)
               for day in missing.next_date.unique()}
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(find_morning, row.symbol6, row.next_date,
                               offsets[row.next_date]): row
                   for row in missing.itertuples(index=False)}
        for future in as_completed(futures):
            row = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"status": "api_error", "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
            results.append({"signal_date": row.signal_date, "next_date": row.next_date,
                            "symbol6": row.symbol6, "name": row.name,
                            "signal_price": row.signal_price,
                            "daily_volume": row.daily_volume, **result})
    result = pd.DataFrame(results).sort_values(["signal_date", "symbol6"])
    output.mkdir(parents=True, exist_ok=True)
    result.to_csv(output / "api_missing_morning_audit.csv", index=False)
    summary = {"requested": len(result), "api_status": result.status.value_counts().to_dict(),
               "daily_volume_positive": int(pd.to_numeric(result.daily_volume).gt(0).sum()),
               "daily_volume_zero": int(pd.to_numeric(result.daily_volume).eq(0).sum())}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, default=PREPARED)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    result = run(args.prepared, args.env_file, args.output, args.workers)
    print(json.dumps({"api_status": result.status.value_counts().to_dict(),
                      "recovered": int(result.second_high.notna().sum()),
                      "output": str(args.output / "api_missing_morning_audit.csv")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

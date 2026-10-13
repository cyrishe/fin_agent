"""Cache the exact 240-bar universe for D3's unconditional event model."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from scripts.benchmark_automl_1440_inference import db_connection, query


ROOT = Path("outputs/stock_automl/deep_v_20261013/model_v1")
SQL = """SELECT stk_code,COUNT(*) AS bars,COUNT(DISTINCT bar_end_time) AS distinct_bars,
 MIN(bar_end_time) AS first_bar,MAX(bar_end_time) AS last_bar,
 SUM(is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time) AS exact_bars,
 MIN(latest_price) AS min_close,MAX(latest_price) AS max_close
 FROM aiia_stock_realtime_minute_snapshot_full
 WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 GROUP BY stk_code"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path,
                        default=Path("outputs/stock_automl/deep_v_20261013"))
    parser.add_argument("--out", type=Path, default=ROOT)
    args = parser.parse_args()
    days = pd.read_csv(args.data_root / "daily_coverage.csv")
    days = days.loc[days.full_exact_stocks.gt(0), "day"].astype(str).tolist()
    cache = args.out / "full_universe"
    cache.mkdir(parents=True, exist_ok=True)
    load_dotenv(".env")
    with db_connection() as conn:
        for day in days:
            path = cache / f"{day}.csv.gz"
            if path.exists():
                continue
            frame = query(conn, SQL, (day,))
            for col in ("bars", "distinct_bars", "exact_bars", "min_close", "max_close"):
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
            full = (frame.bars.eq(240) & frame.distinct_bars.eq(240) &
                    frame.exact_bars.eq(240) &
                    frame.first_bar.astype(str).str.endswith("09:31:00") &
                    frame.last_bar.astype(str).str.endswith("15:00:00") &
                    frame.min_close.gt(0) & frame.max_close.gt(0))
            output = pd.DataFrame({"signal_date": day,
                                   "symbol6": frame.loc[full, "stk_code"].astype(str).str.zfill(6)})
            output.to_csv(path, index=False, compression="gzip")
            print(f"full {day}: {len(output)}", flush=True)
        conn.rollback()
    all_full = pd.concat([pd.read_csv(cache / f"{day}.csv.gz", dtype={"symbol6": str})
                          for day in days], ignore_index=True)
    all_full.to_csv(args.out / "full_universe.csv.gz", index=False, compression="gzip")
    print({"rows": len(all_full), "days": all_full.signal_date.nunique()}, flush=True)


if __name__ == "__main__":
    main()

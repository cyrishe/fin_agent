"""Check 15m close timestamps against finalized August 1m closes."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection


DATES = ("2026-08-05", "2026-08-06", "2026-08-07",
         "2026-08-10", "2026-08-11")
TIMES = ("09:45", "14:45")


def run(cache: Path, env_file: Path, output: Path) -> dict:
    with db_connection(env_file) as db:
        minute = []
        with db.cursor() as cursor:
            for day in DATES:
                for clock in TIMES:
                    cursor.execute("""
                        SELECT LEFT(stk_code,6) AS symbol6, latest_price,
                               is_finalized, is_fallback
                        FROM aiia_stock_realtime_minute_snapshot_full
                        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                          AND bar_end_time=%s
                    """, (day, f"{day} {clock}:00"))
                    minute.extend({"date": day, "time": clock, **row}
                                  for row in cursor.fetchall())
    one = pd.DataFrame(minute)
    one["latest_price"] = pd.to_numeric(one.latest_price, errors="coerce")
    one = one[one.is_finalized.eq(1) & one.is_fallback.eq(0) &
              one.latest_price.gt(0)]
    by_symbol = one.groupby("symbol6")
    pairs = []
    for symbol, observations in by_symbol:
        file = cache / f"{symbol}.json.gz"
        if not file.exists():
            continue
        with gzip.open(file, "rt", encoding="utf8") as source:
            bars = json.load(source)
        lookup = {str(bar.get("day", ""))[:16]: bar for bar in bars}
        for row in observations.itertuples():
            bar = lookup.get(f"{row.date} {row.time}")
            if bar is None:
                continue
            try:
                close_15m = float(bar["close"])
            except (KeyError, TypeError, ValueError):
                continue
            if not np.isfinite(close_15m) or close_15m <= 0:
                continue
            pairs.append({"date": row.date, "time": row.time,
                          "symbol6": symbol, "close_15m": close_15m,
                          "close_1m": float(row.latest_price)})
    match = pd.DataFrame(pairs)
    if match.empty:
        raise ValueError("No shared 15m / finalized 1m closes")
    match["abs_price_difference"] = (match.close_15m-match.close_1m).abs()
    match["abs_relative_difference"] = (
        match.abs_price_difference/match.close_1m)

    def metrics(group: pd.DataFrame) -> dict:
        return {"pairs": len(group),
                "exact_price_match": int(group.abs_price_difference.eq(0).sum()),
                "within_0_01_cny": int(group.abs_price_difference.le(.01).sum()),
                "within_0_1pct": int(group.abs_relative_difference.le(.001).sum()),
                "median_abs_relative_difference": float(
                    group.abs_relative_difference.median()),
                "p99_abs_relative_difference": float(
                    group.abs_relative_difference.quantile(.99))}

    summary = {"all": metrics(match),
               "by_time": {time: metrics(group)
                           for time, group in match.groupby("time")},
               "source": "Sina 15m close vs KingdomAI finalized, non-fallback 1m latest_price at same bar end",
               "dates": list(DATES)}
    output.mkdir(parents=True, exist_ok=True)
    match.to_csv(output / "bridge_15m_vs_1m_pairs.csv", index=False)
    (output / "bridge_15m_vs_1m_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path,
                        default=Path("outputs/stock_automl/sina_15m_rolling/cache"))
    parser.add_argument("--env-file", type=Path,
                        default=Path("/Volumes/ext/fin_agent/.env"))
    parser.add_argument("--output", type=Path,
                        default=Path("docs/stock_automl_runs/20261010_four_class_15m_close_rolling"))
    args = parser.parse_args()
    run(args.cache, args.env_file, args.output)

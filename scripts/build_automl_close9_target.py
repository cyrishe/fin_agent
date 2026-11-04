"""Build exact next-morning 09:32–09:40 close-price targets for 14:40 entries."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_automl_tail_three_class import read_samples


SOURCES = tuple(Path(p) for p in (
    "outputs/stock_automl/tail_expanded_august/trainable_1440.csv",
    "outputs/stock_automl/tail_sep30_check/trainable_1440.csv",
    "outputs/stock_automl/tail_aug05_aug06_exact/trainable_1440.csv",
    "outputs/stock_automl/tail_aug24_exact/trainable_1440.csv",
))
OUT = Path("docs/stock_automl_runs/20261009_close9_target")
MINUTES = tuple(range(32, 41))


def source_rows(paths=SOURCES):
    rows = pd.concat([read_samples(path) for path in paths], ignore_index=True)
    rows["signal_date"] = rows.signal_date.dt.strftime("%Y-%m-%d")
    rows["next_date"] = rows.next_date.dt.strftime("%Y-%m-%d")
    if len(rows) != 14292 or rows.signal_date.nunique() != 40 or rows.duplicated(
            ["signal_date", "symbol6"]).any():
        raise ValueError("Exact historical candidate pool changed")
    return rows


def minute_closes(conn, rows):
    pieces = []
    for day, group in rows.groupby("next_date", sort=True):
        codes = sorted(group.symbol6.unique().tolist())
        placeholders = ",".join(["%s"] * len(codes))
        query_text = (
            "SELECT stk_code, bar_end_time, latest_price, is_fallback, is_finalized, "
            "source_snapshot_time FROM aiia_stock_realtime_minute_snapshot_full "
            "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
            "AND bar_end_time BETWEEN %s AND %s AND stk_code IN (" + placeholders + ")"
        )
        fetched = query(conn, query_text, (day, f"{day} 09:32:00", f"{day} 09:40:00",
                                           *codes))
        if len(fetched) != len(codes) * 9:
            raise ValueError(f"Missing exact morning closes on {day}: {len(fetched)} rows")
        if not (fetched.is_finalized.eq(1) & fetched.is_fallback.eq(0) &
                pd.to_datetime(fetched.bar_end_time).eq(
                    pd.to_datetime(fetched.source_snapshot_time))).all():
            raise ValueError(f"Non-final or fallback minute on {day}")
        fetched["next_date"] = day
        fetched["symbol6"] = fetched.stk_code.str[:6]
        fetched["minute"] = pd.to_datetime(fetched.bar_end_time).dt.strftime("%H:%M")
        if fetched.duplicated(["next_date", "symbol6", "minute"]).any():
            raise ValueError(f"Duplicate exact minute on {day}")
        pieces.append(fetched[["next_date", "symbol6", "minute", "latest_price"]])
        print(f"{day}: {len(codes)} stocks, {len(fetched)} bars", flush=True)
    bars = pd.concat(pieces, ignore_index=True)
    wide = bars.pivot(index=["next_date", "symbol6"], columns="minute",
                      values="latest_price")
    wide = wide.rename(columns={f"09:{minute:02}": f"close_{minute:02}"
                                for minute in MINUTES}).reset_index()
    if list(wide.columns[-9:]) != [f"close_{minute:02}" for minute in MINUTES]:
        raise ValueError("Unexpected morning minute sequence")
    return wide


def build(rows, closes):
    result = rows.merge(closes, on=["next_date", "symbol6"], how="left",
                        validate="one_to_one")
    close_columns = [f"close_{minute:02}" for minute in MINUTES]
    result[close_columns] = result[close_columns].apply(pd.to_numeric, errors="coerce")
    if result[close_columns].isna().any().any() or not result[close_columns].gt(0).all().all():
        raise ValueError("Incomplete or invalid 09:32–09:40 close-price target")
    result["entry_1440"] = pd.to_numeric(result.signal_price, errors="raise")
    if not result.entry_1440.gt(0).all():
        raise ValueError("Invalid exact 14:40 entry price")
    result["max_close9_return"] = result[close_columns].max(axis=1) / result.entry_1440 - 1
    result["target_class"] = np.select(
        [result.max_close9_return.gt(.01), result.max_close9_return.lt(.005)],
        [1, -1], default=0).astype(int)
    result["close_0940_return"] = result.close_40 / result.entry_1440 - 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args()
    rows = source_rows()
    conn = db_connection(args.env_file)
    try:
        closes = minute_closes(conn, rows)
    finally:
        conn.rollback()
        conn.close()
    result = build(rows, closes)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / "exact_1440_to_close9_candidates.csv.gz"
    result.to_csv(path, index=False, compression="gzip")
    summary = {"signal_dates": sorted(result.signal_date.unique().tolist()),
               "stock_days": len(result), "minute_bars": len(result) * 9,
               "target": "max(09:32..09:40 1m closes)/T 14:40 exact price - 1; >1%=1, <0.5%=-1, otherwise 0",
               "target_counts": {str(k): int(result.target_class.eq(k).sum())
                                 for k in (1, 0, -1)},
               "target_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
               "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in SOURCES}}
    (args.output_dir / "build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("stock_days", "minute_bars", "target_counts")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

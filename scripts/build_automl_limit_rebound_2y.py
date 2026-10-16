"""Read-only two-year cooling panel, with resumable monthly price snapshots."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_limit_rebound_features import KEY, build_panel, prepare_daily


START = "2024-10-01"
SOURCE_START = "2024-09-20"
END = "2026-10-13"
OUT = Path("outputs/stock_automl/limit_rebound_2y_r1")
SIZE_FEATURES = {
    "float_mv_proxy_100m": "决策日成交额/(换手率百分数/100)，单位亿元；交易均价对应的流通市值近似值",
    "log_float_mv_proxy": "上述流通市值亿元值的自然对数；成交额或换手率无效时缺失",
}


def fetch_months(root: Path) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    with db_connection() as conn:
        for month in pd.period_range(SOURCE_START[:7], END[:7], freq="M"):
            path = root / f"price_{month}.csv.gz"
            if not path.exists():
                lo = max(SOURCE_START, str(month.start_time.date()))
                hi = min(END, str(month.end_time.date()))
                frame = query(conn, "SELECT trade_date,stk_code,preclose,open,high,low,close,"
                              "volume,amount,turn_ratio,is_limit_price,update_time "
                              "FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s", (lo, hi))
                if frame.empty:
                    raise ValueError(f"No price rows for {month}")
                frame.to_csv(path, index=False, compression="gzip")
                print(f"{month}: {len(frame)} rows", flush=True)
            paths.append(path)
        conn.rollback()
    return paths


def events_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    """Maximal sealed-limit runs; continuation requires adjacent market dates."""
    calendar = sorted(daily.trade_date.unique())
    date_i = {day: i for i, day in enumerate(calendar)}
    events = []
    for symbol, group in daily.groupby("symbol", sort=False):
        day_i = group.trade_date.map(date_i).to_numpy()
        up = group.up.to_numpy(dtype=bool)
        dates = group.trade_date.to_numpy()
        prior = np.r_[False, up[:-1] & (day_i[1:] - day_i[:-1] == 1)]
        starts = np.flatnonzero(up & ~prior)
        next_up = np.r_[up[1:] & (day_i[1:] - day_i[:-1] == 1), False]
        ends = np.flatnonzero(up & ~next_up)
        for begin, end in zip(starts, ends, strict=True):
            first = str(dates[begin])
            if first >= START:
                events.append({"symbol": symbol, "first_limit_date": first,
                               "streak_end_date": str(dates[end]),
                               "streak_length": int(day_i[end] - day_i[begin] + 1)})
    return pd.DataFrame(events)


def add_size_proxy(features: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """At-close traded amount / turnover fraction approximates float market cap."""
    same_day = daily[["trade_date", "symbol", "amount", "turn_ratio"]].copy()
    same_day["float_mv_proxy_100m"] = np.where(
        same_day.amount.gt(0) & same_day.turn_ratio.gt(.01),
        same_day.amount.div(same_day.turn_ratio).mul(100 / 1e8), np.nan)
    same_day["log_float_mv_proxy"] = np.log(same_day.float_mv_proxy_100m.where(
        same_day.float_mv_proxy_100m.gt(0)))
    same_day = same_day[["trade_date", "symbol", "float_mv_proxy_100m", "log_float_mv_proxy"]]
    return features.merge(same_day, left_on=["asof_date", "symbol"],
                          right_on=["trade_date", "symbol"], how="left",
                          validate="one_to_one").drop(columns="trade_date")


def run(root: Path = OUT) -> dict:
    source_paths = fetch_months(root / "price_months")
    raw = pd.concat((pd.read_csv(path, dtype={"stk_code": str, "trade_date": str})
                     for path in source_paths), ignore_index=True)
    daily = prepare_daily(raw)
    events = events_from_daily(daily)
    print(f"source={len(daily)} events={len(events)}", flush=True)
    features, labels = build_panel(daily, events)
    features = add_size_proxy(features, daily)
    root.mkdir(parents=True, exist_ok=True)
    events.to_csv(root / "events.csv.gz", index=False, compression="gzip")
    features.to_csv(root / "features.csv.gz", index=False, compression="gzip")
    labels.to_csv(root / "labels.csv.gz", index=False, compression="gzip")
    manifest = {
        "source": "kingdomai.kcrp_stock_price", "source_start": SOURCE_START,
        "event_start": START, "end": END, "daily_rows": len(daily),
        "events": len(events), "decision_rows": len(features),
        "known_next_labels": int(labels.label_next_observed.sum()),
        "size_proxy_coverage": float(features.log_float_mv_proxy.notna().mean()),
        "source_max_update_time": str(raw.update_time.max()),
        "monthly_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in source_paths},
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in manifest.items() if key != "monthly_sha256"},
                     ensure_ascii=False, indent=2), flush=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    run(parser.parse_args().out)

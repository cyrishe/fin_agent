"""Read-only minute-close trigger audit for D3's prior-day and 09:40 top 20.

This is a price-path simulation, not evidence of real order fills.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from scripts.benchmark_automl_1440_inference import db_connection, query


ROOT = Path("outputs/stock_automl/deep_v_20261013/model_v1")


def fetch_selected(top: pd.DataFrame) -> pd.DataFrame:
    cache = ROOT / "minute_selected"
    cache.mkdir(exist_ok=True)
    full = pd.read_csv(ROOT / "full_universe.csv.gz", dtype={"symbol6": str})
    full_by_day = {day: set(group.symbol6) for day, group in full.groupby("signal_date")}
    load_dotenv(".env")
    with db_connection() as conn:
        for day, group in top.groupby("signal_date"):
            path = cache / f"{day}.csv.gz"
            symbols = sorted(group.symbol6.unique())
            cached = (pd.read_csv(path, dtype={"symbol6": str}) if path.exists()
                      else pd.DataFrame(columns=["signal_date", "symbol6",
                                                 "bar_end_time", "latest_price"]))
            missing = sorted((set(symbols) & full_by_day[day]) -
                             set(cached.symbol6.unique()))
            if not missing:
                continue
            placeholders = ",".join(["%s"] * len(missing))
            sql = f"""SELECT LEFT(stk_code,6) AS symbol6,bar_end_time,latest_price
                FROM aiia_stock_realtime_minute_snapshot_full
                WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                AND is_finalized=1 AND is_fallback=0
                AND source_snapshot_time=bar_end_time
                AND stk_code IN ({placeholders})
                ORDER BY stk_code,bar_end_time"""
            frame = query(conn, sql, (day, *missing))
            frame.insert(0, "signal_date", day)
            frame = pd.concat([cached, frame], ignore_index=True)
            frame.to_csv(path, index=False, compression="gzip")
            print(f"fetched selected {day}: {len(frame)} cached rows, "
                  f"{len(missing)} new symbols", flush=True)
        conn.rollback()
    return pd.concat([pd.read_csv(cache / f"{day}.csv.gz", dtype={"symbol6": str})
                      for day in sorted(top.signal_date.unique())], ignore_index=True)


def simulate_path(frame: pd.DataFrame, prior_close: float,
                  signal_time: str) -> dict:
    frame = frame.sort_values("bar_end_time")
    times = pd.to_datetime(frame.bar_end_time)
    prices = pd.to_numeric(frame.latest_price, errors="coerce").to_numpy(dtype=float)
    full = (len(frame) == 240 and times.nunique() == 240 and
            times.iloc[0].strftime("%H:%M") == "09:31" and
            times.iloc[-1].strftime("%H:%M") == "15:00" and
            np.isfinite(prices).all() and (prices > 0).all())
    result = {"full240": bool(full), "close": np.nan, "prior_trigger": False,
              "prior_entry": np.nan, "prior_return": np.nan,
              "peak_trigger": False, "peak_entry": np.nan, "peak_return": np.nan}
    if not full:
        return result
    result["close"] = prices[-1]
    after = (times.dt.strftime("%H:%M") > signal_time).to_numpy()
    prior_hit = np.where(after & (prices <= 0.95 * prior_close))[0]
    peak = np.maximum.accumulate(prices)
    peak_hit = np.where(after & (prices <= 0.95 * peak))[0]
    for label, hits in (("prior", prior_hit), ("peak", peak_hit)):
        valid = hits[hits + 1 < len(prices)]
        if len(valid):
            entry = prices[valid[0] + 1]
            result[f"{label}_trigger"] = True
            result[f"{label}_entry"] = entry
            result[f"{label}_return"] = prices[-1] / entry - 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-file", default="top20.csv")
    parser.add_argument("--prefix", default="execution")
    parser.add_argument("--models", default="")
    args = parser.parse_args()
    top = pd.read_csv(ROOT / args.top_file, dtype={"symbol6": str})
    if args.models:
        top = top[top.model.isin(args.models.split(","))].copy()
    top["signal_time"] = np.where(top.model.str.contains("prior"), "09:30", "09:40")
    minute = fetch_selected(top)
    top = top.drop(columns=["p40", "full240"], errors="ignore")
    rows = []
    for (day, symbol), group in minute.groupby(["signal_date", "symbol6"]):
        chosen = top[(top.signal_date == day) & (top.symbol6 == symbol)]
        if chosen.empty:
            continue
        prior_close = float(chosen.prior_close.iloc[0])
        for signal_time in chosen.signal_time.unique():
            rows.append({"signal_date": day, "symbol6": symbol,
                         "signal_time": signal_time,
                         **simulate_path(group, prior_close, signal_time)})
    path = pd.DataFrame(rows)
    result = top.merge(path, on=["signal_date", "symbol6", "signal_time"], how="left",
                       validate="many_to_one")
    result["full240"] = result.full240.eq(True)
    result.to_csv(ROOT / f"{args.prefix}_top20.csv", index=False)
    daily = result.groupby(["signal_date", "model"]).agg(
        full240=("full240", "sum"),
        prior_trades=("prior_trigger", "sum"),
        prior_mean_return=("prior_return", "mean"),
        peak_trades=("peak_trigger", "sum"),
        peak_mean_return=("peak_return", "mean"),
        deepv=("label", lambda s: s.eq(1).sum()),
    ).reset_index()
    daily.to_csv(ROOT / f"{args.prefix}_daily.csv", index=False)
    print(result.groupby("model")[["full240", "prior_trigger", "peak_trigger"]].sum(),
          flush=True)
    print(result.groupby("model")[["prior_return", "peak_return"]].mean(), flush=True)


if __name__ == "__main__":
    main()

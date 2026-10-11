"""Build read-only T tail -> T+1 morning research rows.

The requested 14:40 cohort is retrospective: the archived 14:40 bar did not
arrive by 14:49:59. A separate 14:30 cohort contains arrival-verified rows.
Raw stock-level exports stay in the ignored output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import dotenv_values

from scripts.experiment_automl_1450_grid import board_limit, frame, read_only_db


START, END = "2026-08-24", "2026-09-30"
CUTOFF = "14:49:59"
HISTORY_START = "2026-07-15"
MINUTE_TO_SHARES = 100  # Verified against the daily share-volume field.


def load_daily(conn):
    price = frame(conn, """
        SELECT trade_date AS date, LEFT(stk_code,6) AS symbol6, close, adjclose,
          volume, is_limit_price, create_time, update_time
        FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s
    """, (HISTORY_START, END))
    value = frame(conn, """
        SELECT trade_date AS date, LEFT(stk_code,6) AS symbol6,
          float_mv, free_float_mv, float_share, create_time, update_time
        FROM kcrp_stock_pricevaluate WHERE trade_date BETWEEN %s AND %s
    """, (HISTORY_START, END))
    for source in (price, value):
        source["date"] = pd.to_datetime(source.date)
        source["symbol6"] = source.symbol6.astype(str).str.zfill(6)
        source["create_time"] = pd.to_datetime(source.create_time, errors="coerce")
        source["update_time"] = pd.to_datetime(source.update_time, errors="coerce")
        source.drop_duplicates(["date", "symbol6"], keep=False, inplace=True)
    for col in ("close", "adjclose", "volume"):
        price[col] = pd.to_numeric(price[col], errors="coerce")
    for col in ("float_mv", "free_float_mv", "float_share"):
        value[col] = pd.to_numeric(value[col], errors="coerce")
    return price, value


def build_history(price, value):
    calendar = sorted(price.date.unique())
    next_date = dict(zip(calendar[:-1], calendar[1:]))
    day_index = {day: i for i, day in enumerate(calendar)}
    price = price.sort_values(["symbol6", "date"]).copy()
    price["day_index"] = price.date.map(day_index)
    groups = price.groupby("symbol6", sort=False)
    for window in (5, 10, 20):
        price[f"ma{window}_adj"] = groups.adjclose.transform(
            lambda s: s.rolling(window, min_periods=window).mean())
    for lag in range(1, 6):
        price[f"volume_tminus{lag}"] = groups.volume.shift(lag - 1)
    price["avg_volume5_shares"] = price[[f"volume_tminus{i}" for i in range(1, 6)]].mean(axis=1)
    price["history_contiguous20"] = price.day_index.sub(groups.day_index.shift(19)).eq(19)
    for stamp in ("create_time", "update_time"):
        lookback = pd.concat([groups[stamp].shift(i) for i in range(20)], axis=1)
        price[f"history_{stamp}"] = lookback.max(axis=1).where(lookback.notna().all(axis=1))
    price["signal_date"] = price.date.map(next_date)
    cutoff = pd.to_datetime(price.signal_date.astype(str) + f" {CUTOFF}", errors="coerce")
    price["history_ready"] = (
        price.history_contiguous20 & price.close.gt(0) & price.adjclose.gt(0) &
        price.ma20_adj.gt(0) & price.avg_volume5_shares.gt(0) &
        price[[f"volume_tminus{i}" for i in range(1, 6)]].gt(0).all(axis=1) &
        price.history_create_time.le(cutoff) & price.history_update_time.le(cutoff))
    history = price[["signal_date", "symbol6", "date", "close", "adjclose",
                     "history_ready", "avg_volume5_shares", "ma5_adj", "ma10_adj",
                     "ma20_adj", *[f"volume_tminus{i}" for i in range(1, 6)]]].rename(
                         columns={"date": "prior_date", "close": "prior_close",
                                  "adjclose": "prior_adjclose"})
    value["signal_date"] = value.date.map(next_date)
    value_cutoff = pd.to_datetime(value.signal_date.astype(str) + f" {CUTOFF}", errors="coerce")
    value["value_ready"] = (value.float_mv.gt(0) & value.float_share.gt(0) &
                            value.create_time.le(value_cutoff) &
                            value.update_time.le(value_cutoff))
    value = value[["signal_date", "symbol6", "date", "value_ready", "float_mv",
                   "free_float_mv", "float_share"]].rename(columns={"date": "value_date"})
    return history.merge(value, on=["signal_date", "symbol6"], how="left",
                         validate="one_to_one"), calendar


def signal_bars(conn, day):
    rows = frame(conn, """
        SELECT stk_code AS symbol6, stk_name AS name, TIME_FORMAT(bar_end_time,'%%H:%%i') AS signal_time,
          latest_price AS signal_price, is_fallback, is_finalized,
          source_snapshot_time, fetch_time, created_at, updated_at
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time IN (%s, %s)
    """, (day, f"{day} 14:30:00", f"{day} 14:40:00"))
    rows["signal_date"] = pd.Timestamp(day)
    return rows


def intraday_history(conn, day, symbols):
    if not symbols:
        return pd.DataFrame(columns=["symbol6"])
    cutoff = f"{day} {CUTOFF}"
    before30 = f"{day} 14:30:00"
    placeholders = ",".join(["%s"] * len(symbols))
    sql = f"""
        SELECT stk_code AS symbol6,
          COUNT(*) AS bars_1440,
          SUM(bar_end_time<=%s) AS bars_1430,
          SUM(volume) AS minute_volume_1440,
          SUM(CASE WHEN bar_end_time<=%s THEN volume ELSE 0 END) AS minute_volume_1430,
          MIN(low_price) AS min_low_1440,
          MIN(CASE WHEN bar_end_time<=%s THEN low_price END) AS min_low_1430,
          MAX(is_fallback) AS fallback_1440,
          MAX(CASE WHEN bar_end_time<=%s THEN is_fallback END) AS fallback_1430,
          MIN(is_finalized) AS finalized_1440,
          MIN(CASE WHEN bar_end_time<=%s THEN is_finalized END) AS finalized_1430,
          SUM(fetch_time<=%s AND created_at<=%s AND updated_at<=%s
              AND source_snapshot_time=bar_end_time) AS arrived_1440,
          SUM(CASE WHEN bar_end_time<=%s AND fetch_time<=%s AND created_at<=%s
              AND updated_at<=%s AND source_snapshot_time=bar_end_time THEN 1 ELSE 0 END)
              AS arrived_1430
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time BETWEEN %s AND %s AND stk_code IN ({placeholders})
        GROUP BY stk_code
    """
    params = (before30,) * 5 + (cutoff,) * 3 + (before30,) + (cutoff,) * 3 + (
        day, f"{day} 09:31:00", f"{day} 14:40:00", *symbols)
    return frame(conn, sql, params)


def entry_bar(conn, day):
    rows = frame(conn, """
        SELECT stk_code AS symbol6, latest_price AS entry_1450,
          is_fallback AS entry_fallback, is_finalized AS entry_finalized
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time=%s
    """, (day, f"{day} 14:50:00"))
    rows["signal_date"] = pd.Timestamp(day)
    return rows


def next_morning(conn, day):
    rows = frame(conn, """
        SELECT stk_code AS symbol6,
          MAX(CASE WHEN bar_end_time=%s THEN open_price END) AS next_open,
          MAX(CASE WHEN bar_end_time<=%s THEN high_price END) AS next_high5,
          MAX(high_price) AS next_high10,
          SUM(bar_end_time<=%s) AS bars5, COUNT(*) AS bars10,
          MAX(is_fallback) AS morning_fallback,
          MIN(is_finalized) AS morning_finalized,
          SUM(CASE WHEN bar_end_time<=%s THEN volume ELSE 0 END) AS morning_volume5
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time BETWEEN %s AND %s
        GROUP BY stk_code
    """, (f"{day} 09:31:00", f"{day} 09:35:00", f"{day} 09:35:00",
          f"{day} 09:35:00", day, f"{day} 09:31:00", f"{day} 09:40:00"))
    rows["next_date"] = pd.Timestamp(day)
    return rows


def assemble(rows, minute, entry, morning, final_limit):
    rows = rows.merge(minute, on="symbol6", how="left", validate="many_to_one")
    rows = rows.merge(entry, on=["signal_date", "symbol6"], how="left", validate="many_to_one")
    rows = rows.merge(morning, on=["next_date", "symbol6"], how="left", validate="many_to_one")
    rows = rows.merge(final_limit, on=["signal_date", "symbol6"], how="left",
                      validate="many_to_one")
    cutoff = pd.to_datetime(rows.signal_date.astype(str) + f" {CUTOFF}")
    for col in ("signal_price", "prior_close", "prior_adjclose", "entry_1450",
                "next_open", "next_high5", "next_high10", "float_mv", "float_share",
                "minute_volume_1430", "minute_volume_1440", "min_low_1430",
                "min_low_1440"):
        rows[col] = pd.to_numeric(rows[col], errors="coerce")
    for col in ("source_snapshot_time", "fetch_time", "created_at", "updated_at"):
        rows[col] = pd.to_datetime(rows[col], errors="coerce")
    rows["signal_arrived_1449"] = (
        rows[["source_snapshot_time", "fetch_time", "created_at", "updated_at"]]
        .le(cutoff, axis=0).all(axis=1) & rows.is_fallback.eq(0) &
        rows.is_finalized.eq(1))
    rows["signal_return"] = rows.signal_price / rows.prior_close - 1
    rows["limit_buffer"] = [board_limit(symbol, name) for symbol, name in
                            zip(rows.symbol6, rows.name)]
    for time, minutes in (("1430", 210), ("1440", 220)):
        complete = (rows[f"bars_{time}"].eq(minutes) &
                    rows[f"fallback_{time}"].eq(0) & rows[f"finalized_{time}"].eq(1) &
                    rows[f"minute_volume_{time}"].gt(0) & rows[f"min_low_{time}"].gt(0))
        rows[f"minute_complete_{time}"] = complete
        rows[f"minute_ready_{time}"] = complete & rows[f"arrived_{time}"].eq(minutes)
    time = rows.signal_time.str.replace(":", "", regex=False)
    minutes = time.map({"1430": 210, "1440": 220})
    rows["minute_bars"] = np.where(time.eq("1430"), rows.bars_1430, rows.bars_1440)
    rows["minute_volume_shares"] = np.where(
        time.eq("1430"), rows.minute_volume_1430, rows.minute_volume_1440) * MINUTE_TO_SHARES
    rows["min_low_so_far"] = np.where(time.eq("1430"), rows.min_low_1430, rows.min_low_1440)
    rows["minute_ready_1449"] = np.where(time.eq("1430"), rows.minute_ready_1430,
                                          rows.minute_ready_1440)
    rows["minute_complete"] = np.where(time.eq("1430"), rows.minute_complete_1430,
                                        rows.minute_complete_1440)
    rows["volume_ratio"] = rows.minute_volume_shares * 240 / minutes / rows.avg_volume5_shares
    rows["turnover_so_far_pct"] = rows.minute_volume_shares / rows.float_share * 100
    rows["float_mv_100m_cny"] = rows.float_mv / 1e8
    rows["free_float_mv_100m_cny"] = pd.to_numeric(
        rows.free_float_mv, errors="coerce") / 1e8
    for window in (5, 10, 20):
        rows[f"ma{window}"] = (rows[f"ma{window}_adj"] * rows.prior_close /
                               rows.prior_adjclose)
    rows["ma_bull_5_10_20"] = rows.ma5.gt(rows.ma10) & rows.ma10.gt(rows.ma20)
    rows["price_above_all_ma"] = rows.signal_price.gt(rows[["ma5", "ma10", "ma20"]].max(axis=1))
    rows["all_intraday_lows_above_ma"] = rows.min_low_so_far.gt(
        rows[["ma5", "ma10", "ma20"]].max(axis=1))
    for flag in ("ma_bull_5_10_20", "price_above_all_ma", "all_intraday_lows_above_ma"):
        rows[flag] = rows[flag].astype("boolean")
    rows["feature_ready_1449"] = (rows.signal_arrived_1449 & rows.minute_ready_1449 &
                                  rows.history_ready.eq(True) & rows.value_ready.eq(True))
    unavailable = ~rows.history_ready.eq(True)
    historical_cols = ["avg_volume5_shares", "volume_ratio", "ma5", "ma10", "ma20",
                       "ma_bull_5_10_20", "price_above_all_ma",
                       "all_intraday_lows_above_ma", *[f"volume_tminus{i}" for i in range(1, 6)]]
    rows.loc[unavailable, historical_cols] = np.nan
    rows.loc[~rows.value_ready.eq(True), ["float_mv_100m_cny",
              "free_float_mv_100m_cny", "turnover_so_far_pct"]] = np.nan
    rows.loc[~rows.minute_complete, ["minute_volume_shares", "min_low_so_far",
              "volume_ratio", "turnover_so_far_pct", "price_above_all_ma",
              "all_intraday_lows_above_ma"]] = np.nan
    entry_ok = rows.entry_1450.gt(0) & rows.entry_fallback.eq(0) & rows.entry_finalized.eq(1)
    rows["entry_not_near_limit_proxy"] = (entry_ok & rows.limit_buffer.notna() &
                                          (rows.entry_1450 / rows.prior_close - 1)
                                          .lt(rows.limit_buffer))
    rows["label_ready5"] = (entry_ok & rows.next_open.gt(0) & rows.next_high5.gt(0) &
                             rows.bars5.eq(5) & rows.morning_volume5.gt(0) &
                             rows.morning_fallback.eq(0) & rows.morning_finalized.eq(1))
    rows["label_ready10"] = rows.label_ready5 & rows.next_high10.gt(0) & rows.bars10.eq(10)
    rows["target_next_open_return"] = (rows.next_open / rows.entry_1450 - 1).where(rows.label_ready5)
    rows["target_next_high5_return"] = (rows.next_high5 / rows.entry_1450 - 1).where(rows.label_ready5)
    rows["target_next_high10_return"] = (rows.next_high10 / rows.entry_1450 - 1).where(rows.label_ready10)
    return rows


EXPORT = ["signal_date", "next_date", "symbol6", "name", "signal_time",
          "signal_price", "prior_close", "signal_return", "signal_arrived_1449",
          "history_ready", "value_ready", "minute_bars", "minute_complete",
          "minute_ready_1449",
          "feature_ready_1449", "minute_volume_shares", "volume_ratio",
          "turnover_so_far_pct", "float_mv_100m_cny", "free_float_mv_100m_cny",
          "volume_tminus5", "volume_tminus4", "volume_tminus3", "volume_tminus2",
          "volume_tminus1", "avg_volume5_shares", "ma5", "ma10", "ma20",
          "ma_bull_5_10_20", "price_above_all_ma", "all_intraday_lows_above_ma",
          "entry_1450", "next_open", "next_high5", "next_high10", "label_ready5",
          "label_ready10", "limit_buffer", "entry_not_near_limit_proxy",
          "target_next_open_return", "target_next_high5_return",
          "target_next_high10_return", "t_final_limit_flag"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output-dir", default="outputs/stock_automl/tail_sample")
    parser.add_argument("--summary", default="docs/stock_automl_runs/20261008_tail_sample/summary.json")
    args = parser.parse_args()
    config = dotenv_values(args.env_file)
    os.environ.setdefault("SIMPLE_BI_PLATFORM_DB_URL",
                          config.get("SIMPLE_BI_PLATFORM_DB_URL") or config.get("PLATFORM_DB_URL") or "")
    with read_only_db() as conn:
        daily, value = load_daily(conn)
        history, calendar = build_history(daily, value)
        signal_days = [d for d in calendar if pd.Timestamp(START) <= d <= pd.Timestamp(END)]
        next_date = dict(zip(signal_days[:-1], signal_days[1:]))
        final_limit = daily[["date", "symbol6", "is_limit_price"]].rename(
            columns={"date": "signal_date", "is_limit_price": "t_final_limit_flag"})
        cohorts = {"1430": [], "1440": []}
        day_audit = []
        for day, following in next_date.items():
            day_str = str(day)[:10]
            bars = signal_bars(conn, day_str)
            if bars.empty:
                day_audit.append({"date": day_str, "candidates_1430": 0,
                                  "candidates_1440": 0, "missing_signal_bars": True})
                print(f"{day_str}: no signal bars", flush=True)
                continue
            points = bars.merge(
                history[history.signal_date.eq(day)], on=["signal_date", "symbol6"],
                how="left", validate="many_to_one")
            points["signal_price"] = pd.to_numeric(points.signal_price, errors="coerce")
            points["prior_close"] = pd.to_numeric(points.prior_close, errors="coerce")
            points["signal_return"] = points.signal_price / points.prior_close - 1
            selected = points[points.signal_return.between(.03, .06, inclusive="both") &
                              points.signal_time.isin(["14:30", "14:40"])].copy()
            if selected.empty:
                day_audit.append({"date": day_str, "candidates_1430": 0, "candidates_1440": 0})
                continue
            selected["next_date"] = following
            symbols = sorted(selected.symbol6.unique().tolist())
            minute = intraday_history(conn, day_str, symbols)
            entry = entry_bar(conn, day_str)
            morning = next_morning(conn, str(following)[:10])
            ready = assemble(selected, minute, entry, morning,
                             final_limit[final_limit.signal_date.eq(day)])
            for label, clock in (("1430", "14:30"), ("1440", "14:40")):
                cohorts[label].append(ready[ready.signal_time.eq(clock)].copy())
            day_audit.append({"date": day_str,
                              "candidates_1430": int(ready.signal_time.eq("14:30").sum()),
                              "candidates_1440": int(ready.signal_time.eq("14:40").sum()),
                              "ready_1430": int((ready.signal_time.eq("14:30") &
                                                 ready.feature_ready_1449 & ready.label_ready10 &
                                                 ready.limit_buffer.notna() &
                                                 ready.signal_return.lt(ready.limit_buffer)).sum()),
                              "execution_feasible_1430": int((ready.signal_time.eq("14:30") &
                                                               ready.feature_ready_1449 &
                                                               ready.label_ready10 &
                                                               ready.entry_not_near_limit_proxy).sum()),
                              "ready_1440": int((ready.signal_time.eq("14:40") &
                                                 ready.feature_ready_1449 & ready.label_ready10).sum())})
            print(f"{day_str}: {day_audit[-1]}", flush=True)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    complete = {}
    for label in ("1440", "1430"):
        rows = pd.concat(cohorts[label], ignore_index=True) if cohorts[label] else pd.DataFrame(columns=EXPORT)
        rows = rows[EXPORT].sort_values(["signal_date", "symbol6"])
        rows.to_csv(output / f"candidates_{label}.csv", index=False, float_format="%.8f")
        complete[label] = rows
    trainable = complete["1430"][complete["1430"].feature_ready_1449 &
                                  complete["1430"].label_ready10 &
                                  complete["1430"].limit_buffer.notna() &
                                  complete["1430"].signal_return.lt(
                                      complete["1430"].limit_buffer)].copy()
    trainable.to_csv(output / "trainable_1430.csv", index=False, float_format="%.8f")
    execution_feasible = trainable[trainable.entry_not_near_limit_proxy].copy()
    execution_feasible.to_csv(output / "execution_feasible_1430.csv", index=False,
                              float_format="%.8f")
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "signal_period": [START, str(signal_days[-2])[:10]],
        "decision_cutoff": CUTOFF,
        "retrospective_1440_rows": len(complete["1440"]),
        "retrospective_1440_asof_ready": int(complete["1440"].feature_ready_1449.sum()),
        "strict_1430_candidates": len(complete["1430"]),
        "strict_1430_trainable": len(trainable),
        "execution_feasible_1430": len(execution_feasible),
        "strict_1430_label_ready10": int(complete["1430"].label_ready10.sum()),
        "day_audit": day_audit,
        "provenance": {"script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       "database_snapshot_version": None,
                       "raw_row_location": str(output)},
    }
    path = Path(args.summary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {path} and {output}")


if __name__ == "__main__":
    main()

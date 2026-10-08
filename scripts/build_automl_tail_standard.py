"""Build the standard 14:40 candidate dataset without ingestion-time gates.

Historical one-minute bars stand in for the live 14:40 quote. Features use
only T 14:40 or earlier market data and T-1 or older daily/valuation rows.
The T daily preclose field is used only as a proxy for the preclose supplied
by the live quote API. Stock-level exports remain in an ignored local path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import dotenv_values

from scripts.experiment_automl_1450_grid import board_limit, frame, read_only_db


START, END = "2026-08-24", "2026-09-30"
HISTORY_START = "2026-07-15"
MINUTE_TO_SHARES = 100
ADJ_PRICE_BREAK_TOLERANCE = 0.01


def load_daily(conn, history_start=HISTORY_START, end=END):
    price = frame(conn, """
        SELECT trade_date AS date, LEFT(stk_code,6) AS symbol6,
          preclose, close, adjpreclose, adjclose, volume, is_limit_price
        FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s
    """, (history_start, end))
    value = frame(conn, """
        SELECT trade_date AS date, LEFT(stk_code,6) AS symbol6,
          float_mv, float_share
        FROM kcrp_stock_pricevaluate WHERE trade_date BETWEEN %s AND %s
    """, (history_start, end))
    for source in (price, value):
        source["date"] = pd.to_datetime(source.date)
        source["symbol6"] = source.symbol6.astype(str).str.zfill(6)
        source.drop_duplicates(["date", "symbol6"], keep=False, inplace=True)
    for column in ("preclose", "close", "adjpreclose", "adjclose", "volume"):
        price[column] = pd.to_numeric(price[column], errors="coerce")
    for column in ("float_mv", "float_share"):
        value[column] = pd.to_numeric(value[column], errors="coerce")
    return price, value


def four_of_five_volume_increasing(price):
    """Whether any four chronological observations strictly increase."""
    chronological = [f"volume_tminus{i}" for i in range(5, 0, -1)]
    increasing = pd.Series(False, index=price.index)
    for sequence in combinations(chronological, 4):
        pattern = pd.Series(True, index=price.index)
        for earlier, later in zip(sequence, sequence[1:]):
            pattern &= price[earlier].lt(price[later])
        increasing |= pattern
    return increasing.astype("Int8")


def prior_profile(price, value):
    calendar = sorted(price.date.unique())
    next_date = dict(zip(calendar[:-1], calendar[1:]))
    date_index = {day: i for i, day in enumerate(calendar)}
    price = price.sort_values(["symbol6", "date"]).copy()
    price["date_index"] = price.date.map(date_index)
    groups = price.groupby("symbol6", sort=False)
    previous_adjclose = groups.adjclose.shift(1)
    adj_ratio = price.adjpreclose.div(previous_adjclose)
    price["adj_price_break_on_day"] = adj_ratio.notna() & adj_ratio.sub(1).abs().gt(
        ADJ_PRICE_BREAK_TOLERANCE)
    price["adj_price_break_20d"] = price.groupby("symbol6")["adj_price_break_on_day"].transform(
        lambda s: s.rolling(20, min_periods=20).max()).astype("boolean")
    for window in (5, 10, 20):
        price[f"ma{window}_adj"] = groups.adjclose.transform(
            lambda s: s.rolling(window, min_periods=window).mean())
    price["min_adjclose20"] = groups.adjclose.transform(
        lambda s: s.rolling(20, min_periods=20).min())
    price["contiguous20"] = price.date_index.sub(groups.date_index.shift(19)).eq(19)
    for lag in range(1, 6):
        price[f"volume_tminus{lag}"] = groups.volume.shift(lag - 1)
    volume_columns = [f"volume_tminus{i}" for i in range(1, 6)]
    price["avg_volume5_shares"] = price[volume_columns].mean(axis=1)
    price["volume_4of5_increasing"] = four_of_five_volume_increasing(price)
    price["history_complete"] = (
        price.contiguous20 & price.close.gt(0) & price.adjclose.gt(0) &
        price.min_adjclose20.gt(0) & price[volume_columns].gt(0).all(axis=1))
    price["signal_date"] = price.date.map(next_date)
    history = price[["signal_date", "symbol6", "date", "close", "adjclose",
                     "history_complete", "adj_price_break_20d", "avg_volume5_shares",
                     "volume_4of5_increasing", "ma5_adj",
                     "ma10_adj", "ma20_adj", *volume_columns]].rename(
                         columns={"date": "prior_date", "close": "prior_close",
                                  "adjclose": "prior_adjclose"})
    value["signal_date"] = value.date.map(next_date)
    value["value_complete"] = value.float_mv.gt(0) & value.float_share.gt(0)
    value = value[["signal_date", "symbol6", "date", "value_complete", "float_mv",
                   "float_share"]].rename(columns={"date": "value_date"})
    profile = history.merge(value, on=["signal_date", "symbol6"], how="left",
                            validate="one_to_one")
    return profile, calendar


def signal_bar(conn, day):
    rows = frame(conn, """
        SELECT stk_code AS symbol6, stk_name AS name,
          latest_price AS signal_price, is_fallback AS signal_fallback,
          is_finalized AS signal_finalized,
          source_snapshot_time AS signal_source_time
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time=%s
    """, (day, f"{day} 14:40:00"))
    rows["signal_date"] = pd.Timestamp(day)
    return rows


def intraday_to_1440(conn, day, symbols):
    if not symbols:
        return pd.DataFrame(columns=["symbol6"])
    placeholders = ",".join(["%s"] * len(symbols))
    sql = f"""
        SELECT stk_code AS symbol6, COUNT(*) AS minute_bars,
          SUM(volume) AS minute_volume_hands, MIN(low_price) AS min_low_so_far,
          MAX(is_fallback) AS minute_fallback,
          MIN(is_finalized) AS minute_finalized,
          SUM(source_snapshot_time=bar_end_time) AS exact_source_bars
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time BETWEEN %s AND %s
          AND stk_code IN ({placeholders}) GROUP BY stk_code
    """
    return frame(conn, sql, (day, f"{day} 09:31:00", f"{day} 14:40:00", *symbols))


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


def morning_bars(conn, day):
    rows = frame(conn, """
        SELECT stk_code AS symbol6,
          MAX(CASE WHEN bar_end_time=%s THEN open_price END) AS next_open,
          MAX(CASE WHEN bar_end_time<=%s THEN high_price END) AS next_high5,
          MAX(high_price) AS next_high10,
          SUM(bar_end_time<=%s) AS bars5, COUNT(*) AS bars10,
          SUM(CASE WHEN bar_end_time<=%s THEN volume ELSE 0 END) AS volume5,
          MAX(is_fallback) AS morning_fallback,
          MIN(is_finalized) AS morning_finalized
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time BETWEEN %s AND %s
        GROUP BY stk_code
    """, (f"{day} 09:31:00", f"{day} 09:35:00", f"{day} 09:35:00",
          f"{day} 09:35:00", day, f"{day} 09:31:00", f"{day} 09:40:00"))
    rows["next_date"] = pd.Timestamp(day)
    return rows


def assemble(signal, minute, entry, morning):
    rows = signal.merge(minute, on="symbol6", how="left", validate="one_to_one")
    rows = rows.merge(entry, on=["signal_date", "symbol6"], how="left",
                      validate="one_to_one")
    rows = rows.merge(morning, on=["next_date", "symbol6"], how="left",
                      validate="one_to_one")
    numeric = ("signal_price", "t_reference_preclose", "prior_close", "prior_adjclose",
               "minute_volume_hands", "min_low_so_far", "float_mv", "float_share",
               "entry_1450", "next_open", "next_high5", "next_high10")
    for column in numeric:
        rows[column] = pd.to_numeric(rows[column], errors="coerce")
    rows["signal_return"] = rows.signal_price / rows.t_reference_preclose - 1
    rows["limit_buffer"] = [board_limit(symbol, name) for symbol, name in
                            zip(rows.symbol6, rows.name)]
    rows["minute_complete"] = (
        rows.minute_bars.eq(220) & rows.exact_source_bars.eq(220) &
        rows.minute_fallback.eq(0) & rows.minute_finalized.eq(1) &
        rows.minute_volume_hands.gt(0) & rows.min_low_so_far.gt(0))
    rows["minute_volume_shares"] = rows.minute_volume_hands * MINUTE_TO_SHARES
    rows["volume_ratio"] = (rows.minute_volume_shares * 240 /
                            (220 * rows.avg_volume5_shares))
    rows["turnover_so_far_pct"] = rows.minute_volume_shares / rows.float_share * 100
    rows["float_mv_100m_cny"] = rows.float_mv / 1e8
    for window in (5, 10, 20):
        rows[f"ma{window}"] = (rows[f"ma{window}_adj"] * rows.t_reference_preclose /
                               rows.prior_adjclose)
    highest_ma = rows[["ma5", "ma10", "ma20"]].max(axis=1)
    rows["ma_bull_5_10_20"] = (rows.ma5.gt(rows.ma10) &
                               rows.ma10.gt(rows.ma20)).astype("boolean")
    rows["price_above_all_ma"] = rows.signal_price.gt(highest_ma).astype("boolean")
    rows["all_intraday_lows_above_ma"] = rows.min_low_so_far.gt(highest_ma).astype("boolean")
    rows["feature_complete"] = (rows.t_reference_preclose.gt(0) &
                                 rows.signal_price.gt(0) & rows.signal_fallback.eq(0) &
                                 rows.signal_finalized.eq(1) & rows.minute_complete &
                                 rows.history_complete.eq(True) &
                                 rows.adj_price_break_20d.eq(False) &
                                 rows.value_complete.eq(True))
    history_missing = ~rows.history_complete.eq(True)
    history_columns = ("avg_volume5_shares", "volume_4of5_increasing", "volume_ratio",
                       "ma5", "ma10", "ma20", "ma_bull_5_10_20",
                       "price_above_all_ma", "all_intraday_lows_above_ma",
                       *[f"volume_tminus{i}" for i in range(1, 6)])
    rows.loc[history_missing, history_columns] = np.nan
    rows.loc[rows.adj_price_break_20d.eq(True),
             ["ma5", "ma10", "ma20", "ma_bull_5_10_20",
              "price_above_all_ma", "all_intraday_lows_above_ma"]] = np.nan
    rows.loc[~rows.value_complete.eq(True), ["float_mv_100m_cny",
              "turnover_so_far_pct"]] = np.nan
    rows.loc[~rows.minute_complete, ["minute_volume_shares", "min_low_so_far",
              "volume_ratio", "turnover_so_far_pct", "all_intraday_lows_above_ma"]] = np.nan
    entry_ok = rows.entry_1450.gt(0) & rows.entry_fallback.eq(0) & rows.entry_finalized.eq(1)
    rows["entry_not_near_limit_proxy"] = (entry_ok & rows.limit_buffer.notna() &
                                          (rows.entry_1450 / rows.t_reference_preclose - 1)
                                          .lt(rows.limit_buffer))
    rows["label_complete"] = (entry_ok & rows.next_open.gt(0) & rows.next_high5.gt(0) &
                              rows.next_high10.gt(0) & rows.bars5.eq(5) &
                              rows.bars10.eq(10) & rows.volume5.gt(0) &
                              rows.morning_fallback.eq(0) & rows.morning_finalized.eq(1))
    for target, price in (("target_next_open_return", "next_open"),
                          ("target_next_high5_return", "next_high5"),
                          ("target_next_high10_return", "next_high10")):
        rows[target] = (rows[price] / rows.entry_1450 - 1).where(rows.label_complete)
    return rows


EXPORT = ["signal_date", "next_date", "symbol6", "name", "t_reference_preclose",
          "signal_price", "signal_return", "signal_fallback", "signal_finalized",
          "history_complete", "adj_price_break_20d", "value_complete",
          "minute_bars", "minute_complete",
          "feature_complete", "minute_volume_shares", "volume_ratio",
          "turnover_so_far_pct", "float_mv_100m_cny", "volume_tminus5",
          "volume_tminus4", "volume_tminus3", "volume_tminus2", "volume_tminus1",
          "avg_volume5_shares", "volume_4of5_increasing", "ma5", "ma10", "ma20",
          "ma_bull_5_10_20",
          "price_above_all_ma", "all_intraday_lows_above_ma", "limit_buffer",
          "entry_1450", "entry_not_near_limit_proxy", "next_open", "next_high5",
          "next_high10", "label_complete", "target_next_open_return",
          "target_next_high5_return", "target_next_high10_return", "t_final_limit_flag"]

FEATURES = ("t_reference_preclose", "signal_price", "signal_return",
            "minute_volume_shares", "volume_ratio", "turnover_so_far_pct",
            "float_mv_100m_cny", "volume_tminus5", "volume_tminus4",
            "volume_tminus3", "volume_tminus2", "volume_tminus1",
            "avg_volume5_shares", "volume_4of5_increasing", "ma5", "ma10", "ma20",
            "ma_bull_5_10_20",
            "price_above_all_ma", "all_intraday_lows_above_ma")
TARGETS = ("target_next_open_return", "target_next_high5_return",
           "target_next_high10_return")
TRAIN_EXPORT = ("signal_date", "next_date", "symbol6", "name", *FEATURES, *TARGETS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", default=END)
    parser.add_argument("--history-start", default=HISTORY_START)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output-dir", default="outputs/stock_automl/tail_standard")
    parser.add_argument("--summary", default="docs/stock_automl_runs/20261008_tail_standard/summary.json")
    args = parser.parse_args()
    if not args.history_start < args.start < args.end:
        parser.error("Expected history-start < start < end")
    config = dotenv_values(args.env_file)
    os.environ.setdefault("SIMPLE_BI_PLATFORM_DB_URL",
                          config.get("SIMPLE_BI_PLATFORM_DB_URL") or config.get("PLATFORM_DB_URL") or "")
    with read_only_db() as conn:
        daily, value = load_daily(conn, history_start=args.history_start, end=args.end)
        profile, calendar = prior_profile(daily, value)
        signal_days = [day for day in calendar if pd.Timestamp(args.start) <= day <= pd.Timestamp(args.end)]
        if len(signal_days) < 2:
            raise ValueError("Need at least two trading days in the selected range")
        day_after = dict(zip(signal_days[:-1], signal_days[1:]))
        daily_today = daily[["date", "symbol6", "preclose", "is_limit_price"]].rename(
            columns={"date": "signal_date", "preclose": "t_reference_preclose",
                     "is_limit_price": "t_final_limit_flag"})
        result = []
        audit = []
        for day, following in day_after.items():
            day_text = str(day)[:10]
            bars = signal_bar(conn, day_text)
            if bars.empty:
                audit.append({"date": day_text, "candidates": 0, "missing_signal_bar": True})
                continue
            signal = bars.merge(profile[profile.signal_date.eq(day)],
                                on=["signal_date", "symbol6"], how="left",
                                validate="one_to_one")
            signal = signal.merge(daily_today[daily_today.signal_date.eq(day)],
                                  on=["signal_date", "symbol6"], how="left",
                                  validate="one_to_one")
            signal["signal_price"] = pd.to_numeric(signal.signal_price, errors="coerce")
            signal["t_reference_preclose"] = pd.to_numeric(
                signal.t_reference_preclose, errors="coerce")
            signal["signal_return"] = signal.signal_price / signal.t_reference_preclose - 1
            signal = signal[signal.signal_return.between(.03, .06, inclusive="both")].copy()
            if signal.empty:
                audit.append({"date": day_text, "candidates": 0})
                continue
            signal["next_date"] = following
            minute = intraday_to_1440(conn, day_text, sorted(signal.symbol6.tolist()))
            entry = entry_bar(conn, day_text)
            morning = morning_bars(conn, str(following)[:10])
            rows = assemble(signal, minute, entry, morning)
            result.append(rows)
            audit.append({"date": day_text, "candidates": len(rows),
                          "feature_complete": int(rows.feature_complete.sum()),
                          "label_complete": int(rows.label_complete.sum()),
                          "trainable": int((rows.feature_complete & rows.label_complete &
                                            rows.limit_buffer.notna() &
                                            rows.signal_return.lt(rows.limit_buffer)).sum())})
            print(f"{day_text}: {audit[-1]}", flush=True)
    if not result:
        raise ValueError("No 14:40 candidate rows in the selected range")
    rows = pd.concat(result, ignore_index=True)[EXPORT].sort_values(["signal_date", "symbol6"])
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows.to_csv(output / "candidates_1440.csv", index=False, float_format="%.8f")
    training = rows[rows.feature_complete & rows.label_complete &
                    rows.limit_buffer.notna() & rows.signal_return.lt(rows.limit_buffer)].copy()
    training[list(TRAIN_EXPORT)].to_csv(output / "trainable_1440.csv", index=False,
                                        float_format="%.8f")
    feasible = training[training.entry_not_near_limit_proxy].copy()
    feasible[list(TRAIN_EXPORT)].to_csv(output / "execution_feasible_1440.csv",
                                        index=False, float_format="%.8f")
    summary = {"generated_at": datetime.now().isoformat(timespec="seconds"),
               "signal_period": [str(signal_days[0])[:10], str(signal_days[-2])[:10]],
               "requested_period": [args.start, args.end],
               "history_start": args.history_start,
               "minute_ingestion_times_used": False,
               "signal_time": "14:40", "selection_return": [0.03, 0.06],
               "candidates": len(rows), "feature_complete": int(rows.feature_complete.sum()),
               "label_complete": int(rows.label_complete.sum()),
               "trainable": len(training), "execution_feasible": len(feasible),
               "excluded_adj_price_break_20d": int((rows.adj_price_break_20d.eq(True) &
                    rows.history_complete.eq(True) & rows.minute_complete &
                    rows.value_complete.eq(True) & rows.label_complete &
                    rows.limit_buffer.notna() & rows.signal_return.lt(rows.limit_buffer)).sum()),
               "day_audit": audit,
               "provenance": {"script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                              "database_snapshot_version": None,
                              "raw_row_location": str(output)}}
    report = Path(args.summary)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {report} and {output}")


if __name__ == "__main__":
    main()

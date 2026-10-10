"""Audit signal-day buyability of 5–7 month picks using raw unadjusted 1m bars.

This is a price/volume audit, not an order-book fill simulation. It reads only
the supplied ``mk/none`` files and read-only historical ST/daily data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_st_status import attach_st_status, load_st_intervals
from scripts.benchmark_automl_1440_inference import db_connection, query


DEFAULT_SOURCE = Path("outputs/stock_automl/mk_archive/mk/none")
DEFAULT_REPORT = Path("docs/stock_automl_runs/20261010_mk_1m_buyability")
PICKS = Path("docs/stock_automl_runs/20261010_mk_1m_rolling_backtest/top2_verified.csv")


def selected_minutes(source: Path, selected: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for day, group in selected.groupby("signal_date"):
        path = source / f"{day}.csv"
        if not path.exists():
            raise FileNotFoundError(path)
        codes = set(group.symbol6)
        day_parts = []
        for chunk in pd.read_csv(path, usecols=["time", "symbol", "open", "high",
                                                "low", "close", "volume"],
                                 dtype={"symbol": str}, chunksize=200_000):
            mask = chunk.time.str.slice(11).ge("14:40:00") & chunk.symbol.str[:6].isin(codes)
            if mask.any():
                day_parts.append(chunk.loc[mask])
        if not day_parts:
            raise ValueError(f"No selected 14:40 bars on {day}")
        found = pd.concat(day_parts, ignore_index=True)
        found["signal_date"] = day
        found["symbol6"] = found.symbol.str[:6]
        parts.append(found)
    return pd.concat(parts, ignore_index=True)


def minute_evidence(group: pd.DataFrame, signal_price: float) -> dict:
    signal = group[group.time.str.endswith("14:40:00")]
    later = group[group.time.str.slice(11).gt("14:40:00")]
    if len(signal) != 1 or len(later) != 20:
        raise ValueError("Expected one 14:40 bar and 20 later one-minute bars")
    bar = signal.iloc[0]
    if not np.isclose(bar.close, signal_price, rtol=0, atol=1e-8):
        raise ValueError("Saved selection price differs from raw 14:40 close")
    flat = len({bar.open, bar.high, bar.low, bar.close}) == 1
    later_traded_lower = later[(later.volume > 0) &
                               (later.low < signal_price - .005)]
    return {"signal_open": bar.open, "signal_high": bar.high,
            "signal_low": bar.low, "signal_close": bar.close,
            "signal_volume_shares": bar.volume,
            "signal_flat": flat, "signal_zero_volume": bool(bar.volume == 0),
            "later_volume_shares": float(later.volume.sum()),
            "later_traded_below_signal": not later_traded_lower.empty,
            "first_later_trade_below_signal": (
                later_traded_lower.time.iloc[0] if not later_traded_lower.empty else None),
            "later_min_low": float(later.low.min())}


def load_daily_and_status(selected: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    codes = sorted({s + (".SH" if s.startswith("6") else
                         ".BJ" if s.startswith(("4", "8", "9")) else ".SZ")
                    for s in selected.symbol6})
    placeholders = ",".join(["%s"] * len(codes))
    conn = db_connection()
    try:
        daily = query(conn, "SELECT trade_date, LEFT(stk_code,6) AS symbol6, "
                      "preclose, high, close, is_limit_price "
                      "FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s "
                      f"AND stk_code IN ({placeholders})",
                      (selected.signal_date.min(), selected.signal_date.max(), *codes))
        intervals = load_st_intervals(conn)
    finally:
        conn.rollback()
        conn.close()
    return daily, intervals


def classify(rows: pd.DataFrame) -> pd.DataFrame:
    rows = rows.copy()
    rows["near_five_pct"] = rows.signal_return.between(.048, .052)
    rows["historical_st"] = rows.st_type.isin(("S", "Y"))
    rows["day_closed_up_limit"] = (rows.is_limit_price.eq(1) &
                                   rows.day_close.gt(rows.day_preclose))
    rows["signal_equals_limit_close"] = (rows.day_closed_up_limit &
                                          np.isclose(rows.signal_price,
                                                     rows.day_close, rtol=0, atol=.005))
    rows["historical_st_at_limit"] = (rows.historical_st &
                                      rows.signal_equals_limit_close)
    rows["st_near5_at_limit"] = (rows.historical_st & rows.near_five_pct &
                                 rows.signal_equals_limit_close)
    rows["no_trade_after_signal"] = rows.later_volume_shares.eq(0)
    rows["flat_zero_at_signal"] = rows.signal_flat & rows.signal_zero_volume
    rows["no_trade_from_signal_to_close"] = (rows.flat_zero_at_signal &
                                               rows.no_trade_after_signal)
    return rows


def run(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_REPORT) -> dict:
    selected = pd.read_csv(PICKS, dtype={"symbol6": str})
    selected["signal_date"] = selected.signal_date.astype(str)
    selected = selected.drop_duplicates(["arm", "signal_date", "symbol6", "selection_rank"])
    minutes = selected_minutes(source, selected)
    evidence = []
    for (day, symbol), group in minutes.groupby(["signal_date", "symbol6"]):
        prices = selected.loc[selected.signal_date.eq(day) & selected.symbol6.eq(symbol),
                              "signal_price"].unique()
        if len(prices) != 1:
            raise ValueError("Selected arms disagree about signal price")
        evidence.append({"signal_date": day, "symbol6": symbol,
                         **minute_evidence(group, prices[0])})
    rows = selected.merge(pd.DataFrame(evidence), on=["signal_date", "symbol6"],
                          validate="many_to_one")
    daily, intervals = load_daily_and_status(selected)
    daily["trade_date"] = pd.to_datetime(daily.trade_date).dt.strftime("%Y-%m-%d")
    daily = daily.rename(columns={"preclose": "day_preclose", "high": "day_high",
                                  "close": "day_close"})
    for column in ("day_preclose", "day_high", "day_close"):
        daily[column] = pd.to_numeric(daily[column], errors="raise")
    rows = rows.merge(daily, left_on=["signal_date", "symbol6"],
                      right_on=["trade_date", "symbol6"], how="left",
                      validate="many_to_one")
    if rows.trade_date.isna().any():
        raise ValueError("Selected signal day missing from daily price table")
    rows = attach_st_status(rows, intervals)
    if rows.st_type.isna().any() or (rows.ann_date > pd.to_datetime(rows.signal_date)).any():
        raise ValueError("Historical ST status missing or announced after signal")
    rows = classify(rows)
    output.mkdir(parents=True, exist_ok=True)
    rows.to_csv(output / "selected_buyability.csv", index=False)
    summary = {}
    flags = ("near_five_pct", "historical_st", "historical_st_at_limit",
             "st_near5_at_limit",
             "flat_zero_at_signal", "no_trade_after_signal",
             "no_trade_from_signal_to_close", "later_traded_below_signal")
    for (arm, rank), group in rows.groupby(["arm", "selection_rank"]):
        summary[f"{arm}_rank{rank}"] = {"selected": len(group), **{
            key: int(group[key].sum()) for key in flags},
            "st_limit_flat_zero": int((group.historical_st_at_limit &
                                       group.flat_zero_at_signal).sum()),
            "st_limit_no_later_trade": int((group.historical_st_at_limit &
                                            group.no_trade_after_signal).sum()),
            "st_limit_later_below": int((group.historical_st_at_limit &
                                         group.later_traded_below_signal).sum()),
        }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    p.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    a = p.parse_args()
    print(json.dumps(run(a.source, a.output), ensure_ascii=False, indent=2))

"""Audit whether next-morning one-minute highs persist in open and close prices."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query


PICKS = Path("docs/stock_automl_runs/20261009_high20_weighted_result/stocks.csv")
OUT = Path("docs/stock_automl_runs/20261009_high20_minute_spikes")


def fetch_bars(picks, env_file):
    conn = db_connection(env_file)
    pieces = []
    try:
        for day, group in picks.groupby("次交易日", sort=True):
            codes = sorted(group.股票代码.tolist())
            placeholders = ",".join(["%s"] * len(codes))
            sql = ("SELECT stk_code, bar_end_time, open_price, high_price, low_price, "
                   "latest_price, is_finalized, is_fallback, source_snapshot_time "
                   "FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s "
                   "AND kline_type='1m' AND period_minutes=1 "
                   "AND bar_end_time BETWEEN %s AND %s "
                   f"AND stk_code IN ({placeholders}) ORDER BY stk_code,bar_end_time")
            bars = query(conn, sql, (day, f"{day} 09:31:00", f"{day} 09:40:00", *codes))
            if len(bars) != len(codes)*10:
                raise ValueError(f"Missing one-minute bars for {day}: {len(bars)}")
            bars["next_date"] = day
            pieces.append(bars)
    finally:
        conn.rollback()
        conn.close()
    bars = pd.concat(pieces, ignore_index=True)
    bars["symbol6"] = bars.stk_code.str[:6]
    bars["minute"] = pd.to_datetime(bars.bar_end_time).dt.strftime("%H:%M")
    if bars.duplicated(["next_date", "symbol6", "minute"]).any():
        raise ValueError("Duplicate minute bars")
    if not (bars.is_finalized.eq(1) & bars.is_fallback.eq(0) &
            pd.to_datetime(bars.source_snapshot_time).eq(
                pd.to_datetime(bars.bar_end_time))).all():
        raise ValueError("Non-exact one-minute bar")
    for col in ("open_price", "high_price", "low_price", "latest_price"):
        bars[col] = pd.to_numeric(bars[col], errors="raise")
    if not (bars.high_price.ge(bars[["open_price", "latest_price"]].max(axis=1)).all()
            and bars.low_price.le(bars[["open_price", "latest_price"]].min(axis=1)).all()):
        raise ValueError("Invalid OHLC values")
    return bars


def main(env_file):
    picks = pd.read_csv(PICKS, dtype={"股票代码": str})
    if len(picks) != 40 or picks.信号日.nunique() != 20:
        raise ValueError("Expected 40 picks on 20 signal days")
    bars = fetch_bars(picks, env_file)
    picks = picks.rename(columns={"次交易日": "next_date", "股票代码": "symbol6",
                                  "14:40假设买价": "entry_1440"})
    bars = bars.merge(picks[["next_date", "symbol6", "entry_1440"]],
                      on=["next_date", "symbol6"], validate="many_to_one")
    for col in ("open_price", "high_price", "latest_price"):
        bars[f"{col}_ret"] = bars[col]/bars.entry_1440-1
    detail = []
    for (_, _), group in bars.groupby(["next_date", "symbol6"], sort=True):
        group = group.sort_values("minute").reset_index(drop=True)
        price = group.entry_1440.iloc[0]
        highs = group.high_price.to_numpy()
        peak_ix = int(np.argmax(highs))
        peak = group.iloc[peak_ix]
        second = np.sort(highs)[-2]
        hit = group.high_price_ret.gt(.01)
        endpoint_hit = (group.open_price_ret.gt(.01) |
                        group.latest_price_ret.gt(.01))
        first_hit_ix = int(np.flatnonzero(hit)[0]) if hit.any() else None
        next_bar = (group.iloc[first_hit_ix+1] if first_hit_ix is not None and
                    first_hit_ix < 9 else None)
        after_peak = group.iloc[peak_ix+1:]
        next_after_peak = group.iloc[peak_ix+1] if peak_ix < 9 else None
        detail.append({"next_date": group.next_date.iloc[0],
                       "symbol6": group.symbol6.iloc[0],
                       "entry_1440": price,
                       "max_high_ret": float(group.high_price_ret.max()),
                       "max_open_ret": float(group.open_price_ret.max()),
                       "max_close_ret": float(group.latest_price_ret.max()),
                       "second_high_ret": float(second/price-1),
                       "max_high_minute": peak.minute,
                       "peak_bar_open_ret": float(peak.open_price/price-1),
                       "peak_bar_close_ret": float(peak.latest_price/price-1),
                       "peak_to_same_bar_close_pct": float(peak.latest_price/peak.high_price-1),
                       "first_high_hit_minute": group.iloc[first_hit_ix].minute if hit.any() else "",
                       "n_high_hit_bars": int(hit.sum()),
                       "n_endpoint_hit_bars": int(endpoint_hit.sum()),
                       "first_hit_bar_close_ret": float(group.iloc[first_hit_ix].latest_price/price-1)
                       if hit.any() else None,
                       "next_bar_open_ret": float(next_bar.open_price/price-1)
                       if next_bar is not None else None,
                       "next_bar_close_ret": float(next_bar.latest_price/price-1)
                       if next_bar is not None else None,
                       "post_peak_max_high_ret": float(after_peak.high_price.max()/price-1)
                       if len(after_peak) else None,
                       "next_after_peak_high_ret": float(next_after_peak.high_price/price-1)
                       if next_after_peak is not None else None,
                       "next_after_peak_close_ret": float(next_after_peak.latest_price/price-1)
                       if next_after_peak is not None else None})
    result = picks.merge(pd.DataFrame(detail), on=["next_date", "symbol6", "entry_1440"],
                         validate="one_to_one")
    if not (np.allclose(result.max_high_ret, result["最高价验证涨幅"]) and
            np.allclose(result.max_open_ret, result["开盘价验证涨幅"]) and
            np.allclose(result.max_close_ret, result["收盘价验证涨幅"])):
        raise ValueError("Minute bars differ from archived target prices")
    high_hit = result[result.max_high_ret.gt(.01)]
    metrics = {"picks": len(result), "high_hits": len(high_hit),
               "open_hits": int(result.max_open_ret.gt(.01).sum()),
               "close_hits": int(result.max_close_ret.gt(.01).sum()),
               "either_endpoint_hits": int((result.max_open_ret.gt(.01) |
                                            result.max_close_ret.gt(.01)).sum()),
               "high_only_hits": int((high_hit.max_open_ret.le(.01) &
                                      high_hit.max_close_ret.le(.01)).sum()),
               "high_hits_with_second_high_over_1pct": int(high_hit.second_high_ret.gt(.01).sum()),
               "high_hits_one_bar_only": int(high_hit.n_high_hit_bars.eq(1).sum()),
               "high_hits_peak_bar_close_over_1pct": int(high_hit.peak_bar_close_ret.gt(.01).sum()),
               "high_hits_next_bar_close_over_1pct": int(high_hit.next_bar_close_ret.gt(.01).sum()),
               "high_hits_first_bar_0931": int(high_hit.first_high_hit_minute.eq("09:31").sum()),
               "high_hits_max_at_0931": int(high_hit.max_high_minute.eq("09:31").sum()),
               "high_hits_with_later_bar": int(high_hit.post_peak_max_high_ret.notna().sum()),
               "high_hits_later_high_over_1pct": int(high_hit.post_peak_max_high_ret.gt(.01).sum()),
               "high_hits_next_after_peak_high_over_1pct": int(
                   high_hit.next_after_peak_high_ret.gt(.01).sum()),
               "high_hits_next_after_peak_close_over_1pct": int(
                   high_hit.next_after_peak_close_ret.gt(.01).sum()),
               "median_peak_to_bar_close_pct": float(high_hit.peak_to_same_bar_close_pct.median()),
               "median_max_vs_second_high_pct_points": float(
                   (high_hit.max_high_ret-high_hit.second_high_ret).median()*100)}
    OUT.mkdir(parents=True, exist_ok=True)
    bars.to_csv(OUT/"exact_ten_bars.csv", index=False)
    result.to_csv(OUT/"stock_spike_audit.csv", index=False)
    (OUT/"summary.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("/Volumes/ext/fin_agent/.env"))
    main(parser.parse_args().env_file)

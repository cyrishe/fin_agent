"""Build 14:45 candidates from exact database bars; keep next 09:40 labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.build_automl_mk_1m_candidates import load_daily_profile


OUT = Path("outputs/stock_automl/db_1m_1445_exact0940")
START, END, LAST_SIGNAL = "2026-08-05", "2026-10-12", "2026-10-09"
MORNING = set(pd.date_range("09:31", periods=10, freq="min").strftime("%H:%M:%S"))


def raw_day(db, day: str, next_date: str, daily: pd.DataFrame,
            bar_type: str = "15m") -> tuple[pd.DataFrame, dict]:
    if bar_type not in ("1m", "15m"):
        raise ValueError("Unsupported signal bar type")
    period, required, first_clock = ((1, 225, "09:31:00") if bar_type == "1m"
                                     else (15, 15, "09:45:00"))
    current = daily[daily.date.eq(pd.Timestamp(day))][["symbol6", "preclose"]].copy()
    current["preclose"] = pd.to_numeric(current.preclose, errors="coerce")
    prices = query(db, "SELECT stk_code AS symbol6,latest_price AS signal_price,"
                   "is_finalized,is_fallback,source_snapshot_time,bar_end_time "
                   "FROM aiia_stock_realtime_minute_snapshot_full "
                   "WHERE trade_date=%s AND kline_type=%s AND period_minutes=%s "
                   "AND bar_end_time=%s", (day, bar_type, period, f"{day} 14:45:00"))
    if prices.empty:
        return pd.DataFrame(), {"day": day, "snapshot_stocks": 0, "raw_band": 0,
                                "complete_bars": 0}
    valid = (prices.is_finalized.eq(1) & prices.is_fallback.eq(0) &
             prices.source_snapshot_time.eq(prices.bar_end_time))
    prices = prices[valid].copy()
    prices["symbol6"] = prices.symbol6.astype(str).str.zfill(6)
    prices["signal_price"] = pd.to_numeric(prices.signal_price, errors="coerce")
    prices = prices.merge(current, on="symbol6", validate="one_to_one")
    prices["signal_return"] = prices.signal_price / prices.preclose - 1
    selected = prices[prices.preclose.gt(0) &
                      prices.signal_return.between(.03, .06, inclusive="both")].copy()
    codes = selected.symbol6.tolist()
    if not codes:
        return pd.DataFrame(), {"day": day, "snapshot_stocks": len(prices),
                                "raw_band": 0, "complete_bars": 0}
    chunks = []
    for i in range(0, len(codes), 1000):
        part = codes[i:i+1000]
        slots = ",".join(["%s"]*len(part))
        frame = query(db, "SELECT stk_code AS symbol6,COUNT(*) AS bars,"
                      "COUNT(DISTINCT bar_end_time) AS distinct_bars,"
                      "MIN(TIME(bar_end_time)) AS first_clock,"
                      "MAX(TIME(bar_end_time)) AS last_clock,"
                      "SUM(volume)*100 AS minute_volume_shares,"
                      "MIN(low_price) AS min_low_so_far,"
                      "SUM(is_finalized=1 AND is_fallback=0 AND "
                      "source_snapshot_time=bar_end_time) AS exact_bars "
                      "FROM aiia_stock_realtime_minute_snapshot_full "
                      "WHERE trade_date=%s AND kline_type=%s AND period_minutes=%s "
                      "AND bar_end_time BETWEEN %s AND %s "
                      f"AND stk_code IN ({slots}) GROUP BY stk_code",
                      (day, bar_type, period, f"{day} 09:31:00", f"{day} 14:45:00", *part))
        chunks.append(frame)
    bars = pd.concat(chunks, ignore_index=True)
    for col in ("bars", "distinct_bars", "minute_volume_shares", "min_low_so_far",
                "exact_bars"):
        bars[col] = pd.to_numeric(bars[col], errors="coerce")
    bars = bars[bars.bars.eq(required) & bars.distinct_bars.eq(required) &
                bars.first_clock.astype(str).str[-8:].eq(first_clock) &
                bars.last_clock.astype(str).str[-8:].eq("14:45:00") &
                bars.exact_bars.eq(required) & bars.minute_volume_shares.gt(0) &
                bars.min_low_so_far.gt(0)].copy()
    bars["symbol6"] = bars.symbol6.astype(str).str.zfill(6)
    selected = selected.merge(bars[["symbol6", "minute_volume_shares", "min_low_so_far"]],
                              on="symbol6", validate="one_to_one")
    codes = selected.symbol6.tolist()
    morning_frames = []
    for i in range(0, len(codes), 1000):
        part = codes[i:i+1000]
        slots = ",".join(["%s"]*len(part))
        morning_frames.append(query(db, "SELECT stk_code AS symbol6,bar_end_time,"
                                    "high_price,latest_price,volume,is_finalized,is_fallback,"
                                    "source_snapshot_time "
                                    "FROM aiia_stock_realtime_minute_snapshot_full "
                                    "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                                    "AND bar_end_time BETWEEN %s AND %s "
                                    f"AND stk_code IN ({slots})",
                                    (next_date, f"{next_date} 09:31:00",
                                     f"{next_date} 09:40:00", *part)))
    label_rows = []
    if morning_frames:
        morning = pd.concat(morning_frames, ignore_index=True)
        for code, group in morning.groupby("symbol6"):
            group = group.copy()
            group["clock"] = group.bar_end_time.astype(str).str[-8:]
            if (len(group) != 10 or set(group.clock) != MORNING or
                    not (group.is_finalized.eq(1) & group.is_fallback.eq(0) &
                         group.source_snapshot_time.eq(group.bar_end_time)).all()):
                continue
            high = pd.to_numeric(group.high_price, errors="coerce")
            close = pd.to_numeric(group.latest_price, errors="coerce")
            volume = pd.to_numeric(group.volume, errors="coerce")
            if (high.le(0).any() or close.le(0).any() or volume.lt(0).any() or
                    volume[group.clock.le("09:35:00")].sum() <= 0):
                continue
            label_rows.append({"symbol6": str(code).zfill(6),
                               "second_high": float(high.nlargest(2).iloc[-1]),
                               "close_40": float(close[group.clock.eq("09:40:00")].iloc[0])})
    labels = pd.DataFrame(label_rows, columns=["symbol6", "second_high", "close_40"])
    selected = selected.merge(labels, on="symbol6", how="left", validate="one_to_one")
    selected["signal_date"] = day
    selected["next_date"] = next_date
    selected["signal_bar_type"] = bar_type
    return selected, {"day": day, "snapshot_stocks": len(prices),
                      "signal_bar_type": bar_type,
                      "raw_band": len(codes), "complete_bars": len(selected),
                      "labeled": int(selected.second_high.notna().sum())}


def build(output: Path = OUT, bar_type: str = "auto") -> dict:
    if bar_type not in ("auto", "1m", "15m"):
        raise ValueError("Unsupported signal bar type")
    daily, profile, calendar, proxy_audit = load_daily_profile(Path(".env"), START, END)
    # The frozen Aug-Oct 14:40 baseline uses reported T-1 valuation fields.
    with db_connection(Path(".env")) as db:
        reported = query(db, "SELECT trade_date AS value_date,"
                         "LEFT(stk_code,6) AS symbol6,float_mv,float_share "
                         "FROM kcrp_stock_pricevaluate WHERE trade_date BETWEEN %s AND %s",
                         (str(profile.value_date.min())[:10],
                          str(profile.value_date.max())[:10]))
    reported["value_date"] = pd.to_datetime(reported.value_date)
    reported["symbol6"] = reported.symbol6.astype(str).str.zfill(6)
    reported = reported.drop_duplicates(["value_date", "symbol6"], keep=False)
    for field in ("float_mv", "float_share"):
        reported[field] = pd.to_numeric(reported[field], errors="coerce")
    profile = profile.drop(columns=["float_mv", "float_share", "value_complete"])
    profile = profile.merge(reported, on=["value_date", "symbol6"], how="left",
                            validate="many_to_one")
    profile["value_complete"] = profile.float_mv.gt(0) & profile.float_share.gt(0)
    days = [str(day)[:10] for day in calendar
            if START <= str(day)[:10] <= LAST_SIGNAL]
    following = {str(a)[:10]: str(b)[:10] for a, b in zip(calendar[:-1], calendar[1:])}
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "days"
    cache.mkdir(exist_ok=True)
    audits = []
    with db_connection(Path(".env")) as db:
        for day in days:
            if day not in following:
                raise ValueError(f"No next trading day for {day}")
            path = cache / f"{day}.csv"
            if path.exists():
                frame = pd.read_csv(path, dtype={"symbol6": str})
                if "signal_bar_type" not in frame:
                    frame["signal_bar_type"] = "1m"
                    frame.to_csv(path, index=False)
                audit = {"day": day, "complete_bars": len(frame),
                         "signal_bar_type": frame.signal_bar_type.iloc[0],
                         "labeled": int(frame.second_high.notna().sum())}
            else:
                source = bar_type
                if source == "auto":
                    available = query(db, "SELECT 1 AS found FROM "
                                      "aiia_stock_realtime_minute_snapshot_full "
                                      "WHERE trade_date=%s AND kline_type='15m' "
                                      "AND period_minutes=15 AND bar_end_time=%s LIMIT 1",
                                      (day, f"{day} 14:45:00"))
                    source = "15m" if not available.empty else "1m"
                frame, audit = raw_day(db, day, following[day], daily, source)
                if frame.empty:
                    raise ValueError(f"No exact 14:45 candidates on {day}")
                frame.to_csv(path, index=False)
            audits.append(audit)
            print("db-1445", day, len(frame), flush=True)
    signals = pd.concat([pd.read_csv(cache / f"{day}.csv",
                                     dtype={"symbol6": str, "signal_date": str,
                                            "next_date": str}) for day in days],
                        ignore_index=True)
    signals["signal_date"] = pd.to_datetime(signals.signal_date)
    signals = signals.merge(profile, on=["signal_date", "symbol6"], how="left",
                            validate="one_to_one")
    signals = signals.merge(daily[["date", "symbol6", "name"]].rename(
        columns={"date": "signal_date"}), on=["signal_date", "symbol6"],
        how="left", validate="one_to_one")
    signals["volume_ratio"] = (signals.minute_volume_shares * 240 /
                               (225 * signals.avg_volume5_shares))
    signals["turnover_so_far_pct"] = signals.minute_volume_shares / signals.float_share * 100
    signals["float_mv_100m_cny"] = signals.float_mv / 1e8
    for window in (5, 10, 20):
        signals[f"ma{window}"] = (signals[f"ma{window}_adj"] *
                                  signals.preclose / signals.prior_adjclose)
    signals["ma_bull_5_10_20"] = (signals.ma5.gt(signals.ma10) &
                                   signals.ma10.gt(signals.ma20)).astype(int)
    signals["all_intraday_lows_above_ma"] = signals.min_low_so_far.gt(
        signals[["ma5", "ma10", "ma20"]].max(axis=1)).astype(int)
    keep = (signals.history_complete.eq(True) & signals.adj_price_break_20d.eq(False) &
            signals.value_complete.eq(True) & signals[list(FEATURES)].notna().all(axis=1))
    result = signals.loc[keep].copy()
    result["t_reference_preclose"] = result.preclose
    result["label_complete"] = result.second_high.notna() & result.close_40.notna()
    result["signal_date"] = result.signal_date.dt.strftime("%Y-%m-%d")
    result.sort_values(["signal_date", "symbol6"]).to_csv(
        output / "trainable_candidates.csv.gz", index=False, compression="gzip")
    summary = {"decision_time": "14:45", "signal_bar_type": bar_type,
               "signal_bars_by_type": {"1m": 225, "15m": 15},
               "days_by_source": pd.Series([a["signal_bar_type"] for a in audits]).value_counts().to_dict(),
               "first_day": days[0], "last_day": days[-1],
               "signal_days": len(days), "raw_complete_candidates": len(signals),
               "trainable_candidates": len(result),
               "labeled": int(result.label_complete.sum()),
               "valuation_source": "T-1 kcrp_stock_pricevaluate",
               "proxy_float_mv_audit": proxy_audit, "daily_audits": audits}
    (output / "build_summary.json").write_text(json.dumps(
        summary, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "daily_audits"},
                     ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--bar-type", choices=("auto", "1m", "15m"), default="auto")
    args = parser.parse_args()
    build(args.output, args.bar_type)

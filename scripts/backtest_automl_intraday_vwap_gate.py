"""Apply the existing prior-20 individual turnover gate to both VWAP replay arms."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_automl_intraday_vwap import (
    EXPECTED_TIMES, LATE_MARKET, MK, MK_CANDIDATES, OUT,
)
from scripts.mk_minute_archive import day_file


def required_codes(picks: pd.DataFrame, days: list[str]) -> dict[str, set[str]]:
    index = {day: i for i, day in enumerate(days)}
    needed = {day: set() for day in days}
    for row in picks.itertuples(index=False):
        if row.signal_date not in index or index[row.signal_date] < 20:
            raise ValueError(f"No prior-20 calendar for {row.signal_date}")
        for day in days[index[row.signal_date]-20:index[row.signal_date]+1]:
            needed[day].add(row.symbol6)
    return needed


def archive_amounts(day: str, codes: set[str], cutoff: str = "14:40") -> pd.DataFrame:
    required = 220 if cutoff == "14:40" else 225
    expected_times = set(pd.date_range("09:31", periods=120, freq="min").strftime("%H:%M:%S"))
    expected_times.update(pd.date_range("13:01", periods=required-120,
                                        freq="min").strftime("%H:%M:%S"))
    pieces = []
    for chunk in pd.read_csv(day_file(MK, day), usecols=["time", "symbol", "turnover"],
                             dtype={"symbol": str}, chunksize=300_000):
        mask = chunk.symbol.str[:6].isin(codes) & chunk.time.str[-8:].isin(expected_times)
        if mask.any():
            part = chunk.loc[mask].copy()
            part["symbol6"] = part.symbol.str[:6]
            pieces.append(part)
    if not pieces:
        return pd.DataFrame(columns=["date", "symbol6", "amount_1440"])
    bars = pd.concat(pieces, ignore_index=True)
    group = bars.groupby("symbol6").agg(bars=("time", "size"),
                                         distinct=("time", "nunique"),
                                         amount_1440=("turnover", "sum")).reset_index()
    group = group[group.bars.eq(required) & group.distinct.eq(required) &
                  group.amount_1440.ge(0)].copy()
    group["date"] = day
    return group[["date", "symbol6", "amount_1440"]]


def db_amounts(db, day: str, codes: set[str], cutoff: str = "14:40",
               bar_type: str = "1m") -> pd.DataFrame:
    if bar_type not in ("1m", "15m"):
        raise ValueError("Unsupported amount bar type")
    required = (220 if cutoff == "14:40" else 225) if bar_type == "1m" else 15
    period = 1 if bar_type == "1m" else 15
    symbols = sorted(codes)
    chunks = []
    batch_size = 10 if bar_type == "1m" and cutoff == "14:45" else 100
    for i in range(0, len(symbols), batch_size):
        group = symbols[i:i+batch_size]
        placeholders = ",".join(["%s"] * len(group))
        rows = query(db, "SELECT stk_code AS symbol6,COUNT(*) AS bars,"
                     "COUNT(DISTINCT bar_end_time) AS distinct_bars,SUM(amount) AS amount_1440,"
                     "SUM(is_finalized=1 AND is_fallback=0 AND "
                     "source_snapshot_time=bar_end_time) AS exact_bars "
                     "FROM aiia_stock_realtime_minute_snapshot_full "
                     "WHERE trade_date=%s AND kline_type=%s AND period_minutes=%s "
                     "AND bar_end_time BETWEEN %s AND %s "
                     f"AND stk_code IN ({placeholders}) GROUP BY stk_code",
                     (day, bar_type, period, f"{day} 09:31:00",
                      f"{day} {cutoff}:00", *group))
        chunks.append(rows)
    frame = pd.concat(chunks, ignore_index=True)
    if frame.empty:
        return pd.DataFrame(columns=["date", "symbol6", "amount_1440"])
    for col in ("bars", "distinct_bars", "exact_bars", "amount_1440"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    frame = frame[frame.bars.eq(required) & frame.distinct_bars.eq(required) &
                  frame.exact_bars.eq(required) & frame.amount_1440.ge(0)].copy()
    frame["date"] = day
    frame["symbol6"] = frame.symbol6.astype(str).str.zfill(6)
    return frame[["date", "symbol6", "amount_1440"]]


def gate(current: float | None, history: list[float | None]) -> tuple[bool, float | None]:
    if len(history) != 20 or current is None or any(x is None for x in history):
        return False, None
    median = float(np.median(history))
    return bool(current >= median), median


def run(output: Path = OUT) -> dict:
    picks = pd.read_csv(output / "paired_top1.csv", dtype={"symbol6": str,
                                                            "signal_date": str})
    early_days = sorted(pd.read_csv(MK_CANDIDATES, usecols=["signal_date"]).signal_date.unique())
    late_days = pd.read_csv(LATE_MARKET).trade_date.astype(str).tolist()
    calendars = {"feb_jul": early_days, "sep_oct": late_days}
    cache = output / "amount_days"
    cache.mkdir(exist_ok=True)
    for segment, days in calendars.items():
        needed = required_codes(picks[picks.segment.eq(segment)], days)
        if segment == "feb_jul":
            for day, codes in needed.items():
                if not codes:
                    continue
                path = cache / f"{day}.csv"
                if not path.exists():
                    archive_amounts(day, codes).to_csv(path, index=False)
                print("amount", day, len(codes), flush=True)
        else:
            with db_connection(Path(".env")) as db:
                for day, codes in needed.items():
                    if not codes:
                        continue
                    path = cache / f"{day}.csv"
                    if not path.exists():
                        db_amounts(db, day, codes).to_csv(path, index=False)
                    print("amount", day, len(codes), flush=True)
    frames = [pd.read_csv(path, dtype={"symbol6": str, "date": str})
              for path in sorted(cache.glob("2026-*.csv"))]
    amounts = pd.concat(frames, ignore_index=True)
    if amounts.duplicated(["date", "symbol6"]).any():
        raise ValueError("Duplicate amount cache key")
    lookup = {(r.date, r.symbol6): float(r.amount_1440) for r in amounts.itertuples(index=False)}
    results = []
    for row in picks.itertuples(index=False):
        days = calendars[row.segment]
        index = days.index(row.signal_date)
        history = [lookup.get((day, row.symbol6)) for day in days[index-20:index]]
        current = lookup.get((row.signal_date, row.symbol6))
        actionable, median = gate(current, history)
        results.append({"segment": row.segment, "signal_date": row.signal_date,
                        "arm": row.arm, "symbol6": row.symbol6,
                        "amount_1440": current, "prior20_valid": sum(x is not None for x in history),
                        "prior20_median": median, "actionable": actionable,
                        "actual_0940_return": row.actual_0940_return,
                        "realized_return_if_gate": row.actual_0940_return if actionable else 0.0})
    detailed = pd.DataFrame(results)
    detailed.to_csv(output / "paired_top1_amount_gate.csv", index=False)
    summaries = []
    for segment, days in calendars.items():
        test_days = [day for day in days if
                     ("2026-02-02" <= day <= "2026-07-30" if segment == "feb_jul"
                      else "2026-09-02" <= day <= "2026-10-09")]
        for arm in ("baseline7", "vwap8"):
            group = detailed[detailed.segment.eq(segment) & detailed.arm.eq(arm)]
            trades = group[group.actionable]
            daily = pd.Series(0., index=test_days)
            daily.loc[group.signal_date] = group.realized_return_if_gate.to_numpy()
            summaries.append({"segment": segment, "arm": arm,
                              "test_days": len(test_days), "selected_top1": len(group),
                              "trades": len(trades), "idle_days": len(test_days)-len(trades),
                              "ge3_trades": int(trades.actual_0940_return.ge(.03).sum()),
                              "negative_trades": int(trades.actual_0940_return.lt(0).sum()),
                              "mean_return_per_trade_pct": float(trades.actual_0940_return.mean()*100)
                              if len(trades) else None,
                              "mean_return_per_calendar_day_pct": float(daily.mean()*100),
                              "compounded_price_proxy_pct": float(((1+daily).prod()-1)*100)})
    pd.DataFrame(summaries).to_csv(output / "paired_amount_gate_summary.csv", index=False)
    summary = {"method": "Current stock amount through 14:40 >= median of its exact prior 20 trading days; no Top2 replacement",
               "segments": summaries}
    (output / "amount_gate_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    run()

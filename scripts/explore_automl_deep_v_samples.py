"""Read-only 2026 Aug-Oct minute-close exploration for a conditional deep-V label.

This is an event and coverage audit, not a model or a trading backtest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from scripts.benchmark_automl_1440_inference import db_connection, query


OUT = Path("outputs/stock_automl/deep_v_20261013")
START, END = "2026-08-01", "2026-10-12"
EXPECTED = set(pd.date_range("09:31", periods=120, freq="min").strftime("%H:%M:%S"))
EXPECTED.update(pd.date_range("13:01", periods=120, freq="min").strftime("%H:%M:%S"))


def classify(closes: np.ndarray) -> dict | None:
    """Label by whole-day minute-close range and finish versus whole-day high."""
    high = float(np.max(closes))
    low = float(np.min(closes))
    global_range = 1 - low / high
    if global_range <= 0.05:
        return None
    peak = int(np.argmax(closes))
    trough = int(np.argmin(closes))
    running_peak = np.maximum.accumulate(closes)
    drawdown = 1 - closes / running_peak
    finish = float(closes[-1] / high - 1)
    label = 1 if finish >= -0.01 else 0 if finish >= -0.03 else -1
    triggers = np.flatnonzero(drawdown > 0.05)
    first_trigger = int(triggers[0]) if len(triggers) else None
    last10 = closes[-10:] / high - 1
    return {
        "class": label,
        "max_drawdown": float(np.max(drawdown)),
        "peak_slot": peak + 1,
        "trough_slot": trough + 1,
        "first_trigger_slot": first_trigger + 1 if first_trigger is not None else None,
        "first_trigger_price": float(closes[first_trigger]) if first_trigger is not None else None,
        "close_vs_peak": finish,
        "close_vs_first_trigger": (float(closes[-1] / closes[first_trigger] - 1)
                                   if first_trigger is not None else None),
        "last10_above_minus1": int(np.sum(last10 >= -0.01)),
        "last10_min_vs_peak": float(np.min(last10)),
        "global_range": global_range,
        "global_high_before_low": bool(peak < trough),
        "chronological_drop5": bool(len(triggers)),
    }


def run_day(db, day: str, output: Path) -> None:
    compact = query(db, """SELECT stk_code,COUNT(*) AS bars,
        COUNT(DISTINCT bar_end_time) AS distinct_bars,
        MIN(bar_end_time) AS first_bar,MAX(bar_end_time) AS last_bar,
        SUM(is_finalized=1 AND is_fallback=0 AND
            source_snapshot_time=bar_end_time) AS exact_bars,
        MIN(latest_price) AS min_close,MAX(latest_price) AS max_close
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
        GROUP BY stk_code""", (day,))
    if compact.empty:
        audit = {"day": day, "minute_stocks": 0, "full_exact_stocks": 0,
                 "global_range5_stocks": 0, "events": 0,
                 "class_1": 0, "class_0": 0, "class_minus1": 0,
                 "chronological_drop5": 0}
        pd.DataFrame().to_csv(output / f"{day}_events.csv", index=False)
        (output / f"{day}_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
        return
    for col in ("bars", "distinct_bars", "exact_bars", "min_close", "max_close"):
        compact[col] = pd.to_numeric(compact[col], errors="coerce")
    full = (compact.bars.eq(240) & compact.distinct_bars.eq(240) &
            compact.exact_bars.eq(240) &
            compact.first_bar.astype(str).str.endswith("09:31:00") &
            compact.last_bar.astype(str).str.endswith("15:00:00") &
            compact.min_close.gt(0) & compact.max_close.gt(0))
    possible = compact.loc[full & (compact.min_close / compact.max_close < 0.95),
                           "stk_code"].astype(str).str.zfill(6).tolist()
    events = []
    if possible:
        marks = ",".join(["%s"] * len(possible))
        bars = query(db, "SELECT stk_code,bar_end_time,latest_price FROM "
                     "aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s "
                     "AND kline_type='1m' AND period_minutes=1 AND stk_code IN (" +
                     marks + ") ORDER BY stk_code,bar_end_time", (day, *possible))
        bars["latest_price"] = pd.to_numeric(bars.latest_price, errors="coerce")
        for symbol, group in bars.groupby("stk_code", sort=False):
            clock = group.bar_end_time.astype(str).str[-8:]
            if (len(group) != 240 or set(clock) != EXPECTED or
                    not np.isfinite(group.latest_price.to_numpy()).all() or
                    not group.latest_price.gt(0).all()):
                continue
            values = group.latest_price.to_numpy(dtype=float)
            result = classify(values)
            if result is not None:
                result.update({"day": day, "symbol": str(symbol).zfill(6),
                               "peak_time": clock.iloc[result["peak_slot"] - 1],
                               "trough_time": clock.iloc[result["trough_slot"] - 1],
                               "first_trigger_time": (clock.iloc[result["first_trigger_slot"] - 1]
                                                      if result["first_trigger_slot"] is not None else None)})
                events.append(result)
    frame = pd.DataFrame(events)
    frame.to_csv(output / f"{day}_events.csv", index=False)
    counts = frame["class"].value_counts() if not frame.empty else pd.Series(dtype=int)
    audit = {"day": day, "minute_stocks": len(compact),
             "full_exact_stocks": int(full.sum()),
             "global_range5_stocks": len(possible), "events": len(frame),
             "class_1": int(counts.get(1, 0)), "class_0": int(counts.get(0, 0)),
             "class_minus1": int(counts.get(-1, 0)),
             "chronological_drop5": int(frame.chronological_drop5.sum()) if not frame.empty else 0}
    (output / f"{day}_audit.json").write_text(json.dumps(audit, indent=2) + "\n")


def summarize(days: list[str], output: Path) -> None:
    audits = pd.DataFrame([json.loads((output / f"{day}_audit.json").read_text())
                           for day in days])
    frames = [pd.read_csv(output / f"{day}_events.csv", dtype={"symbol": str})
              for day in days if (output / f"{day}_events.csv").stat().st_size > 1]
    events = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    events.to_csv(output / "all_events.csv.gz", index=False, compression="gzip")
    shsz = (events[events.symbol.str.startswith(("0", "3", "6"))]
            if not events.empty else pd.DataFrame(columns=["day", "symbol", "class"]))
    for name, values in (("shsz_events", shsz),
                         ("shsz_class_1", shsz[shsz["class"].eq(1)]),
                         ("shsz_class_0", shsz[shsz["class"].eq(0)]),
                         ("shsz_class_minus1", shsz[shsz["class"].eq(-1)])):
        counts = values.groupby("day").size() if not values.empty else pd.Series(dtype=int)
        audits[name] = audits.day.map(counts).fillna(0).astype(int)
    audits.to_csv(output / "daily_coverage.csv", index=False)
    windows = []
    for i in range(20, len(days)):
        prior = audits.iloc[i - 20:i]
        windows.append({"test_day": days[i], "history_start": days[i - 20],
                        "history_end": days[i - 1],
                        "history_data_days": int(prior.full_exact_stocks.gt(0).sum()),
                        "history_full_exact_stock_days": int(prior.full_exact_stocks.sum()),
                        "history_events": int(prior.events.sum()),
                        "history_class_1": int(prior.class_1.sum()),
                        "history_class_0": int(prior.class_0.sum()),
                        "history_class_minus1": int(prior.class_minus1.sum()),
                        "history_shsz_events": int(prior.shsz_events.sum()),
                        "history_shsz_class_1": int(prior.shsz_class_1.sum()),
                        "history_shsz_class_0": int(prior.shsz_class_0.sum()),
                        "history_shsz_class_minus1": int(prior.shsz_class_minus1.sum()),
                        "test_full_exact_stocks": int(audits.iloc[i].full_exact_stocks),
                        "test_events": int(audits.iloc[i].events),
                        "test_class_1": int(audits.iloc[i].class_1),
                        "test_class_0": int(audits.iloc[i].class_0),
                        "test_class_minus1": int(audits.iloc[i].class_minus1)})
    pd.DataFrame(windows).to_csv(output / "rolling20_coverage.csv", index=False)
    summary = {"source": "kingdomai.aiia_stock_realtime_minute_snapshot_full",
               "source_filter": "1m, period_minutes=1, 240 exact finalized nonfallback bars",
               "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "first_day": days[0], "last_day": days[-1], "days": len(days),
               "full_exact_stock_days": int(audits.full_exact_stocks.sum()),
               "global_range5_stock_days": int(audits.global_range5_stocks.sum()),
               "range5_events": int(audits.events.sum()),
               "chronological_drop5": int(audits.chronological_drop5.sum()),
               "class_1": int(audits.class_1.sum()),
               "class_0": int(audits.class_0.sum()),
               "class_minus1": int(audits.class_minus1.sum()),
               "shsz_events": int(audits.shsz_events.sum()),
               "shsz_class_1": int(audits.shsz_class_1.sum()),
               "shsz_class_0": int(audits.shsz_class_0.sum()),
               "shsz_class_minus1": int(audits.shsz_class_minus1.sum()),
               "rolling_test_days": len(windows),
               "rolling_test_days_with_20_data_days": sum(
                   row["history_data_days"] == 20 for row in windows)}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--max-days", type=int)
    args = parser.parse_args()
    load_dotenv(".env")
    args.out.mkdir(parents=True, exist_ok=True)
    with db_connection() as db:
        calendar = query(db, "SELECT DISTINCT trade_date FROM kcrp_stock_price "
                         "WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date",
                         (START, END))
        days = [str(x)[:10] for x in calendar.trade_date]
        if args.max_days:
            days = days[:args.max_days]
        for i, day in enumerate(days, 1):
            if not ((args.out / f"{day}_audit.json").exists() and
                    (args.out / f"{day}_events.csv").exists()):
                run_day(db, day, args.out)
            audit = json.loads((args.out / f"{day}_audit.json").read_text())
            print(f"{i}/{len(days)} {day} full={audit['full_exact_stocks']} "
                  f"events={audit['events']} class1={audit['class_1']}", flush=True)
    summarize(days, args.out)


if __name__ == "__main__":
    main()

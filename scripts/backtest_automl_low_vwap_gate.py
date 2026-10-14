"""Apply the existing individual-turnover idle rule to the low-VWAP model."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from scripts.backtest_automl_intraday_vwap_gate import (
    archive_amounts, db_amounts, gate, required_codes,
)
from scripts.benchmark_automl_1440_inference import db_connection
from scripts.experiment_automl_intraday_vwap import LATE_MARKET, MK_CANDIDATES, OUT
from scripts.experiment_automl_low_vwap import MONTHS, required_days


def run(segment: str, output: Path = OUT) -> None:
    picks = pd.read_csv(output / f"low_vwap_top1_{segment}.csv",
                        dtype={"signal_date": str, "symbol6": str})
    source = "mk" if segment == "feb_jul" else "db"
    calendar = (sorted(pd.read_csv(MK_CANDIDATES,
                                   usecols=["signal_date"]).signal_date.unique()) if source == "mk"
                else pd.read_csv(LATE_MARKET).trade_date.astype(str).tolist())
    needed = required_codes(picks, calendar)
    cache = output / "amount_days"
    for day in required_days(calendar, MONTHS[source]):
        codes = needed[day]
        if not codes:
            continue
        path = cache / f"{day}.csv"
        existing = (pd.read_csv(path, dtype={"date": str, "symbol6": str}) if path.exists()
                    else pd.DataFrame(columns=["date", "symbol6", "amount_1440"]))
        missing = codes - set(existing.symbol6)
        if missing:
            if source == "mk":
                extra = archive_amounts(day, missing)
            else:
                with db_connection(Path(".env")) as db:
                    extra = db_amounts(db, day, missing)
            combined = pd.concat([existing, extra], ignore_index=True)
            if combined.duplicated(["date", "symbol6"]).any():
                raise ValueError(f"Duplicate stock-day amount: {day}")
            combined.to_csv(path, index=False)
        print("low-gate-amount", segment, day, len(codes), len(missing), flush=True)
    frames = [pd.read_csv(cache / f"{day}.csv", dtype={"date": str, "symbol6": str})
              for day in required_days(calendar, MONTHS[source])
              if (cache / f"{day}.csv").exists()]
    amounts = pd.concat(frames, ignore_index=True)
    if amounts.duplicated(["date", "symbol6"]).any():
        raise ValueError("Duplicate amount cache rows")
    lookup = {(row.date, row.symbol6): float(row.amount_1440)
              for row in amounts.itertuples(index=False)}
    details = []
    for row in picks.itertuples(index=False):
        i = calendar.index(row.signal_date)
        history = [lookup.get((day, row.symbol6)) for day in calendar[i-20:i]]
        current = lookup.get((row.signal_date, row.symbol6))
        actionable, median = gate(current, history)
        details.append({"segment": segment, "signal_date": row.signal_date,
                        "arm": "low_vwap8", "symbol6": row.symbol6,
                        "amount_1440": current, "prior20_valid": sum(x is not None for x in history),
                        "prior20_median": median, "actionable": actionable,
                        "actual_0940_return": row.actual_0940_return,
                        "realized_return_if_gate": row.actual_0940_return if actionable else 0.0})
    detail = pd.DataFrame(details)
    detail.to_csv(output / f"low_vwap_gate_{segment}.csv", index=False)
    old = pd.read_csv(output / "paired_top1_amount_gate.csv",
                      dtype={"signal_date": str, "symbol6": str})
    old = old[old.segment.eq(segment) & old.arm.eq("baseline7") &
              old.signal_date.str[:7].isin(MONTHS[source])]
    comparison = pd.concat([old, detail], ignore_index=True)
    rows = []
    test_days = [day for day in calendar if day[:7] in MONTHS[source] and
                 not (segment == "feb_jul" and day == "2026-07-31")]
    for arm, group in comparison.groupby("arm"):
        trades = group[group.actionable]
        daily = pd.Series(0., index=test_days)
        daily.loc[group.signal_date] = group.realized_return_if_gate.to_numpy()
        rows.append({"segment": segment, "arm": arm, "test_days": len(test_days),
                     "selected_top1": len(group), "trades": len(trades),
                     "idle_days": len(test_days)-len(trades),
                     "sale_ge3": int(trades.actual_0940_return.ge(.03).sum()),
                     "sale_negative": int(trades.actual_0940_return.lt(0).sum()),
                     "mean_trade_pct": float(trades.actual_0940_return.mean()*100),
                     "mean_test_day_pct": float(daily.mean()*100),
                     "compounded_price_proxy_pct": float(((1+daily).prod()-1)*100)})
    summary = pd.DataFrame(rows)
    summary.to_csv(output / f"low_vwap_gate_summary_{segment}.csv", index=False)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("segment", choices=("sep_oct", "feb_jul"))
    args = parser.parse_args()
    run(args.segment)

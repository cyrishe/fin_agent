"""Apply the existing prior-20 stock-amount idle rule at the 14:45 cutoff."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from scripts.backtest_automl_intraday_vwap_gate import (
    archive_amounts, db_amounts, gate, required_codes,
)
from scripts.benchmark_automl_1440_inference import db_connection
from scripts.experiment_automl_1445_exact0940 import EARLY, LATE, OUT
from scripts.experiment_automl_intraday_vwap import LATE_MARKET


def run(output: Path = OUT, late_source: Path = LATE,
        amount_cache: Path | None = None) -> dict:
    cache = amount_cache or Path("outputs/stock_automl/amount_1445_cache") / output.name
    cache.mkdir(parents=True, exist_ok=True)
    summaries = []
    details = []
    for segment in ("jan_jul", "aug_oct"):
        pick_path = output / f"top1_{segment}.csv"
        if not pick_path.exists():
            continue
        picks = pd.read_csv(pick_path, dtype={"symbol6": str, "signal_date": str})
        days = (sorted(pd.read_csv(EARLY, usecols=["signal_date"]).signal_date.unique())
                if segment == "jan_jul" else
                pd.read_csv(LATE_MARKET).trade_date.astype(str).tolist())
        needed = required_codes(picks, days)
        folder = cache / segment
        folder.mkdir(exist_ok=True)
        if segment == "jan_jul":
            for day, codes in needed.items():
                if codes:
                    path = folder / f"{day}.csv"
                    existing = (pd.read_csv(path, dtype={"symbol6": str}) if path.exists()
                                else pd.DataFrame(columns=["date", "symbol6", "amount_1445"]))
                    missing = codes - set(existing.symbol6)
                    if missing:
                        extra = archive_amounts(day, missing, "14:45").rename(
                            columns={"amount_1440": "amount_1445"})
                        absent = missing - set(extra.symbol6)
                        if absent:
                            extra = pd.concat([extra, pd.DataFrame({
                                "date": day, "symbol6": sorted(absent),
                                "amount_1445": float("nan")})], ignore_index=True)
                        combined = extra if existing.empty else pd.concat(
                            [existing, extra], ignore_index=True)
                        combined.to_csv(path, index=False)
                    print("amount-1445", day, len(codes), flush=True)
        else:
            sources = pd.read_csv(late_source, usecols=["signal_date", "signal_bar_type"])
            sources = sources.drop_duplicates()
            if sources.duplicated("signal_date").any():
                raise ValueError("Mixed signal sources within one day")
            source_by_day = dict(zip(sources.signal_date, sources.signal_bar_type))
            with db_connection(Path(".env")) as db:
                for day, codes in needed.items():
                    if codes:
                        path = folder / f"{day}.csv"
                        existing = (pd.read_csv(path, dtype={"symbol6": str}) if path.exists()
                                    else pd.DataFrame(columns=["date", "symbol6", "amount_1445"]))
                        missing = codes - set(existing.symbol6)
                        if missing:
                            extra = db_amounts(db, day, missing, "14:45",
                                               source_by_day[day]).rename(
                                columns={"amount_1440": "amount_1445"})
                            absent = missing - set(extra.symbol6)
                            if absent:
                                extra = pd.concat([extra, pd.DataFrame({
                                    "date": day, "symbol6": sorted(absent),
                                    "amount_1445": float("nan")})], ignore_index=True)
                            combined = extra if existing.empty else pd.concat(
                                [existing, extra], ignore_index=True)
                            combined.to_csv(path, index=False)
                        print("amount-1445", day, len(codes), flush=True)
        files = sorted(folder.glob("2026-*.csv"))
        amounts = pd.concat([pd.read_csv(path, dtype={"symbol6": str, "date": str})
                             for path in files], ignore_index=True)
        if amounts.duplicated(["date", "symbol6"]).any():
            raise ValueError("Duplicate stock amount")
        lookup = {(r.date, r.symbol6): (None if pd.isna(r.amount_1445) else
                                       float(r.amount_1445))
                  for r in amounts.itertuples(index=False)}
        positions = {day: index for index, day in enumerate(days)}
        daily = pd.Series(0.0, index=days[20:])
        for row in picks.itertuples(index=False):
            i = positions[row.signal_date]
            prior = [lookup.get((day, row.symbol6)) for day in days[i-20:i]]
            current = lookup.get((row.signal_date, row.symbol6))
            actionable, median = gate(current, prior)
            ret = row.actual_0940_return if actionable else 0.0
            if pd.notna(ret):
                daily.loc[row.signal_date] = ret
            details.append({"segment": segment, "signal_date": row.signal_date,
                            "next_date": row.next_date, "symbol6": row.symbol6,
                            "name": row.name, "signal_price": row.signal_price,
                            "second_high": row.second_high, "close_40": row.close_40,
                            "amount_1445": current,
                            "prior20_valid": sum(value is not None for value in prior),
                            "prior20_median": median, "actionable": actionable,
                            "actual_0940_return": row.actual_0940_return,
                            "realized_return_if_gate": ret})
        segment_rows = pd.DataFrame([r for r in details if r["segment"] == segment])
        trades = segment_rows[segment_rows.actionable & segment_rows.actual_0940_return.notna()]
        test_days = daily.index
        if segment == "jan_jul":
            test_days = test_days[test_days <= "2026-07-30"]
        else:
            test_days = test_days[test_days >= "2026-09-02"]
        returns = daily.loc[test_days]
        summaries.append({"segment": segment, "test_days": len(test_days),
                          "selected_top1": len(picks), "trades": len(trades),
                          "idle_days": len(test_days)-len(trades),
                          "target_ge3_trades": int((trades.second_high /
                                                     trades.signal_price - 1 >= .03).sum()),
                          "sale_ge3_trades": int(trades.actual_0940_return.ge(.03).sum()),
                          "negative_trades": int(trades.actual_0940_return.lt(0).sum()),
                          "mean_return_per_trade_pct": float(trades.actual_0940_return.mean()*100),
                          "mean_return_per_day_pct": float(returns.mean()*100),
                          "compounded_price_proxy_pct": float(((1+returns).prod()-1)*100)})
    pd.DataFrame(details).to_csv(output / "top1_amount_gate.csv", index=False)
    pd.DataFrame(summaries).to_csv(output / "amount_gate_summary.csv", index=False)
    result = {"method": "14:45 T stock amount >= median of its prior 20 complete trading days at 14:45; no replacement",
              "segments": summaries}
    (output / "amount_gate_summary.json").write_text(json.dumps(
        result, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--late-source", type=Path, default=LATE)
    parser.add_argument("--amount-cache", type=Path)
    args = parser.parse_args()
    run(args.output, args.late_source, args.amount_cache)

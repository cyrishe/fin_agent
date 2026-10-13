"""Reprice frozen Top1 cash-gated trades from exact unadjusted minute bars.

The entry proxy is (high + low) / 2 of the bar ending 14:41 on the signal
date. The exit proxy is the same midpoint of the next trading day's bar
ending 09:40. Stock selection and idle days are inherited from the frozen
replay; no model is retrained here.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from scripts.backtest_automl_top1_cash_gate import load_inputs, run, save_csv
from scripts.benchmark_automl_1440_inference import db_connection, query


OUT = Path("outputs/stock_automl/capital_replay_midpoint_20261012")


def read_local_bars(path: Path, targets: list[dict]) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(path)
    data = pd.read_csv(path, usecols=["time", "symbol", "high", "low", "close"],
                       dtype={"time": str, "symbol": str})
    codes = {x["symbol6"] for x in targets}
    clocks = {x["bar_end_time"] for x in targets}
    data = data.loc[data.symbol.str[:6].isin(codes) & data.time.isin(clocks)]
    if data.duplicated(["time", "symbol"]).any():
        raise ValueError(f"Duplicate one-minute bars: {path}")
    return [{"bar_end_time": r.time, "symbol6": r.symbol[:6],
             "high": r.high, "low": r.low, "close": r.close,
             "source": str(path)} for r in data.itertuples(index=False)]


def read_database_bars(db, date: str, targets: list[dict]) -> list[dict]:
    codes = sorted({x["symbol6"] for x in targets})
    ends = sorted({x["bar_end_time"] for x in targets})
    code_slots = ",".join(["%s"] * len(codes))
    end_slots = ",".join(["%s"] * len(ends))
    bars = query(db, f"""SELECT stk_code AS symbol6, bar_end_time,
        high_price AS high, low_price AS low, latest_price AS close,
        is_finalized, is_fallback, source_snapshot_time
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND stk_code IN ({code_slots}) AND bar_end_time IN ({end_slots})""",
        (date, *codes, *ends))
    result = []
    for r in bars.itertuples(index=False):
        end = pd.Timestamp(r.bar_end_time)
        if (r.is_finalized != 1 or r.is_fallback != 0 or
                pd.Timestamp(r.source_snapshot_time) != end):
            raise ValueError(f"Non-final minute bar: {date} {r.symbol6} {end}")
        result.append({"bar_end_time": str(end), "symbol6": str(r.symbol6)[:6],
                       "high": r.high, "low": r.low, "close": r.close,
                       "source": "aiia_stock_realtime_minute_snapshot_full"})
    if len(result) != len({(x["bar_end_time"], x["symbol6"]) for x in result}):
        raise ValueError(f"Duplicate database minute bar: {date}")
    return result


def build_overrides(compact_dir: Path, mk_dir: Path) -> tuple[dict[str, dict], list[dict]]:
    _, signals, picks = load_inputs()
    eligible = {d: picks[d] for d, row in signals.items()
                if row["Top1代码"] and
                row["个股成交额低于此前20日中位数（标签）"] == "否"}
    targets: dict[str, list[dict]] = defaultdict(list)
    for signal, pick in eligible.items():
        code, exit_day = pick["symbol6"], pick["next_date"]
        for date, clock, kind, expected in (
            (signal, "14:40:00", "baseline_buy", pick["signal_price"]),
            (signal, "14:41:00", "midpoint_buy", None),
            (exit_day, "09:40:00", "midpoint_sell", pick["close_40"]),
        ):
            targets[date].append({"signal_date": signal, "date": date,
                                  "symbol6": code, "bar_end_time": f"{date} {clock}",
                                  "kind": kind, "expected_close": expected})
    observed: dict[tuple[str, str], dict] = {}
    with db_connection() as db:
        for date, expected in sorted(targets.items()):
            month = int(date[5:7])
            if month <= 4:
                path = compact_dir / f"{date}.csv.zst"
                bars = read_local_bars(path, expected)
            elif month <= 7:
                path = mk_dir / f"{date}.csv"
                bars = read_local_bars(path, expected)
            elif month == 8:
                raise ValueError(f"Unexpected August position: {date}")
            else:
                bars = read_database_bars(db, date, expected)
            for bar in bars:
                key = (bar["symbol6"], bar["bar_end_time"])
                if key in observed:
                    raise ValueError(f"Duplicate observed minute bar: {key}")
                observed[key] = bar

    audit: list[dict] = []
    prices: dict[tuple[str, str], Decimal] = {}
    for date, expected in sorted(targets.items()):
        for item in expected:
            key = (item["symbol6"], item["bar_end_time"])
            bar = observed.get(key)
            pending = item["signal_date"] == "2026-10-09" and item["kind"] == "midpoint_sell"
            if bar is None:
                if pending:
                    continue
                raise ValueError(f"Missing exact one-minute bar: {item}")
            high, low, close = [Decimal(str(bar[k])) for k in ("high", "low", "close")]
            if not (low > 0 and low <= close <= high):
                raise ValueError(f"Invalid OHLC: {item} {bar}")
            if item["expected_close"] not in (None, ""):
                if abs(close - Decimal(str(item["expected_close"]))) > Decimal("0.011"):
                    raise ValueError(f"Frozen close differs from minute bar: {item} {bar}")
            mid = (high + low) / 2
            prices[(item["signal_date"], item["kind"])] = mid
            audit.append({**item, "high": float(high), "low": float(low),
                          "close": float(close), "midpoint": float(mid),
                          "source": bar["source"]})
    overrides: dict[str, dict] = {}
    for signal, pick in eligible.items():
        overrides[signal] = {
            "symbol6": pick["symbol6"], "next_date": pick["next_date"],
            "buy_midpoint": str(prices[(signal, "midpoint_buy")]),
            "sell_midpoint": (str(prices[(signal, "midpoint_sell")])
                              if (signal, "midpoint_sell") in prices else ""),
        }
    return overrides, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact-dir", type=Path, default=Path("/tmp/finagent_lfs"))
    parser.add_argument("--mk-dir", type=Path,
                        default=Path("/tmp/fin_agent_intraday_mk/mk/none"))
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    overrides, audit = build_overrides(args.compact_dir, args.mk_dir)
    result = run(overrides)
    result["price_method"] = "unadjusted one-minute (high+low)/2; buy 14:40-14:41; sell 09:39-09:40"
    result["minute_bar_count"] = len(audit)
    result["minute_sources"] = {
        "February-April": "verified remote Git LFS minute CSV objects",
        "May-July": "/Users/imac/Downloads/mk.tar.xz / mk/none",
        "September-October": "aiia_stock_realtime_minute_snapshot_full finalized 1m bars",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    save_csv(args.output / "minute_audit.csv", audit)
    save_csv(args.output / "daily.csv", result["daily"])
    save_csv(args.output / "monthly.csv", result["monthly"])
    save_csv(args.output / "trades.csv", result["trades"])
    (args.output / "replay.json").write_text(json.dumps(result, ensure_ascii=False,
                                                         indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

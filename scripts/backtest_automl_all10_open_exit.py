"""Compare all-ten-minute exits and audit the 14:40-to-close entry-price gap."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.backtest_automl_seven_factor_oos import ROOT as OLD_REPORT
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.reprice_automl_first10_high_exit import load_inputs, simulate


OUT = Path("docs/stock_automl_runs/20261009_all10_open_exit")


def tail_gap_audit(env_file: Path, old_trades: pd.DataFrame):
    conn = db_connection(env_file)
    minute_parts, daily_parts = [], []
    try:
        for day, group in old_trades.groupby("信号日"):
            symbols = group["代码"].tolist()
            placeholders = ",".join(["%s"] * len(symbols))
            minute_parts.append(query(conn, "SELECT trade_date, stk_code, bar_end_time, "
                                      "source_snapshot_time, latest_price, is_finalized, "
                                      "is_fallback FROM aiia_stock_realtime_minute_snapshot_full "
                                      "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                                      f"AND bar_end_time=%s AND stk_code IN ({placeholders})",
                                      (day, f"{day} 14:40:00", *symbols)))
            daily_parts.append(query(conn, "SELECT trade_date, LEFT(stk_code,6) AS symbol6, "
                                     "close FROM kcrp_stock_price WHERE trade_date=%s "
                                     f"AND LEFT(stk_code,6) IN ({placeholders})",
                                     (day, *symbols)))
    finally:
        conn.rollback()
        conn.close()
    minute = pd.concat(minute_parts, ignore_index=True)
    daily = pd.concat(daily_parts, ignore_index=True)
    if len(minute) != len(old_trades) or len(daily) != len(old_trades):
        raise ValueError("Missing 14:40 minute or daily close for selected stock")
    if not (minute.is_finalized.eq(1) & minute.is_fallback.eq(0) &
            pd.to_datetime(minute.bar_end_time).eq(pd.to_datetime(minute.source_snapshot_time))).all():
        raise ValueError("14:40 bars are not exact and finalized")
    minute["信号日"] = minute.trade_date.astype(str).str[:10]
    minute["代码"] = minute.stk_code.str[:6]
    daily["信号日"] = daily.trade_date.astype(str).str[:10]
    daily = daily.rename(columns={"symbol6": "代码", "close": "收盘价"})
    minute = minute.rename(columns={"latest_price": "14时40分价"})
    rows = old_trades.merge(minute[["信号日", "代码", "14时40分价"]],
                            on=["信号日", "代码"], validate="one_to_one")
    rows = rows.merge(daily[["信号日", "代码", "收盘价"]],
                      on=["信号日", "代码"], validate="one_to_one")
    rows["买价_14时50分"] = pd.to_numeric(rows["买价_14时50分"])
    rows["14时40分价"] = pd.to_numeric(rows["14时40分价"])
    rows["收盘价"] = pd.to_numeric(rows["收盘价"])
    rows["14时40分到收盘"] = rows["收盘价"] / rows["14时40分价"] - 1
    rows["14时50分到收盘"] = rows["收盘价"] / rows["买价_14时50分"] - 1
    rows["次晨开盘相对前收盘"] = rows["次晨开盘价"] / rows["收盘价"] - 1
    rows["次晨开盘相对买价"] = rows["次晨开盘价"] / rows["买价_14时50分"] - 1
    summary = {"selected_stocks": len(rows), "actual_entry_time": "14:50 one-minute close proxy",
               "from_1440_to_close_down_more_than_1pct": int(rows["14时40分到收盘"].lt(-.01).sum()),
               "from_1450_to_close_down_more_than_1pct": int(rows["14时50分到收盘"].lt(-.01).sum()),
               "from_1450_to_close_down_more_than_0_5pct": int(rows["14时50分到收盘"].lt(-.005).sum()),
               "median_1450_to_close_return": float(rows["14时50分到收盘"].median()),
               "next_open_up_vs_close_but_below_entry": int((
                   rows["次晨开盘相对前收盘"].gt(0) & rows["次晨开盘相对买价"].lt(0)).sum())}
    cols = ["信号日", "代码", "名称", "14时40分价", "买价_14时50分", "收盘价", "次晨开盘价",
            "14时40分到收盘", "14时50分到收盘", "次晨开盘相对前收盘", "次晨开盘相对买价"]
    return rows[cols], summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args()
    picks, bars = load_inputs()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    for scenario, fill in (("minute_high_optimistic", "high"),
                           ("next_minute_open_delay", "next_open")):
        result[scenario] = {}
        for name, selected in picks.items():
            metrics, daily, trades = simulate(selected, bars, skip_minutes=0,
                                              fill_mode=fill)
            result[scenario][name] = metrics
            daily.to_csv(args.output_dir / f"{scenario}_{name}_daily.csv", index=False)
            trades.to_csv(args.output_dir / f"{scenario}_{name}_trades.csv", index=False)
    old_trades = pd.read_csv(OLD_REPORT / "trades.csv", dtype={"代码": str})
    gaps, gap_summary = tail_gap_audit(args.env_file, old_trades)
    gaps.to_csv(args.output_dir / "selected_tail_and_open_prices.csv", index=False)
    report = {"rules": {
        "trigger": "Inspect 09:31–09:40 minute highs in order; first high strictly above T 14:50 entry ×1.01 triggers.",
        "minute_high_optimistic": "Sell at that minute high; price touch does not prove execution.",
        "next_minute_open_delay": "After the qualifying minute completes, sell at the following minute open; if 09:40 triggers, use 09:40 close.",
        "fallback": "If no minute triggers, sell at 09:40 close."},
        "starting_cash": 100000, "results": result, "tail_gap_audit": gap_summary,
        "input_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in (OLD_REPORT / "trades.csv",
                                      OLD_REPORT / "selected_1m_bars.csv",
                                      Path("docs/stock_automl_runs/20261009_market_shape_training/selected_1m_bars.csv"))}}
    (args.output_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"old_all": {key: result[key]["old_all"] for key in result},
                      "tail_gap_audit": gap_summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

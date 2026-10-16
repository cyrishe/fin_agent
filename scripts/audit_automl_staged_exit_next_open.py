"""Price observed-close exit signals at the next minute's open as a sensitivity."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.backtest_automl_staged_minute_exit import OUT, START_CASH
from scripts.backtest_automl_tail_top2_cash import buy_quantity
from scripts.benchmark_automl_1440_inference import db_connection, query


BARS = OUT / "selected_morning_opens.csv.gz"
TRADES = OUT / "trades.csv"


def fetch_opens(trades: pd.DataFrame, env_file: Path) -> pd.DataFrame:
    conn = db_connection(env_file)
    pieces = []
    try:
        for day, group in trades.groupby("next_date", sort=True):
            codes = sorted(group.symbol6.unique())
            placeholders = ",".join(["%s"]*len(codes))
            bars = query(conn, "SELECT LEFT(stk_code,6) AS symbol6, bar_end_time, "
                         "open_price, latest_price, is_finalized, is_fallback, "
                         "source_snapshot_time FROM aiia_stock_realtime_minute_snapshot_full "
                         "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                         "AND bar_end_time BETWEEN %s AND %s "
                         f"AND LEFT(stk_code,6) IN ({placeholders})",
                         (day, f"{day} 09:31:00", f"{day} 09:40:00", *codes))
            if len(bars) != len(codes)*10 or bars.duplicated(["symbol6", "bar_end_time"]).any():
                raise ValueError(f"Missing exact 1m open bars: {day}")
            if not (bars.is_finalized.eq(1) & bars.is_fallback.eq(0) &
                    pd.to_datetime(bars.source_snapshot_time).eq(
                        pd.to_datetime(bars.bar_end_time))).all():
                raise ValueError(f"Non-exact 1m bars: {day}")
            bars["open_price"] = pd.to_numeric(bars.open_price, errors="raise")
            bars["latest_price"] = pd.to_numeric(bars.latest_price, errors="raise")
            if not (bars.open_price.gt(0) & bars.latest_price.gt(0)).all():
                raise ValueError(f"Nonpositive 1m prices: {day}")
            bars["next_date"] = day
            bars["minute"] = pd.to_datetime(bars.bar_end_time).dt.strftime("%H:%M")
            pieces.append(bars[["next_date", "symbol6", "minute", "open_price", "latest_price"]])
    finally:
        conn.rollback()
        conn.close()
    result = pd.concat(pieces, ignore_index=True)
    result.to_csv(BARS, index=False, compression="gzip")
    return result


def run(env_file: Path = Path("/Volumes/ext/fin_agent/.env"), rebuild: bool = False) -> dict:
    trades = pd.read_csv(TRADES, dtype={"symbol6": str})
    bars = fetch_opens(trades, env_file) if rebuild or not BARS.exists() else pd.read_csv(
        BARS, dtype={"symbol6": str})
    expected = trades[["next_date", "symbol6"]].drop_duplicates()
    if len(bars) != len(expected)*10 or bars.duplicated(["next_date", "symbol6", "minute"]).any():
        raise ValueError("Archived bar count does not match selected stocks")
    bar_lookup = bars.set_index(["next_date", "symbol6", "minute"])
    delayed_rows, daily_rows, summaries = [], [], {}
    for model, group in trades.groupby("model", sort=True):
        cash, peak, drawdown = START_CASH, START_CASH, Decimal(0)
        selected = []
        for day, picks in group.groupby("signal_date", sort=True):
            before = cash
            for pick in picks.sort_values("rank").itertuples(index=False):
                current_bar = bar_lookup.loc[(pick.next_date, pick.symbol6, pick.exit_minute)]
                if not np.isclose(current_bar.latest_price, pick.exit_price):
                    raise ValueError("Exit trigger close disagrees with exact DB bar")
                if pick.exit_minute == "09:40":
                    fill_minute = "09:40"
                    fill = Decimal(str(pick.exit_price))
                else:
                    fill_minute = f"09:{int(pick.exit_minute[-2:])+1:02}"
                    fill = Decimal(str(bar_lookup.loc[(pick.next_date, pick.symbol6,
                                                       fill_minute)].open_price))
                entry = Decimal(str(pick.entry_1440))
                budget = before*(Decimal("0.6") if pick.rank == 1 else Decimal("0.4"))
                shares = buy_quantity(budget, entry, pick.symbol6)
                pnl = (fill-entry)*shares
                cash += pnl
                row = {"model": model, "signal_date": day, "next_date": pick.next_date,
                       "rank": pick.rank, "symbol6": pick.symbol6,
                       "trigger_minute": pick.exit_minute, "exit_rule": pick.exit_rule,
                       "fill_minute": fill_minute, "entry": float(entry),
                       "observed_close": pick.exit_price, "next_open_fill": float(fill),
                       "shares": shares, "pnl": float(pnl),
                       "fill_return": float(fill/entry-1)}
                selected.append(row)
                delayed_rows.append(row)
            peak = max(peak, cash)
            drawdown = min(drawdown, cash/peak-1)
            daily_rows.append({"model": model, "signal_date": day,
                               "cash_before": float(before), "cash_after": float(cash)})
        selected = pd.DataFrame(selected)
        summaries[model] = {"bought": int(selected.shares.gt(0).sum()),
                            "mean_fill_return_pct": float(selected.fill_return.mean()*100),
                            "below_zero": int(selected.fill_return.lt(0).sum()),
                            "ending_cash": float(cash),
                            "gross_cash_return": float(cash/START_CASH-1),
                            "max_daily_drawdown": float(drawdown)}
    pd.DataFrame(delayed_rows).to_csv(OUT / "next_open_trades.csv", index=False)
    pd.DataFrame(daily_rows).to_csv(OUT / "next_open_daily.csv", index=False)
    summary = {"execution": "Observe each 1m close, fill at next 1m open; 09:40 exits at 09:40 close. Next open is another historical price proxy, not a proven fill.",
               "results": summaries,
               "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (TRADES, BARS)}}
    (OUT / "next_open_summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                            indent=2)+"\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(run()["results"], ensure_ascii=False, indent=2))

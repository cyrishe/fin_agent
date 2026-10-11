"""Cash-path audit for the exploratory eight-factor daily top-two rule.

The historical model scores and stock rows are local, ignored inputs. This
script never refits a model or uses the next morning to select a stock. It
compares three specified morning exits, including a configurable touch-based limit
scenario whose execution remains an assumption rather than a proven fill.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values


STARTING_CASH = Decimal("1000000")
TARGET_PCT = Decimal("0.02")
EXIT_MODES = ("next_open", "next_0940", "limit_then_0940")
SENSITIVITY_PCTS = tuple(Decimal(value) for value in
                         ("0.005", "0.01", "0.015", "0.02", "0.025", "0.03"))


def target_price(entry: Decimal, target_pct: Decimal = TARGET_PCT) -> Decimal:
    return (entry * (1 + target_pct)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def minimum_buy_shares(symbol: str) -> int:
    # Explicit trading-unit assumption: 688/689 starts at 200; others at 100.
    return 200 if symbol.startswith(("688", "689")) else 100


def buy_quantity(budget: Decimal, entry: Decimal, symbol: str) -> int:
    affordable = int(budget / entry)
    if affordable < minimum_buy_shares(symbol):
        return 0
    if symbol.startswith(("688", "689")):
        return affordable  # Above the 200-share minimum, allow one-share increments.
    return affordable // 100 * 100


def fetch_morning_bars(workbook_path: Path, env_path: Path, output_path: Path) -> None:
    """Fetch only the selected stocks' 09:40 bars from the read-only source."""
    config = dotenv_values(env_path)
    url = config.get("SIMPLE_BI_PLATFORM_DB_URL") or config.get("PLATFORM_DB_URL")
    if url:
        os.environ["SIMPLE_BI_PLATFORM_DB_URL"] = url
    from scripts.experiment_automl_1450_grid import frame, read_only_db

    selected = [row for row in json.loads(workbook_path.read_text())["all"]
                if row["rank_binary"] <= 2]
    by_day: dict[str, list[str]] = {}
    for row in selected:
        by_day.setdefault(row["next_date"], []).append(row["symbol6"])
    output = []
    with read_only_db() as conn:
        for day, symbols in sorted(by_day.items()):
            placeholders = ",".join(["%s"] * len(symbols))
            query = f"""
                SELECT trade_date, stk_code, bar_end_time, latest_price,
                       is_fallback, is_finalized, source_snapshot_time
                FROM aiia_stock_realtime_minute_snapshot_full
                WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                  AND bar_end_time=%s AND stk_code IN ({placeholders})
            """
            output.append(frame(conn, query, (day, f"{day} 09:40:00", *symbols)))
    bars = pd.concat(output, ignore_index=True)
    if len(bars) != len(selected):
        raise ValueError(f"Expected {len(selected)} 09:40 bars, got {len(bars)}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bars.to_csv(output_path, index=False)


def load_selection(workbook_path: Path, candidate_path: Path, bar_path: Path) -> pd.DataFrame:
    workbook = json.loads(workbook_path.read_text())
    selected = pd.DataFrame(row for row in workbook["all"] if row["rank_binary"] <= 2)
    if selected.empty or selected.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Selection is empty or contains duplicate stock-days")
    if not selected.groupby("signal_date").size().eq(2).all():
        raise ValueError("This audit requires exactly two selections per signal day")

    candidates = pd.read_csv(candidate_path, dtype={"symbol6": str})[
        ["signal_date", "symbol6", "entry_1450", "next_open", "next_high10"]]
    bars = pd.read_csv(bar_path, dtype={"stk_code": str}).rename(columns={
        "trade_date": "next_date", "stk_code": "symbol6", "latest_price": "next_0940"})
    bars = bars[["next_date", "symbol6", "bar_end_time", "source_snapshot_time",
                 "next_0940", "is_fallback", "is_finalized"]]
    if bars.duplicated(["next_date", "symbol6"]).any():
        raise ValueError("Duplicate 09:40 bars")
    if not (bars.is_fallback.eq(0) & bars.is_finalized.eq(1) &
            pd.to_datetime(bars.bar_end_time).eq(pd.to_datetime(bars.source_snapshot_time)) &
            pd.to_datetime(bars.bar_end_time).dt.strftime("%H:%M:%S").eq("09:40:00")).all():
        raise ValueError("09:40 bars must be exact, finalized, and not fallback")

    rows = selected[["signal_date", "next_date", "symbol6", "name", "rank_binary",
                     "binary_pass_score", "class"]].merge(
                         candidates, on=["signal_date", "symbol6"], how="left",
                         validate="one_to_one").merge(
                             bars[["next_date", "symbol6", "next_0940"]],
                             on=["next_date", "symbol6"], how="left",
                             validate="one_to_one")
    numeric = ["entry_1450", "next_open", "next_high10", "next_0940"]
    rows[numeric] = rows[numeric].apply(pd.to_numeric, errors="coerce")
    if len(rows) != len(selected) or rows[numeric].isna().any().any() or not rows[numeric].gt(0).all().all():
        raise ValueError("Missing or invalid entry / morning prices")
    if not rows.next_high10.ge(rows.next_open).all():
        raise ValueError("The first-ten-minute high cannot be below its opening price")
    return rows.rename(columns={"class": "actual_class"}).sort_values(
        ["signal_date", "rank_binary", "symbol6"]).reset_index(drop=True)


def exit_price(row, mode: str, target_pct: Decimal = TARGET_PCT) -> Decimal:
    entry = Decimal(str(row.entry_1450))
    if mode == "next_open":
        return Decimal(str(row.next_open))
    if mode == "next_0940":
        return Decimal(str(row.next_0940))
    if mode == "limit_then_0940":
        target = target_price(entry, target_pct)
        if Decimal(str(row.next_high10)) >= target:
            # An opening price above the sell limit executes at the opening
            # price in this idealized auction model, not below it.
            return max(target, Decimal(str(row.next_open)))
        return Decimal(str(row.next_0940))
    raise ValueError(f"Unknown exit mode: {mode}")


def simulate(rows: pd.DataFrame, *, mode: str, lot_constrained: bool,
             starting_cash: Decimal = STARTING_CASH,
             target_pct: Decimal = TARGET_PCT) -> tuple[dict, list[dict], list[dict]]:
    if starting_cash <= 0 or target_pct <= 0:
        raise ValueError("Starting cash and target percentage must be positive")
    equity = starting_cash
    peak = starting_cash
    max_drawdown = Decimal("0")
    daily = []
    trades = []
    for day, group in rows.groupby("signal_date", sort=True):
        if len(group) != 2:
            raise ValueError("Two selections are required per day")
        start = equity
        budget = start / 2
        profit = Decimal("0")
        for row in group.itertuples(index=False):
            entry = Decimal(str(row.entry_1450))
            sell = exit_price(row, mode, target_pct)
            if lot_constrained:
                quantity = Decimal(buy_quantity(budget, entry, str(row.symbol6)))
            else:
                quantity = budget / entry
            buy_cost = quantity * entry
            if lot_constrained and buy_cost > budget:
                raise ValueError("Purchase exceeded equal daily allocation")
            profit += quantity * (sell - entry)
            trades.append({
                "signal_date": day, "next_date": row.next_date,
                "symbol6": row.symbol6, "name": row.name,
                "rank_binary": int(row.rank_binary), "actual_class": int(row.actual_class),
                "entry_1450": float(entry), "next_open": float(row.next_open),
                "next_high10": float(row.next_high10), "next_0940": float(row.next_0940),
                "target_price": float(target_price(entry, target_pct)),
                "target_touched": Decimal(str(row.next_high10)) >= target_price(entry, target_pct),
                "sell_price": float(sell), "shares": float(quantity),
                "buy_cost": float(buy_cost), "profit": float(quantity * (sell - entry)),
                "executed": quantity > 0,
            })
        equity += profit
        peak = max(peak, equity)
        drawdown = equity / peak - 1
        max_drawdown = min(max_drawdown, drawdown)
        daily.append({
            "signal_date": day, "next_date": group.iloc[0].next_date,
            "starting_cash": float(start), "ending_cash": float(equity),
            "daily_return": float(equity / start - 1),
            "executed_count": sum(t["executed"] for t in trades[-2:]),
        })
    result = {
        "exit_mode": mode, "lot_constrained": lot_constrained,
        "target_pct": float(target_pct),
        "starting_cash": float(starting_cash), "ending_cash": float(equity),
        "total_return": float(equity / starting_cash - 1),
        "max_close_to_close_drawdown": float(max_drawdown),
        "signal_days": len(daily), "ranked_stocks": len(trades),
        "executed_stocks": sum(t["executed"] for t in trades),
        "unaffordable_stocks": sum(not t["executed"] for t in trades),
    }
    return result, daily, trades


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook-data", type=Path, default=Path(
        "outputs/stock_automl/tail_critical_review/workbook_data.json"))
    parser.add_argument("--candidates", type=Path, default=Path(
        "outputs/stock_automl/tail_standard/candidates_1440.csv"))
    parser.add_argument("--morning-bars", type=Path, default=Path(
        "outputs/stock_automl/tail_critical_review/next_0940_bars.csv"))
    parser.add_argument("--fetch-morning-bars", action="store_true")
    parser.add_argument("--starting-cash", type=Decimal, default=STARTING_CASH)
    parser.add_argument("--target-pct", type=Decimal, default=TARGET_PCT)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--summary", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_tail_cash_backtest_1m_2pct/summary.json"))
    parser.add_argument("--daily", type=Path, default=Path(
        "outputs/stock_automl/tail_critical_review/cash_backtest_1m_2pct_daily.csv"))
    parser.add_argument("--trades", type=Path, default=Path(
        "outputs/stock_automl/tail_critical_review/cash_backtest_1m_2pct_trades.csv"))
    args = parser.parse_args()
    if args.starting_cash <= 0 or args.target_pct <= 0:
        parser.error("--starting-cash and --target-pct must be positive")
    if args.fetch_morning_bars:
        fetch_morning_bars(args.workbook_data, args.env_file, args.morning_bars)
    rows = load_selection(args.workbook_data, args.candidates, args.morning_bars)
    runs = []
    day_rows = []
    trade_rows = []
    for mode in EXIT_MODES:
        for lot_constrained in (False, True):
            metrics, daily, trades = simulate(rows, mode=mode, lot_constrained=lot_constrained,
                                              starting_cash=args.starting_cash,
                                              target_pct=args.target_pct)
            runs.append(metrics)
            for item in daily:
                day_rows.append({"exit_mode": mode, "lot_constrained": lot_constrained, **item})
            for item in trades:
                trade_rows.append({"exit_mode": mode, "lot_constrained": lot_constrained, **item})

    report = {
        "policy": "Eight-factor binary pass score, rank <= 2 per signal day",
        "backtest_signal_period": [rows.signal_date.min(), rows.signal_date.max()],
        "backtest_exit_period": [rows.next_date.min(), rows.next_date.max()],
        "signal_days": int(rows.signal_date.nunique()),
        "ranked_stocks": len(rows),
        "target_pct": float(args.target_pct),
        "target_touched_stocks": sum(Decimal(str(row.next_high10)) >=
                                     target_price(Decimal(str(row.entry_1450)), args.target_pct)
                                     for row in rows.itertuples(index=False)),
        "assumptions": [
            "Buy at the T 14:50 minute latest-price proxy; close all positions the next morning.",
            "Split available cash equally between the two daily names and roll end cash into the next signal day.",
            "Ideal equal-weight uses fractional shares. Trading-unit mode uses 100-share multiples for regular A shares, a 200-share minimum and one-share increments thereafter for 688/689, and leaves unaffordable allocations in cash.",
            "The target percentage rounds upward to the next CNY 0.01. A first-ten-minute high touch is assumed filled at the target (or at the opening price if higher); otherwise sell at the 09:40 minute latest-price proxy.",
            "No commissions, stamp duty, slippage, queue priority, or actual fill data are included.",
        ],
        "runs": runs,
        "same_period_target_sensitivity_not_oos_optimization": [
            {
                "target_pct": float(pct),
                "target_touched_stocks": sum(
                    Decimal(str(row.next_high10)) >=
                    target_price(Decimal(str(row.entry_1450)), pct)
                    for row in rows.itertuples(index=False)),
                "ending_cash": float(simulate(rows, mode="limit_then_0940",
                                              lot_constrained=True,
                                              starting_cash=args.starting_cash,
                                              target_pct=pct)[0]["ending_cash"]),
            } for pct in SENSITIVITY_PCTS],
        "provenance": {
            "workbook_data_sha256": sha256(args.workbook_data),
            "candidates_sha256": sha256(args.candidates),
            "morning_bars_sha256": sha256(args.morning_bars),
            "script_sha256": sha256(Path(__file__)),
        },
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.daily.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    pd.DataFrame(day_rows).to_csv(args.daily, index=False)
    pd.DataFrame(trade_rows).to_csv(args.trades, index=False)
    print(f"{len(rows)} ranked stocks; {report['signal_days']} signal days; "
          f"wrote {len(runs)} cash paths")


if __name__ == "__main__":
    main()

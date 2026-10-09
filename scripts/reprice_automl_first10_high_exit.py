"""Reprice frozen top-two selections with first-ten-minute high-trigger exit."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pandas as pd

from scripts.backtest_automl_tail_top2_cash import buy_quantity


OLD = Path("docs/stock_automl_runs/20261009_seven_factor_cash_backtest")
NEW = Path("docs/stock_automl_runs/20261009_market_shape_training")
OUT = Path("docs/stock_automl_runs/20261009_first10_high_exit")
START_CASH = Decimal("100000")


def exit_at_observed_high(entry: Decimal, morning: pd.DataFrame):
    """First qualifying 09:34–09:40 minute high; otherwise 09:40 close."""
    ordered = morning.sort_values("minute")
    if ordered.minute.tolist() != [f"09:{n:02}" for n in range(31, 41)]:
        raise ValueError("Expected ten exact morning minutes")
    threshold = entry * Decimal("1.01")
    for bar in ordered.iloc[3:].itertuples():
        high = Decimal(str(bar.high_price))
        if high > threshold:
            return high, bar.minute + "分钟最高价", True
    return Decimal(str(ordered.iloc[-1].latest_price)), "09:40收盘价", False


def simulate(picks: pd.DataFrame, bars: pd.DataFrame):
    equity = START_CASH
    peak = equity
    drawdown = Decimal(0)
    daily, trades = [], []
    for day, group in picks.groupby("signal_date", sort=True):
        if len(group) != 2 or sorted(group["rank"].tolist()) != [1, 2]:
            raise ValueError("Each signal day needs exactly ranks one and two")
        before = equity
        for row in group.sort_values("rank").itertuples():
            entry_bar = bars[(bars.day == row.signal_date) & (bars.symbol6 == row.symbol6) &
                             bars.minute.eq("14:50")]
            morning = bars[(bars.day == row.next_date) & (bars.symbol6 == row.symbol6) &
                           bars.minute.between("09:31", "09:40")]
            if len(entry_bar) != 1:
                raise ValueError(f"Missing entry minute: {day} {row.symbol6}")
            entry = Decimal(str(entry_bar.iloc[0].latest_price))
            sell, when, triggered = exit_at_observed_high(entry, morning)
            budget = before * (Decimal("0.6") if row.rank == 1 else Decimal("0.4"))
            shares = buy_quantity(budget, entry, row.symbol6)
            pnl = (sell - entry) * shares
            equity += pnl
            trades.append({"信号日": day, "卖出日": row.next_date, "名次": row.rank,
                           "代码": row.symbol6, "名称": row.name, "模型分数": float(row.score),
                           "分配资金": float(budget), "买价": float(entry), "股数": shares,
                           "实际买入": int(shares > 0), "首次触发": int(triggered),
                           "卖出时点": when, "卖价": float(sell), "单笔盈亏": float(pnl),
                           "单股收益率": float(sell / entry - 1)})
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
        daily.append({"信号日": day, "卖出日": group.iloc[0].next_date,
                      "期初资金": float(before), "当日盈亏": float(equity - before),
                      "期末资金": float(equity)})
    result = {"signal_days": len(daily), "selected_stocks": len(trades),
              "bought_stocks": sum(t["实际买入"] for t in trades),
              "unaffordable_stocks": sum(not t["实际买入"] for t in trades),
              "bought_first_trigger_count": sum(t["首次触发"] and t["实际买入"] for t in trades),
              "ending_cash": float(equity), "return": float(equity/START_CASH-1),
              "max_daily_drawdown": float(drawdown)}
    return result, pd.DataFrame(daily), pd.DataFrame(trades)


def load_inputs():
    old = pd.read_csv(OLD / "trades.csv", dtype={"代码": str}).rename(columns={
        "信号日": "signal_date", "卖出日": "next_date", "名次": "rank",
        "代码": "symbol6", "名称": "name", "模型分数": "score"})
    pick_columns = ["signal_date", "next_date", "rank", "symbol6", "name", "score"]
    selections = {"old_all": old[pick_columns].copy()}
    for name in ("old_common", "new_common", "new_all_out_of_training"):
        selections[name] = pd.read_csv(NEW / f"{name}_picks.csv", dtype={"symbol6": str})[
            pick_columns]
    sources = []
    for directory in (OLD, NEW):
        frame = pd.read_csv(directory / "selected_1m_bars.csv", dtype={"symbol6": str})
        sources.append(frame)
    all_bars = pd.concat(sources, ignore_index=True)
    keys = ["day", "symbol6", "minute"]
    duplicates = all_bars[all_bars.duplicated(keys, keep=False)]
    if not duplicates.empty and duplicates.groupby(keys)[["open_price", "high_price", "latest_price"]].nunique().gt(1).any().any():
        raise ValueError("Archived one-minute sources disagree")
    return selections, all_bars.drop_duplicates(keys)


def main():
    picks, bars = load_inputs()
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, selected in picks.items():
        result, daily, trades = simulate(selected, bars)
        results[name] = result
        daily.to_csv(OUT / f"{name}_daily.csv", index=False)
        trades.to_csv(OUT / f"{name}_trades.csv", index=False)
    summary = {"rule": "Ignore 09:31–09:33; inspect 09:34–09:40 one-minute highs in order. First high strictly above entry ×1.01 exits at that high. Otherwise exit at 09:40 close. Minute-high fill is optimistic, not proven execution.",
               "starting_cash": float(START_CASH), "results": results,
               "input_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (OLD / "trades.csv", OLD / "selected_1m_bars.csv",
                                             NEW / "selected_1m_bars.csv", *(
                                                 NEW / f"{name}_picks.csv" for name in
                                                 ("old_common", "new_common", "new_all_out_of_training")))}}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

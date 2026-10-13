"""Replay the frozen Top1 picks with the stock-turnover skip rule and full capital."""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, getcontext
from pathlib import Path

import pandas as pd


getcontext().prec = 32
ROOT = Path("docs/stock_automl_runs")
REPORT = ROOT / "20261011_market_state_asof/daily_2026.csv"
OLD_PICKS = ROOT / "20261011_mk_1m_tradable_st_rolling20/top1_verified.csv"
NEW_PICKS = ROOT / "20261011_aug_oct_independent20/top2_verified.csv"
OLD_CALENDAR = ROOT / "20261011_mk_1m_market_amount_board/experiments/daily_market.csv"
NEW_CALENDAR = ROOT / "20261011_aug_oct_independent20/market_context.csv"
OUTPUT = Path("outputs/stock_automl/capital_replay_20261012")
CAPITAL = Decimal("1000000")


def day(value: str) -> str:
    return datetime.strptime(str(value).replace("/", "-"), "%Y-%m-%d").date().isoformat()


def dec(value: object) -> Decimal:
    return Decimal(str(value))


def number(value: Decimal | float | int | None) -> float | None:
    return None if value is None else float(value)


def load_inputs() -> tuple[list[str], dict[str, dict], dict[str, dict]]:
    calendar = sorted(set(pd.read_csv(OLD_CALENDAR).trade_date) |
                      set(pd.read_csv(NEW_CALENDAR).trade_date))
    calendar = [d for d in calendar if "2026-02-02" <= d <= "2026-10-09"]
    report = pd.read_csv(REPORT, dtype={"Top1代码": str}).fillna("")
    signals: dict[str, dict] = {}
    for row in report.to_dict("records"):
        signal = day(row["选股日"])
        if signal in signals:
            raise ValueError(f"Duplicate signal date {signal}")
        signals[signal] = row
    if set(signals) - set(calendar):
        raise ValueError("Signal date missing from market calendar")
    picks: dict[str, dict] = {}
    for path in (OLD_PICKS, NEW_PICKS):
        table = pd.read_csv(path, dtype={"symbol6": str}).fillna("")
        for row in table.loc[table.selection_rank.eq(1)].to_dict("records"):
            if row["signal_date"] in picks:
                raise ValueError(f"Duplicate frozen Top1: {row['signal_date']}")
            picks[row["signal_date"]] = row
    for signal, row in signals.items():
        if not row["Top1代码"]:
            continue
        pick = picks.get(signal)
        if pick is None or pick["symbol6"] != row["Top1代码"] or pick["next_date"] != day(row["次交易日"]):
            raise ValueError(f"Frozen Top1 mismatch: {signal}")
        if row["Top1收益（%）"] != "":
            if pick["close_40"] == "":
                raise ValueError(f"Missing frozen 09:40 price: {signal}")
            price_return = 100 * (float(pick["close_40"]) / float(pick["signal_price"]) - 1)
            if abs(price_return - float(row["Top1收益（%）"])) > 0.00001:
                raise ValueError(f"09:40 return mismatch: {signal}")
    return calendar, signals, picks


def metrics(daily: list[dict], trades: list[dict], monthly: list[dict]) -> dict:
    nav = [CAPITAL] + [dec(x["ending_equity"]) for x in daily]
    daily_returns = [float(x["daily_return"]) for x in daily]
    peak = CAPITAL
    peak_date = daily[0]["date"]
    worst = Decimal(0)
    worst_peak = worst_trough = None
    for row in daily:
        value = dec(row["ending_equity"])
        if value > peak:
            peak, peak_date = value, row["date"]
        drawdown = value / peak - 1
        if drawdown < worst:
            worst, worst_peak, worst_trough = drawdown, peak_date, row["date"]
    days = (date.fromisoformat(daily[-1]["date"]) -
            date.fromisoformat(daily[0]["date"])).days
    total = float(nav[-1] / CAPITAL - 1)
    cagr = (1 + total) ** (365 / days) - 1
    avg = sum(daily_returns) / len(daily_returns)
    sample_std = pd.Series(daily_returns).std(ddof=1)
    annual_vol = float(sample_std * math.sqrt(252))
    sharpe = float(avg / sample_std * math.sqrt(252)) if sample_std > 0 else None
    downside = math.sqrt(sum(min(x, 0) ** 2 for x in daily_returns) / len(daily_returns))
    sortino = avg / downside * math.sqrt(252) if downside > 0 else None
    positives = [dec(t["profit_cny"]) for t in trades if dec(t["profit_cny"]) > 0]
    negatives = [dec(t["profit_cny"]) for t in trades if dec(t["profit_cny"]) < 0]
    result = {
        "starting_capital_cny": float(CAPITAL),
        "first_signal_date": daily[0]["date"],
        "last_verified_exit_date": daily[-1]["date"],
        "trading_days": len(daily),
        "completed_trades": len(trades),
        "idle_trading_days": sum(not r["bought_symbol6"] for r in daily),
        "ending_equity_at_cost_cny": float(nav[-1]),
        "realized_profit_cny": float(nav[-1] - CAPITAL),
        "total_return": total,
        "cagr_calendar_365": cagr,
        "annualized_volatility_252": annual_vol,
        "sharpe_rf0_252": sharpe,
        "sortino_rf0_252": sortino,
        "max_drawdown": float(worst),
        "max_drawdown_peak_date": worst_peak,
        "max_drawdown_trough_date": worst_trough,
        "calmar": cagr / abs(float(worst)) if worst < 0 else None,
        "winning_trades": len(positives),
        "losing_trades": len(negatives),
        "win_rate": len(positives) / len(trades),
        "profit_factor_cash": float(sum(positives) / -sum(negatives)) if negatives else None,
        "average_trade_return": sum(float(t["return"]) for t in trades) / len(trades),
        "best_daily_return": max(daily_returns),
        "worst_daily_return": min(daily_returns),
        "positive_months": sum(m["monthly_return"] > 0 for m in monthly),
        "negative_months": sum(m["monthly_return"] < 0 for m in monthly),
    }
    return result


def run(price_overrides: dict[str, dict] | None = None) -> dict:
    calendar, signals, picks = load_inputs()
    equity = CAPITAL
    holding: dict | None = None
    daily: list[dict] = []
    trades: list[dict] = []
    month_rows: dict[str, list[dict]] = defaultdict(list)
    for current in calendar:
        opening = equity
        sold = None
        if holding is not None and holding["next_date"] == current:
            sold = holding
            price = dec(sold["sell_price"])
            equity = dec(sold["shares"]) * price
            trades.append({
                "signal_date": sold["signal_date"], "exit_date": current,
                "symbol6": sold["symbol6"], "name": sold["name"],
                "buy_price": number(dec(sold["buy_price"])), "sell_price": number(price),
                "shares": number(dec(sold["shares"])),
                "starting_equity_cny": number(opening),
                "ending_equity_cny": number(equity),
                "profit_cny": number(equity - opening),
                "return": number(price / dec(sold["buy_price"]) - 1),
            })
            holding = None
        elif holding is not None:
            # No intervening trading day should exist between the frozen
            # signal and its designated next trading day.
            raise ValueError(f"Unresolved overnight holding on {current}")
        row = signals.get(current)
        bought = None
        reason = ""
        status = "轮空"
        if row is None or not row["Top1代码"]:
            reason = "无可用模型/分钟K" if current.startswith("2026-08") else "当日无Top1"
            status = "缺分钟K轮空" if current.startswith("2026-08") else "轮空"
        elif row["个股成交额低于此前20日中位数（标签）"] != "否":
            reason = ("成交额历史不足" if row["个股此前20日有效天数"] != 20
                      else "当日成交额低于此前20日中位数")
        else:
            pick = dict(picks[current])
            if price_overrides is not None:
                override = price_overrides[current]
                if override["symbol6"] != pick["symbol6"] or override["next_date"] != pick["next_date"]:
                    raise ValueError(f"Midpoint price identity mismatch: {current}")
                pick["signal_price"] = override["buy_midpoint"]
                pick["close_40"] = override["sell_midpoint"]
            if pick["close_40"] == "":
                if current != "2026-10-09":
                    raise ValueError(f"Unverified next-morning sell price: {current}")
                reason = "次晨09:40卖价待验证；按买入成本计值"
            price = dec(pick["signal_price"])
            shares = equity / price
            holding = {
                "signal_date": current, "next_date": pick["next_date"],
                "symbol6": pick["symbol6"], "name": pick["name"],
                "buy_price": str(price), "sell_price": str(pick["close_40"]),
                "shares": str(shares),
            }
            bought = holding
            status = "卖出后买入" if sold else "买入"
        if sold and not bought:
            status = "卖出后轮空"
        daily_return = equity / opening - 1
        record = {
            "date": current, "status": status,
            "sold_symbol6": sold["symbol6"] if sold else "",
            "sold_name": sold["name"] if sold else "",
            "sold_buy_price": number(dec(sold["buy_price"])) if sold else None,
            "sold_price": number(dec(sold["sell_price"])) if sold else None,
            "bought_symbol6": bought["symbol6"] if bought else "",
            "bought_name": bought["name"] if bought else "",
            "bought_price": number(dec(bought["buy_price"])) if bought else None,
            "held_shares": number(dec(bought["shares"])) if bought else None,
            "opening_equity": number(opening),
            "profit_cny": number(equity - opening),
            "daily_return": number(daily_return),
            "ending_equity": number(equity),
            "cash_balance": 0.0 if bought else number(equity),
            "reason": reason,
        }
        daily.append(record)
        month_rows[current[:7]].append(record)
    monthly: list[dict] = []
    for month, rows in sorted(month_rows.items()):
        start = dec(rows[0]["opening_equity"])
        end = dec(rows[-1]["ending_equity"])
        local_peak = start
        local_dd = Decimal(0)
        for row in rows:
            balance = dec(row["ending_equity"])
            local_peak = max(local_peak, balance)
            local_dd = min(local_dd, balance / local_peak - 1)
        returns = [r["daily_return"] for r in rows]
        std = pd.Series(returns).std(ddof=1)
        monthly.append({
            "month": month, "start_date": rows[0]["date"], "end_date": rows[-1]["date"],
            "trading_days": len(rows),
            "completed_trades": sum(bool(r["sold_symbol6"]) for r in rows),
            "new_positions": sum(bool(r["bought_symbol6"]) for r in rows),
            "idle_days": sum(not r["bought_symbol6"] for r in rows),
            "starting_equity": number(start), "profit_cny": number(end - start),
            "monthly_return": number(end / start - 1), "ending_equity": number(end),
            "monthly_max_drawdown": number(local_dd),
            "monthly_sharpe_rf0_252": (sum(returns) / len(returns) / std * math.sqrt(252)
                                         if std > 0 else None),
        })
    overall = metrics(daily, trades, monthly)
    # The last dated signal has no trustworthy exit yet. Its cost-valued
    # position is visible, but no unobserved return enters the metrics.
    if holding is None or holding["next_date"] != "2026-10-12":
        raise ValueError("Expected one outstanding 2026-10-09 Top1 position")
    if sum(t["return"] for t in trades if t["signal_date"].startswith("2026-09")) <= 0:
        raise ValueError("Unexpected September source returns")
    return {"daily": daily, "monthly": monthly, "trades": trades,
            "metrics": overall, "pending": holding,
            "sources": [str(REPORT), str(OLD_PICKS), str(NEW_PICKS),
                        str(OLD_CALENDAR), str(NEW_CALENDAR)]}


def save_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    result = run()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    save_csv(OUTPUT / "daily.csv", result["daily"])
    save_csv(OUTPUT / "monthly.csv", result["monthly"])
    save_csv(OUTPUT / "trades.csv", result["trades"])
    (OUTPUT / "replay.json").write_text(json.dumps(result, ensure_ascii=False,
                                                     indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

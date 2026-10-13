"""May–July 2026 cooling-rebound entry and exit-policy cash replay.

Research only: daily open and next-minute open are fill proxies, not orders.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_limit_rebound_features import KEY
from scripts.experiment_automl_limit_rebound_2y import BASE
from scripts.experiment_automl_limit_rebound_model import event_keys, make_model
from scripts.mk_minute_archive import day_file


SOURCE = Path("outputs/stock_automl/limit_rebound_2y_r1")
OUT = Path("outputs/stock_automl/limit_rebound_may_july_exit_r1")
MINUTE_ROOT = Path("outputs/stock_automl/mk_1m_compact")
START_CASH = 1_000_000.0
COMMISSION = .00025
MIN_FEE = 5.0
TRANSFER = .00001
STAMP_SELL = .0005
POLICIES = ("first_limit_touch", "first_ge7_touch", "hold_until_unsealed", "fixed_3day")


def standard_limit_rate(symbol: str) -> float | None:
    """Ordinary mainland A shares only; status checked against known first board."""
    if symbol.startswith(("300", "301", "688", "689")):
        return .20
    if symbol.startswith(("0", "6")):
        return .10
    return None


def cap_price(preclose: float, rate: float) -> float:
    return float((Decimal(str(preclose)) * (1 + Decimal(str(rate)))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP))


def buy_fee(notional: float) -> float:
    return max(MIN_FEE, notional * COMMISSION) + notional * TRANSFER


def sell_fee(notional: float) -> float:
    return max(MIN_FEE, notional * COMMISSION) + notional * (TRANSFER + STAMP_SELL)


def load_prices(source: Path = SOURCE) -> pd.DataFrame:
    paths = [source / "price_months" / f"price_2026-{month:02}.csv.gz"
             for month in range(4, 9)]
    d = pd.concat((pd.read_csv(path, dtype={"stk_code": str, "trade_date": str})
                   for path in paths), ignore_index=True)
    d["symbol"] = d.stk_code.str[:6]
    if d.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("Duplicate daily price key")
    d = d.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    prior_raw_close = d.groupby("symbol").close.shift(1)
    # The vendor's preclose changes on ex-rights/ex-dividend days. Treat the
    # ratio as a total-return share factor rather than a price crash.
    d["action_factor"] = (prior_raw_close / d.preclose).where(
        prior_raw_close.notna() & d.preclose.gt(0), 1.0)
    if (~np.isfinite(d.action_factor) | d.action_factor.le(0)).any():
        raise ValueError("Invalid corporate-action factor")
    return d.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def select_signals(prices: pd.DataFrame, source: Path = SOURCE) -> tuple[pd.DataFrame, dict]:
    features = pd.read_csv(source / "features.csv.gz", dtype={"symbol": str})
    labels = pd.read_csv(source / "labels.csv.gz", dtype={"symbol": str})
    panel = features.merge(labels, on=KEY, validate="one_to_one")
    train = panel[panel.asof_date.between("2024-10-01", "2026-04-27") &
                  panel.label_next3_observed.eq(True)].copy()
    score_dates = panel[panel.asof_date.between("2026-04-30", "2026-07-30")].copy()
    if event_keys(train) & event_keys(score_dates):
        raise ValueError("Training event crosses the signal boundary")
    if train.empty or score_dates.empty:
        raise ValueError("Missing training or signal decisions")
    # Exclude special-treatment and unsupported regimes using the first board,
    # which was already observable when the cooling decision was made.
    first = prices[["trade_date", "symbol", "preclose", "close"]].copy()
    first["first_return"] = first.close / first.preclose - 1
    score_dates = score_dates.merge(
        first[["trade_date", "symbol", "first_return"]],
        left_on=["first_limit_date", "symbol"], right_on=["trade_date", "symbol"],
        how="left", validate="many_to_one").drop(columns="trade_date")
    score_dates["rate"] = score_dates.symbol.map(standard_limit_rate)
    eligible = score_dates.rate.notna() & score_dates.first_return.ge(
        score_dates.rate * .85)
    excluded = int((~eligible).sum())
    score_dates = score_dates[eligible].copy()
    train["label_next3_any_limit_up"] = train.label_next3_any_limit_up.astype(int)
    model = make_model("shallow_boost")
    model.fit(train[list(BASE)], train.label_next3_any_limit_up)
    score_dates["score"] = model.predict_proba(score_dates[list(BASE)])[:, 1]
    score_dates = score_dates.sort_values(
        ["asof_date", "score", "symbol", "first_limit_date"],
        ascending=[True, False, True, True])
    score_dates["rank"] = score_dates.groupby("asof_date").cumcount() + 1
    picks = score_dates[score_dates["rank"].le(5)].copy()
    return picks, {"train_rows": len(train), "train_last_decision": train.asof_date.max(),
                   "candidate_rows": len(panel[panel.asof_date.between(
                       "2026-04-30", "2026-07-30")]),
                   "unsupported_or_st_proxy_excluded": excluded,
                   "signal_days": picks.asof_date.nunique(), "selected": len(picks),
                   "selected_three_day_label_hits": int(picks.label_next3_any_limit_up.eq(True).sum())}


def make_entries(picks: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    dates = sorted(prices.trade_date.unique())
    next_day = dict(zip(dates[:-1], dates[1:]))
    price = prices.set_index(["trade_date", "symbol"])
    rows = []
    reasons = defaultdict(int)
    for p in picks.itertuples(index=False):
        day = next_day.get(p.asof_date)
        if day is None or not "2026-05-01" <= day <= "2026-07-31":
            reasons["outside_entry_window"] += 1
            continue
        key = (day, p.symbol)
        if key not in price.index:
            reasons["no_next_day_quote"] += 1
            continue
        bar = price.loc[key]
        cap = cap_price(float(bar.preclose), float(p.rate))
        if not np.isfinite(bar.open) or bar.open <= 0 or bar.volume <= 0:
            reasons["no_open_trade"] += 1
            continue
        if bar.open >= cap - .005:
            reasons["open_at_upper_limit"] += 1
            continue
        rows.append({"signal_date": p.asof_date, "entry_date": day,
                     "symbol": p.symbol, "first_limit_date": p.first_limit_date,
                     "cooling_day": int(p.cooling_day), "rank": int(p.rank),
                     "score": float(p.score), "rate": float(p.rate),
                     "entry_price": float(bar.open)})
    return pd.DataFrame(rows), dict(reasons)


def minute_demand(entries: pd.DataFrame, prices: pd.DataFrame) -> dict[str, set[str]]:
    dates = sorted(prices.trade_date.unique())
    date_index = {day: i for i, day in enumerate(dates)}
    price = prices.set_index(["trade_date", "symbol"])
    demand: dict[str, set[str]] = defaultdict(set)
    for row in entries.itertuples(index=False):
        start = date_index[row.entry_date]
        for day in dates[start:start + 3]:
            key = (day, row.symbol)
            if key not in price.index:
                continue
            bar = price.loc[key]
            if bar.high >= min(cap_price(float(bar.preclose), row.rate),
                               float(bar.preclose) * 1.07) - .005:
                demand[day].add(row.symbol)
    return demand


def validate_minute(frame: pd.DataFrame, daily_close: float) -> pd.DataFrame | None:
    if len(frame) != 240:
        return None
    frame = frame.sort_values("time").copy()
    clock = pd.to_datetime(frame.time).dt.strftime("%H:%M:%S")
    cols = frame[["open", "high", "close"]].apply(pd.to_numeric, errors="coerce")
    if (clock.nunique() != 240 or clock.iloc[0] != "09:31:00" or
            clock.iloc[-1] != "15:00:00" or cols.isna().any().any() or
            cols.le(0).any().any() or
            not np.isclose(cols.close.iloc[-1], daily_close, atol=.005, rtol=0)):
        return None
    return frame.assign(open=cols.open, high=cols.high, close=cols.close)


def load_minutes(demand: dict[str, set[str]], prices: pd.DataFrame,
                 root: Path = MINUTE_ROOT) -> tuple[dict[tuple[str, str], pd.DataFrame], dict]:
    price = prices.set_index(["trade_date", "symbol"])
    out = {}
    counts = defaultdict(int)
    db_days = sorted(day for day in demand if day >= "2026-08-01")
    with db_connection() as conn:
        for day, symbols in sorted(demand.items()):
            if day < "2026-08-01":
                try:
                    path = day_file(root, day)
                except FileNotFoundError:
                    counts["missing_local_day"] += len(symbols)
                    continue
                chunks = []
                for chunk in pd.read_csv(path, dtype={"symbol": str}, chunksize=200_000):
                    chosen = chunk[chunk.symbol.str[:6].isin(symbols)]
                    if not chosen.empty:
                        chunks.append(chosen)
                frame = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
            else:
                marks = ",".join(["%s"] * len(symbols))
                sql = ("SELECT LEFT(stk_code,6) AS symbol,bar_end_time AS time,"
                       "open_price AS open,high_price AS high,latest_price AS close "
                       "FROM aiia_stock_realtime_minute_snapshot_full "
                       "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                       "AND is_finalized=1 AND is_fallback=0 "
                       "AND source_snapshot_time=bar_end_time AND stk_code IN (" + marks + ")")
                frame = query(conn, sql, (day, *sorted(symbols)))
            groups = {} if frame.empty else {
                str(symbol)[:6]: group for symbol, group in frame.groupby("symbol")}
            for symbol in symbols:
                bar = price.loc[(day, symbol)]
                valid = validate_minute(groups[symbol], float(bar.close)) if symbol in groups else None
                if valid is None:
                    counts["incomplete_stock_day"] += 1
                else:
                    out[(day, symbol)] = valid
                    counts["complete_stock_day"] += 1
        conn.rollback()
    counts["requested_stock_days"] = sum(map(len, demand.values()))
    counts["database_days"] = len(db_days)
    return out, dict(counts)


def next_open(dates: list[str], index: int, symbol: str,
              price: pd.DataFrame) -> tuple[str, float] | None:
    for day in dates[index + 1:]:
        if (day, symbol) in price.index:
            quote = float(price.loc[(day, symbol)].open)
            if np.isfinite(quote) and quote > 0:
                return day, quote
    return None


def trigger_exit(row, policy: str, prices: pd.DataFrame,
                 minute: dict[tuple[str, str], pd.DataFrame],
                 dates: list[str], date_index: dict[str, int]) -> dict:
    price = prices.set_index(["trade_date", "symbol"])
    start = date_index[row.entry_date]
    symbol = row.symbol
    if policy in ("first_limit_touch", "first_ge7_touch"):
        for holding_day in range(3):
            index = start + holding_day
            if index >= len(dates):
                break
            day = dates[index]
            if (day, symbol) not in price.index:
                continue
            d = price.loc[(day, symbol)]
            threshold = (cap_price(float(d.preclose), row.rate) if policy == "first_limit_touch"
                         else float(d.preclose) * 1.07)
            if float(d.high) < threshold - .005:
                continue
            if holding_day == 0:
                later = next_open(dates, index, symbol, price)
                if later is not None:
                    return {"exit_date": later[0], "exit_price": later[1],
                            "exit_reason": "t_plus_one_after_trigger", "minute_used": False}
                break
            bars = minute.get((day, symbol))
            if bars is not None:
                if float(bars.open.iloc[0]) >= threshold - .005:
                    return {"exit_date": day, "exit_price": float(bars.open.iloc[0]),
                            "exit_reason": "trigger_at_open", "minute_used": True}
                hits = np.flatnonzero(bars.high.to_numpy(float) >= threshold - .005)
                if len(hits) and hits[0] + 1 < 240:
                    return {"exit_date": day, "exit_price": float(bars.open.iloc[hits[0] + 1]),
                            "exit_reason": "next_minute_after_trigger", "minute_used": True}
            later = next_open(dates, index, symbol, price)
            if later is not None:
                return {"exit_date": later[0], "exit_price": later[1],
                        "exit_reason": "next_open_after_trigger", "minute_used": False}
            break
    elif policy == "hold_until_unsealed":
        first_seal = None
        for holding_day in range(3):
            index = start + holding_day
            if index >= len(dates):
                break
            day = dates[index]
            if (day, symbol) in price.index:
                d = price.loc[(day, symbol)]
                if d.is_limit_price == 1 and d.close > d.preclose:
                    first_seal = index
                    break
        if first_seal is not None:
            for index in range(first_seal + 1, min(start + 10, len(dates))):
                day = dates[index]
                if (day, symbol) not in price.index:
                    continue
                d = price.loc[(day, symbol)]
                if d.is_limit_price != 1 or d.close <= d.preclose:
                    later = next_open(dates, index, symbol, price)
                    if later is not None:
                        return {"exit_date": later[0], "exit_price": later[1],
                                "exit_reason": "first_unsealed_next_open", "minute_used": False}
            last = min(start + 9, len(dates) - 2)
            later = next_open(dates, last, symbol, price)
            if later is not None:
                return {"exit_date": later[0], "exit_price": later[1],
                        "exit_reason": "ten_day_cap_next_open", "minute_used": False}
    # No trigger: three full holding sessions, then next market open.
    last = min(start + 2, len(dates) - 2)
    later = next_open(dates, last, symbol, price)
    if later is None:
        raise ValueError(f"No exit quote for {symbol} after {row.entry_date}")
    return {"exit_date": later[0], "exit_price": later[1],
            "exit_reason": "three_day_timeout", "minute_used": False}


def make_trade_paths(entries: pd.DataFrame, prices: pd.DataFrame,
                     minute: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    dates = sorted(prices.trade_date.unique())
    date_index = {day: i for i, day in enumerate(dates)}
    adjusted = prices.sort_values(["symbol", "trade_date"]).copy()
    adjusted["cum_factor"] = adjusted.groupby("symbol").action_factor.cumprod()
    cumulative = adjusted.set_index(["trade_date", "symbol"]).cum_factor
    rows = []
    for entry in entries.itertuples(index=False):
        for policy in POLICIES:
            exit_info = trigger_exit(entry, policy, prices, minute, dates, date_index)
            if exit_info["exit_date"] <= entry.entry_date:
                raise AssertionError("A-share same-day exit")
            factor = float(cumulative.loc[(exit_info["exit_date"], entry.symbol)] /
                           cumulative.loc[(entry.entry_date, entry.symbol)])
            rows.append({**entry._asdict(), "policy": policy, **exit_info,
                         "adjustment_factor": factor,
                         "gross_return": exit_info["exit_price"] * factor / entry.entry_price - 1})
    return pd.DataFrame(rows)


def down_open_demand(paths: pd.DataFrame, prices: pd.DataFrame) -> dict[str, set[str]]:
    """Get minute paths for exit opens pinned to the theoretical lower limit."""
    dates = sorted(prices.trade_date.unique())
    index = {day: i for i, day in enumerate(dates)}
    quote = prices.set_index(["trade_date", "symbol"])
    demand: dict[str, set[str]] = defaultdict(set)
    for row in paths.itertuples(index=False):
        if row.exit_reason == "next_minute_after_trigger":
            continue
        for day in dates[index[row.exit_date]:]:
            if (day, row.symbol) not in quote.index:
                continue
            d = quote.loc[(day, row.symbol)]
            floor = cap_price(float(d.preclose), -row.rate)
            if float(d.open) > floor + .005:
                break
            demand[day].add(row.symbol)
    return demand


def resolve_down_limit_exits(paths: pd.DataFrame, prices: pd.DataFrame,
                             minute: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    """A lower-limit opening is not treated as a guaranteed sell fill."""
    dates = sorted(prices.trade_date.unique())
    index = {day: i for i, day in enumerate(dates)}
    quote = prices.set_index(["trade_date", "symbol"])
    adjusted = prices.sort_values(["symbol", "trade_date"]).copy()
    adjusted["cum_factor"] = adjusted.groupby("symbol").action_factor.cumprod()
    factor = adjusted.set_index(["trade_date", "symbol"]).cum_factor
    results = []
    for row in paths.itertuples(index=False):
        result = row._asdict()
        if row.exit_reason == "next_minute_after_trigger":
            results.append(result)
            continue
        day = row.exit_date
        while True:
            d = quote.loc[(day, row.symbol)]
            floor = cap_price(float(d.preclose), -row.rate)
            if float(d.open) > floor + .005:
                if day != row.exit_date:
                    result.update(exit_date=day, exit_price=float(d.open),
                                  exit_reason="down_limit_retry_next_open", minute_used=False)
                break
            bars = minute.get((day, row.symbol))
            if bars is not None:
                reopened = np.flatnonzero(bars.high.to_numpy(float) > floor + .005)
                if len(reopened) and reopened[0] + 1 < 240:
                    result.update(exit_date=day, exit_price=float(bars.open.iloc[reopened[0] + 1]),
                                  exit_reason="down_limit_reopen_next_minute", minute_used=True)
                    break
            later = next_open(dates, index[day], row.symbol, quote)
            if later is None:
                raise ValueError(f"No executable lower-limit exit for {row.symbol} after {day}")
            day = later[0]
        result["adjustment_factor"] = float(
            factor.loc[(result["exit_date"], row.symbol)] /
            factor.loc[(row.entry_date, row.symbol)])
        result["gross_return"] = (result["exit_price"] * result["adjustment_factor"] /
                                  row.entry_price - 1)
        results.append(result)
    return pd.DataFrame(results)


def replay_cash(paths: pd.DataFrame, prices: pd.DataFrame, policy: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Five concurrent equal budget slots, process open exits before open entries."""
    selected = paths[paths.policy.eq(policy)].copy()
    days = sorted(prices.trade_date.unique())
    days = [day for day in days if "2026-05-06" <= day <= "2026-08-31"]
    quotes = prices.set_index(["trade_date", "symbol"])
    scheduled = {day: group.sort_values("rank") for day, group in selected.groupby("entry_date")}
    cash = START_CASH
    holdings: dict[str, dict] = {}
    entered_events: set[tuple[str, str]] = set()
    completed = []
    curve = []
    skipped = defaultdict(int)
    for day in days:
        # Approximate distributions/bonus shares by reinvesting the vendor
        # preclose adjustment. These shares can be fractional in the replay.
        for symbol, position in holdings.items():
            if (day, symbol) in quotes.index:
                position["shares"] *= float(quotes.loc[(day, symbol)].action_factor)
        for symbol, position in list(holdings.items()):
            if position["exit_date"] == day and position["exit_reason"] != "next_minute_after_trigger":
                proceeds = position["shares"] * position["exit_price"]
                fee = sell_fee(proceeds)
                cash += proceeds - fee
                completed.append({**position, "sell_fee": fee,
                                  "net_pnl": proceeds - fee - position["entry_cost"]})
                del holdings[symbol]
        last = days[days.index(day) - 1] if day != days[0] else None
        marked_before = cash
        for symbol, position in holdings.items():
            mark = (quotes.loc[(last, symbol)].close if last and (last, symbol) in quotes.index
                    else position["entry_price"])
            marked_before += position["shares"] * float(mark)
        for trade in scheduled.get(day, pd.DataFrame()).itertuples(index=False):
            event = (trade.symbol, trade.first_limit_date)
            if event in entered_events:
                skipped["event_already_traded"] += 1
                continue
            if trade.symbol in holdings:
                skipped["already_holding"] += 1
                continue
            if len(holdings) >= 5:
                skipped["five_slots_full"] += 1
                continue
            budget = min(cash, marked_before / 5)
            lot = 200 if trade.symbol.startswith(("688", "689")) else 100
            shares = int(budget // (trade.entry_price * lot)) * lot
            while shares and shares * trade.entry_price + buy_fee(shares * trade.entry_price) > cash:
                shares -= lot
            if shares <= 0:
                skipped["insufficient_cash"] += 1
                continue
            notional = shares * trade.entry_price
            entry_cost = notional + buy_fee(notional)
            cash -= entry_cost
            holdings[trade.symbol] = {**trade._asdict(), "shares": shares,
                                      "entry_shares": shares,
                                      "entry_cost": entry_cost, "buy_fee": buy_fee(notional)}
            entered_events.add(event)
        # Minute-trigger sales happen after today's open and cannot fund today's buys.
        for symbol, position in list(holdings.items()):
            if position["exit_date"] == day:
                proceeds = position["shares"] * position["exit_price"]
                fee = sell_fee(proceeds)
                cash += proceeds - fee
                completed.append({**position, "sell_fee": fee,
                                  "net_pnl": proceeds - fee - position["entry_cost"]})
                del holdings[symbol]
        equity = cash
        for symbol, position in holdings.items():
            if (day, symbol) not in quotes.index:
                raise ValueError(f"Cannot mark {symbol} on {day}")
            equity += position["shares"] * float(quotes.loc[(day, symbol)].close)
        curve.append({"date": day, "policy": policy, "cash": cash,
                      "equity": equity, "holdings": len(holdings)})
    if holdings:
        raise ValueError(f"Positions not exited by August end: {sorted(holdings)}")
    curve = pd.DataFrame(curve)
    completed = pd.DataFrame(completed)
    curve.attrs["skipped"] = dict(skipped)
    return curve, completed


def summarize(paths: pd.DataFrame, curves: pd.DataFrame,
              completed: pd.DataFrame, selection: dict, fills: dict,
              minute_quality: dict) -> dict:
    result = {"window": "2026-05-06 to 2026-07-31 entry dates",
              "initial_cash": START_CASH,
              "selection": selection, "entry_skips": fills,
              "minute_quality": minute_quality, "policies": {}}
    for policy in POLICIES:
        trade = paths[paths.policy.eq(policy)]
        cash_trades = completed[completed.policy.eq(policy)]
        curve = curves[curves.policy.eq(policy)].set_index("date")
        month_ends = [max(day for day in curve.index if day[:7] == month)
                      for month in ("2026-05", "2026-06", "2026-07")]
        bases = [START_CASH] + [float(curve.loc[day, "equity"])
                               for day in month_ends[:-1]]
        monthly = {month[:7]: float(curve.loc[month, "equity"] / base - 1)
                   for month, base in zip(month_ends, bases)}
        result["policies"][policy] = {
            "common_fillable_trades": len(trade),
            "common_gross_mean": float(trade.gross_return.mean()),
            "trigger_reasons": {key: int(value) for key, value in
                                trade.exit_reason.value_counts().items()},
            "portfolio_buys": len(cash_trades),
            "portfolio_realized_win_rate": float(cash_trades.net_pnl.gt(0).mean()),
            "portfolio_net_pnl": float(cash_trades.net_pnl.sum()),
            "monthly_equity_returns": monthly,
            "may_july_equity_return": float(curve.loc[month_ends[-1], "equity"] / START_CASH - 1),
            "post_july_final_equity_return": float(curve.equity.iloc[-1] / START_CASH - 1),
            "max_drawdown_may_july": float((curve.loc[:month_ends[-1], "equity"] /
                  curve.loc[:month_ends[-1], "equity"].cummax() - 1).min()),
        }
    key = ["signal_date", "entry_date", "symbol", "first_limit_date", "cooling_day"]
    matched = paths.pivot(index=key, columns="policy", values="gross_return").reset_index()
    matched["week"] = pd.to_datetime(matched.signal_date).dt.to_period("W").astype(str)
    comparisons = {}
    for other in ("first_limit_touch", "hold_until_unsealed", "fixed_3day"):
        matched["difference"] = matched.first_ge7_touch - matched[other]
        groups = matched.groupby("week").agg(total=("difference", "sum"), n=("difference", "size"))
        rng = np.random.default_rng(20261014)
        sampled = rng.integers(0, len(groups), size=(5000, len(groups)))
        deltas = (groups.total.to_numpy()[sampled].sum(axis=1) /
                  groups.n.to_numpy()[sampled].sum(axis=1))
        comparisons[f"first_ge7_minus_{other}"] = {
            "paired_mean_gross_return_difference": float(matched.difference.mean()),
            "week_bootstrap_95pct": np.quantile(deltas, [.025, .975]).tolist(),
            "weeks": len(groups)}
    result["same_entries_comparison"] = comparisons
    return result


def run(source: Path = SOURCE, out: Path = OUT) -> dict:
    prices = load_prices(source)
    picks, selection = select_signals(prices, source)
    entries, fills = make_entries(picks, prices)
    demand = minute_demand(entries, prices)
    minute, minute_quality = load_minutes(demand, prices)
    paths = make_trade_paths(entries, prices, minute)
    down_demand = down_open_demand(paths, prices)
    if down_demand:
        down_minute, down_quality = load_minutes(down_demand, prices)
        minute.update(down_minute)
        minute_quality["lower_limit_open_stock_days"] = down_quality["requested_stock_days"]
        minute_quality["lower_limit_complete_stock_days"] = down_quality.get("complete_stock_day", 0)
        paths = resolve_down_limit_exits(paths, prices, minute)
    curves, trades = [], []
    skipped = {}
    for policy in POLICIES:
        curve, completed = replay_cash(paths, prices, policy)
        skipped[policy] = curve.attrs["skipped"]
        curves.append(curve)
        trades.append(completed)
    curve = pd.concat(curves, ignore_index=True)
    completed = pd.concat(trades, ignore_index=True)
    result = summarize(paths, curve, completed, selection, fills, minute_quality)
    for policy in POLICIES:
        result["policies"][policy]["portfolio_skips"] = skipped[policy]
    result["assumptions"] = {
        "model": "31 daily/market features, fixed shallow boost; train decisions through 2026-04-27",
        "signal": "Daily top 5 after cooling close; enter next open; rank and candidate filter known at signal close",
        "entry": "Skip absent open, zero-volume, or open at theoretical upper limit",
        "execution": "First touch uses next 1m open on T+1 or later; entry-day touch exits following open. Missing/incomplete minute or 15:00 touch exits following open. Sell orders at a lower-limit open wait for the first observed minute reopening (then next minute open) or a later tradable open.",
        "hold_until_unsealed": "Seek first sealed close in entry sessions 1–3, then hold sealed closes; first unsealed close exits following open, at most 10 holding sessions. No seal exits fourth-session open.",
        "fees": {"commission_each_side": COMMISSION, "minimum_each_side_cny": MIN_FEE,
                 "transfer_each_side": TRANSFER, "stamp_on_sell": STAMP_SELL},
        "capital": "CNY 1m initial; at most five positions; one purchase per initial limit-run event; each new position targets one fifth of marked equity at open, board lots; sell at open before buy; no leverage",
        "corporate_actions": "Vendor previous-close/raw-close ratio is used as a reinvested total-return share factor on ex-rights/ex-dividend days; exact distribution cash and tax are unavailable.",
        "limitations": "Historical bar/open prices are fill proxies; no order book, spread, slippage, intraminute sequence, suspension exit or market impact. Previously explored May–July dates are not a blind holdout.",
    }
    result["source_sha256"] = {"features": hashlib.sha256((source / "features.csv.gz").read_bytes()).hexdigest(),
                               "labels": hashlib.sha256((source / "labels.csv.gz").read_bytes()).hexdigest()}
    result["provenance"] = {
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "daily_month_sha256": {
            f"2026-{month:02}": hashlib.sha256((source / "price_months" /
                f"price_2026-{month:02}.csv.gz").read_bytes()).hexdigest()
            for month in range(4, 9)},
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    out.mkdir(parents=True, exist_ok=True)
    picks[[*KEY, "score", "rank", "rate"]].to_csv(out / "signals.csv.gz", index=False, compression="gzip")
    paths.to_csv(out / "common_trade_paths.csv.gz", index=False, compression="gzip")
    curve.to_csv(out / "portfolio_equity.csv", index=False)
    completed.to_csv(out / "portfolio_trades.csv", index=False)
    (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


if __name__ == "__main__":
    run()

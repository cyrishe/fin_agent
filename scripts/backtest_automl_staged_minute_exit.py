"""Replay a four-rule next-morning exit on observed one-minute closes."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.backtest_automl_tail_top2_cash import buy_quantity


OLD = Path("docs/stock_automl_runs/20261009_second_high_regression")
NEW = Path("docs/stock_automl_runs/20261010_second_high_four_class")
OUT = Path("docs/stock_automl_runs/20261010_staged_minute_exit")
START_CASH = Decimal("100000")
MINUTES = tuple(f"09:{minute:02}" for minute in range(31, 41))


def _validate_prices(entry: float, closes: list[float]) -> None:
    if len(closes) != 10 or not np.isfinite(closes).all() or min(closes) <= 0 or entry <= 0:
        raise ValueError("Need ten positive minute closes and a positive entry")


def exit_on_closes(entry: float, closes: list[float], *,
                   allow_rebound: bool = True) -> dict:
    """Return the first rule that fires; optionally omit the rebound rule."""
    _validate_prices(entry, closes)
    previous_low_return = None
    for index, close in enumerate(closes):
        current_return = close / entry - 1
        if index < 3 and current_return > .03:
            rule = "first3_over_3pct"
        elif (allow_rebound and previous_low_return is not None
              and current_return-previous_low_return >= .01-1e-12):
            rule = "rebound_1pp_from_prior_low"
        elif index >= 3 and current_return > .01:
            rule = "last7_over_1pct"
        elif index == 9:
            rule = "0940_close"
        else:
            previous_low_return = (current_return if previous_low_return is None
                                   else min(previous_low_return, current_return))
            continue
        return {"exit_minute": MINUTES[index], "exit_price": float(close),
                "exit_return": float(current_return), "rule": rule,
                "prior_low_return": previous_low_return}
    raise AssertionError("09:40 fallback must exit")


def exit_at_0940(entry: float, closes: list[float]) -> dict:
    """Hold through 09:40 and sell at its observed one-minute close."""
    _validate_prices(entry, closes)
    close = float(closes[-1])
    return {"exit_minute": "09:40", "exit_price": close,
            "exit_return": float(close/entry-1), "rule": "always_0940_close",
            "prior_low_return": None}


def run() -> dict:
    closes = pd.read_csv(OLD / "daily_top2_minute_close_returns.csv", dtype={"symbol6": str})
    old = pd.read_csv(OLD / "daily_top2_open_review.csv", dtype={"symbol6": str})
    new = pd.read_csv(NEW / "daily_top2.csv", dtype={"symbol6": str})
    base = pd.read_csv("docs/stock_automl_runs/20261009_close9_target/exact_1440_to_close9_candidates.csv.gz",
                       dtype={"symbol6": str})
    first = pd.read_csv("docs/stock_automl_runs/20261009_close10_target/exact_0931_closes.csv.gz",
                        dtype={"symbol6": str})
    close_columns = [f"close_{i}" for i in range(31, 41)]
    complete = base.merge(first, on=["next_date", "symbol6"], validate="one_to_one")
    if len(complete) != 14292 or not np.isfinite(complete[close_columns].to_numpy()).all():
        raise ValueError("Exact archived minute closes are incomplete")
    archived = old[["signal_date", "next_date", "symbol6"]].merge(
        complete[["signal_date", "next_date", "symbol6", "entry_1440", *close_columns]],
        on=["signal_date", "next_date", "symbol6"], validate="one_to_one")
    archived_returns = archived[close_columns].div(archived.entry_1440, axis=0).sub(1)
    archived_returns.index = pd.MultiIndex.from_frame(archived[["signal_date", "symbol6"]])
    saved_returns = closes.set_index(["signal_date", "symbol6"])[list(MINUTES)]
    if not np.allclose(archived_returns.sort_index().to_numpy(),
                       saved_returns.sort_index().to_numpy()):
        raise ValueError("Minute close source disagrees with previous archived top-two paths")
    selections = {
        "previous_second_high_regression": old.assign(model="previous_second_high_regression"),
        "four_class_logistic": new[new.model.eq("logistic")],
        "four_class_boosted": new[new.model.eq("small_boosted_tree")],
    }
    summaries = {}
    daily_rows, trade_rows = [], []
    for name, picks in selections.items():
        data = picks[["signal_date", "next_date", "symbol6", "name", "rank"]].merge(
            complete[["signal_date", "next_date", "symbol6", "entry_1440", *close_columns]],
            on=["signal_date", "next_date", "symbol6"], validate="one_to_one")
        if len(data) != 40 or data.signal_date.nunique() != 20:
            raise ValueError(f"Expected two picks on each of 20 days: {name}")
        cash = START_CASH
        close_0940_cash = START_CASH
        peak = cash
        worst_drawdown = Decimal(0)
        group_trades = []
        for day, group in data.groupby("signal_date", sort=True):
            before = cash
            close_0940_before = close_0940_cash
            if sorted(group["rank"].tolist()) != [1, 2]:
                raise ValueError("Missing daily ranks")
            for row in group.sort_values("rank").itertuples(index=False):
                result = exit_on_closes(row.entry_1440, [getattr(row, c) for c in close_columns])
                budget = before * (Decimal("0.6") if row.rank == 1 else Decimal("0.4"))
                entry = Decimal(str(row.entry_1440))
                sell = Decimal(str(result["exit_price"]))
                shares = buy_quantity(budget, entry, row.symbol6)
                pnl = (sell-entry)*shares
                cash += pnl
                baseline_budget = close_0940_before * (
                    Decimal("0.6") if row.rank == 1 else Decimal("0.4"))
                baseline_shares = buy_quantity(baseline_budget, entry, row.symbol6)
                close_0940_cash += (Decimal(str(row.close_40))-entry)*baseline_shares
                trade = {"model": name, "signal_date": day, "next_date": row.next_date,
                         "rank": row.rank, "symbol6": row.symbol6, "name": row.name,
                         "entry_1440": row.entry_1440, "shares": shares,
                         "exit_minute": result["exit_minute"], "exit_price": result["exit_price"],
                         "exit_return": result["exit_return"], "exit_rule": result["rule"],
                         "prior_low_return": result["prior_low_return"],
                         "pnl": float(pnl)}
                trade_rows.append(trade)
                group_trades.append(trade)
            peak = max(peak, cash)
            worst_drawdown = min(worst_drawdown, cash/peak-1)
            daily_rows.append({"model": name, "signal_date": day,
                               "next_date": group.next_date.iloc[0], "cash_before": float(before),
                               "daily_pnl": float(cash-before), "cash_after": float(cash)})
        selected = pd.DataFrame(group_trades)
        summaries[name] = {"signal_days": 20, "selected": len(selected),
                           "bought": int(selected.shares.gt(0).sum()),
                           "rule_counts": {str(k): int(v) for k, v in
                                           selected.exit_rule.value_counts().items()},
                           "mean_selected_return_pct": float(selected.exit_return.mean()*100),
                           "median_selected_return_pct": float(selected.exit_return.median()*100),
                           "selected_below_zero": int(selected.exit_return.lt(0).sum()),
                           "ending_cash": float(cash), "gross_cash_return": float(cash/START_CASH-1),
                           "always_0940_ending_cash": float(close_0940_cash),
                           "always_0940_gross_cash_return": float(close_0940_cash/START_CASH-1),
                           "max_daily_drawdown": float(worst_drawdown)}
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(trade_rows).to_csv(OUT / "trades.csv", index=False)
    pd.DataFrame(daily_rows).to_csv(OUT / "daily.csv", index=False)
    summary = {"rule": "09:31–09:33 close > entry×1.03; otherwise any later close >= prior observed minimum close return + 1 percentage point; otherwise 09:34–09:40 close > entry×1.01; otherwise 09:40 close. Each exit uses that minute close.",
               "execution": "Historical minute-close proxy; T 14:40 close entry and next-morning close exits are not verified fills. No fees, slippage, or order-book constraints.",
               "capital": "100000 CNY; rank 1 gets 60%, rank 2 gets 40%; existing board-lot buy_quantity; remaining allocation stays cash",
               "results": summaries,
               "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
                   OLD / "daily_top2_open_review.csv", OLD / "daily_top2_minute_close_returns.csv",
                   NEW / "daily_top2.csv",
                   Path("docs/stock_automl_runs/20261009_close9_target/exact_1440_to_close9_candidates.csv.gz"),
                   Path("docs/stock_automl_runs/20261009_close10_target/exact_0931_closes.csv.gz"))}}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(run()["results"], ensure_ascii=False, indent=2))

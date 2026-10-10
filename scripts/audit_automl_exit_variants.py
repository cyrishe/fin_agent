"""Replay three next-morning exits on unchanged four/five-class stock picks."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from scripts.audit_automl_four_class_decisions import BASE, FIRST
from scripts.backtest_automl_staged_minute_exit import exit_at_0940, exit_on_closes
from scripts.experiment_automl_second_high_five_class import OUT


FOUR = OUT.parent / "20261010_second_high_four_class"
CLOSES = [f"close_{minute:02}" for minute in range(31, 41)]


def load_selected() -> pd.DataFrame:
    five = pd.read_csv(OUT / "daily_top2.csv.gz", dtype={"symbol6": str})
    five = five[five.scope.eq("next_day_test")].copy()
    five["selection_rule"] = five.selection_score
    four = pd.read_csv(FOUR / "class_priority_fallback_top2.csv",
                       dtype={"symbol6": str})
    four["model"] = "four_class_small_tree"
    four["selection_rule"] = "class_priority_fallback"
    selected = pd.concat([five, four], ignore_index=True)
    groups = selected.groupby(["model", "selection_rule"])
    if len(groups) != 7 or not all(
            len(group) == 40 and group.signal_date.nunique() == 20
            and group.groupby("signal_date").size().eq(2).all()
            for _, group in groups):
        raise ValueError("Expected seven fixed pick lists of two stocks on 20 days")
    return selected


def with_minute_closes(selected: pd.DataFrame) -> pd.DataFrame:
    base = pd.read_csv(BASE, dtype={"symbol6": str})
    first = pd.read_csv(FIRST, dtype={"symbol6": str})
    rows = selected.merge(
        base[["signal_date", "next_date", "symbol6", "entry_1440", *CLOSES[1:]]],
        on=["signal_date", "next_date", "symbol6"], validate="many_to_one",
        suffixes=("", "_source")).merge(
            first[["next_date", "symbol6", CLOSES[0]]],
            on=["next_date", "symbol6"], validate="many_to_one")
    if len(rows) != len(selected) or not np.allclose(
            rows.entry_1440, rows.entry_1440_source):
        raise ValueError("Selected entries do not match exact saved minute paths")
    return rows


def replay(rows: pd.DataFrame) -> pd.DataFrame:
    records = []
    for _, row in rows.iterrows():
        prices = [row[column] for column in CLOSES]
        variants = {
            "original_four_rules": exit_on_closes(row.entry_1440, prices),
            "no_rebound": exit_on_closes(
                row.entry_1440, prices, allow_rebound=False),
            "always_0940": exit_at_0940(row.entry_1440, prices),
        }
        for strategy, result in variants.items():
            records.append({
                "model": row.model, "selection_rule": row.selection_rule,
                "signal_date": row.signal_date, "next_date": row.next_date,
                "symbol6": row.symbol6, "name": row["name"],
                "selection_rank": int(row.selection_rank),
                "entry_1440": float(row.entry_1440),
                "exit_strategy": strategy, **result,
            })
    return pd.DataFrame(records)


def summarize(trades: pd.DataFrame) -> list[dict]:
    summaries = []
    for (model, rule, strategy), group in trades.groupby(
            ["model", "selection_rule", "exit_strategy"], sort=True):
        summaries.append({
            "model": model, "selection_rule": rule, "exit_strategy": strategy,
            "trades": len(group), "days": int(group.signal_date.nunique()),
            "mean_trade_return_pct": float(group.exit_return.mean()*100),
            "median_trade_return_pct": float(group.exit_return.median()*100),
            "positive": int(group.exit_return.gt(0).sum()),
            "negative": int(group.exit_return.lt(0).sum()),
            "below_minus1": int(group.exit_return.lt(-.01).sum()),
            "rule_counts": {str(key): int(value) for key, value in
                            group.rule.value_counts().items()},
        })
    return summaries


def run() -> list[dict]:
    trades = replay(with_minute_closes(load_selected()))
    summaries = summarize(trades)
    if len(trades) != 840 or len(summaries) != 21:
        raise ValueError("Expected 280 picks replayed under three exit rules")
    old = json.loads((FOUR / "class_priority_fallback_summary.json").read_text())
    prior = json.loads((OUT / "summary.json").read_text())
    for row in summaries:
        if row["exit_strategy"] != "original_four_rules":
            continue
        if row["model"] == "four_class_small_tree":
            expected = old["fallback_minute_close_exit"]["mean_return_pct"]
        else:
            expected = prior["models"][row["model"]]["next_day_test"]["top2"][
                row["selection_rule"]]["minute_close_exit_mean_return_pct"]
        if not np.isclose(row["mean_trade_return_pct"], expected, atol=1e-10):
            raise ValueError("Original exit does not reproduce the saved baseline")
    trades.to_csv(OUT / "exit_strategy_trades.csv", index=False)
    (OUT / "exit_strategy_summary.json").write_text(
        json.dumps({"scope": "Same 280 selected stock-days across seven pick lists; each replayed under three exits. Minute close price proxy, before costs.",
                    "strategies": {
                        "original_four_rules": "first 3 >3%; rebound >=1 percentage point; last 7 >1%; otherwise 09:40 close",
                        "no_rebound": "first 3 >3%; last 7 >1%; otherwise 09:40 close",
                        "always_0940": "09:40 minute close for every selected stock",
                    }, "results": summaries}, ensure_ascii=False, indent=2)+"\n")
    return summaries


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))

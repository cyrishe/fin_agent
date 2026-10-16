"""Audit cross-date adjusted-price resets in next-open features and labels.

Read-only 1000-stock history. Compares the original cross-date adjusted ratios
with same-session open/preclose and close/preclose ratios, which remain on one
price scale when the source's adjustment factor is rebased between dates.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from scripts.experiment_automl_next_open_high1 import PERIODS
from scripts.experiment_automl_next_open_weekly import END, START, TRAIN_START, _load_batches
from src.quant_research.automl.config import ResearchSpec
from src.quant_research.automl.data import _market_calendar, kingdom_connection, select_symbols


ROOT = Path("outputs/stock_automl/next_open_targets")


def build_panel():
    with kingdom_connection() as conn:
        spec = ResearchSpec(start=TRAIN_START, end=END, max_symbols=1000, seed=20261007)
        symbols = select_symbols(conn, {}, spec)
        calendar = pd.DatetimeIndex(pd.to_datetime(_market_calendar(conn, START, END)))
        price = _load_batches(conn, "stk_code AS symbol, trade_date AS date, open, close, preclose, "
                              "adjopen, adjclose, adjpreclose, amount, update_time",
                              "kcrp_stock_price", symbols)
    price.date = pd.to_datetime(price.date)
    price = price.sort_values(["symbol", "date"]).reset_index(drop=True)
    for col in ("open", "close", "preclose", "adjopen", "adjclose", "adjpreclose", "amount"):
        price[col] = pd.to_numeric(price[col], errors="coerce")
    next_day = dict(zip(calendar[:-1], calendar[1:]))
    group = price.groupby("symbol", sort=False)
    price["previous_adjclose"] = group.adjclose.shift(1)
    price["adjustment_rebase_ratio"] = price.adjpreclose / price.previous_adjclose
    price["rebase_over_10pct"] = price.adjustment_rebase_ratio.gt(1.1) | price.adjustment_rebase_ratio.lt(1 / 1.1)
    price["old_return1"] = group.adjclose.pct_change(1, fill_method=None)
    price["old_return5"] = group.adjclose.pct_change(5, fill_method=None)
    price["old_return20"] = group.adjclose.pct_change(20, fill_method=None)
    price["clean_return1"] = price.close / price.preclose - 1
    log_return = np.log1p(price.clean_return1)
    for n in (5, 20):
        price[f"clean_return{n}"] = np.expm1(log_return.groupby(price.symbol).transform(
            lambda s: s.rolling(n, min_periods=n).sum()))
    price["recent20_rebase"] = price.rebase_over_10pct.astype(int).groupby(price.symbol).transform(
        lambda s: s.rolling(20, min_periods=20).sum())
    price["signal_date"] = price.date.map(next_day)
    cutoff = price.signal_date + pd.Timedelta(hours=14, minutes=45)
    updated = pd.to_datetime(price.update_time)
    history_max = updated.astype("int64").groupby(price.symbol).transform(
        lambda s: s.rolling(21, min_periods=21).max())
    price["price_ready"] = updated.le(cutoff)
    price["history_ready"] = history_max.le(cutoff.astype("int64"))
    previous = price[["symbol", "signal_date", "amount", "price_ready", "history_ready",
                      "old_return1", "old_return5", "old_return20", "clean_return1",
                      "clean_return5", "clean_return20", "recent20_rebase"]]
    current = price[["symbol", "date", "adjclose"]].rename(
        columns={"date": "signal_date", "adjclose": "current_adjclose"})
    current["next_date"] = current.signal_date.map(next_day)
    following = price[["symbol", "date", "adjopen", "adjpreclose", "open", "preclose"]].rename(
        columns={"date": "next_date", "adjopen": "next_adjopen",
                 "adjpreclose": "next_adjpreclose", "open": "next_open", "preclose": "next_preclose"})
    labels = current.merge(following, on=["symbol", "next_date"], how="left", validate="one_to_one")
    labels["old_gap"] = labels.next_adjopen / labels.current_adjclose - 1
    labels["clean_gap"] = labels.next_open / labels.next_preclose - 1
    labels["clean_adj_gap"] = labels.next_adjopen / labels.next_adjpreclose - 1
    panel = previous.merge(labels[["symbol", "signal_date", "old_gap", "clean_gap", "clean_adj_gap"]],
                           on=["symbol", "signal_date"], how="left", validate="one_to_one")
    panel = panel[panel.signal_date.notna() & panel.price_ready & panel.history_ready &
                  panel.amount.gt(1e7) & panel.old_return20.notna() &
                  panel.old_gap.notna() & np.isfinite(panel.old_gap)].copy()
    expected = {"train": 234122, "validation": 117737, "test": 132713,
                "later_check": 39097}
    for name, (first, last) in PERIODS.items():
        actual = int(panel.signal_date.between(first, last).sum())
        if actual != expected[name]:
            raise ValueError(f"eligible panel changed: {name} {actual} != {expected[name]}")
    return price, panel


def summarize(frame):
    old1, clean1 = frame.old_gap.gt(.01), frame.clean_gap.gt(.01)
    old2, clean2 = frame.old_gap.gt(.02), frame.clean_gap.gt(.02)
    return {"rows": len(frame),
            "clean_gap_missing": int(frame.clean_gap.isna().sum()),
            "clean_prior_return20_missing": int(frame.clean_return20.isna().sum()),
            "rebase_in_prior20": int(frame.recent20_rebase.gt(0).sum()),
            "prior_return1_difference_over_5pp": int((frame.old_return1 - frame.clean_return1).abs().gt(.05).sum()),
            "prior_return5_difference_over_5pp": int((frame.old_return5 - frame.clean_return5).abs().gt(.05).sum()),
            "prior_return20_difference_over_5pp": int((frame.old_return20 - frame.clean_return20).abs().gt(.05).sum()),
            "old_high1": int(old1.sum()), "clean_high1": int(clean1.sum()),
            "high1_label_flips": int(old1.ne(clean1).sum()),
            "old_high2": int(old2.sum()), "clean_high2": int(clean2.sum()),
            "high2_label_flips": int(old2.ne(clean2).sum()),
            "max_clean_raw_vs_adj_gap_difference": float(
                (frame.clean_gap - frame.clean_adj_gap).abs().max())}


def main():
    load_dotenv(".env")
    price, panel = build_panel()
    report = {"source_price_rows": len(price),
              "rebase_events_over_10pct": int(price.rebase_over_10pct.sum()),
              "rebase_events_over_2x": int((price.adjustment_rebase_ratio.gt(2) |
                                            price.adjustment_rebase_ratio.lt(.5)).sum()),
              "by_period": {name: summarize(panel[panel.signal_date.between(first, last)])
                            for name, (first, last) in PERIODS.items()}}
    old_selected = pd.read_csv(ROOT / "selected_signals.csv", dtype={"symbol": str})
    old_selected["signal_date"] = pd.to_datetime(old_selected.decision_date)
    old_selected = old_selected.merge(panel[["symbol", "signal_date", "old_gap", "clean_gap",
                                             "recent20_rebase", "old_return5", "clean_return5"]],
                                      on=["symbol", "signal_date"], validate="one_to_one")
    report["frozen_high2_selections"] = {
        "n": len(old_selected), "high2_old": int(old_selected.old_gap.gt(.02).sum()),
        "high2_clean": int(old_selected.clean_gap.gt(.02).sum()),
        "prior20_rebase": int(old_selected.recent20_rebase.gt(0).sum()),
        "prior_return5_difference_over_5pp": int((old_selected.old_return5 -
                                                    old_selected.clean_return5).abs().gt(.05).sum())}
    high1 = pd.read_csv(ROOT / "high1_selected_diagnostics.csv", dtype={"symbol": str})
    high1.signal_date = pd.to_datetime(high1.signal_date)
    high1 = high1.merge(panel[["symbol", "signal_date", "old_gap", "clean_gap",
                               "recent20_rebase", "old_return5", "clean_return5"]],
                        on=["symbol", "signal_date"], validate="many_to_one")
    selected_stats = {}
    for (scope, model, period), part in high1.groupby(["scope", "model", "period"]):
        key = f"{scope}/{model}/{period}"
        selected_stats[key] = {"n": len(part), "old_high1": int(part.old_gap.gt(.01).sum()),
            "clean_high1": int(part.clean_gap.gt(.01).sum()),
            "label_flips": int(part.old_gap.gt(.01).ne(part.clean_gap.gt(.01)).sum()),
            "prior20_rebase": int(part.recent20_rebase.gt(0).sum()),
            "prior_return5_difference_over_5pp": int((part.old_return5 -
                                                         part.clean_return5).abs().gt(.05).sum())}
    report["high1_selected"] = selected_stats
    ROOT.mkdir(parents=True, exist_ok=True)
    output = ROOT / "price_adjustment_audit.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", output, flush=True)
    print("rebases", report["rebase_events_over_10pct"],
          "frozen high2", report["frozen_high2_selections"], flush=True)
    for name, stats in report["by_period"].items():
        print(name, stats, flush=True)
    for key, stats in selected_stats.items():
        if key.endswith("/later_check"):
            print(key, stats, flush=True)


if __name__ == "__main__":
    main()

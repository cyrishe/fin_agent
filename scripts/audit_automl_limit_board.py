"""Audit frozen next-open signals against the source limit-up flag and simple rules.

Read-only KingdomAI query. Research outputs stay in the ignored local outputs tree.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from scripts.experiment_automl_next_open_weekly import (
    END, START, TEST_END, TEST_START, TRAIN_END, TRAIN_START,
    VALID_END, VALID_START, _load_batches, weekly_policy,
)
from scripts.experiment_automl_next_open_targets import LATER_END, LATER_START
from src.quant_research.automl.config import ResearchSpec
from src.quant_research.automl.data import _market_calendar, kingdom_connection, select_symbols


PERIODS = (("validation", VALID_START, VALID_END),
           ("test", TEST_START, TEST_END), ("later_check", LATER_START, LATER_END))


def rate(frame):
    return {"n": len(frame), "high2": int(frame.gap.gt(.02).sum()),
            "high2_rate": float(frame.gap.gt(.02).mean()) if len(frame) else None,
            "low0_rate": float(frame.gap.lt(0).mean()) if len(frame) else None}


def build_price_panel():
    with kingdom_connection() as conn:
        spec = ResearchSpec(start=TRAIN_START, end=END, max_symbols=1000, seed=20261007)
        symbols = select_symbols(conn, {}, spec)
        calendar = pd.DatetimeIndex(pd.to_datetime(_market_calendar(conn, START, END)))
        price = _load_batches(conn, "stk_code AS symbol, trade_date AS date, open, preclose, adjopen, adjclose, "
                              "amount, is_limit_price, update_time", "kcrp_stock_price", symbols)
    price.date = pd.to_datetime(price.date)
    price = price.sort_values(["symbol", "date"]).reset_index(drop=True)
    for col in ("open", "preclose", "adjopen", "adjclose", "amount", "is_limit_price"):
        price[col] = pd.to_numeric(price[col], errors="coerce")
    prev_market = dict(zip(calendar[1:], calendar[:-1]))
    next_market = dict(zip(calendar[:-1], calendar[1:]))
    group = price.groupby("symbol", sort=False)
    price["prior_quote_date"] = group.date.shift(1)
    price["prior_limit_flag"] = group.is_limit_price.shift(1)
    price["consecutive_prior_board"] = price.prior_quote_date.eq(price.date.map(prev_market)) & price.prior_limit_flag.eq(1)
    price["board_category"] = np.select(
        [price.is_limit_price.eq(1) & price.consecutive_prior_board,
         price.is_limit_price.eq(1)], ["repeat", "first"], default="non_limit")
    price["prior5_limit_count"] = price.is_limit_price.eq(1).astype(int).groupby(price.symbol).transform(
        lambda s: s.rolling(5, min_periods=5).sum())
    price["listing_board"] = np.select(
        [price.symbol.str.endswith(".BJ"), price.symbol.str.startswith("68"),
         price.symbol.str.startswith("30"), price.symbol.str.startswith("60"),
         price.symbol.str.startswith("00")],
        ["BJ", "STAR", "CHINEXT", "SH_MAIN", "SZ_MAIN"], default="OTHER")
    price["return20"] = group.adjclose.pct_change(20, fill_method=None)
    price["signal_date"] = price.date.map(next_market)
    cutoff = price.signal_date + pd.Timedelta(hours=14, minutes=45)
    updated = pd.to_datetime(price.update_time)
    history_updated = updated.astype("int64").groupby(price.symbol).transform(
        lambda s: s.rolling(21, min_periods=21).max())
    price["history_ready"] = history_updated.le(cutoff.astype("int64"))
    price["price_ready"] = updated.le(cutoff)
    previous = price[["symbol", "signal_date", "date", "amount", "is_limit_price",
                      "board_category", "prior5_limit_count", "listing_board", "return20",
                      "history_ready", "price_ready"]].rename(
        columns={"date": "previous_date", "amount": "previous_amount",
                 "is_limit_price": "previous_limit_flag"})
    today = price[["symbol", "date", "adjclose", "is_limit_price"]].rename(
        columns={"date": "signal_date", "adjclose": "label_close",
                 "is_limit_price": "signal_day_limit_flag"})
    today["next_date"] = today.signal_date.map(next_market)
    next_open = price[["symbol", "date", "adjopen", "open", "preclose"]].rename(
        columns={"date": "next_date", "adjopen": "label_next_open",
                 "open": "raw_next_open", "preclose": "raw_next_preclose"})
    labels = today.merge(next_open, on=["symbol", "next_date"], how="left", validate="one_to_one")
    labels["gap"] = labels.label_next_open / labels.label_close - 1
    labels["clean_gap"] = labels.raw_next_open / labels.raw_next_preclose - 1
    panel = previous.merge(labels[["symbol", "signal_date", "gap", "clean_gap",
                                   "signal_day_limit_flag"]],
                           on=["symbol", "signal_date"], how="left", validate="one_to_one")
    panel = panel[panel.signal_date.notna() & panel.price_ready & panel.history_ready &
                  panel.previous_amount.gt(1e7) & panel.return20.notna() &
                  panel.gap.notna() & np.isfinite(panel.gap)].copy()
    return panel.sort_values(["signal_date", "symbol"]).reset_index(drop=True)


def selected_day_matched(panel, selected):
    """Condition each selected signal on its date and exact T-1 board category."""
    pools = panel.groupby(["signal_date", "board_category"]).gap.agg(
        n="size", high2_rate=lambda x: x.gt(.02).mean(), low0_rate=lambda x: x.lt(0).mean())
    keyed = pd.MultiIndex.from_frame(selected[["signal_date", "board_category"]])
    matched = pools.reindex(keyed).reset_index(drop=True)
    if matched.n.isna().any():
        raise ValueError("missing selected-day board-category pool")
    return {"expected_high2_rate": float(matched.high2_rate.mean()),
            "expected_high2_count": float(matched.high2_rate.sum()),
            "expected_low0_rate": float(matched.low0_rate.mean()),
            "min_pool_size": int(matched.n.min()), "median_pool_size": float(matched.n.median())}


def simple_rule(panel, selection_days=None, selection_counts=None):
    """T-1 limit-up, highest T-1 turnover amount; no fitted weights."""
    candidates = panel[panel.previous_limit_flag.eq(1)].copy()
    if selection_days is None:
        return weekly_policy(candidates, candidates.previous_amount.to_numpy(), -np.inf)
    candidates = candidates[candidates.signal_date.isin(selection_days)].copy()
    candidates = candidates.sort_values(["signal_date", "previous_amount", "symbol"],
                                        ascending=[True, False, True])
    candidates["rank"] = candidates.groupby("signal_date").cumcount()
    candidates = candidates.merge(selection_counts.rename("quota"), left_on="signal_date",
                                  right_index=True, validate="many_to_one")
    return candidates[candidates["rank"].lt(candidates.quota)]


def main():
    load_dotenv(".env")
    panel = build_price_panel()
    selected_file = Path("outputs/stock_automl/next_open_targets/selected_signals.csv")
    selected = pd.read_csv(selected_file, dtype={"symbol": str})
    selected["signal_date"] = pd.to_datetime(selected.decision_date)
    selected = selected.merge(panel[["symbol", "signal_date", "gap", "previous_limit_flag",
                                     "board_category", "signal_day_limit_flag"]],
                              on=["symbol", "signal_date"], validate="one_to_one")
    if len(selected) != 116 or not np.allclose(selected.gap.to_numpy(),
                                                selected.next_open_gap_pct.to_numpy() / 100, atol=1e-6):
        raise ValueError("frozen model selections do not reconcile to source price panel")
    expected_rows = {"validation": 117737, "test": 132713, "later_check": 39097}
    result = {"scope": "2026-10-07 frozen 1000-symbol HGB selections; source is_limit_price flag",
              "selected_all": {"n": len(selected),
                               "previous_up": int(selected.previous_limit_flag.eq(1).sum()),
                               "previous_first": int(selected.board_category.eq("first").sum()),
                               "previous_repeat": int(selected.board_category.eq("repeat").sum()),
                               "signal_day_up_ex_post": int(selected.signal_day_limit_flag.eq(1).sum())},
              "periods": {}}
    for key, first, last in PERIODS:
        part = panel[panel.signal_date.between(first, last)].copy()
        if len(part) != expected_rows[key]:
            raise ValueError(f"eligible panel mismatch {key}: {len(part)} != {expected_rows[key]}")
        picks = selected[selected.signal_date.between(first, last)].copy()
        category = {name: rate(rows) for name, rows in part.groupby("board_category")}
        chosen_category = {name: rate(rows) for name, rows in picks.groupby("board_category")}
        quotas = picks.groupby("signal_date").size()
        amount_on_model_days = simple_rule(part, selection_days=set(quotas.index), selection_counts=quotas)
        full_rule = simple_rule(part)
        result["periods"][key] = {
            "pool_rows": len(part), "pool_rate": rate(part), "pool_by_board": category,
            "model": rate(picks), "model_by_board": chosen_category,
            "model_selected_day_board_matched": selected_day_matched(part, picks),
            "simple_prior_up_amount_same_model_days": rate(amount_on_model_days),
            "simple_prior_up_amount_weekly": rate(full_rule),
            "simple_weekly_active_weeks": int(full_rule.signal_date.dt.strftime("%G-W%V").nunique()),
            "model_active_weeks": int(picks.signal_date.dt.strftime("%G-W%V").nunique()),
        }
    output = Path("outputs/stock_automl/next_open_targets/limit_board_audit.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

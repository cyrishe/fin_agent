"""Compare >1% picks with random stocks on the same date and listing board.

Reads the same eligible 1000-symbol price panel. Previous-day and five-day
limit-up exclusions are reproduced before computing conditional base rates.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from scripts.audit_automl_limit_board import build_price_panel
from scripts.experiment_automl_next_open_high1 import PERIODS, TARGET


ROOT = Path("outputs/stock_automl/next_open_targets")


def main():
    load_dotenv(".env")
    panel = build_price_panel()
    panel["gap"] = panel.clean_gap
    if panel.gap.isna().any():
        raise ValueError("clean opening gap missing from eligible panel")
    selected = pd.read_csv(ROOT / "high1_consistent_selected_diagnostics.csv", dtype={"symbol": str})
    selected.signal_date = pd.to_datetime(selected.signal_date)
    selected = selected.merge(panel[["signal_date", "symbol", "gap", "listing_board",
                                     "prior5_limit_count", "previous_limit_flag"]],
        on=["signal_date", "symbol"], suffixes=("", "_source"), validate="many_to_one")
    if not np.allclose(selected.gap.to_numpy(), selected.gap_source.to_numpy(), atol=1e-8):
        raise ValueError("selected labels disagree with rebuilt price panel")
    if selected.listing_board.eq("OTHER").any():
        raise ValueError("unclassified listing board")
    report = {"target": "next open gap >1%", "results": {}, "pool_by_board": {}}
    for period, (start, end) in PERIODS.items():
        pool = panel[panel.signal_date.between(start, end)]
        report["pool_by_board"][period] = {name: {"n": len(part),
             "high1_rate": float(part.gap.gt(TARGET).mean())}
             for name, part in pool.groupby("listing_board")}
    for (scope, model, period), chosen in selected.groupby(["scope", "model", "period"]):
        start, end = PERIODS[period]
        pool = panel[panel.signal_date.between(start, end)]
        pool = pool[pool.previous_limit_flag.ne(1)] if scope == "exclude_prior1_limit" else pool[
            pool.prior5_limit_count.eq(0)]
        base = pool.groupby(["signal_date", "listing_board"]).gap.agg(
            pool_n="size", high1_rate=lambda s: s.gt(TARGET).mean())
        day = pool.groupby("signal_date").gap.apply(lambda s: s.gt(TARGET).mean())
        matches = base.reindex(pd.MultiIndex.from_frame(
            chosen[["signal_date", "listing_board"]])).reset_index(drop=True)
        if matches.pool_n.isna().any():
            raise ValueError("selected board/date has no eligible comparison pool")
        weekly = chosen.assign(board_base=matches.high1_rate.to_numpy(),
                               hit=chosen.gap.gt(TARGET).astype(int),
                               week=chosen.signal_date.dt.strftime("%G-W%V")).groupby("week").agg(
            n=("hit", "size"), wins=("hit", "sum"), expected=("board_base", "sum"))
        rng = np.random.default_rng(42)
        draws = rng.integers(0, len(weekly), size=(1000, len(weekly)))
        sampled_n = weekly.n.to_numpy()[draws].sum(axis=1)
        weekly_excess = ((weekly.wins.to_numpy()[draws].sum(axis=1) -
                          weekly.expected.to_numpy()[draws].sum(axis=1)) / sampled_n)
        board_count = chosen.listing_board.value_counts().to_dict()
        board_hits = {board: {"signals": len(part), "wins": int(part.gap.gt(TARGET).sum())}
                      for board, part in chosen.groupby("listing_board")}
        ranked = chosen.sort_values(["signal_date", "score", "symbol"],
                                    ascending=[True, False, True]).copy()
        ranked["day_rank"] = ranked.groupby("signal_date").cumcount() + 1
        rank_hits = {str(rank): {"signals": len(part), "wins": int(part.gap.gt(TARGET).sum()),
                                "low_open": int(part.gap.lt(0).sum())}
                     for rank, part in ranked.groupby("day_rank")}
        month_hits = {month: {"signals": len(part), "wins": int(part.gap.gt(TARGET).sum())}
                      for month, part in chosen.groupby(chosen.signal_date.dt.strftime("%Y-%m"))}
        key = f"{scope}/{model}/{period}"
        report["results"][key] = {
            "signals": len(chosen), "unique_stocks": int(chosen.symbol.nunique()),
            "active_days": int(chosen.signal_date.nunique()),
            "active_weeks": int(chosen.signal_date.dt.strftime("%G-W%V").nunique()),
            "most_repeated_stock_signals": int(chosen.symbol.value_counts().iloc[0]),
            "high1": int(chosen.gap.gt(TARGET).sum()),
            "precision": float(chosen.gap.gt(TARGET).mean()),
            "same_day_base": float(chosen.signal_date.map(day).mean()),
            "same_day_board_base": float(matches.high1_rate.mean()),
            "same_day_board_expected_wins": float(matches.high1_rate.sum()),
            "week_bootstrap_board_excess_p10": float(np.quantile(weekly_excess, .1)),
            "min_board_pool_n": int(matches.pool_n.min()),
            "median_board_pool_n": float(matches.pool_n.median()),
            "listing_board_counts": board_count,
            "listing_board_hits": board_hits,
            "day_rank_hits": rank_hits,
            "month_hits": month_hits}
    output = ROOT / "high1_consistent_board_matched_audit.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", output, flush=True)
    for key, item in report["results"].items():
        if key.endswith("/test") or key.endswith("/later_check"):
            print(key, {k: item[k] for k in ("signals", "unique_stocks", "high1", "precision",
                   "same_day_base", "same_day_board_base", "listing_board_counts")}, flush=True)


if __name__ == "__main__":
    main()

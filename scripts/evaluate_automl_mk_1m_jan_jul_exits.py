"""Evaluate unchanged daily Top1 picks against three observed-minute exit rules."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_st_status import attach_st_status, load_st_intervals
from scripts.audit_automl_mk_1m_entry_limit import mark_entry_limit
from scripts.backtest_automl_staged_minute_exit import exit_at_0940, exit_on_closes
from scripts.benchmark_automl_1440_inference import db_connection, query


CLOSES = [f"next_close_{minute:02d}" for minute in range(31, 41)]
STRATEGIES = {
    "fixed_0940": exit_at_0940,
    "staged_four_rules": exit_on_closes,
    "staged_no_rebound": lambda entry, closes: exit_on_closes(
        entry, closes, allow_rebound=False),
}


def evaluate(picks: pd.DataFrame, rows: pd.DataFrame,
             intervals: pd.DataFrame, daily: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    if picks.duplicated(["arm", "signal_date"]).any() or picks.selection_rank.ne(1).any():
        raise ValueError("Expected exactly one Top1 per arm and signal day")
    observations = rows[["signal_date", "symbol6", "next_date", *CLOSES]]
    joined = picks.merge(observations, on=["signal_date", "symbol6"],
                         validate="many_to_one", suffixes=("", "_source"))
    if len(joined) != len(picks) or not joined.next_date.eq(joined.next_date_source).all():
        raise ValueError("Selected morning price paths are incomplete or mismatched")
    joined = attach_st_status(joined, intervals)
    joined["signal_date"] = joined.signal_date.dt.strftime("%Y-%m-%d")
    joined = joined.merge(daily, on=["signal_date", "symbol6"], how="left",
                          validate="many_to_one")
    joined = mark_entry_limit(joined.assign(preclose=joined.day_preclose))
    joined["historical_st"] = joined.st_type.isin(("S", "Y"))
    joined["historical_st_at_limit"] = (
        joined.historical_st & joined.is_limit_price.eq(1)
        & joined.day_close.gt(joined.day_preclose)
        & np.isclose(joined.signal_price, joined.day_close, rtol=0, atol=.005))
    trades = []
    for row in joined.itertuples(index=False):
        closes = [getattr(row, col) for col in CLOSES]
        morning_known = np.isfinite(closes).all()
        for strategy, rule in STRATEGIES.items():
            result = (rule(row.signal_price, closes) if morning_known else
                      {"exit_minute": None, "exit_price": np.nan,
                       "exit_return": np.nan, "rule": "unobservable"})
            trades.append({
                "arm": row.arm, "signal_date": row.signal_date,
                "next_date": row.next_date, "symbol6": row.symbol6,
                "name": row.name, "predicted_class": row.predicted_class,
                "p_ge3": row.p_ge3, "entry_1440": row.signal_price,
                "actual_second_high_return": row.actual_second_high_return,
                "historical_st_type": row.st_type,
                "entry_at_limit": row.entry_at_limit,
                "entry_status_known": row.entry_status_known,
                "missing_daily_limit_marker": pd.isna(row.is_limit_price),
                "historical_st_at_limit": row.historical_st_at_limit,
                "exit_strategy": strategy, "exit_minute": result["exit_minute"],
                "exit_rule": result["rule"], "exit_price": result["exit_price"],
                "exit_return": result["exit_return"],
            })
    result = pd.DataFrame(trades)
    summary = {}
    for (arm, strategy), group in result.groupby(["arm", "exit_strategy"]):
        ordered = group.sort_values("signal_date")
        returns = ordered.exit_return
        observed = returns.dropna()
        monthly = {}
        for month, month_rows in ordered.groupby(ordered.signal_date.str[:7]):
            month_returns = month_rows.exit_return.dropna()
            monthly[str(month)] = {
                "selected": len(month_rows), "observed": len(month_returns),
                "ge3": int(month_rows.exit_return.ge(.03).sum()),
                "mean_return_pct": float(month_returns.mean() * 100)
                if len(month_returns) else None,
                "compounded_return_pct": float(((1 + month_returns).prod() - 1) * 100)
                if len(month_returns) == len(month_rows) else None,
            }
        summary[f"{arm}/{strategy}"] = {
            "trades": len(group), "first": str(group.signal_date.min()),
            "last": str(group.signal_date.max()),
            "observed_exits": len(observed),
            "unobservable_exits": int(returns.isna().sum()),
            "mean_trade_return_pct": float(observed.mean() * 100)
            if len(observed) else None,
            "median_trade_return_pct": float(observed.median() * 100)
            if len(observed) else None,
            "positive": int(returns.gt(0).sum()),
            "ge3": int(returns.ge(.03).sum()),
            "negative": int(returns.lt(0).sum()),
            "below_minus1": int(returns.lt(-.01).sum()),
            "gross_compounded_return_pct": float(((1 + observed).prod() - 1) * 100)
            if len(observed) == len(group) else None,
            "historical_st": int(group.historical_st_type.isin(("S", "Y")).sum()),
            "entry_at_limit": int(group.entry_at_limit.sum()),
            "entry_status_unknown": int((~group.entry_status_known).sum()),
            "unknown_historical_st": int(group.historical_st_type.isna().sum()),
            "missing_daily_limit_marker": int(group.missing_daily_limit_marker.sum()),
            "historical_st_at_limit": int(group.historical_st_at_limit.sum()),
            "exit_rule_counts": {str(k): int(v) for k, v in group.exit_rule.value_counts().items()},
            "monthly": monthly,
        }
    return result, summary


def run(picks_path: Path, candidates_path: Path, output: Path) -> dict:
    picks = pd.read_csv(picks_path, dtype={"symbol6": str, "signal_date": str,
                                           "next_date": str})
    rows = pd.read_csv(candidates_path,
                       usecols=["signal_date", "symbol6", "next_date", *CLOSES],
                       dtype={"symbol6": str, "signal_date": str, "next_date": str})
    codes = sorted(set(picks.symbol6))
    symbols = [s + (".SH" if s.startswith("6") else ".SZ") for s in codes]
    placeholders = ",".join(["%s"] * len(symbols))
    with db_connection(Path(".env")) as db:
        intervals = load_st_intervals(db)
        daily = query(db, "SELECT trade_date AS signal_date, LEFT(stk_code,6) AS symbol6, "
                      "preclose AS day_preclose, close AS day_close, is_limit_price "
                      "FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s "
                      f"AND stk_code IN ({placeholders})",
                      (picks.signal_date.min(), picks.signal_date.max(), *symbols))
    daily["signal_date"] = pd.to_datetime(daily.signal_date).dt.strftime("%Y-%m-%d")
    for col in ("day_preclose", "day_close"):
        daily[col] = pd.to_numeric(daily[col])
    trades, summary = evaluate(picks, rows, intervals, daily)
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "top1_exit_trades.csv", index=False)
    (output / "exit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--picks", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.picks, args.candidates, args.output),
                     ensure_ascii=False, indent=2))

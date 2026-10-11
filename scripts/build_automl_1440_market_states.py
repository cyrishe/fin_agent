"""Reconstruct 14:40 market states for causal similar-day selection.

Historical minute bars are market-time proxies; live inference must use a
contemporaneous full-market quote batch with the same field definitions.
Returns cover each exchange's stocks; volume covers only the 3%-6% basket.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query


SOURCE = Path("outputs/stock_automl/tail_20261010_wide_025_065/candidates_1440.csv")
OUTPUT = Path("docs/stock_automl_runs/20261010_four_class_similar_days/market_states_1440.csv")


def exchange(code: str) -> str | None:
    code = str(code)[:6]
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    if code.startswith(("4", "8", "9")):
        return "bj"
    return None


def candidate_state(candidates: pd.DataFrame) -> pd.DataFrame:
    rows = candidates[candidates.signal_return.between(.03, .06, inclusive="both")].copy()
    rows["exchange"] = rows.symbol6.map(exchange)
    if rows.exchange.isna().any() or rows.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Candidate exchange or identity is invalid")
    daily = rows.groupby("signal_date").agg(candidate_count=("symbol6", "size"))
    groups = rows.groupby(["signal_date", "exchange"])
    by_exchange = (groups.minute_volume_shares.sum(min_count=1) / 100).unstack()
    volume_coverage = (groups.minute_volume_shares.count() /
                       groups.minute_volume_shares.size()).unstack()
    by_exchange = by_exchange.where(volume_coverage.ge(.95))
    by_exchange = by_exchange.rename(columns={name: f"{name}_volume_hands"
                                              for name in ("sh", "sz", "bj")})
    return daily.join(by_exchange).reset_index()


def market_state_day(conn, day: str) -> dict:
    # The T daily row is used only for its preclose field, a historical proxy
    # for the same-day reference price supplied by live quotes.
    quoted = query(conn, """
        SELECT stk_code AS symbol6, latest_price AS price
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time=%s AND is_finalized=1 AND is_fallback=0
    """, (day, f"{day} 14:40:00"))
    daily = query(conn, "SELECT LEFT(stk_code,6) AS symbol6, preclose "
                  "FROM kcrp_stock_price WHERE trade_date=%s", (day,))
    previous = query(conn, "SELECT MAX(trade_date) AS prior_date FROM kcrp_stock_price "
                     "WHERE trade_date<%s", (day,)).iloc[0].prior_date
    prior_universe = query(conn, "SELECT LEFT(stk_code,6) AS symbol6 "
                           "FROM kcrp_stock_price WHERE trade_date=%s", (previous,))
    prior_universe["exchange"] = prior_universe.symbol6.map(exchange)
    quoted = quoted.merge(daily, on="symbol6", how="inner", validate="one_to_one")
    quoted["exchange"] = quoted.symbol6.map(exchange)
    quoted["return_1440"] = (pd.to_numeric(quoted.price, errors="coerce") /
                              pd.to_numeric(quoted.preclose, errors="coerce") - 1)
    quoted = quoted[quoted.exchange.notna() & quoted.return_1440.notna()]
    if quoted.duplicated("symbol6").any():
        raise ValueError(f"Duplicate 14:40 quote on {day}")

    state = {"signal_date": day, "market_quote_count": len(quoted)}
    for group in ("sh", "sz", "bj"):
        q = quoted[quoted.exchange.eq(group)]
        universe_count = int(prior_universe.exchange.eq(group).sum())
        if len(q) < 50 and group != "bj":
            raise ValueError(f"Insufficient {group} 14:40 market data on {day}")
        # Several historical dates have only 16 Beijing-stock bars. They are
        # not a representative Beijing-market quote; preserve the gap.
        state[f"{group}_return"] = float(q.return_1440.mean()) if len(q) >= 50 else np.nan
        state[f"{group}_breadth"] = float(q.return_1440.gt(0).mean()) if len(q) >= 50 else np.nan
        state[f"{group}_quote_count"] = len(q)
        state[f"{group}_universe_count"] = universe_count
        state[f"{group}_coverage"] = len(q) / universe_count if universe_count else 0
    state["market_state_valid"] = (state["sh_coverage"] >= .95 and
                                    state["sz_coverage"] >= .95)
    return state


def run(source: Path, output: Path, env_file: Path,
        start_day: str | None = None, end_day: str | None = None) -> pd.DataFrame:
    candidates = pd.read_csv(source, dtype={"symbol6": str}, low_memory=False)
    selected = candidate_state(candidates)
    conn = db_connection(env_file)
    try:
        calendar = query(conn, "SELECT DISTINCT trade_date FROM kcrp_stock_price "
                         "WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date",
                         (start_day or str(candidates.signal_date.min()),
                          end_day or str(candidates.signal_date.max()))).trade_date.astype(str).tolist()
        states = []
        for day in calendar:
            states.append(market_state_day(conn, day))
            print(f"market state {day}: {states[-1]['market_quote_count']} quotes", flush=True)
    finally:
        conn.rollback()
        conn.close()
    result = pd.DataFrame(states).merge(selected, on="signal_date", how="left",
                                        validate="one_to_one")
    no_candidates = result.candidate_count.isna()
    result.loc[no_candidates, ["candidate_count", "sh_volume_hands",
                               "sz_volume_hands", "bj_volume_hands"]] = 0
    result["candidate_count"] = result.candidate_count.astype(int)
    result.loc[result.bj_quote_count.lt(50), "bj_volume_hands"] = np.nan
    if result.drop(columns=["bj_return", "bj_breadth", "bj_volume_hands",
                            "sh_volume_hands", "sz_volume_hands"]).isna().any().any():
        raise ValueError("Incomplete 14:40 market state")
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--start-day")
    parser.add_argument("--end-day")
    arguments = parser.parse_args()
    run(arguments.source, arguments.output, arguments.env_file,
        arguments.start_day, arguments.end_day)

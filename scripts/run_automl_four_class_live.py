"""Score a trading day's 14:40 quote batch with the eight-stage four-class flow.

Historical feature/label preparation and market states must first be refreshed
through the preceding trading day. This command does not place any orders.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import (
    FEATURES, db_connection, fetch_full_market, make_features, query,
    select_candidates,
)
from scripts.build_automl_1440_market_states import exchange
from scripts.run_automl_four_class_pipeline import (
    generate_training_features, generate_training_labels,
    infer, select_top, select_training_samples, similar_days, train_model,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def live_market_state(quotes: pd.DataFrame, day: str, prior_universe: pd.DataFrame) -> dict:
    """Use only the current full-market quote and yesterday's stock universe."""
    clean = quotes.copy()
    clean["exchange"] = clean.symbol6.map(exchange)
    clean["return_1440"] = clean.signal_price / clean.preclose - 1
    clean = clean[clean.exchange.notna() & np.isfinite(clean.return_1440)]
    candidates = select_candidates(clean)
    prior = prior_universe.copy()
    prior["exchange"] = prior.symbol6.map(exchange)
    state = {"signal_date": day, "candidate_count": len(candidates)}
    for group in ("sh", "sz", "bj"):
        q = clean[clean.exchange.eq(group)]
        c = candidates[candidates.exchange.eq(group)]
        universe_count = int(prior.exchange.eq(group).sum())
        state[f"{group}_quote_count"] = len(q)
        state[f"{group}_universe_count"] = universe_count
        state[f"{group}_coverage"] = len(q)/universe_count if universe_count else 0
        state[f"{group}_return"] = float(q.return_1440.mean()) if len(q) >= 50 else np.nan
        state[f"{group}_volume_hands"] = float(c.volume_hands.sum()) if len(q) >= 50 else np.nan
    state["market_state_valid"] = (state["sh_coverage"] >= .95 and
                                   state["sz_coverage"] >= .95)
    return state


def run(day: str, prepared: Path, market: Path, env_file: Path,
        output: Path) -> dict:
    if day != datetime.now(SHANGHAI).date().isoformat():
        raise ValueError("Live scoring day must be today's Shanghai date")
    historical = pd.read_csv(prepared, dtype={"symbol6": str}, low_memory=False)
    states = pd.read_csv(market)
    conn = db_connection(env_file)
    try:
        calendar = query(conn, "SELECT DISTINCT trade_date FROM kcrp_stock_price "
                         "WHERE trade_date<%s ORDER BY trade_date DESC LIMIT 41",
                         (day,)).trade_date.astype(str).tolist()
        if len(calendar) < 41:
            raise ValueError("Need 41 completed trading-day daily histories")
        prior_day = calendar[0]
        if states.signal_date.max() != prior_day or historical.signal_date.max() > prior_day:
            raise ValueError("Refresh labeled candidates and market states through T-1")
        prior_candidates = int(states.loc[states.signal_date.eq(prior_day),
                                         "candidate_count"].iloc[0])
        if prior_candidates and prior_day not in set(historical.signal_date):
            raise ValueError("T-1 has market candidates but no prepared stock rows")
        if not set(calendar[:20]).issubset(set(states.signal_date)):
            raise ValueError("The previous 20 trading days are absent from market states")
        universe = query(conn, "SELECT LEFT(stk_code,6) AS symbol6, stk_code "
                         "FROM kcrp_stock_price WHERE trade_date=%s", (prior_day,))
        codes = sorted(universe.symbol6.unique())
        quotes, metadata = fetch_full_market(codes)
        start = datetime.fromisoformat(metadata["request_started_at"])
        end = datetime.fromisoformat(metadata["request_ended_at"])
        begin = datetime.fromisoformat(f"{day}T14:40:00+08:00")
        deadline = datetime.fromisoformat(f"{day}T14:41:00+08:00")
        if not (begin <= start and end <= deadline):
            raise ValueError("Full-market quote batch was not completed in the 14:40 window")
        if len(quotes) != len(codes):
            raise ValueError("Full-market quote batch is incomplete")
        current_state = live_market_state(quotes, day, universe)
        if not current_state["market_state_valid"]:
            raise ValueError("Current Shanghai/Shenzhen quote coverage is insufficient")
        all_states = pd.concat([states, pd.DataFrame([current_state])], ignore_index=True)
        recent = calendar[:20]
        similar, distances = similar_days(all_states, day)

        available = select_candidates(quotes)
        available = available[available.symbol6.map(exchange).notna()]
        if available.empty:
            selections = {"recent20": [], "similar20": []}
            scores = pd.DataFrame()
        else:
            stock_codes = universe[universe.symbol6.isin(available.symbol6)].stk_code.tolist()
            placeholders = ",".join(["%s"] * len(stock_codes))
            history = query(conn, "SELECT trade_date,stk_code,close,adjclose,adjpreclose,volume "
                            "FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s "
                            f"AND stk_code IN ({placeholders})",
                            (calendar[20], prior_day, *stock_codes))
            value = query(conn, "SELECT stk_code,float_mv,float_share "
                          "FROM kcrp_stock_pricevaluate WHERE trade_date=%s "
                          f"AND stk_code IN ({placeholders})", (prior_day, *stock_codes))
            candidates, exclusions = make_features(
                available, history, value, list(reversed(calendar[:21])))
            if candidates.empty:
                selections = {"recent20": [], "similar20": []}
                scores = pd.DataFrame()
            else:
                candidates = candidates.merge(
                    available[["symbol6", "signal_price"]], on="symbol6",
                    validate="one_to_one")
                candidates["signal_date"] = day
                candidates["next_date"] = None
                scored = []
                selections = {}
                for method, dates in (("recent20", recent), ("similar20", similar)):
                    chosen = select_training_samples(historical, dates)
                    if not (chosen.next_date <= day).all():
                        raise ValueError("A training label is not mature by 14:40")
                    model = train_model(generate_training_features(chosen),
                                        generate_training_labels(chosen))
                    frame = infer(model, candidates)
                    frame["method"] = method
                    selected = select_top(frame)
                    selections[method] = selected[["symbol6", "name", "signal_price",
                                                   "signal_return",
                                                   "selection_rank", "predicted_class",
                                                   "p_lt0", "p_0to1", "p_1to3",
                                                   "p_ge3"]].to_dict("records")
                    scored.append(frame)
                scores = pd.concat(scored, ignore_index=True)
        output.mkdir(parents=True, exist_ok=True)
        if not scores.empty:
            scores.to_csv(output / f"{day}_all_predictions.csv.gz", index=False,
                          compression="gzip")
        result = {"signal_date": day, "prior_day": prior_day,
                  "quote": metadata, "market_state": current_state,
                  "recent20_dates": recent, "similar20_dates": similar,
                  "similarity_distances": distances.head(20).to_dict("records"),
                  "candidates_3_to_6": len(available),
                  "feature_complete": len(candidates) if len(available) else 0,
                  "selections": selections,
                  "orders_placed": 0}
        (output / f"{day}_decision.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str)+"\n")
        return result
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", default=datetime.now(SHANGHAI).date().isoformat())
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--market", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    folder = arguments.output or Path("outputs/stock_automl/four_class_live") / arguments.day
    prepared = arguments.prepared or folder / "prepared_candidates.csv.gz"
    market = arguments.market or folder / "market_states_1440.csv"
    print(json.dumps(run(arguments.day, prepared, market,
                         arguments.env_file, folder), ensure_ascii=False,
                     indent=2, default=str))

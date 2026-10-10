"""Compare fixed exits and lagged modal best exits of all qualifying stocks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.audit_automl_four_class_decisions import FIRST
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_second_high_regression import SECOND_HIGHS


OCT = Path("docs/stock_automl_runs/20261010_second_high_four_class_oct9")
WEIGHTED = Path("docs/stock_automl_runs/20261010_second_high_four_class_weighted")
OUT = Path("docs/stock_automl_runs/20261010_second_high_sell_minute_mode")
SLOTS = ("open_0931",) + tuple(f"close_{minute:02}" for minute in range(31, 41))


def load_cohorts() -> pd.DataFrame:
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    first = pd.read_csv(FIRST, dtype={"symbol6": str})
    prices = pd.read_csv(SECOND_HIGHS, dtype={"symbol6": str})
    dates = sorted(base.signal_date.unique())[-20:]
    historical = base[base.signal_date.isin(dates)].copy().merge(
        first[["next_date", "symbol6", "close_31"]],
        on=["next_date", "symbol6"], validate="one_to_one").merge(
        prices[["next_date", "symbol6", "second_high"]],
        on=["next_date", "symbol6"], validate="one_to_one")
    historical["second_high_return"] = (
        historical.second_high/historical.entry_1440-1)
    oct8 = pd.read_csv(OCT / "predictions_with_outcomes.csv", dtype={"symbol6": str})
    oct8 = oct8[oct8.model.eq("unweighted")].copy()
    columns = ["signal_date", "next_date", "symbol6", "name", "entry_1440",
               "second_high_return", *SLOTS[1:]]
    rows = pd.concat([historical[columns], oct8[columns]], ignore_index=True)
    if (len(historical) != 5818 or len(oct8) != 178 or len(rows) != 5996
            or rows.signal_date.nunique() != 21
            or rows.duplicated(["signal_date", "symbol6"]).any()):
        raise ValueError("Expected 20 historical inference cohorts plus October 8")
    return rows


def load_open_prices(conn, cohorts: pd.DataFrame) -> pd.DataFrame:
    records = []
    for next_date, group in cohorts.groupby("next_date", sort=True):
        codes = sorted(group.symbol6.unique())
        placeholders = ",".join(["%s"]*len(codes))
        part = query(conn, "SELECT LEFT(stk_code,6) symbol6, open_price, "
                     "is_finalized, is_fallback, bar_end_time, source_snapshot_time "
                     "FROM aiia_stock_realtime_minute_snapshot_full "
                     "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                     "AND bar_end_time=%s AND LEFT(stk_code,6) IN ("+placeholders+")",
                     (next_date, f"{next_date} 09:31:00", *codes))
        if (len(part) != len(codes) or part.symbol6.duplicated().any()
                or set(part.symbol6) != set(codes)
                or not (part.is_finalized.eq(1) & part.is_fallback.eq(0)
                        & pd.to_datetime(part.bar_end_time).eq(
                            pd.to_datetime(part.source_snapshot_time))).all()):
            raise ValueError(f"Missing exact 09:31 open prices on {next_date}")
        part["open_0931"] = part.open_price.astype(float)
        if not part.open_0931.gt(0).all():
            raise ValueError(f"Invalid 09:31 open prices on {next_date}")
        part["next_date"] = next_date
        records.append(part[["next_date", "symbol6", "open_0931"]])
        print(f"open {next_date}: {len(codes)}", flush=True)
    return pd.concat(records, ignore_index=True)


def choose_modal_slots(cohorts: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One vote per >=1% stock; first occurrence breaks an individual price tie."""
    qualifying = cohorts[cohorts.second_high_return.ge(.01)].copy()
    if qualifying.signal_date.nunique() != cohorts.signal_date.nunique():
        raise ValueError("Cannot choose a slot without a qualifying stock")
    prices = qualifying[list(SLOTS)].to_numpy(dtype=float)
    maximum = prices.max(axis=1, keepdims=True)
    qualifying["best_slot"] = np.asarray(SLOTS)[prices.argmax(axis=1)]
    qualifying["tied_best_slots"] = (prices == maximum).sum(axis=1)
    qualifying["best_return_pct"] = (maximum[:, 0]/qualifying.entry_1440-1)*100
    guide = []
    for date, group in qualifying.groupby("signal_date", sort=True):
        counts = group.best_slot.value_counts()
        top_votes = int(counts.max())
        tied = [slot for slot in SLOTS if int(counts.get(slot, 0)) == top_votes]
        guide.append({
            "signal_date": date, "next_date": group.next_date.iloc[0],
            "qualifying_stocks": len(group),
            "stocks_with_tied_personal_peak": int(group.tied_best_slots.gt(1).sum()),
            "chosen_slot": tied[0], "max_votes": top_votes,
            "tied_modal_slots": ",".join(tied),
            **{f"votes_{slot}": int(counts.get(slot, 0)) for slot in SLOTS},
        })
    return pd.DataFrame(guide), qualifying[[
        "signal_date", "next_date", "symbol6", "name", "entry_1440",
        "second_high_return", "best_slot", "tied_best_slots", "best_return_pct"]]


def load_picks(cohorts: pd.DataFrame) -> pd.DataFrame:
    previous = pd.read_csv(WEIGHTED / "daily_top2.csv", dtype={"symbol6": str})
    previous = previous[previous.selection_rule.eq("class_priority_fallback")]
    current = pd.read_csv(OCT / "selected_before_outcomes.csv", dtype={"symbol6": str})
    current = current[current.selection_rule.eq("class_priority_fallback")]
    picks = pd.concat([previous, current], ignore_index=True)
    cols = ["signal_date", "next_date", "symbol6", "name", "model", "selection_rank"]
    picks = picks[cols].merge(
        cohorts[["signal_date", "next_date", "symbol6", "entry_1440", *SLOTS]],
        on=["signal_date", "next_date", "symbol6"], validate="many_to_one")
    if (len(picks) != 84 or picks.groupby(["model", "signal_date"]).size().ne(2).any()
            or picks.duplicated(["model", "signal_date", "symbol6"]).any()):
        raise ValueError("Expected two picks per model across 21 signal days")
    return picks


def fixed_slot_results(picks: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for model, frame in picks[picks.signal_date.eq("2026-10-08")].groupby("model"):
        for top_k in (1, 2):
            group = frame[frame.selection_rank.le(top_k)]
            for slot in SLOTS:
                returns = group[slot]/group.entry_1440-1
                rows.append({"model": model, "top_k": top_k, "slot": slot,
                             "trades": len(group),
                             "mean_trade_return_pct": float(returns.mean()*100),
                             "positive": int(returns.gt(0).sum())})
    return pd.DataFrame(rows)


def lagged_replay(picks: pd.DataFrame, guide: pd.DataFrame) -> pd.DataFrame:
    guide = guide.sort_values("signal_date").reset_index(drop=True)
    guidance = {}
    for index in range(1, len(guide)):
        previous, current = guide.iloc[index-1], guide.iloc[index]
        if previous.next_date != current.signal_date:
            raise ValueError("Previous morning must precede current afternoon signal")
        guidance[current.signal_date] = (
            previous.signal_date, previous.chosen_slot)
    records = []
    for row in picks.itertuples(index=False):
        if row.signal_date not in guidance:
            continue
        prior_date, chosen_slot = guidance[row.signal_date]
        for strategy, slot in (("prior_cohort_mode", chosen_slot),
                               ("always_open", "open_0931"),
                               ("always_0940", "close_40")):
            records.append({
                "model": row.model, "signal_date": row.signal_date,
                "next_date": row.next_date, "symbol6": row.symbol6,
                "name": row.name, "selection_rank": row.selection_rank,
                "guide_signal_date": prior_date, "strategy": strategy,
                "exit_slot": slot, "entry_1440": row.entry_1440,
                "exit_price": getattr(row, slot),
                "exit_return": getattr(row, slot)/row.entry_1440-1,
            })
    result = pd.DataFrame(records)
    if (result.signal_date.nunique() != 20 or len(result) != 240
            or result.groupby(["model", "signal_date", "strategy"]).size().ne(2).any()):
        raise ValueError("Expected 19 historical days plus October 8 in lagged replay")
    return result


def append_original_rule(trades: pd.DataFrame) -> pd.DataFrame:
    old = pd.read_csv(WEIGHTED / "exit_trades.csv", dtype={"symbol6": str})
    new = pd.read_csv(OCT / "exit_trades.csv", dtype={"symbol6": str})
    original = pd.concat([old, new], ignore_index=True)
    original = original[
        original.selection_rule.eq("class_priority_fallback")
        & original.exit_strategy.eq("original_four_rules")
        & original.signal_date.isin(trades.signal_date.unique())].copy()
    keys = ["model", "signal_date", "next_date", "symbol6", "selection_rank"]
    reference = trades[trades.strategy.eq("always_open")][keys + ["entry_1440"]]
    checked = reference.merge(original, on=keys, validate="one_to_one",
                              suffixes=("_fixed", "_original"))
    if (len(checked) != 80 or not np.allclose(
            checked.entry_1440_fixed, checked.entry_1440_original)):
        raise ValueError("Original rule does not match the lagged selection set")
    checked = checked.rename(columns={
        "entry_1440_fixed": "entry_1440", "exit_minute": "exit_slot"})
    checked["strategy"] = "original_four_rules"
    checked["guide_signal_date"] = pd.NA
    records = checked[[*keys, "name", "guide_signal_date", "strategy",
                       "exit_slot", "entry_1440", "exit_price", "exit_return"]]
    return pd.concat([trades, records], ignore_index=True)


def summarize_lagged(trades: pd.DataFrame) -> list[dict]:
    results = []
    for (model, strategy), frame in trades.groupby(["model", "strategy"]):
        for top_k in (1, 2):
            group = frame[frame.selection_rank.le(top_k)]
            results.append({
                "model": model, "strategy": strategy, "top_k": top_k,
                "signal_days": int(group.signal_date.nunique()),
                "trades": len(group),
                "mean_trade_return_pct": float(group.exit_return.mean()*100),
                "positive": int(group.exit_return.gt(0).sum()),
                "below_minus1": int(group.exit_return.lt(-.01).sum()),
            })
    return results


def run(env_file: Path = Path("/Volumes/ext/fin_agent/.env")) -> dict:
    cohorts = load_cohorts()
    conn = db_connection(env_file)
    try:
        opening = load_open_prices(conn, cohorts)
    finally:
        conn.rollback()
        conn.close()
    cohorts = cohorts.merge(opening, on=["next_date", "symbol6"],
                            validate="one_to_one")
    if len(cohorts) != 5996 or cohorts[list(SLOTS)].isna().any().any():
        raise ValueError("Opening and closing price paths are incomplete")
    guide, qualifying = choose_modal_slots(cohorts)
    picks = load_picks(cohorts)
    fixed = fixed_slot_results(picks)
    lagged = append_original_rule(lagged_replay(picks, guide))
    OUT.mkdir(parents=True, exist_ok=True)
    opening.to_csv(OUT / "opening_prices.csv", index=False)
    guide.to_csv(OUT / "daily_guide.csv", index=False)
    qualifying.to_csv(OUT / "qualifying_stock_best_slots.csv.gz",
                      index=False, compression="gzip")
    fixed.to_csv(OUT / "oct9_fixed_exit_returns.csv", index=False)
    lagged.to_csv(OUT / "lagged_exit_trades.csv", index=False)
    summary = {
        "qualifier": "next-morning second-high return >=1% relative to T 14:40 entry",
        "best_slot": "For each qualifying stock, highest among 09:31 open and 09:31-09:40 minute closes; tied stock prices choose first occurrence. Daily modal ties choose earliest slot.",
        "lag": "A cohort's outcome at its next morning 09:40 chooses one slot for the next signal cohort's following morning; no same-cohort outcome selects its own exit.",
        "cohort_stock_days": len(cohorts), "cohort_days": len(guide),
        "qualifying_stock_days": len(qualifying),
        "latest_guide": guide.iloc[-1].to_dict(),
        "lagged_slot_exact_match_with_next_day": int(sum(
            guide.chosen_slot.iloc[i] == guide.chosen_slot.iloc[i+1]
            for i in range(len(guide)-1))),
        "lagged_transitions": len(guide)-1,
        "lagged_results": summarize_lagged(lagged),
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (DATA, FIRST, SECOND_HIGHS,
                                       WEIGHTED / "daily_top2.csv",
                                       OCT / "predictions_with_outcomes.csv",
                                       OCT / "selected_before_outcomes.csv",
                                       OUT / "opening_prices.csv")},
    }
    (OUT / "summary.json").write_text(json.dumps(
        summary, ensure_ascii=False, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))

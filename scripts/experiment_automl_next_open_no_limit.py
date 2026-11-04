"""Repeat the >2% next-open experiment after excluding T-1 limit-up stocks.

The exclusion uses a completed prior daily bar and is known at 14:45. It does
not assert that remaining names are buyable at 14:45; that requires minute data.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from scripts.experiment_automl_next_open_targets import (
    LATER_END, LATER_START, MIN_VALID_SIGNALS, MIN_VALID_WEEKS, QUANTILES,
    TARGET_GAP, model_specs, score,
)
from scripts.experiment_automl_next_open_weekly import (
    END, START, TEST_END, TEST_START, TRAIN_END, TRAIN_START,
    VALID_END, VALID_START, _load_batches, load_sources, prepare_dataset, weekly_policy,
)
from src.quant_research.automl.data import kingdom_connection


MODELS = ("direct_all", "direct_tree_depth4_all", "direct_hist_boost_all")


def add_prior_limit_flag(data, symbols, calendar):
    with kingdom_connection() as conn:
        flags = _load_batches(conn, "stk_code AS symbol, trade_date AS previous_date, "
                              "is_limit_price AS prior_limit_flag", "kcrp_stock_price", symbols)
    flags.previous_date = pd.to_datetime(flags.previous_date)
    flags.prior_limit_flag = pd.to_numeric(flags.prior_limit_flag, errors="coerce")
    next_day = dict(zip(calendar[:-1], calendar[1:]))
    flags["signal_date"] = flags.previous_date.map(next_day)
    merged = data.merge(flags[["symbol", "signal_date", "prior_limit_flag"]],
                        on=["symbol", "signal_date"], how="left", validate="one_to_one")
    if merged.prior_limit_flag.isna().any():
        raise ValueError("T-1 source limit flag missing for eligible rows")
    return merged


def small_evidence(frame, picks):
    if picks.empty:
        return {"signals": 0, "high2_wins": 0, "high2_rate": None,
                "low0_rate": None, "active_weeks": 0, "same_day_random_rate": None}
    day_base = frame.assign(high2=frame.gap.gt(TARGET_GAP)).groupby("signal_date").high2.mean()
    return {"signals": len(picks), "high2_wins": int(picks.gap.gt(TARGET_GAP).sum()),
            "high2_rate": float(picks.gap.gt(TARGET_GAP).mean()),
            "low0_rate": float(picks.gap.lt(0).mean()),
            "active_weeks": int(picks.signal_date.dt.strftime("%G-W%V").nunique()),
            "same_day_random_rate": float(picks.signal_date.map(day_base).mean())}


def main():
    load_dotenv(".env")
    symbols, calendar, price, flow, value, industry, indices = load_sources(1000)
    data = prepare_dataset(calendar, price, flow, value, industry, indices)
    data = add_prior_limit_flag(data, symbols, calendar)
    parts = {"train": (TRAIN_START, TRAIN_END), "validation": (VALID_START, VALID_END),
             "test": (TEST_START, TEST_END), "later_check": (LATER_START, LATER_END)}
    periods = {name: data[data.signal_date.between(left, right) &
                          data.prior_limit_flag.ne(1)].copy()
               for name, (left, right) in parts.items()}
    report = {"design": "Exclude T-1 is_limit_price=1 before fitting and ranking; no T0 close filter",
              "periods": {name: {"rows": len(frame), "high2_rate": float(frame.gap.gt(TARGET_GAP).mean()),
                                 "low0_rate": float(frame.gap.lt(0).mean())}
                          for name, frame in periods.items()}, "models": {}}
    train, valid = periods["train"], periods["validation"]
    for name in MODELS:
        columns, mode, model = model_specs()[name]
        if mode != "high2":
            raise ValueError("unexpected training target")
        model.fit(train[list(columns)].replace([np.inf, -np.inf], np.nan),
                  train.gap.gt(TARGET_GAP).astype(int))
        val_scores = score(model, valid, columns)
        trials = []
        for q in QUANTILES:
            cutoff = float(np.quantile(val_scores, q))
            picks = weekly_policy(valid, val_scores, cutoff)
            trials.append({"quantile": q, "cutoff": cutoff,
                           "validation": small_evidence(valid, picks)})
        supported = [trial for trial in trials if
                     trial["validation"]["signals"] >= MIN_VALID_SIGNALS and
                     trial["validation"]["active_weeks"] >= MIN_VALID_WEEKS]
        # Freeze the validation threshold before reading test outcomes.
        chosen = max(supported, key=lambda t: (
            t["validation"]["high2_rate"] - t["validation"]["same_day_random_rate"],
            t["validation"]["high2_rate"], t["validation"]["signals"])) if supported else None
        entry = {"training_rows": len(train), "trials": trials,
                 "chosen_quantile": chosen["quantile"] if chosen else None}
        if chosen:
            for period in ("validation", "test", "later_check"):
                frame = periods[period]
                scores = val_scores if period == "validation" else score(model, frame, columns)
                picks = weekly_policy(frame, scores, chosen["cutoff"])
                entry[period] = small_evidence(frame, picks)
        report["models"][name] = entry
        print(name, {key: entry.get(key) for key in
                     ("chosen_quantile", "validation", "test", "later_check")}, flush=True)
    path = Path("outputs/stock_automl/next_open_targets/no_prior_limit_up.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", path, flush=True)


if __name__ == "__main__":
    main()

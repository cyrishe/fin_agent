"""Exploratory >1% next-open study with automatic board and factor audits.

Uses the frozen 1000-stock history and T-1 or earlier inputs. Price returns
and opening labels use same-session close/preclose and open/preclose ratios,
avoiding cross-date adjusted-price rebase artifacts. The current day's final
limit flag is audit-only; this is not an executable 14:45 strategy.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from scripts.experiment_automl_next_open_targets import (
    LATER_END, LATER_START, MIN_VALID_SIGNALS, MIN_VALID_WEEKS,
    QUANTILES, model_specs,
)
from scripts.experiment_automl_next_open_weekly import (
    ALL_FEATURES, END, START, TEST_END, TEST_START, TRAIN_END, TRAIN_START,
    VALID_END, VALID_START, _load_batches, load_sources, prepare_dataset,
    weekly_policy,
)
from src.quant_research.automl.data import kingdom_connection


TARGET = .01
PERIODS = {"train": (TRAIN_START, TRAIN_END), "validation": (VALID_START, VALID_END),
           "test": (TEST_START, TEST_END), "later_check": (LATER_START, LATER_END)}
BASE_MODELS = ("direct_all", "direct_tree_depth4_all", "direct_hist_boost_all")
EXTRA = ("price_acceleration_5", "flow_acceleration_5", "sector_relative_1",
         "volume_confirmed_move")
OUT = Path("outputs/stock_automl/next_open_targets")


def same_session_price_features(daily):
    """Compound per-session returns without comparing adjusted levels across dates."""
    daily = daily.sort_values(["symbol", "date"]).copy()
    daily["daily_return"] = daily.close / daily.preclose - 1
    log_return = np.log1p(daily.daily_return)
    daily["clean_price_return_1"] = daily.daily_return
    for n in (3, 5, 20):
        daily[f"clean_price_return_{n}"] = np.expm1(log_return.groupby(daily.symbol).transform(
            lambda s: s.rolling(n, min_periods=n).sum()))
    daily["clean_price_volatility_20"] = daily.daily_return.groupby(daily.symbol).transform(
        lambda s: s.rolling(20, min_periods=20).std())
    daily["clean_price_gap"] = daily.open / daily.preclose - 1
    return daily


def attach_limit_audit(data, symbols, calendar):
    with kingdom_connection() as conn:
        flags = _load_batches(conn, "stk_code AS symbol, trade_date AS date, is_limit_price, "
                              "open, close, preclose",
                              "kcrp_stock_price", symbols)
    flags.date = pd.to_datetime(flags.date)
    for column in ("is_limit_price", "open", "close", "preclose"):
        flags[column] = pd.to_numeric(flags[column], errors="coerce")
    flags = same_session_price_features(flags)
    flags["limit_up"] = flags.is_limit_price.eq(1).astype(int)
    flags["prior5_limit_count"] = flags.groupby("symbol").limit_up.transform(
        lambda s: s.rolling(5, min_periods=5).sum())
    next_day = dict(zip(calendar[:-1], calendar[1:]))
    prev_day = dict(zip(calendar[1:], calendar[:-1]))
    corrected_features = [f"clean_price_return_{n}" for n in (1, 3, 5, 20)] + [
        "clean_price_volatility_20", "clean_price_gap"]
    prior = flags[["symbol", "date", "is_limit_price", "prior5_limit_count",
                   *corrected_features]].copy()
    prior["signal_date"] = prior.date.map(next_day)
    prior = prior.rename(columns={"is_limit_price": "prior_limit_flag"})
    merged = data.merge(prior[["symbol", "signal_date", "prior_limit_flag",
                               "prior5_limit_count", *corrected_features]],
                        on=["symbol", "signal_date"], validate="one_to_one")
    current = flags[["symbol", "date", "is_limit_price"]].rename(
        columns={"date": "signal_date", "is_limit_price": "current_final_limit_flag"})
    merged = merged.merge(current, on=["symbol", "signal_date"], validate="one_to_one")
    following = flags[["symbol", "date", "open", "preclose"]].copy()
    following["signal_date"] = following.date.map(prev_day)
    following["consistent_gap"] = following.open / following.preclose - 1
    merged = merged.merge(following[["symbol", "signal_date", "consistent_gap"]],
                          on=["symbol", "signal_date"], validate="one_to_one")
    if len(merged) != len(data) or merged[["prior_limit_flag", "prior5_limit_count",
                                          "current_final_limit_flag", "consistent_gap",
                                          *corrected_features]].isna().any().any():
        raise ValueError("same-session price and limit audit did not reconcile to eligible panel")
    merged["crossdate_gap"] = merged.gap
    merged["gap"] = merged.consistent_gap
    for field in ("price_return_1", "price_return_3", "price_return_5",
                  "price_return_20", "price_volatility_20", "price_gap"):
        merged[field] = merged[f"clean_{field}"]
    merged["price_acceleration_5"] = merged.price_return_1 - merged.price_return_5 / 5
    merged["flow_acceleration_5"] = merged.flow_main_ratio_1 - merged.flow_main_ratio_5
    merged["sector_relative_1"] = merged.sector_return_1 - merged.csi300_return_1
    merged["volume_confirmed_move"] = merged.price_return_1 * (merged.price_amount_ratio_5 - 1)
    return merged


def rate(frame):
    return {"n": int(len(frame)), "high1": int(frame.gap.gt(TARGET).sum()),
            "high1_rate": float(frame.gap.gt(TARGET).mean()) if len(frame) else None,
            "low0_rate": float(frame.gap.lt(0).mean()) if len(frame) else None}


def evidence(pool, picks):
    if picks.empty:
        return {"signals": 0, "active_weeks": 0, "high1": 0,
                "high1_rate": None, "low0_rate": None, "same_day_base": None,
                "week_bootstrap_excess_p10": None}
    day_base = pool.assign(high1=pool.gap.gt(TARGET)).groupby("signal_date").high1.mean()
    enriched = picks.join(day_base.rename("day_base"), on="signal_date")
    week = enriched.assign(week=enriched.signal_date.dt.strftime("%G-W%V"),
                           hit=enriched.gap.gt(TARGET)).groupby("week").agg(
        n=("hit", "size"), wins=("hit", "sum"), expected=("day_base", "sum"))
    rng = np.random.default_rng(42)
    draws = rng.integers(0, len(week), size=(1000, len(week)))
    sampled_n = week.n.to_numpy()[draws].sum(axis=1)
    excess = ((week.wins.to_numpy()[draws].sum(axis=1) -
               week.expected.to_numpy()[draws].sum(axis=1)) / sampled_n)
    chosen = enriched.merge(pool[["symbol", "signal_date", "current_final_limit_flag",
                                   "prior5_limit_count", "price_return_1", "price_return_5",
                                   "price_amount_ratio_5", "flow_main_ratio_1"]],
                            on=["symbol", "signal_date"], validate="one_to_one")
    limit = chosen.current_final_limit_flag.eq(1)
    return {"signals": len(chosen), "active_weeks": len(week),
            "high1": int(chosen.gap.gt(TARGET).sum()),
            "high1_rate": float(chosen.gap.gt(TARGET).mean()),
            "low0_rate": float(chosen.gap.lt(0).mean()),
            "same_day_base": float(chosen.day_base.mean()),
            "week_bootstrap_excess_p10": float(np.quantile(excess, .1)),
            "current_final_limit_ex_post": int(limit.sum()),
            "high1_on_current_final_limit_ex_post": int(chosen.loc[limit, "gap"].gt(TARGET).sum()),
            "recent5_limit_after_prior1_filter": int(chosen.prior5_limit_count.gt(0).sum()),
            "median_prior_return_1": float(chosen.price_return_1.median()),
            "median_prior_return_5": float(chosen.price_return_5.median()),
            "median_prior_amount_ratio_5": float(chosen.price_amount_ratio_5.median()),
            "median_prior_main_flow_ratio": float(chosen.flow_main_ratio_1.median())
                if chosen.flow_main_ratio_1.notna().any() else None}


def model_candidates(scope):
    specs = model_specs()
    models = {name: specs[name] for name in BASE_MODELS}
    if scope == "exclude_prior5_limit":
        deduplicated = Pipeline([("impute", SimpleImputer(strategy="median")),
                                 ("scale", StandardScaler()),
                                 ("model", LogisticRegression(C=.2, max_iter=400))])
        deduplicated_features = tuple(feature for feature in ALL_FEATURES
                                      if feature not in ("price_body", "sector_relative_5"))
        models["direct_all_less_redundant"] = (deduplicated_features, "high1", deduplicated)
        extended = Pipeline([("impute", SimpleImputer(strategy="median")),
                             ("scale", StandardScaler()),
                             ("model", LogisticRegression(C=.2, max_iter=400))])
        models["direct_all_plus_four_interactions"] = (ALL_FEATURES + EXTRA, "high1", extended)
    return models


def main():
    load_dotenv(".env")
    symbols, calendar, price, flow, value, industry, indices = load_sources(1000)
    data = prepare_dataset(calendar, price, flow, value, industry, indices)
    data = attach_limit_audit(data, symbols, calendar)
    sizes = {"train": 234122, "validation": 117737, "test": 132713,
             "later_check": 39097}
    for name, (first, last) in PERIODS.items():
        actual = int(data.signal_date.between(first, last).sum())
        if actual != sizes[name]:
            raise ValueError(f"eligible panel changed for {name}: {actual} != {sizes[name]}")
    report = {"target": "next session open / next session preclose - 1 > 1%",
              "target_gap": TARGET, "sample_symbols": len(symbols),
              "price_basis": "same-session close/preclose for T-1 returns; next open/preclose for gap",
              "crossdate_label_flips_high1": int(data.crossdate_gap.gt(TARGET).ne(
                  data.gap.gt(TARGET)).sum()),
              "factors": list(ALL_FEATURES), "extra_candidates": list(EXTRA),
              "all_panel": {name: rate(data[data.signal_date.between(first, last)])
                            for name, (first, last) in PERIODS.items()},
              "scopes": {}}
    detail = []
    for scope in ("exclude_prior1_limit", "exclude_prior5_limit"):
        condition = data.prior_limit_flag.ne(1) if scope == "exclude_prior1_limit" else data.prior5_limit_count.eq(0)
        panel = data[condition].copy()
        periods = {name: panel[panel.signal_date.between(first, last)].copy()
                   for name, (first, last) in PERIODS.items()}
        scope_report = {"periods": {name: rate(frame) for name, frame in periods.items()},
                        "feature_nonmissing_train": {col: float(periods["train"][col].notna().mean())
                                                     for col in ALL_FEATURES + EXTRA},
                        "models": {}}
        for name, (cols, _, model) in model_candidates(scope).items():
            train, valid = periods["train"], periods["validation"]
            model.fit(train[list(cols)].replace([np.inf, -np.inf], np.nan),
                      train.gap.gt(TARGET).astype(int))
            valid_scores = model.predict_proba(valid[list(cols)].replace([np.inf, -np.inf], np.nan))[:, 1]
            trials = []
            for q in QUANTILES:
                cutoff = float(np.quantile(valid_scores, q))
                picks = weekly_policy(valid, valid_scores, cutoff)
                trials.append({"quantile": q, "cutoff": cutoff,
                               "validation": evidence(valid, picks)})
            supported = [trial for trial in trials if
                         trial["validation"]["signals"] >= MIN_VALID_SIGNALS and
                         trial["validation"]["active_weeks"] >= MIN_VALID_WEEKS]
            chosen = max(supported, key=lambda item: (
                item["validation"]["week_bootstrap_excess_p10"],
                item["validation"]["high1_rate"],
                item["validation"]["signals"])) if supported else None
            model_report = {"train_rows": len(train), "trials": trials,
                            "chosen_quantile": chosen["quantile"] if chosen else None}
            if isinstance(model, Pipeline) and isinstance(model.named_steps.get("model"), LogisticRegression):
                logistic = model.named_steps["model"]
                model_report["standardized_coefficients"] = {
                    feature: float(weight) for feature, weight in zip(cols, logistic.coef_[0])}
                model_report["intercept"] = float(logistic.intercept_[0])
            if chosen:
                for period_name in ("validation", "test", "later_check"):
                    frame = periods[period_name]
                    scores = valid_scores if period_name == "validation" else model.predict_proba(
                        frame[list(cols)].replace([np.inf, -np.inf], np.nan))[:, 1]
                    picks = weekly_policy(frame, scores, chosen["cutoff"])
                    model_report[period_name] = evidence(frame, picks)
                    if period_name != "validation" and not picks.empty:
                        rows = picks.merge(frame[["symbol", "signal_date", "prior_limit_flag",
                                                  "prior5_limit_count", "current_final_limit_flag",
                                                  "price_return_1", "price_return_5",
                                                  "flow_main_ratio_1", "price_amount_ratio_5"]],
                                           on=["symbol", "signal_date"], validate="one_to_one")
                        rows["scope"] = scope
                        rows["model"] = name
                        rows["period"] = period_name
                        rows["high1"] = rows.gap.gt(TARGET)
                        detail.append(rows)
            scope_report["models"][name] = model_report
            print(scope, name, {key: model_report.get(key) for key in
                                ("chosen_quantile", "validation", "test", "later_check")}, flush=True)
        report["scopes"][scope] = scope_report
    OUT.mkdir(parents=True, exist_ok=True)
    report_path = OUT / "high1_consistent_audit.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    detail_path = OUT / "high1_consistent_selected_diagnostics.csv"
    if detail:
        pd.concat(detail, ignore_index=True).to_csv(detail_path, index=False,
                                                     encoding="utf-8-sig", float_format="%.8f")
    print("saved", report_path, detail_path, flush=True)


if __name__ == "__main__":
    main()

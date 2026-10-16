"""Compare material next-open events with same-day random-stock baselines.

Read-only research. Reuses the point-in-time T-1 panel and fixed weekly budget;
stores aggregate evidence only. Run from the repository root with PYTHONPATH=.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text
import sklearn

from scripts.experiment_automl_next_open_weekly import (
    ALL_FEATURES, FLOW, MARKET, PRICE, TEST_END, TEST_START, TRAIN_END,
    TRAIN_START, VALID_END, VALID_START, load_sources, prepare_dataset, weekly_policy,
)


TARGET_GAP = 0.02
QUANTILES = (.975, .99, .995, .9975, .999, .9995, .9998)
MIN_VALID_SIGNALS = 30
MIN_VALID_WEEKS = 10
LATER_START, LATER_END = "2026-08-01", "2026-09-29"
PROFILE_FEATURES = ("price_return_1", "price_return_5", "price_volatility_20",
                    "price_amount_ratio_5", "price_turnover", "flow_main_ratio_1",
                    "csi300_return_1", "sector_relative_5", "log_market_cap")


def rate_table(frame):
    gap = frame.gap
    by_day = gap.gt(TARGET_GAP).groupby(frame.signal_date).mean()
    return {
        "rows": int(len(frame)), "days": int(frame.signal_date.nunique()),
        "weeks": int(frame.signal_date.dt.strftime("%G-W%V").nunique()),
        "high_open_gt_0_rate": float(gap.gt(0).mean()),
        "high_open_gt_2pct_rate": float(gap.gt(TARGET_GAP).mean()),
        "low_open_lt_0_rate": float(gap.lt(0).mean()),
        "near_flat_0_to_2pct_rate": float(gap.between(0, TARGET_GAP, inclusive="both").mean()),
        "random_stock_mean_gap": float(gap.mean()),
        "random_stock_median_gap": float(gap.median()),
        "random_stock_gap_p10": float(gap.quantile(.1)),
        "random_stock_gap_p90": float(gap.quantile(.9)),
        "daily_high2_rate_p10": float(by_day.quantile(.1)),
        "daily_high2_rate_median": float(by_day.median()),
        "daily_high2_rate_p90": float(by_day.quantile(.9)),
    }


def matched_day_evidence(pool, picks):
    """Expected wins from random eligible stocks on the exact chosen dates."""
    if picks.empty:
        return {"signals": 0, "active_days": 0, "active_weeks": 0,
                "high2_precision": None, "same_day_random_rate": None,
                "lift_vs_same_day_random": None, "excess_percentage_points": None,
                "high0_rate": None, "low0_rate": None, "low1_rate": None,
                "low2_rate": None, "mean_gap": None, "median_gap": None,
                "same_day_vol_matched_high2_rate": None,
                "lift_vs_day_vol_matched": None,
                "week_bootstrap_excess_p10": None,
                "random_tail_probability": None}
    enriched = pool[["signal_date", "symbol", "gap", "price_volatility_20"]].copy()
    enriched["high2"] = enriched.gap.gt(TARGET_GAP)
    enriched["low2"] = enriched.gap.lt(-TARGET_GAP)
    enriched["vol_decile"] = np.ceil(enriched.groupby("signal_date").price_volatility_20.rank(
        pct=True).mul(10)).clip(1, 10).astype(int)
    day_pool = enriched.groupby("signal_date").agg(
        positives=("high2", "sum"), total=("high2", "size"),
        low2_rate=("low2", "mean"))
    vol_pool = enriched.groupby(["signal_date", "vol_decile"]).agg(
        vol_high2_rate=("high2", "mean"), vol_low2_rate=("low2", "mean"))
    chosen = picks.assign(high2=picks.gap.gt(TARGET_GAP),
                          week=picks.signal_date.dt.strftime("%G-W%V"))
    chosen = chosen.join(enriched.set_index(["signal_date", "symbol"])["vol_decile"],
                         on=["signal_date", "symbol"])
    chosen = chosen.join(day_pool, on="signal_date")
    chosen = chosen.join(vol_pool, on=["signal_date", "vol_decile"])
    chosen["random_rate"] = chosen.positives / chosen.total
    precision = float(chosen.high2.mean())
    baseline = float(chosen.random_rate.mean())
    weekly = chosen.groupby("week").agg(n=("high2", "size"), wins=("high2", "sum"),
                                        expected=("random_rate", "sum"))
    rng = np.random.default_rng(42)
    sampled = rng.integers(0, len(weekly), size=(1000, len(weekly)))
    count = weekly.n.to_numpy()[sampled].sum(axis=1)
    excess = (weekly.wins.to_numpy()[sampled].sum(axis=1) -
              weekly.expected.to_numpy()[sampled].sum(axis=1)) / count

    # Conditional null: same number of random stocks on each of the selected days.
    day_counts = chosen.groupby("signal_date").size()
    simulations = np.zeros(10000, dtype=np.int32)
    for day, count_on_day in day_counts.items():
        p = day_pool.loc[day]
        simulations += rng.hypergeometric(int(p.positives), int(p.total - p.positives),
                                          int(count_on_day), size=len(simulations))
    actual_wins = int(chosen.high2.sum())
    return {
        "signals": int(len(chosen)), "active_days": int(chosen.signal_date.nunique()),
        "active_weeks": int(len(weekly)), "high2_wins": actual_wins,
        "high2_precision": precision, "same_day_random_rate": baseline,
        "lift_vs_same_day_random": float(precision / baseline) if baseline else None,
        "same_day_vol_matched_high2_rate": float(chosen.vol_high2_rate.mean()),
        "lift_vs_day_vol_matched": float(precision / chosen.vol_high2_rate.mean())
            if chosen.vol_high2_rate.mean() else None,
        "same_day_random_low2_rate": float(chosen.low2_rate.mean()),
        "same_day_vol_matched_low2_rate": float(chosen.vol_low2_rate.mean()),
        "excess_percentage_points": float(100 * (precision - baseline)),
        "high0_rate": float(chosen.gap.gt(0).mean()),
        "low0_rate": float(chosen.gap.lt(0).mean()),
        "low1_rate": float(chosen.gap.lt(-.01).mean()),
        "low2_rate": float(chosen.gap.lt(-TARGET_GAP).mean()),
        "mean_gap": float(chosen.gap.mean()),
        "median_gap": float(chosen.gap.median()),
        "week_bootstrap_excess_p10": float(np.quantile(excess, .1)),
        "random_tail_probability": float((1 + np.count_nonzero(simulations >= actual_wins)) /
                                         (len(simulations) + 1)),
        "random_wins_p95": float(np.quantile(simulations, .95)),
    }


def rank_buckets(frame, scores):
    ranked = frame[["signal_date", "symbol", "gap"]].copy()
    ranked["score"] = scores
    ranked = ranked.sort_values(["signal_date", "score", "symbol"], ascending=[True, False, True])
    ranked["rank"] = ranked.groupby("signal_date").cumcount() + 1
    return [{"rank": f"{lo}-{hi}", "rows": int(len(part)),
             "high2_rate": float(part.gap.gt(TARGET_GAP).mean()),
             "high0_rate": float(part.gap.gt(0).mean())}
            for lo, hi in ((1, 1), (2, 5), (6, 20), (21, 100), (101, 500))
            for part in (ranked[ranked["rank"].between(lo, hi)],)]


def feature_profile(pool, picks):
    """Descriptive median contrasts, not causal feature attribution."""
    if picks.empty:
        return None
    chosen = picks[["signal_date", "symbol"]].merge(
        pool[["signal_date", "symbol", *PROFILE_FEATURES]],
        on=["signal_date", "symbol"], validate="one_to_one")
    active_pool = pool[pool.signal_date.isin(picks.signal_date)]
    return {name: {"chosen_median": float(chosen[name].median())
                   if chosen[name].notna().any() else None,
                   "active_day_pool_median": float(active_pool[name].median())
                   if active_pool[name].notna().any() else None}
            for name in PROFILE_FEATURES}


def model_explanation(model, columns):
    if not isinstance(model, Pipeline):
        return {"kind": "ensemble_reference", "readable_rules": None}
    estimator = model.named_steps["model"]
    if isinstance(estimator, LogisticRegression):
        coefficients = sorted(zip(columns, estimator.coef_[0]),
                              key=lambda pair: abs(pair[1]), reverse=True)
        return {"kind": "standardized_logistic",
                "coefficients": [{"feature": feature, "coefficient": float(value)}
                                 for feature, value in coefficients]}
    if isinstance(estimator, DecisionTreeClassifier):
        tree = estimator.tree_
        leaves = np.flatnonzero(tree.children_left == -1)
        return {"kind": "single_decision_tree",
                "rules": export_text(estimator, feature_names=list(columns), decimals=5),
                "leaves": [{"node": int(node), "train_rows": int(tree.n_node_samples[node]),
                            "train_positive_rate": float(tree.value[node, 0, 1])}
                           for node in leaves]}
    return {"kind": type(estimator).__name__}


def model_specs():
    linear = lambda: Pipeline([("impute", SimpleImputer(strategy="median")),
                               ("scale", StandardScaler()),
                               ("model", LogisticRegression(C=.2, max_iter=400))])
    return {
        "direction_price": (PRICE, "direction", linear()),
        "direction_price_flow_market": (PRICE + FLOW + MARKET, "direction", linear()),
        "direct_price": (PRICE, "high2", linear()),
        "direct_price_flow": (PRICE + FLOW, "high2", linear()),
        "direct_price_flow_market": (PRICE + FLOW + MARKET, "high2", linear()),
        "direct_all": (ALL_FEATURES, "high2", linear()),
        "clear_band_price_flow_market": (PRICE + FLOW + MARKET, "clear_band", linear()),
        "direct_tree_depth3_all": (ALL_FEATURES, "high2", Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("model", DecisionTreeClassifier(max_depth=3, min_samples_leaf=500,
                random_state=42))])),
        "direct_tree_depth4_all": (ALL_FEATURES, "high2", Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("model", DecisionTreeClassifier(max_depth=4, min_samples_leaf=500,
                random_state=42))])),
        "direct_hist_boost_all": (ALL_FEATURES, "high2",
            HistGradientBoostingClassifier(max_iter=80, max_leaf_nodes=7,
                min_samples_leaf=300, learning_rate=.05, l2_regularization=2,
                random_state=42)),
    }


def score(model, frame, columns):
    x = frame[list(columns)].replace([np.inf, -np.inf], np.nan)
    return model.predict_proba(x)[:, 1]


def evaluate(data):
    parts = {
        "train": data[data.signal_date.between(TRAIN_START, TRAIN_END)],
        "validation": data[data.signal_date.between(VALID_START, VALID_END)],
        "test": data[data.signal_date.between(TEST_START, TEST_END)],
        "later_check": data[data.signal_date.between(LATER_START, LATER_END)],
    }
    for name, frame in parts.items():
        if frame.signal_date.nunique() < (20 if name == "later_check" else 100):
            raise ValueError(f"insufficient decision dates in {name}")
    summaries = {name: rate_table(frame) for name, frame in parts.items()}
    print("base rates", summaries, flush=True)
    train, valid = parts["train"], parts["validation"]
    rows = []
    for name, (columns, target_mode, model) in model_specs().items():
        fit = train[(train.gap.gt(TARGET_GAP) | train.gap.lt(0))] if target_mode == "clear_band" else train
        model.fit(fit[list(columns)].replace([np.inf, -np.inf], np.nan),
                  fit.gap.gt(0 if target_mode == "direction" else TARGET_GAP).astype(int))
        validation_scores = score(model, valid, columns)
        trials = []
        for quantile in QUANTILES:
            cutoff = float(np.quantile(validation_scores, quantile))
            picks = weekly_policy(valid, validation_scores, cutoff)
            evidence = matched_day_evidence(valid, picks)
            trials.append({"quantile": quantile, "cutoff": cutoff, "validation": evidence})
        supported = [item for item in trials if
                     item["validation"]["signals"] >= MIN_VALID_SIGNALS and
                     item["validation"]["active_weeks"] >= MIN_VALID_WEEKS]
        # Selection uses the target event and validation data alone.
        chosen = max(supported, key=lambda item: (
            item["validation"]["week_bootstrap_excess_p10"],
            item["validation"]["high2_precision"],
            item["validation"]["signals"])) if supported else None
        result = {"model": name, "feature_count": len(columns),
                  "training_target": target_mode,
                  "model_explanation": model_explanation(model, columns),
                  "training_rows": int(len(fit)), "trials": trials,
                  "chosen_quantile": chosen["quantile"] if chosen else None,
                  "validation": chosen["validation"] if chosen else None,
                  "test": None, "later_check": None,
                  "test_rank_buckets": None, "later_rank_buckets": None,
                  "test_feature_profile": None, "later_feature_profile": None}
        if chosen:
            for part_name, bucket_name in (("test", "test_rank_buckets"),
                                           ("later_check", "later_rank_buckets")):
                frame = parts[part_name]
                scores = score(model, frame, columns)
                picks = weekly_policy(frame, scores, chosen["cutoff"])
                result[part_name] = matched_day_evidence(frame, picks)
                result[bucket_name] = rank_buckets(frame, scores)
                result["test_feature_profile" if part_name == "test" else
                       "later_feature_profile"] = feature_profile(frame, picks)
            print(name, "validation", result["validation"], "test", result["test"],
                  "later", result["later_check"], flush=True)
        else:
            print(name, "no supported validation cutoff", flush=True)
        rows.append(result)
    eligible = [row for row in rows if row["validation"]]
    best = max(eligible, key=lambda row: (
        row["validation"]["week_bootstrap_excess_p10"],
        row["validation"]["high2_precision"])) if eligible else None
    selected = best["model"] if best and best["validation"]["week_bootstrap_excess_p10"] > 0 else None
    return {"target": "next adjusted open / current adjusted close - 1 > 0.02",
            "threshold_strictly_greater_than": TARGET_GAP,
            "period_base_rates": summaries,
            "model_selected_on_validation": selected, "models": rows}


def main(output, sample_symbols):
    load_dotenv(".env")
    symbols, calendar, price, flow, value, industry, indices = load_sources(sample_symbols)
    data = prepare_dataset(calendar, price, flow, value, industry, indices)
    report = evaluate(data)
    report.update({
        "as_of": datetime.now().astimezone().isoformat(),
        "sample_symbols": len(symbols), "sample_seed": 20261007,
        "labelled_rows": int(len(data)), "labelled_dates": int(data.signal_date.nunique()),
        "minimum_validation_signals": MIN_VALID_SIGNALS,
        "minimum_validation_weeks": MIN_VALID_WEEKS,
        "weekly_budget": {"stocks": 5, "active_days": 2, "stocks_per_day": 3},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "environment": {"numpy": np.__version__, "pandas": pd.__version__,
                        "scikit_learn": sklearn.__version__},
        "limitations": [
            "The 1000-symbol universe is sampled and not a full-market ranking.",
            "The Jan-Jul 2026 period was viewed for the prior >0 target, so it is diagnostic rather than pristine blind validation for this redesign.",
            "The Aug-Sep 2026 later check has few weeks and some dates were viewed in the earlier minute experiment.",
            "Same-day 14:45 minute features are not included in this long-history comparison.",
            "Random-tail probabilities are conditional on chosen dates and counts and do not adjust for trying multiple models and cutoffs.",
            "A score from training only clear positive/negative cases is not calibrated to the full candidate universe.",
        ],
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    print("saved", output, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-symbols", type=int, default=1000)
    parser.add_argument("--output", default="outputs/stock_automl/next_open_targets/high2_1000.json")
    args = parser.parse_args()
    main(Path(args.output), args.sample_symbols)

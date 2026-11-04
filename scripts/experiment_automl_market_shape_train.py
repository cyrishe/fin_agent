"""Train a seven-factor model on 20 market-diverse dates and compare fixed models."""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from scripts.backtest_automl_seven_factor_oos import (
    EXACT, FEATURES, OCT8, TRAIN_END, TRAIN_START, database_bars, oct9_api_bars,
    label_oct8_after_ranking, simulate,
)
from scripts.benchmark_automl_1440_inference import db_connection, frozen_score, query, verify_model
from scripts.evaluate_automl_sina_15m_july import fitted
from scripts.experiment_automl_tail_three_class import read_samples


SAMPLES = tuple(Path(p) for p in (
    "outputs/stock_automl/tail_expanded_august/trainable_1440.csv",
    "outputs/stock_automl/tail_sep30_check/trainable_1440.csv",
    "outputs/stock_automl/tail_aug05_aug06_exact/trainable_1440.csv",
    "outputs/stock_automl/tail_aug24_exact/trainable_1440.csv",
))
OUTPUT = Path("docs/stock_automl_runs/20261009_market_shape_training")


def market_states(conn, signal_dates):
    """Describe a signal day only with market breadth/returns known at T-1."""
    market = query(conn, "SELECT trade_date, AVG(close/preclose-1) AS market_return, "
                   "AVG(close>preclose) AS breadth FROM kcrp_stock_price "
                   "WHERE trade_date BETWEEN %s AND %s AND preclose>0 AND close>0 "
                   "GROUP BY trade_date ORDER BY trade_date", ("2026-07-20", "2026-09-30"))
    market.trade_date = pd.to_datetime(market.trade_date)
    for column in ("market_return", "breadth"):
        market[column] = pd.to_numeric(market[column], errors="raise")
    market["return_5d"] = market.market_return.rolling(5).sum()
    market["breadth_5d"] = market.breadth.rolling(5).mean()
    features = ["market_return", "breadth", "return_5d", "breadth_5d"]
    known = market[features].shift(1).rename(columns={
        "market_return": "prior_market_return", "breadth": "prior_breadth",
        "return_5d": "prior_market_return_5d", "breadth_5d": "prior_breadth_5d"})
    states = pd.concat([market[["trade_date"]], known], axis=1)
    states = states[states.trade_date.isin(signal_dates)].copy()
    if len(states) != len(signal_dates) or states.isna().any().any():
        raise ValueError("Missing prior-market shape for a labeled signal date")
    return states


def balanced_dates(states):
    columns = ["prior_market_return", "prior_breadth", "prior_market_return_5d",
               "prior_breadth_5d"]
    standardized = StandardScaler().fit_transform(states[columns])
    states = states.copy()
    states["market_group"] = KMeans(n_clusters=4, random_state=0, n_init=10).fit_predict(
        standardized)
    selected = []
    for _, group in states.groupby("market_group"):
        group = group.sort_values("trade_date")
        if len(group) < 5:
            raise ValueError("Market group has fewer than five dates")
        positions = np.rint(np.linspace(0, len(group)-1, 5)).astype(int)
        selected.extend(group.iloc[positions].trade_date.dt.strftime("%Y-%m-%d").tolist())
    if len(selected) != 20 or len(set(selected)) != 20:
        raise ValueError("Expected 20 different market-diverse training dates")
    states["selected_for_training"] = states.trade_date.dt.strftime("%Y-%m-%d").isin(selected)
    return states, sorted(selected)


def scored_rows(model, candidates):
    rows = candidates.copy()
    rows["score"] = frozen_score(model, rows[list(FEATURES)])
    rows = rows.sort_values(["signal_date", "score", "symbol6"],
                            ascending=[True, False, True])
    rows["rank"] = rows.groupby("signal_date").cumcount() + 1
    return rows[rows["rank"].le(2)].copy()


def run(env_file: Path, output: Path):
    samples = pd.concat([read_samples(p) for p in SAMPLES], ignore_index=True)
    samples["signal_date"] = samples.signal_date.dt.strftime("%Y-%m-%d")
    samples["next_date"] = samples.next_date.dt.strftime("%Y-%m-%d")
    if len(samples) != 14292 or samples.signal_date.nunique() != 40 or samples.duplicated(
            ["signal_date", "symbol6"]).any():
        raise ValueError("Exact one-minute candidate pool changed")
    old_model, validation = verify_model()
    conn = db_connection(env_file)
    try:
        states = market_states(conn, pd.to_datetime(samples.signal_date.unique()))
    finally:
        conn.rollback()
        conn.close()
    states, new_train_dates = balanced_dates(states)
    old_train_dates = set(samples.loc[samples.signal_date.between(TRAIN_START, TRAIN_END),
                                      "signal_date"].unique())
    if len(old_train_dates) != 20:
        raise ValueError("Old fixed training dates changed")
    new_train = samples[samples.signal_date.isin(new_train_dates)].copy()
    new_model = fitted(new_train, FEATURES)

    oct8 = pd.read_csv(OCT8, dtype={"symbol6": str})
    oct8["signal_date"] = "2026-10-08"
    oct8["next_date"] = "2026-10-09"
    oct8["class"] = pd.NA
    all_candidates = pd.concat([samples, oct8], ignore_index=True)
    all_dates = set(all_candidates.signal_date.unique())
    common_dates = sorted(all_dates - old_train_dates - set(new_train_dates))
    new_test_dates = sorted(all_dates - set(new_train_dates))
    if len(common_dates) < 5 or "2026-10-08" not in common_dates:
        raise ValueError("Too few common out-of-training dates")
    old_common = scored_rows(old_model, all_candidates[all_candidates.signal_date.isin(common_dates)])
    new_common = scored_rows(new_model, all_candidates[all_candidates.signal_date.isin(common_dates)])
    new_all = scored_rows(new_model, all_candidates[all_candidates.signal_date.isin(new_test_dates)])
    archived = pd.read_csv(EXACT, dtype={"symbol6": str})
    archived = archived[(archived.model == "精确训练七因子_去当前价高于均线") &
                        archived["rank"].le(2)]
    archive_check = old_common[old_common.signal_date.ne("2026-10-08")].merge(
        archived, on=["signal_date", "symbol6", "rank"], suffixes=("_now", "_saved"))
    if (len(archive_check) != len(old_common) - 2 or
            (archive_check.score_now - archive_check.score_saved).abs().max() > 1e-12):
        raise ValueError("Old frozen model does not reproduce archived common-date picks")

    # A common set of exact minute bars makes the two cash paths directly comparable.
    combined = pd.concat([old_common, new_common, new_all], ignore_index=True).drop_duplicates(
        ["signal_date", "symbol6"])
    db = database_bars(combined, env_file)
    api = oct9_api_bars(combined)
    db["source"] = "kingdomai_full_1m"
    api["source"] = "kLineData_1m"
    bars = pd.concat([db, api], ignore_index=True)
    needed = set()
    for row in combined.itertuples():
        needed.add((row.signal_date, row.symbol6, "14:50"))
        needed.update((row.next_date, row.symbol6, f"09:{minute:02}") for minute in range(31, 41))
    bars = bars[[key in needed for key in zip(bars.day, bars.symbol6, bars.minute)]].copy()
    old_common = label_oct8_after_ranking(old_common, bars)
    new_common = label_oct8_after_ranking(new_common, bars)
    new_all = label_oct8_after_ranking(new_all, bars)

    reports = {}
    output.mkdir(parents=True, exist_ok=True)
    for name, chosen in (("old_common", old_common), ("new_common", new_common),
                         ("new_all_out_of_training", new_all)):
        result, daily, trades = simulate(chosen, bars, Decimal("100000"))
        result["strong"] = int(chosen["class"].eq(1).sum())
        result["neutral"] = int(chosen["class"].eq(0).sum())
        result["critical"] = int(chosen["class"].eq(-1).sum())
        result["labeled_stocks"] = int(chosen["class"].notna().sum())
        reports[name] = result
        chosen[["signal_date", "next_date", "rank", "symbol6", "name", "score", "class"]].to_csv(
            output / f"{name}_picks.csv", index=False)
        daily.to_csv(output / f"{name}_daily.csv", index=False)
        trades.to_csv(output / f"{name}_trades.csv", index=False)

    states.trade_date = states.trade_date.dt.strftime("%Y-%m-%d")
    states.to_csv(output / "market_states.csv", index=False)
    bars[["day", "symbol6", "minute", "open_price", "high_price", "latest_price", "source"]].sort_values(
        ["day", "symbol6", "minute"]).to_csv(output / "selected_1m_bars.csv", index=False)
    model_path = output / "market_diverse_seven_factor.joblib"
    joblib.dump(new_model, model_path)
    summary = {
        "selection_method": "Four KMeans groups from T-1 equal-weight market return, breadth, trailing five-day return and breadth; five chronologically spaced dates from each group; random_state=0, n_init=10; no next-morning labels in date selection.",
        "old_training_dates": sorted(old_train_dates), "new_training_dates": new_train_dates,
        "new_training_rows": len(new_train), "common_test_dates": common_dates,
        "new_all_out_of_training_dates": new_test_dates,
        "market_groups": states.groupby("market_group").agg(
            available_dates=("trade_date", "count"), selected_dates=("selected_for_training", "sum"),
            mean_prior_return=("prior_market_return", "mean"),
            mean_prior_breadth=("prior_breadth", "mean"),
            mean_prior_5d_return=("prior_market_return_5d", "mean")).to_dict("index"),
        "old_model_validation": validation, "results": reports,
        "artifact_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in (model_path, output / "market_states.csv",
                                      output / "selected_1m_bars.csv")},
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (*SAMPLES, OCT8, EXACT)},
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"training_rows": len(new_train), "common_dates": len(common_dates),
                      "results": reports}, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    run(args.env_file, args.output_dir)

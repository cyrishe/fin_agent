"""Train interpretable models for 09:32–09:40 close gains over a 14:40 entry."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text

from scripts.audit_automl_minute_api_backfill import request_bars
from scripts.backtest_automl_tail_top2_cash import buy_quantity
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query


DATA = Path("docs/stock_automl_runs/20261009_close9_target/exact_1440_to_close9_candidates.csv.gz")
MARKET = Path("docs/stock_automl_runs/20261009_market_shape_training/market_states.csv")
OCT8 = Path("docs/stock_automl_runs/20261009_seven_factor_cash_backtest/20261008_exact_candidates.csv")
OUT = Path("docs/stock_automl_runs/20261009_close9_target")
COMPACT = ("signal_return", "volume_ratio", "turnover_so_far_pct",
           "float_mv_100m_cny", "all_intraday_lows_above_ma")
BINARY = ("volume_4of5_increasing", "ma_bull_5_10_20", "all_intraday_lows_above_ma")
CLOSES = tuple(f"close_{minute:02}" for minute in range(32, 41))


def split_dates(states):
    """Within each exogenous market group: ~half train, quarter validation, rest test."""
    assignments = {}
    for _, group in states.groupby("market_group"):
        dates = group.sort_values("trade_date").trade_date.tolist()
        train_count = math.ceil(len(dates) / 2)
        val_count = max(1, math.floor(len(dates) / 4))
        train_ix = set(np.rint(np.linspace(0, len(dates)-1, train_count)).astype(int))
        remaining = [ix for ix in range(len(dates)) if ix not in train_ix]
        val_positions = np.rint(np.linspace(0, len(remaining)-1, val_count)).astype(int)
        val_ix = {remaining[ix] for ix in val_positions}
        for ix, date in enumerate(dates):
            assignments[date] = "train" if ix in train_ix else "validation" if ix in val_ix else "test"
    if len(assignments) != 40 or len(set(assignments)) != 40:
        raise ValueError("Expected 40 distinct dates in market-shape split")
    return assignments


def day_weights(rows):
    counts = rows.groupby("signal_date").symbol6.transform("size")
    return len(rows) / (rows.signal_date.nunique() * counts.to_numpy())


def models():
    def logistic(columns):
        continuous = [c for c in columns if c not in BINARY]
        binary = [c for c in columns if c in BINARY]
        transformer = ColumnTransformer([
            ("numeric", StandardScaler(), continuous),
            ("binary", "passthrough", binary)])
        return make_pipeline(transformer, LogisticRegression(C=.2, max_iter=2000))
    return {
        "seven_logistic": (FEATURES, logistic(FEATURES)),
        "five_logistic": (COMPACT, logistic(COMPACT)),
        "shallow_tree": (FEATURES, DecisionTreeClassifier(
            max_depth=3, min_samples_leaf=200, random_state=42)),
    }


def top_two(model, columns, rows):
    selected = rows.copy()
    selected["score"] = model.predict_proba(selected[list(columns)])[:, 1]
    selected = selected.sort_values(["signal_date", "score", "symbol6"],
                                    ascending=[True, False, True])
    selected["rank"] = selected.groupby("signal_date").cumcount() + 1
    return selected[selected["rank"].le(2)].copy()


def cash_simulation(selected, capital=Decimal("100000")):
    equity = capital
    peak = equity
    drawdown = Decimal(0)
    trades, daily = [], []
    for day, group in selected.groupby("signal_date", sort=True):
        if len(group) != 2:
            raise ValueError("Every test day needs two ranked candidates")
        before = equity
        for row in group.sort_values("rank").itertuples():
            entry = Decimal(str(row.entry_1440))
            closes = [Decimal(str(getattr(row, col))) for col in CLOSES]
            hit = next((ix for ix, price in enumerate(closes) if price > entry * Decimal("1.01")),
                       None)
            sell = closes[hit] if hit is not None else closes[-1]
            exit_minute = f"09:{hit+32:02}" if hit is not None else "09:40"
            budget = before * (Decimal("0.6") if row.rank == 1 else Decimal("0.4"))
            shares = buy_quantity(budget, entry, row.symbol6)
            pnl = (sell-entry) * shares
            equity += pnl
            trades.append({"signal_date": day, "next_date": row.next_date,
                           "rank": int(row.rank), "symbol6": row.symbol6, "name": row.name,
                           "score": float(row.score), "entry_1440": float(entry),
                           "budget": float(budget), "shares": shares, "bought": int(shares>0),
                           "max_close9_return": float(row.max_close9_return),
                           "target_class": int(row.target_class), "exit_minute": exit_minute,
                           "triggered": int(hit is not None), "exit_price": float(sell),
                           "pnl": float(pnl)})
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
        daily.append({"signal_date": day, "next_date": group.iloc[0].next_date,
                      "start_cash": float(before), "end_cash": float(equity),
                      "daily_pnl": float(equity-before)})
    report = {"signal_days": len(daily), "selected_stocks": len(trades),
              "bought_stocks": sum(t["bought"] for t in trades),
              "triggered_bought": sum(t["triggered"] and t["bought"] for t in trades),
              "strong": int(selected.target_class.eq(1).sum()),
              "neutral": int(selected.target_class.eq(0).sum()),
              "critical": int(selected.target_class.eq(-1).sum()),
              "ending_cash": float(equity), "return": float(equity/capital-1),
              "max_daily_drawdown": float(drawdown)}
    return report, pd.DataFrame(daily), pd.DataFrame(trades)


def metrics(selected):
    return {"days": int(selected.signal_date.nunique()), "stocks": len(selected),
            "strong": int(selected.target_class.eq(1).sum()),
            "neutral": int(selected.target_class.eq(0).sum()),
            "critical": int(selected.target_class.eq(-1).sum())}


def rank_tie_audit(model, columns, rows):
    scores = rows[["signal_date", "symbol6"]].copy()
    scores["score"] = model.predict_proba(rows[list(columns)])[:, 1]
    tied_at_second = {}
    for day, group in scores.groupby("signal_date"):
        cutoff = group.score.nlargest(2).iloc[-1]
        tied_at_second[day] = int(group.score.ge(cutoff).sum())
    return tied_at_second


def october_extension(conn, model, columns):
    candidates = pd.read_csv(OCT8, dtype={"symbol6": str})
    candidates["signal_date"] = "2026-10-08"
    candidates["next_date"] = "2026-10-09"
    scored = top_two(model, columns, candidates)
    symbols = scored.symbol6.tolist()
    placeholders = ",".join(["%s"] * len(symbols))
    entries = query(conn, "SELECT stk_code, latest_price, is_finalized, is_fallback, "
                    "bar_end_time, source_snapshot_time FROM aiia_stock_realtime_minute_snapshot_full "
                    "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                    f"AND bar_end_time=%s AND stk_code IN ({placeholders})",
                    ("2026-10-08", "2026-10-08 14:40:00", *symbols))
    if len(entries) != 2 or not (entries.is_finalized.eq(1) & entries.is_fallback.eq(0) &
                                 pd.to_datetime(entries.bar_end_time).eq(
                                     pd.to_datetime(entries.source_snapshot_time))).all():
        raise ValueError("October 8 exact 14:40 entry unavailable")
    entry_map = dict(zip(entries.stk_code.str[:6], entries.latest_price))
    result = []
    for row in scored.itertuples():
        raw = request_bars(row.symbol6, 0, 400)
        relevant = {int(bar["sttDateTime"]["shtTime"]): bar for bar in raw
                    if int(bar["sttDateTime"]["iDate"]) == 20261009 and
                    572 <= int(bar["sttDateTime"]["shtTime"]) <= 580}
        if set(relevant) != set(range(572, 581)):
            raise ValueError(f"Missing October 9 close minutes for {row.symbol6}")
        record = row._asdict()
        record["entry_1440"] = float(entry_map[row.symbol6])
        for minute in range(32, 41):
            record[f"close_{minute:02}"] = float(relevant[minute + 540]["fClose"])
        result.append(record)
    test = pd.DataFrame(result)
    test["max_close9_return"] = test[list(CLOSES)].max(axis=1) / test.entry_1440 - 1
    test["target_class"] = np.select(
        [test.max_close9_return.gt(.01), test.max_close9_return.lt(.005)],
        [1, -1], default=0)
    return test


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args()
    data = pd.read_csv(DATA, dtype={"symbol6": str})
    states = pd.read_csv(MARKET)
    split = split_dates(states)
    data["partition"] = data.signal_date.map(split)
    if data.partition.isna().any() or data.signal_date.nunique() != 40:
        raise ValueError("Split does not cover every exact signal date")
    train = data[data.partition.eq("train")]
    val = data[data.partition.eq("validation")]
    test = data[data.partition.eq("test")]
    weights = day_weights(train)
    fitted = {}
    validation = {}
    validation_rank_ties = {}
    for name, (columns, model) in models().items():
        target = train.target_class.eq(1).astype(int)
        if "logistic" in name:
            model.fit(train[list(columns)], target,
                      logisticregression__sample_weight=weights)
        else:
            model.fit(train[list(columns)], target, sample_weight=weights)
        fitted[name] = (columns, model)
        chosen = top_two(model, columns, val)
        validation[name] = metrics(chosen)
        validation_rank_ties[name] = rank_tie_audit(model, columns, val)
    # Validation only: a top-two score must identify at most two stocks per day.
    # A shallow tree has broad tied leaves and its code-order tie break is not a model rank.
    order = ["five_logistic", "seven_logistic", "shallow_tree"]
    eligible = [name for name in order if max(validation_rank_ties[name].values()) <= 2]
    if not eligible:
        raise ValueError("No model can rank two stocks without arbitrary score ties")
    winner = sorted(eligible, key=lambda name: (-validation[name]["strong"],
                                                 validation[name]["critical"],
                                                 order.index(name)))[0]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    test_results = {}
    for name, (columns, model) in fitted.items():
        selected = top_two(model, columns, test)
        report, daily, trades = cash_simulation(selected)
        test_results[name] = report
        selected[["signal_date", "next_date", "rank", "symbol6", "name", "score",
                  "max_close9_return", "target_class"]].to_csv(
                      args.output_dir / f"{name}_test_picks.csv", index=False)
        daily.to_csv(args.output_dir / f"{name}_test_daily.csv", index=False)
        trades.to_csv(args.output_dir / f"{name}_test_trades.csv", index=False)
    chosen_columns, chosen_model = fitted[winner]
    model_path = args.output_dir / "selected_close9_model.joblib"
    joblib.dump(chosen_model, model_path)
    if winner == "shallow_tree":
        explanation = export_text(chosen_model, feature_names=list(chosen_columns))
    else:
        numeric = [f for f in chosen_columns if f not in BINARY]
        scaler = chosen_model.named_steps["columntransformer"].named_transformers_["numeric"]
        logistic = chosen_model.named_steps["logisticregression"]
        explanation = {"intercept": float(logistic.intercept_[0]),
                       "coefficients": dict(zip(chosen_columns, map(float, logistic.coef_[0]))),
                       "numeric_means": dict(zip(numeric, map(float, scaler.mean_))),
                       "numeric_scales": dict(zip(numeric, map(float, scaler.scale_)))}
    conn = db_connection(args.env_file)
    try:
        oct8 = october_extension(conn, chosen_model, chosen_columns)
    finally:
        conn.rollback()
        conn.close()
    oct_result, oct_daily, oct_trades = cash_simulation(oct8)
    oct8[["signal_date", "next_date", "rank", "symbol6", "name", "score",
          "entry_1440", *CLOSES, "max_close9_return", "target_class"]].to_csv(
              args.output_dir / "selected_model_oct8_picks.csv", index=False)
    oct_trades.to_csv(args.output_dir / "selected_model_oct8_trades.csv", index=False)
    report = {"target": "max 09:32–09:40 minute closes / exact T 14:40 price - 1; strong if >1%, critical if <0.5%",
              "trading": "T 14:40 price proxy; first 09:32–09:40 minute close strictly > entry×1.01 exits at that close; otherwise 09:40 close. Rank 1 gets 60%, rank 2 gets 40%, trading units enforced.",
              "split_method": "40 exact dates grouped by T-1 market shape; within each group date-spaced 50% train, 25% validation, remaining test; no labels in date selection.",
              "split_dates": {part: sorted([date for date, value in split.items() if value == part])
                              for part in ("train", "validation", "test")},
              "split_rows": {"train": len(train), "validation": len(val), "test": len(test)},
              "train_target_counts": {str(label): int(train.target_class.eq(label).sum())
                                      for label in (1, 0, -1)},
              "validation_top2": validation,
              "validation_count_at_or_above_second_score": validation_rank_ties,
              "validation_rank_eligible_models": eligible,
              "model_selected_on_validation": winner,
              "test": test_results, "oct8_extension": oct_result,
              "model_explanation": explanation,
              "hashes": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (DATA, MARKET, OCT8, model_path)}}
    (args.output_dir / "experiment_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"split_rows": report["split_rows"], "validation": validation,
                      "winner": winner, "test": test_results[winner],
                      "oct8": oct_result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

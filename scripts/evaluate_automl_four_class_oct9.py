"""Replay the fixed four-class rules on the Oct 8 signal / Oct 9 morning."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix

from scripts.audit_automl_exit_variants import CLOSES, replay
from scripts.audit_automl_four_class_decisions import (
    class_priority_fallback, label_outcomes)
from scripts.benchmark_automl_1440_inference import (
    FEATURES, db_connection, query)
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_four_class_weighted import (
    BASELINE, MODEL, balanced_class_weights)
from scripts.experiment_automl_second_high_four_class import (
    CLASSES, WINDOW, four_class, models)
from scripts.experiment_automl_second_high_regression import SECOND_HIGHS


SIGNAL_DATE = "2026-10-08"
NEXT_DATE = "2026-10-09"
CANDIDATES = Path(
    "docs/stock_automl_runs/20261009_seven_factor_cash_backtest/20261008_exact_candidates.csv")
OUT = Path("docs/stock_automl_runs/20261010_second_high_four_class_oct9")


def morning_bars(conn, codes: list[str]) -> pd.DataFrame:
    placeholders = ",".join(["%s"]*len(codes))
    bars = query(conn, "SELECT LEFT(stk_code,6) symbol6, bar_end_time, "
                 "high_price, latest_price, is_finalized, is_fallback, "
                 "source_snapshot_time FROM aiia_stock_realtime_minute_snapshot_full "
                 "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                 "AND bar_end_time BETWEEN %s AND %s "
                 "AND LEFT(stk_code,6) IN ("+placeholders+") "
                 "ORDER BY stk_code, bar_end_time",
                 (NEXT_DATE, f"{NEXT_DATE} 09:31:00",
                  f"{NEXT_DATE} 09:40:00", *codes))
    if (len(bars) != len(codes)*10 or bars.duplicated(
            ["symbol6", "bar_end_time"]).any()
            or not bars.groupby("symbol6").size().eq(10).all()
            or set(bars.symbol6) != set(codes)):
        raise ValueError("Missing or duplicate October 9 morning bars")
    if not (bars.is_finalized.eq(1) & bars.is_fallback.eq(0)
            & pd.to_datetime(bars.bar_end_time).eq(
                pd.to_datetime(bars.source_snapshot_time))).all():
        raise ValueError("October 9 bars are not exact and finalized")
    bars["minute"] = pd.to_datetime(bars.bar_end_time).dt.strftime("%H:%M")
    if set(bars.minute) != {f"09:{minute:02}" for minute in range(31, 41)}:
        raise ValueError("Unexpected October 9 minute set")
    bars[["high_price", "latest_price"]] = bars[
        ["high_price", "latest_price"]].astype(float)
    if not bars[["high_price", "latest_price"]].gt(0).all().all():
        raise ValueError("Invalid October 9 prices")
    return bars[["symbol6", "minute", "high_price", "latest_price"]]


def score_current(current: pd.DataFrame, train: pd.DataFrame) -> pd.DataFrame:
    scored = []
    weights = balanced_class_weights(train["class"])
    for name in (BASELINE, MODEL):
        model = models()["small_boosted_tree"]
        fit_args = {} if name == BASELINE else {"sample_weight":
                    train["class"].astype(str).map(weights).to_numpy(dtype=float)}
        model.fit(train[list(FEATURES)], train["class"].astype(str), **fit_args)
        probability = model.predict_proba(current[list(FEATURES)])
        labels = list(model.classes_)
        result = current[["signal_date", "next_date", "symbol6", "name",
                          "entry_1440", *FEATURES]].copy()
        result["model"] = name
        for label in CLASSES:
            result[f"p_{label}"] = probability[:, labels.index(label)]
        result["predicted_class"] = np.asarray(labels)[probability.argmax(axis=1)]
        scored.append(result)
    return pd.concat(scored, ignore_index=True)


def select_current(scored: pd.DataFrame) -> pd.DataFrame:
    selected = []
    for _, group in scored.groupby("model"):
        fallback = class_priority_fallback(group)
        fallback["selection_rule"] = "class_priority_fallback"
        selected.append(fallback)
        forced = group.sort_values(["p_ge3", "symbol6"],
                                   ascending=[False, True]).head(2).copy()
        forced["selection_rank"] = range(1, len(forced)+1)
        forced["selection_rule"] = "p_ge3"
        selected.append(forced)
    return pd.concat(selected, ignore_index=True)


def add_outcomes(scored: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    second = bars.groupby("symbol6").high_price.apply(
        lambda prices: float(np.sort(prices.to_numpy())[-2])).rename(
            "second_high").reset_index()
    closes = bars.pivot(index="symbol6", columns="minute",
                        values="latest_price").rename(columns={
        f"09:{minute:02}": f"close_{minute:02}" for minute in range(31, 41)
    }).reset_index()
    result = scored.merge(second, on="symbol6", validate="many_to_one").merge(
        closes, on="symbol6", validate="many_to_one")
    if len(result) != len(scored) or result[["second_high", *CLOSES]].isna().any().any():
        raise ValueError("Incomplete outcome join")
    result["second_high_return"] = result.second_high/result.entry_1440-1
    result["class"] = four_class(result.second_high_return)
    return result


def summarize(picks: pd.DataFrame, trades: pd.DataFrame,
              predictions: pd.DataFrame) -> dict:
    result = {}
    for model, group in predictions.groupby("model"):
        matrix = confusion_matrix(group["class"].astype(str),
                                  group.predicted_class, labels=list(CLASSES))
        result[model] = {
            "candidate_class_counts": {label: int(group["class"].eq(label).sum())
                                       for label in CLASSES},
            "predicted_class_counts": {label: int(group.predicted_class.eq(label).sum())
                                       for label in CLASSES},
            "candidate_confusion_rows_actual_columns_predicted": matrix.tolist(),
            "candidate_accuracy": float(np.trace(matrix)/len(group)),
            "selection": {},
        }
        for rule, selection in picks[picks.model.eq(model)].groupby("selection_rule"):
            by_rank = {}
            for k in (1, 2):
                chosen = selection[selection.selection_rank.le(k)]
                exits = trades[(trades.model.eq(model))
                               & trades.selection_rule.eq(rule)
                               & trades.selection_rank.le(k)]
                by_rank[f"top{k}"] = {
                    "labels": label_outcomes(chosen),
                    "exits": {strategy: {
                        "trades": len(x),
                        "mean_trade_return_pct": float(x.exit_return.mean()*100),
                        "positive": int(x.exit_return.gt(0).sum()),
                        "below_minus1": int(x.exit_return.lt(-.01).sum()),
                    } for strategy, x in exits.groupby("exit_strategy")},
                }
            result[model]["selection"][rule] = by_rank
    return result


def run(env_file: Path = Path("/Volumes/ext/fin_agent/.env")) -> dict:
    historical = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(SECOND_HIGHS, dtype={"symbol6": str})
    history = historical.merge(prices[["next_date", "symbol6", "second_high"]],
                               on=["next_date", "symbol6"], validate="one_to_one")
    dates = sorted(history.signal_date.unique())
    if len(history) != 14292 or len(dates) != 40 or dates[-1] != "2026-09-30":
        raise ValueError("Historical training source changed")
    history["second_high_return"] = history.second_high/history.entry_1440-1
    history["class"] = four_class(history.second_high_return)
    train_dates = dates[-WINDOW:]
    train = history[history.signal_date.isin(train_dates)].copy()
    current = pd.read_csv(CANDIDATES, dtype={"symbol6": str})
    if (len(current) != 178 or current.symbol6.duplicated().any()
            or current[list(FEATURES)].isna().any().any()
            or not current.signal_return.between(.03, .06).all()):
        raise ValueError("October 8 seven-factor candidate source changed")
    current["signal_date"] = SIGNAL_DATE
    current["next_date"] = NEXT_DATE
    conn = db_connection(env_file)
    try:
        codes = current.symbol6.tolist()
        placeholders = ",".join(["%s"]*len(codes))
        entry = query(conn, "SELECT LEFT(stk_code,6) symbol6, latest_price entry_1440, "
                      "is_finalized, is_fallback, bar_end_time, source_snapshot_time "
                      "FROM aiia_stock_realtime_minute_snapshot_full "
                      "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                      "AND bar_end_time=%s AND LEFT(stk_code,6) IN ("+placeholders+")",
                      (SIGNAL_DATE, f"{SIGNAL_DATE} 14:40:00", *codes))
        preclose = query(conn, "SELECT LEFT(stk_code,6) symbol6, preclose "
                         "FROM kcrp_stock_price WHERE trade_date=%s "
                         "AND LEFT(stk_code,6) IN ("+placeholders+")",
                         (SIGNAL_DATE, *codes))
        if (len(entry) != len(codes) or len(preclose) != len(codes)
                or entry.symbol6.duplicated().any() or preclose.symbol6.duplicated().any()
                or not (entry.is_finalized.eq(1) & entry.is_fallback.eq(0)
                        & pd.to_datetime(entry.bar_end_time).eq(
                            pd.to_datetime(entry.source_snapshot_time))).all()):
            raise ValueError("October 8 exact entry validation failed")
        current = current.merge(entry[["symbol6", "entry_1440"]],
                                on="symbol6", validate="one_to_one").merge(
            preclose, on="symbol6", validate="one_to_one")
        current[["entry_1440", "preclose"]] = current[
            ["entry_1440", "preclose"]].astype(float)
        if (not current[["entry_1440", "preclose"]].gt(0).all().all()
                or not np.allclose(current.entry_1440/current.preclose-1,
                                   current.signal_return, atol=1e-10, rtol=0)):
            raise ValueError("October 8 candidate factors disagree with exact entry")
        scored = score_current(current, train)
        picks = select_current(scored)
        OUT.mkdir(parents=True, exist_ok=True)
        scored.to_csv(OUT / "predictions_before_outcomes.csv", index=False)
        picks.to_csv(OUT / "selected_before_outcomes.csv", index=False)
        # Predictions and picks are frozen before reading the next morning's prices.
        bars = morning_bars(conn, codes)
    finally:
        conn.rollback()
        conn.close()
    bars.to_csv(OUT / "morning_bars.csv", index=False)
    predictions = add_outcomes(scored, bars)
    selected = picks.merge(predictions[["model", "symbol6", "second_high",
                                        "second_high_return", "class", *CLOSES]],
                           on=["model", "symbol6"], validate="many_to_one")
    if len(selected) != len(picks):
        raise ValueError("Selected picks did not match outcome records")
    trades = replay(selected)
    predictions.to_csv(OUT / "predictions_with_outcomes.csv", index=False)
    selected.to_csv(OUT / "selected_with_outcomes.csv", index=False)
    trades.to_csv(OUT / "exit_trades.csv", index=False)
    summary = {
        "signal_date": SIGNAL_DATE, "exit_date": NEXT_DATE,
        "training_signal_dates": [train_dates[0], train_dates[-1]],
        "training_rows": len(train), "candidate_rows": len(current),
        "selection_frozen_before_outcome_query": True,
        "weights": balanced_class_weights(train["class"]),
        "models": summarize(selected, trades, predictions),
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (DATA, SECOND_HIGHS, CANDIDATES,
                                       OUT / "predictions_before_outcomes.csv",
                                       OUT / "selected_before_outcomes.csv",
                                       OUT / "morning_bars.csv")},
    }
    (OUT / "summary.json").write_text(json.dumps(
        summary, ensure_ascii=False, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))

"""Compare four-class accuracy baselines and a diagnostic class-priority fallback."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.backtest_automl_staged_minute_exit import exit_on_closes
from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_second_high_four_class import (
    CLASSES, OUT, four_class, models)
from scripts.experiment_automl_second_high_regression import SECOND_HIGHS


BASE = Path("docs/stock_automl_runs/20261009_close9_target/exact_1440_to_close9_candidates.csv.gz")
FIRST = Path("docs/stock_automl_runs/20261009_close10_target/exact_0931_closes.csv.gz")


def matrix_metrics(matrix: pd.DataFrame) -> dict:
    values = matrix.loc[list(CLASSES), list(CLASSES)].to_numpy(dtype=int)
    total = int(values.sum())
    if total == 0:
        raise ValueError("Empty confusion matrix")
    grouped = {}
    for predicted in ("ge3", "1to3"):
        column = matrix[predicted]
        count = int(column.sum())
        grouped[predicted] = {
            "predicted_count": count,
            "actual_ge1": int(column["1to3"]+column["ge3"]),
            "actual_0to1": int(column["0to1"]),
            "actual_lt0": int(column["lt0"]),
        }
    return {
        "total": total, "correct": int(np.trace(values)),
        "accuracy": float(np.trace(values)/total),
        "uniform_random_four_class_accuracy": .25,
        "majority_label": CLASSES[int(values.sum(axis=1).argmax())],
        "majority_class_accuracy": float(values.sum(axis=1).max()/total),
        "predicted_column_groups": grouped,
    }


def class_priority_fallback(frame: pd.DataFrame) -> pd.DataFrame:
    """Diagnostic only: >=3% class first, then 1–3% class, up to two/day."""
    eligible = frame[frame.predicted_class.isin(("ge3", "1to3"))].copy()
    eligible["p_ge1"] = eligible.p_1to3+eligible.p_ge3
    eligible["class_priority"] = eligible.predicted_class.map({"ge3": 0, "1to3": 1})
    eligible["within_class_score"] = eligible.p_ge3.where(
        eligible.predicted_class.eq("ge3"), eligible.p_ge1)
    eligible = eligible.sort_values(
        ["signal_date", "class_priority", "within_class_score", "symbol6"],
        ascending=[True, True, False, True])
    eligible["selection_rank"] = eligible.groupby("signal_date").cumcount()+1
    return eligible[eligible.selection_rank.le(2)].copy()


def label_outcomes(frame: pd.DataFrame) -> dict:
    n = len(frame)
    return {"selected": n, "days": int(frame.signal_date.nunique()),
            "actual_ge1": int(frame["class"].isin(("1to3", "ge3")).sum()),
            "actual_ge3": int(frame["class"].eq("ge3").sum()),
            "actual_0to1": int(frame["class"].eq("0to1").sum()),
            "actual_lt0": int(frame["class"].eq("lt0").sum()),
            "actual_below_minus1": int(frame.second_high_return.lt(-.01).sum()),
            "mean_second_high_return_pct": float(frame.second_high_return.mean()*100)}


def selection_columns(frame: pd.DataFrame) -> dict:
    """Actual label counts grouped by the selected stocks' predicted class."""
    result = {}
    for predicted in CLASSES:
        subset = frame[frame.predicted_class.eq(predicted)]
        result[predicted] = {
            "selected": len(subset),
            "actual_ge1": int(subset["class"].isin(("1to3", "ge3")).sum()),
            "actual_0to1": int(subset["class"].eq("0to1").sum()),
            "actual_lt0": int(subset["class"].eq("lt0").sum()),
        }
    return result


def same_fold_train_top2() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Refit each forward fold and select two within each day of its training window."""
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(SECOND_HIGHS, dtype={"symbol6": str})
    data = base.merge(prices[["next_date", "symbol6", "second_high"]],
                      on=["next_date", "symbol6"], validate="one_to_one")
    data["second_high_return"] = data.second_high/data.entry_1440-1
    data["class"] = four_class(data.second_high_return)
    dates = sorted(data.signal_date.unique())
    if len(data) != 14292 or len(dates) != 40:
        raise ValueError("Expected the saved 40-day exact candidate pool")
    fallback_rows, forced_rows = [], []
    for index in range(20, len(dates)):
        train = data[data.signal_date.isin(dates[index-20:index])].copy()
        model = models()["small_boosted_tree"]
        model.fit(train[list(FEATURES)], train["class"].astype(str))
        labels = list(model.classes_)
        probability = model.predict_proba(train[list(FEATURES)])
        train["predicted_class"] = np.asarray(labels)[probability.argmax(axis=1)]
        for label in CLASSES:
            train[f"p_{label}"] = probability[:, labels.index(label)]
        train["fit_test_date"] = dates[index]
        fallback_rows.append(class_priority_fallback(train))
        forced = train.sort_values(["signal_date", "p_ge3", "symbol6"],
                                   ascending=[True, False, True]).copy()
        forced["selection_rank"] = forced.groupby("signal_date").cumcount()+1
        forced_rows.append(forced[forced.selection_rank.le(2)])
    return (pd.concat(fallback_rows, ignore_index=True),
            pd.concat(forced_rows, ignore_index=True))


def exit_observations(selection: pd.DataFrame) -> pd.DataFrame:
    base = pd.read_csv(BASE, dtype={"symbol6": str})
    first = pd.read_csv(FIRST, dtype={"symbol6": str})
    closes = [f"close_{minute:02}" for minute in range(31, 41)]
    rows = selection[["signal_date", "next_date", "symbol6", "name", "entry_1440",
                      "selection_rank", "predicted_class", "class"]].merge(
        base[["signal_date", "symbol6", *closes[1:]]],
        on=["signal_date", "symbol6"], validate="one_to_one").merge(
        first[["next_date", "symbol6", closes[0]]],
        on=["next_date", "symbol6"], validate="one_to_one")
    if len(rows) != len(selection):
        raise ValueError("Missing exact minute closes for fallback selection")
    results = []
    for _, row in rows.iterrows():
        result = exit_on_closes(row.entry_1440, [row[column] for column in closes])
        results.append({"signal_date": row.signal_date, "next_date": row.next_date,
                        "symbol6": row.symbol6, "name": row["name"],
                        "selection_rank": int(row.selection_rank),
                        "predicted_class": row.predicted_class,
                        "actual_class": row["class"], **result})
    return pd.DataFrame(results)


def run() -> dict:
    train = pd.read_csv(OUT / "train_confusion_aggregate.csv", index_col=0)
    test = pd.read_csv(OUT / "next_day_confusion_aggregate.csv", index_col=0)
    predictions = pd.read_csv(OUT / "predictions.csv.gz", dtype={"symbol6": str})
    boosted = predictions[predictions.model.eq("small_boosted_tree")].copy()
    selected = class_priority_fallback(boosted)
    selected.to_csv(OUT / "class_priority_fallback_top2.csv", index=False)
    exits = exit_observations(selected)
    exits.to_csv(OUT / "class_priority_fallback_exits.csv", index=False)
    forced = boosted[boosted["rank"].le(2)].copy()
    forced["selection_rank"] = forced["rank"]
    forced_exits = exit_observations(forced)
    train_fallback, train_forced = same_fold_train_top2()
    train_fallback.to_csv(OUT / "same_fold_train_class_priority_top2.csv", index=False)
    train_forced.to_csv(OUT / "same_fold_train_forced_p_ge3_top2.csv", index=False)
    summary = {
        "train_confusion": matrix_metrics(train),
        "next_day_confusion": matrix_metrics(test),
        "grouping": "Actual second-high return >=1% / [0,1%) / <0%; differs from historical close9 >1% / [0.5,1%] / <0.5% target",
        "fallback_rule": "predicted ge3 first by P(ge3), then predicted 1to3 by P(>=1); at most two/day; retrospective diagnostic",
        "fallback": label_outcomes(selected),
        "forced_p_ge3_top2": label_outcomes(forced),
        "top2_by_predicted_class": {
            "same_fold_train_class_priority": selection_columns(train_fallback),
            "same_fold_train_forced_p_ge3": selection_columns(train_forced),
            "next_day_class_priority": selection_columns(selected),
            "next_day_forced_p_ge3": selection_columns(forced),
        },
        "fallback_minute_close_exit": {
            "positive": int(exits.exit_return.gt(0).sum()),
            "negative": int(exits.exit_return.lt(0).sum()),
            "below_minus1": int(exits.exit_return.lt(-.01).sum()),
            "mean_return_pct": float(exits.exit_return.mean()*100)},
        "forced_minute_close_exit": {
            "positive": int(forced_exits.exit_return.gt(0).sum()),
            "negative": int(forced_exits.exit_return.lt(0).sum()),
            "below_minus1": int(forced_exits.exit_return.lt(-.01).sum()),
            "mean_return_pct": float(forced_exits.exit_return.mean()*100)},
    }
    (OUT / "class_priority_fallback_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))

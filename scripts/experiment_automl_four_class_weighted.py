"""Compare fold-balanced four-class training with the saved unweighted tree."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             confusion_matrix, roc_auc_score)

from scripts.audit_automl_exit_variants import replay, with_minute_closes
from scripts.audit_automl_four_class_decisions import (
    class_priority_fallback, label_outcomes)
from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_second_high_four_class import (
    CLASSES, OUT as OLD, WINDOW, four_class, models)
from scripts.experiment_automl_second_high_regression import SECOND_HIGHS


OUT = Path("docs/stock_automl_runs/20261010_second_high_four_class_weighted")
MODEL = "balanced_class_weight"
BASELINE = "unweighted"


def balanced_class_weights(labels: pd.Series) -> dict[str, float]:
    """Each observed class contributes one quarter of total training weight."""
    counts = labels.astype(str).value_counts().reindex(CLASSES, fill_value=0)
    if counts.eq(0).any():
        raise ValueError("All four classes are required in a training fold")
    return {label: float(len(labels)/(len(CLASSES)*counts[label]))
            for label in CLASSES}


def score_frame(frame: pd.DataFrame, probability: np.ndarray,
                labels: list[str], model_name: str) -> pd.DataFrame:
    result = frame[["signal_date", "next_date", "symbol6", "name",
                    "entry_1440", "second_high_return", "class"]].copy()
    result["model"] = model_name
    for label in CLASSES:
        result[f"p_{label}"] = probability[:, labels.index(label)]
    result["predicted_class"] = np.asarray(labels)[probability.argmax(axis=1)]
    return result


def prediction_metrics(frame: pd.DataFrame) -> dict:
    true = frame["class"].astype(str)
    probability = frame[[f"p_{label}" for label in CLASSES]].to_numpy()
    true_index = true.map({label: index for index, label in enumerate(CLASSES)}).to_numpy()
    matrix = confusion_matrix(true, frame.predicted_class, labels=CLASSES)
    return {
        "rows": len(frame), "days": int(frame.signal_date.nunique()),
        "accuracy": float(accuracy_score(true, frame.predicted_class)),
        "balanced_accuracy": float(balanced_accuracy_score(true, frame.predicted_class)),
        "log_loss_on_natural_distribution": float(-np.log(np.clip(
            probability[np.arange(len(frame)), true_index], 1e-15, 1)).mean()),
        "ge3_auc": float(roc_auc_score(true.eq("ge3"), frame.p_ge3)),
        "actual_counts": {label: int(true.eq(label).sum()) for label in CLASSES},
        "predicted_counts": {label: int(frame.predicted_class.eq(label).sum())
                             for label in CLASSES},
        "confusion_rows_actual_columns_predicted": matrix.tolist(),
    }


def select(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    if rule == "class_priority_fallback":
        result = class_priority_fallback(frame)
    elif rule == "p_ge3":
        result = frame.sort_values(
            ["signal_date", "p_ge3", "symbol6"],
            ascending=[True, False, True]).copy()
        result["selection_rank"] = result.groupby("signal_date").cumcount()+1
        result = result[result.selection_rank.le(2)].copy()
    else:
        raise ValueError(f"Unknown selection rule: {rule}")
    result["selection_rule"] = rule
    return result


def summarize_selections(selected: pd.DataFrame, trades: pd.DataFrame,
                         test_dates: list[str]) -> list[dict]:
    rows = []
    for (model, rule), picks in selected.groupby(["model", "selection_rule"]):
        for top_k in (1, 2):
            subset = picks[picks.selection_rank.le(top_k)]
            outcomes = label_outcomes(subset)
            same_trades = trades[
                trades.model.eq(model) & trades.selection_rule.eq(rule)
                & trades.selection_rank.le(top_k)]
            for strategy, group in same_trades.groupby("exit_strategy"):
                if len(group) != len(subset):
                    raise ValueError("Selected picks and exit records differ")
                daily = group.groupby("signal_date").exit_return.mean()
                rows.append({
                    "model": model, "selection_rule": rule,
                    "top_k": top_k, "exit_strategy": strategy,
                    **outcomes, "days_skipped": len(test_dates)-outcomes["days"],
                    "mean_trade_exit_pct": float(group.exit_return.mean()*100),
                    "median_trade_exit_pct": float(group.exit_return.median()*100),
                    "mean_calendar_day_exit_pct_with_skip_zero": float(
                        daily.reindex(test_dates, fill_value=0).mean()*100),
                    "positive_exits": int(group.exit_return.gt(0).sum()),
                    "below_minus1_exits": int(group.exit_return.lt(-.01).sum()),
                })
    return rows


def run(expected_rows: int = 14292) -> dict:
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(SECOND_HIGHS, dtype={"symbol6": str})
    data = base.merge(prices[["next_date", "symbol6", "second_high"]],
                      on=["next_date", "symbol6"], validate="one_to_one")
    dates = sorted(data.signal_date.unique())
    if len(data) != expected_rows or len(dates) != 40 or data[list(FEATURES)].isna().any().any():
        raise ValueError("Expected the same complete 40-day candidate pool")
    data["second_high_return"] = data.second_high/data.entry_1440-1
    data["class"] = four_class(data.second_high_return)
    train_scored, test_scored, fold_rows = [], [], []
    for index in range(WINDOW, len(dates)):
        train_dates = dates[index-WINDOW:index]
        train = data[data.signal_date.isin(train_dates)].copy()
        test = data[data.signal_date.eq(dates[index])].copy()
        weights = balanced_class_weights(train["class"])
        sample_weight = train["class"].astype(str).map(weights).to_numpy(dtype=float)
        model = models()["small_boosted_tree"]
        model.fit(train[list(FEATURES)], train["class"].astype(str),
                  sample_weight=sample_weight)
        labels = list(model.classes_)
        fitted = score_frame(train, model.predict_proba(train[list(FEATURES)]),
                             labels, MODEL)
        predicted = score_frame(test, model.predict_proba(test[list(FEATURES)]),
                                labels, MODEL)
        train_scored.append(fitted)
        test_scored.append(predicted)
        fold_rows.append({
            "test_date": dates[index], "train_start": train_dates[0],
            "train_end": train_dates[-1], "train_rows": len(train),
            "test_rows": len(test),
            **{f"weight_{label}": weights[label] for label in CLASSES},
            "train_accuracy": float(accuracy_score(
                fitted["class"].astype(str), fitted.predicted_class)),
            "train_balanced_accuracy": float(balanced_accuracy_score(
                fitted["class"].astype(str), fitted.predicted_class)),
            "test_accuracy": float(accuracy_score(
                predicted["class"].astype(str), predicted.predicted_class)),
            "test_balanced_accuracy": float(balanced_accuracy_score(
                predicted["class"].astype(str), predicted.predicted_class)),
        })
        print(f"weighted test {dates[index]}: {len(test)} candidates", flush=True)
    weighted = pd.concat(test_scored, ignore_index=True)
    fitted = pd.concat(train_scored, ignore_index=True)
    old = pd.read_csv(OLD / "predictions.csv.gz", dtype={"symbol6": str})
    baseline = old[old.model.eq("small_boosted_tree")].copy()
    baseline["model"] = BASELINE
    if len(weighted) != len(baseline) or not weighted[
            ["signal_date", "symbol6"]].sort_values(
                ["signal_date", "symbol6"]).reset_index(drop=True).equals(
            baseline[["signal_date", "symbol6"]].sort_values(
                ["signal_date", "symbol6"]).reset_index(drop=True)):
        raise ValueError("Weighted and unweighted candidate pools differ")
    selected = pd.concat([
        select(frame, rule)
        for frame in (baseline, weighted)
        for rule in ("class_priority_fallback", "p_ge3")], ignore_index=True)
    trades = replay(with_minute_closes(selected))
    summaries = summarize_selections(selected, trades, dates[WINDOW:])
    prior_summary = OLD / "class_priority_fallback_summary.json"
    if prior_summary.exists():
        old_fallback = json.loads(prior_summary.read_text())
        reference = next(row for row in summaries if
                         row["model"] == BASELINE
                         and row["selection_rule"] == "class_priority_fallback"
                         and row["top_k"] == 2
                         and row["exit_strategy"] == "original_four_rules")
        if not np.isclose(reference["mean_trade_exit_pct"],
                          old_fallback["fallback_minute_close_exit"]["mean_return_pct"]):
            raise ValueError("Unweighted baseline did not reproduce the saved exit return")
    summary = {
        "weight_rule": "Within each 20-day training fold, weight(class)=train_rows/(4*class_count); each class has equal total training weight. No test labels enter weights.",
        "same_model": "HistGradientBoostingClassifier; 60 iterations, learning rate .05, 7 leaves, min leaf 100, L2 5; seven original features.",
        "test_dates": [dates[WINDOW], dates[-1]],
        "baseline_next_day": prediction_metrics(baseline),
        "weighted_training_repeated_windows": prediction_metrics(fitted),
        "weighted_next_day": prediction_metrics(weighted),
        "selection_results": summaries,
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (DATA, SECOND_HIGHS, OLD / "predictions.csv.gz")},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    weighted.to_csv(OUT / "predictions.csv.gz", index=False, compression="gzip")
    pd.DataFrame(fold_rows).to_csv(OUT / "fold_metrics.csv", index=False)
    selected.to_csv(OUT / "daily_top2.csv", index=False)
    trades.to_csv(OUT / "exit_trades.csv", index=False)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--baseline", type=Path, default=OLD)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--expected-rows", type=int, default=14292)
    args = parser.parse_args()
    DATA, OLD, OUT = args.data, args.baseline, args.out
    print(json.dumps(run(args.expected_rows), ensure_ascii=False, indent=2))

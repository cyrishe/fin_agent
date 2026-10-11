"""Chronological three-class study of the 14:40 tail candidate table.

The label is next morning's first-ten-minute high relative to the 14:50
entry-price proxy. Stock-level predictions stay in an ignored local output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (balanced_accuracy_score, confusion_matrix, f1_score,
                             log_loss)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text

from scripts.build_automl_tail_standard import FEATURES


TARGET = "target_next_high10_return"
CLASSES = (-1, 0, 1)
BINARY = ("volume_4of5_increasing", "ma_bull_5_10_20",
          "price_above_all_ma", "all_intraday_lows_above_ma")
NUMERIC = tuple(feature for feature in FEATURES if feature not in BINARY)
COMPACT_FEATURES = ("signal_return", "volume_ratio", "turnover_so_far_pct",
                    "float_mv_100m_cny", *BINARY)
TRAIN_DAYS = 10
BLOCK_DAYS = 5
SCORE_THRESHOLD = 0.75
MAX_PER_DAY = 2
MAX_PER_WEEK = 5


def classify_high10(returns):
    """Strict boundaries: >1%=1, <0.5%=-1, otherwise 0."""
    values = np.asarray(returns, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("The ten-minute target must be finite")
    return np.select((values > 0.01, values < 0.005), (1, -1), default=0).astype(int)


def read_samples(path):
    rows = pd.read_csv(path, dtype={"symbol6": str})
    required = {"signal_date", "next_date", "symbol6", "name", TARGET, *FEATURES}
    missing = sorted(required.difference(rows.columns))
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    if rows.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate signal date and stock code")
    rows["signal_date"] = pd.to_datetime(rows.signal_date)
    rows["next_date"] = pd.to_datetime(rows.next_date)
    for feature in BINARY:
        if not rows[feature].isin([0, 1, True, False]).all():
            raise ValueError(f"{feature} must contain only zero or one")
        rows[feature] = rows[feature].astype(int)
    for feature in NUMERIC:
        rows[feature] = pd.to_numeric(rows[feature], errors="raise")
    if rows[list(FEATURES)].isna().any().any() or not np.isfinite(
            rows[list(FEATURES)].to_numpy(dtype=float)).all():
        raise ValueError("All training features must be finite")
    rows["class"] = classify_high10(rows[TARGET])
    return rows.sort_values(["signal_date", "symbol6"]).reset_index(drop=True)


def day_folds(rows):
    days = sorted(rows.signal_date.unique())
    if len(days) < TRAIN_DAYS + BLOCK_DAYS:
        raise ValueError("Not enough trading days for chronological evaluation")
    for end in range(TRAIN_DAYS, len(days), BLOCK_DAYS):
        testing_days = days[end:end + BLOCK_DAYS]
        training = rows[rows.signal_date.isin(days[:end])]
        testing = rows[rows.signal_date.isin(testing_days)]
        yield training, testing


def make_models():
    def logistic(features):
        numeric = [feature for feature in features if feature not in BINARY]
        binary = [feature for feature in features if feature in BINARY]
        scaled = ColumnTransformer([
            ("numeric", StandardScaler(), numeric),
            ("binary", "passthrough", binary),
        ])
        return make_pipeline(scaled, LogisticRegression(C=0.2, max_iter=2000))

    return {
        "三分类逻辑回归": (FEATURES, logistic(FEATURES)),
        "八因子逻辑回归_探索性": (COMPACT_FEATURES, logistic(COMPACT_FEATURES)),
        "三分类浅决策树": (FEATURES, DecisionTreeClassifier(
            max_depth=3, min_samples_leaf=120, random_state=42)),
    }


def model_explanation(model, name, features):
    if "逻辑回归" in name:
        fitted = model.named_steps["logisticregression"]
        class_index = list(fitted.classes_).index(1)
        ordered = [feature for feature in features if feature not in BINARY] + [
            feature for feature in features if feature in BINARY]
        coefficients = dict(zip(ordered, fitted.coef_[class_index]))
        return [{"feature": key, "positive_class_logit_coefficient": round(float(value), 5),
                "unit": "one standard deviation" if key not in BINARY else "zero to one"}
                for key, value in sorted(coefficients.items(),
                                         key=lambda item: abs(item[1]), reverse=True)]
    return export_text(model, feature_names=list(features), max_depth=3)


def out_of_sample_predictions(rows):
    predictions = []
    fold_manifest = []
    for fold_number, (training, testing) in enumerate(day_folds(rows), 1):
        y_train = training["class"]
        fold_manifest.append({
            "fold": fold_number,
            "train_start": training.signal_date.min().date().isoformat(),
            "train_end": training.signal_date.max().date().isoformat(),
            "test_start": testing.signal_date.min().date().isoformat(),
            "test_end": testing.signal_date.max().date().isoformat(),
            "train_rows": len(training), "test_rows": len(testing),
        })
        for name, (features, model) in make_models().items():
            x_test = testing[list(features)]
            model.fit(training[list(features)], y_train)
            probabilities = model.predict_proba(x_test)
            probability = pd.DataFrame(probabilities, columns=model.classes_, index=testing.index)
            result = testing[["signal_date", "next_date", "symbol6", "name", TARGET,
                              "class"]].copy()
            result["model"] = name
            result["fold"] = fold_number
            for category, column in zip(CLASSES, ("p_negative", "p_neutral", "p_positive")):
                result[column] = probability[category]
            result["predicted_class"] = model.predict(x_test)
            predictions.append(result)
    return pd.concat(predictions, ignore_index=True), fold_manifest


def select_online(rows, threshold=SCORE_THRESHOLD):
    """Only present and past signal scores determine a week's selections."""
    selected = []
    weekly_used = {}
    for day, pool in rows.groupby("signal_date", sort=True):
        week = day.to_period("W-SUN").start_time.date().isoformat()
        remaining = MAX_PER_WEEK - weekly_used.get(week, 0)
        if remaining <= 0:
            continue
        eligible = pool[pool.p_positive >= threshold].sort_values(
            ["p_positive", "symbol6"], ascending=[False, True])
        chosen = eligible.head(min(MAX_PER_DAY, remaining))
        selected.extend(chosen.index.tolist())
        weekly_used[week] = weekly_used.get(week, 0) + len(chosen)
    return rows.loc[selected].sort_values(["signal_date", "p_positive"],
                                           ascending=[True, False]).copy()


def positive_precision(rows, day_rates):
    if rows.empty:
        return {"n": 0, "positive": 0, "precision": None,
                "same_day_base": None, "lift": None, "days": 0, "weeks": 0}
    precision = float(rows["class"].eq(1).mean())
    same_day_base = float(rows.signal_date.map(day_rates).mean())
    return {
        "n": len(rows), "positive": int(rows["class"].eq(1).sum()),
        "precision": precision, "same_day_base": same_day_base,
        "lift": precision / same_day_base if same_day_base else None,
        "days": rows.signal_date.nunique(),
        "weeks": rows.signal_date.dt.to_period("W-SUN").nunique(),
    }


def score_bands(rows, day_rates):
    bins = [0, 0.5, 0.6, 0.7, 0.8, 1.0000001]
    groups = pd.cut(rows.p_positive, bins=bins, right=False, include_lowest=True)
    return [{"score_band": str(interval), **positive_precision(group, day_rates)}
            for interval, group in rows.groupby(groups, observed=True)]


def binary_factor_diagnostics(rows):
    """Descriptive associations on the test dates, adjusted for each day's pool."""
    day_rates = rows.groupby("signal_date")["class"].apply(lambda s: s.eq(1).mean())
    results = {}
    for feature in BINARY:
        groups = []
        for value, group in rows.groupby(feature):
            positive_rate = float(group["class"].eq(1).mean())
            same_day_base = float(group.signal_date.map(day_rates).mean())
            groups.append({"value": int(value), "n": len(group),
                           "positive_rate": positive_rate,
                           "same_day_base": same_day_base,
                           "difference_from_same_day_base": positive_rate - same_day_base})
        results[feature] = groups
    return results


def evaluate_model(rows):
    day_rates = rows.groupby("signal_date")["class"].apply(lambda s: s.eq(1).mean())
    classes = list(CLASSES)
    probabilities = rows[["p_negative", "p_neutral", "p_positive"]].to_numpy()
    online = select_online(rows)
    metrics = {
        "rows": len(rows),
        "class_counts": {str(label): int(rows["class"].eq(label).sum()) for label in classes},
        "positive_pool_base_rate": float(rows["class"].eq(1).mean()),
        "accuracy": float(rows["class"].eq(rows.predicted_class).mean()),
        "balanced_accuracy": float(balanced_accuracy_score(rows["class"], rows.predicted_class)),
        "macro_f1": float(f1_score(rows["class"], rows.predicted_class, labels=classes,
                                   average="macro", zero_division=0)),
        "multiclass_log_loss": float(log_loss(rows["class"], probabilities, labels=classes)),
        "confusion_matrix": confusion_matrix(rows["class"], rows.predicted_class,
                                               labels=classes).tolist(),
        "score_bands": score_bands(rows, day_rates),
        "top_one_each_day": positive_precision(rows.sort_values(
            ["signal_date", "p_positive", "symbol6"], ascending=[True, False, True])
            .groupby("signal_date").head(1), day_rates),
        "top_three_each_day": positive_precision(rows.sort_values(
            ["signal_date", "p_positive", "symbol6"], ascending=[True, False, True])
            .groupby("signal_date").head(3), day_rates),
        "online_policy": positive_precision(online, day_rates),
        "online_policy_by_fold": {
            str(fold): positive_precision(online[online.fold.eq(fold)], day_rates)
            for fold in sorted(rows.fold.unique())
        },
    }
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="outputs/stock_automl/tail_standard/trainable_1440.csv")
    parser.add_argument("--output-dir", default="outputs/stock_automl/tail_three_class")
    parser.add_argument("--report", default="docs/stock_automl_runs/20261008_tail_three_class/summary.json")
    args = parser.parse_args()
    source = Path(args.input)
    rows = read_samples(source)
    predictions, folds = out_of_sample_predictions(rows)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(output / "out_of_sample_predictions.csv", index=False,
                       float_format="%.8f")
    full_models = make_models()
    explanations = {}
    model_files = {"三分类逻辑回归": "multinomial_logistic.joblib",
                   "八因子逻辑回归_探索性": "compact_logistic.joblib",
                   "三分类浅决策树": "shallow_tree.joblib"}
    for name, (features, model) in full_models.items():
        model.fit(rows[list(features)], rows["class"])
        explanations[name] = model_explanation(model, name, features)
        joblib.dump(model, output / model_files[name])
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": {"source": TARGET, "positive": "> 0.01", "negative": "< 0.005",
                   "neutral": "0.005 <= return <= 0.01",
                   "denominator": "T 14:50 minute latest_price proxy"},
        "features": {"binary": list(BINARY), "numeric": list(NUMERIC),
                     "by_model": {name: list(features)
                                  for name, (features, _) in full_models.items()}},
        "classes": list(CLASSES),
        "all_sample_class_counts": {str(label): int(rows["class"].eq(label).sum())
                                    for label in CLASSES},
        "folds": folds,
        "models": {name: evaluate_model(predictions[predictions.model.eq(name)].copy())
                   for name in full_models},
        "binary_factor_diagnostics_on_test_dates": binary_factor_diagnostics(
            rows[rows.signal_date.isin(predictions.signal_date.unique())]),
        "full_sample_explanations": explanations,
        "policy": {"score_threshold": SCORE_THRESHOLD, "max_per_day": MAX_PER_DAY,
                   "max_per_week": MAX_PER_WEEK, "selection_order": "online by signal day"},
        "provenance": {
            "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "sklearn_version": sklearn.__version__,
            "stock_level_output": str(output),
        },
    }
    destination = Path(args.report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"trained {len(full_models)} models on {len(rows)} rows; "
          f"wrote {len(predictions)} out-of-sample scored rows and {destination}")


if __name__ == "__main__":
    main()

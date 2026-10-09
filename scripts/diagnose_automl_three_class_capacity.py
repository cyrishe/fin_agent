"""Diagnose class separability with fixed rolling folds and seven features."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             confusion_matrix, log_loss, roc_auc_score)
from sklearn.tree import DecisionTreeClassifier

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_fuzzy_boundary import C, PRICES, prepare, targets


OUT = Path("docs/stock_automl_runs/20261009_three_class_diagnosis")
CLASSES = np.array([-1, 0, 1])


def probabilities(model, x):
    result = model.predict_proba(x)
    if not np.array_equal(model.classes_, CLASSES):
        raise ValueError(f"Unexpected classes: {model.classes_}")
    return result


def metrics(frame):
    y = frame.y.to_numpy()
    p = frame[["pneg", "p0", "p1"]].to_numpy()
    hard = CLASSES[p.argmax(axis=1)]
    confusion = confusion_matrix(y, hard, labels=CLASSES)
    aucs = {str(label): float(roc_auc_score(y == label, p[:, col]))
            for col, label in enumerate(CLASSES)}
    two = frame[frame.y.isin([-1, 0])]
    aucs["0_vs_-1"] = float(roc_auc_score(two.y.eq(0), two.p0 / (two.p0 + two.pneg)))
    return {"rows": len(frame), "accuracy": float(accuracy_score(y, hard)),
            "balanced_accuracy": float(balanced_accuracy_score(y, hard)),
            "log_loss": float(log_loss(y, p, labels=CLASSES)),
            "aucs": aucs, "true_counts": frame.y.value_counts().sort_index().to_dict(),
            "predicted_counts": pd.Series(hard).value_counts().sort_index().to_dict(),
            "confusion_true_rows_predicted_columns_-1_0_1": confusion.tolist()}


def main():
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(PRICES, dtype={"symbol6": str})
    data = base.merge(prices, on=["next_date", "symbol6"], validate="one_to_one")
    dates = sorted(data.signal_date.unique())
    if len(data) != 14292 or len(dates) != 40:
        raise ValueError("Historical data changed")
    data["return"] = data.max_high / data.entry_1440 - 1
    data["y"] = np.select([data["return"] > .01, data["return"] < 0],
                           [1, -1], default=0).astype(int)
    records = []
    for index in range(20, len(dates)):
        train = data[data.signal_date.isin(dates[index-20:index])]
        test = data[data.signal_date.eq(dates[index])]
        if train.signal_date.nunique() != 20:
            raise ValueError("Incomplete fold")
        x_train, x_test = prepare(train, test)
        q, weights = targets(train["return"], "边界降权")
        weights = weights / weights.mean()
        y_train = CLASSES[q.argmax(axis=1)]
        if not np.array_equal(y_train, train.y.to_numpy()):
            raise ValueError("Target mismatch")
        tree_train = train[list(FEATURES)].to_numpy()
        tree_test = test[list(FEATURES)].to_numpy()
        models = {
            "原三分类逻辑回归": (LogisticRegression(
                C=C, max_iter=2000, solver="lbfgs"), x_train[:, 1:], x_test[:, 1:]),
            "平衡类别逻辑回归": (LogisticRegression(
                C=C, max_iter=2000, solver="lbfgs", class_weight="balanced"),
                x_train[:, 1:], x_test[:, 1:]),
            "三层决策树": (DecisionTreeClassifier(
                max_depth=3, min_samples_leaf=100, random_state=42),
                tree_train, tree_test),
            "小型梯度提升树": (HistGradientBoostingClassifier(
                max_iter=60, learning_rate=.05, max_leaf_nodes=7,
                min_samples_leaf=100, l2_regularization=5,
                early_stopping=False, random_state=42), tree_train, tree_test),
        }
        for name, (model, fit_x, test_x) in models.items():
            model.fit(fit_x, y_train, sample_weight=weights)
            for scope, frame, matrix in (("训练", train, fit_x),
                                          ("折外", test, test_x)):
                prob = probabilities(model, matrix)
                result = frame[["signal_date", "symbol6", "y"]].copy()
                result["scope"] = scope
                result["model"] = name
                result[["pneg", "p0", "p1"]] = prob
                records.append(result)
        print(dates[index], flush=True)
    predictions = pd.concat(records, ignore_index=True)
    report = {}
    for (name, scope), group in predictions.groupby(["model", "scope"]):
        report.setdefault(name, {})[scope] = metrics(group)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(report, ensure_ascii=False,
                                               indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

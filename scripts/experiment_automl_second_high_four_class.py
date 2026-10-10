"""Walk-forward four-class study of next-morning second-high returns."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import BINARY, DATA
from scripts.experiment_automl_second_high_regression import SECOND_HIGHS


OUT = Path("docs/stock_automl_runs/20261010_second_high_four_class")
WINDOW = 20
CLASSES = ("lt0", "0to1", "1to3", "ge3")


def four_class(ret: pd.Series) -> pd.Categorical:
    """Exact boundaries: <0, [0,1%), [1,3%), and >=3%."""
    return pd.Categorical(np.select(
        [ret.lt(0), ret.lt(.01), ret.lt(.03)],
        CLASSES[:3], default=CLASSES[3]), categories=CLASSES, ordered=True)


def models():
    numeric = [feature for feature in FEATURES if feature not in BINARY]
    binary = [feature for feature in FEATURES if feature in BINARY]
    logistic = make_pipeline(ColumnTransformer([
        ("numeric", StandardScaler(), numeric),
        ("binary", "passthrough", binary)]),
        LogisticRegression(C=.2, max_iter=2000, solver="lbfgs"))
    boosted = HistGradientBoostingClassifier(
        max_iter=60, learning_rate=.05, max_leaf_nodes=7,
        min_samples_leaf=100, l2_regularization=5,
        early_stopping=False, random_state=42)
    return {"logistic": logistic, "small_boosted_tree": boosted}


def run() -> dict:
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(SECOND_HIGHS, dtype={"symbol6": str})
    data = base.merge(prices[["next_date", "symbol6", "second_high"]],
                      on=["next_date", "symbol6"], validate="one_to_one")
    dates = sorted(data.signal_date.unique())
    if len(data) != 14292 or len(dates) != 40 or data[list(FEATURES)].isna().any().any():
        raise ValueError("Expected complete 40-day exact candidate pool")
    data["second_high_return"] = data.second_high / data.entry_1440 - 1
    data["class"] = four_class(data.second_high_return)
    predictions = []
    folds = []
    for index in range(WINDOW, len(dates)):
        train_dates = dates[index-WINDOW:index]
        train = data[data.signal_date.isin(train_dates)]
        test = data[data.signal_date.eq(dates[index])]
        if train_dates[-1] >= dates[index] or train["class"].nunique() != 4:
            raise ValueError("Invalid forward fold or missing class")
        folds.append({"test_date": dates[index], "train_start": train_dates[0],
                      "train_end": train_dates[-1], "train_rows": len(train),
                      "test_rows": len(test), **{
                          f"train_{label}": int(train["class"].eq(label).sum())
                          for label in CLASSES}})
        for name, model in models().items():
            model.fit(train[list(FEATURES)], train["class"].astype(str))
            probabilities = model.predict_proba(test[list(FEATURES)])
            labels = model.classes_ if hasattr(model, "classes_") else model[-1].classes_
            frame = test[["signal_date", "next_date", "symbol6", "name",
                          "entry_1440", "second_high", "second_high_return", "class",
                          *FEATURES]].copy()
            frame["model"] = name
            for label in CLASSES:
                frame[f"p_{label}"] = probabilities[:, list(labels).index(label)]
            frame["predicted_class"] = np.asarray(labels)[probabilities.argmax(axis=1)]
            frame["score"] = frame.p_ge3
            frame = frame.sort_values(["score", "symbol6"], ascending=[False, True])
            frame["rank"] = np.arange(1, len(frame)+1)
            predictions.append(frame)
        print(f"test {dates[index]}: {len(test)} candidates", flush=True)
    scored = pd.concat(predictions, ignore_index=True)
    metrics = {}
    for name, frame in scored.groupby("model"):
        actual = frame["class"].astype(str)
        probability_matrix = frame[[f"p_{label}" for label in CLASSES]].to_numpy(dtype=float)
        actual_index = actual.map({label: index for index, label in enumerate(CLASSES)}).to_numpy()
        top = frame[frame["rank"].le(2)]
        baseline = frame.groupby("signal_date")["class"].apply(
            lambda values: values.eq("ge3").mean()).mean()
        metrics[name] = {
            "rows": len(frame), "days": frame.signal_date.nunique(),
            "accuracy": float(accuracy_score(actual, frame.predicted_class)),
            "balanced_accuracy": float(balanced_accuracy_score(actual, frame.predicted_class)),
            "log_loss": float(-np.log(np.clip(probability_matrix[
                np.arange(len(frame)), actual_index], 1e-15, 1)).mean()),
            "confusion_rows_actual_columns_predicted": confusion_matrix(
                actual, frame.predicted_class, labels=list(CLASSES)).tolist(),
            "pool_classes": {c: int(actual.eq(c).sum()) for c in CLASSES},
            "top2_classes": {c: int(top["class"].eq(c).sum()) for c in CLASSES},
            "top2_mean_second_high_return_pct": float(top.second_high_return.mean()*100),
            "daily_equal_weight_pool_ge3_rate": float(baseline),
            "top2_ge3_rate": float(top["class"].eq("ge3").mean()),
            "top2_below_minus1": int(top.second_high_return.lt(-.01).sum()),
            "days_with_top2_ge3": int(top.groupby("signal_date")["class"].apply(
                lambda values: values.eq("ge3").any()).sum()),
        }
    OUT.mkdir(parents=True, exist_ok=True)
    scored.to_csv(OUT / "predictions.csv.gz", index=False, compression="gzip")
    scored[scored["rank"].le(2)].to_csv(OUT / "daily_top2.csv", index=False)
    pd.DataFrame(folds).to_csv(OUT / "folds.csv", index=False)
    summary = {"boundary": {"lt0": "return < 0", "0to1": "0 <= return < 0.01",
                            "1to3": "0.01 <= return < 0.03", "ge3": "return >= 0.03"},
               "target": "second highest of T+1 09:31–09:40 exact 1m highs / T 14:40 price - 1",
               "ranking": "descending P(ge3), symbol ascending for ties",
               "training": "previous 20 available signal days only; 20 forward test days",
               "models": metrics,
               "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (DATA, SECOND_HIGHS)}}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(run()["models"], ensure_ascii=False, indent=2))

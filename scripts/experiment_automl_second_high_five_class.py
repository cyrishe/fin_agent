"""Five ordered next-morning second-high classes, evaluated by rolling day."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (average_precision_score, balanced_accuracy_score,
                             confusion_matrix, roc_auc_score)

from scripts.audit_automl_four_class_diagnostics import load_data
from scripts.audit_automl_four_class_decisions import (
    class_priority_fallback, exit_observations)
from scripts.benchmark_automl_1440_inference import FEATURES


OUT = Path("docs/stock_automl_runs/20261010_second_high_five_class")
LABELS = ("ltm1", "m1to0", "0to1", "1to3", "ge3")
WINDOW = 20


def five_class(ret: pd.Series) -> pd.Categorical:
    """<-1%, [-1%,0), [0,1%), [1,3%), >=3%."""
    return pd.Categorical(np.select(
        [ret.lt(-.01), ret.lt(0), ret.lt(.01), ret.lt(.03)],
        LABELS[:4], default=LABELS[4]), categories=LABELS, ordered=True)


def small_tree() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=60, learning_rate=.05, max_leaf_nodes=7,
        min_samples_leaf=100, l2_regularization=5,
        early_stopping=False, random_state=42)


def project_cumulative(ge_threshold: np.ndarray) -> np.ndarray:
    """Project four boundary probabilities into a valid five-class distribution."""
    monotone = np.minimum.accumulate(np.clip(ge_threshold, 0, 1), axis=1)
    return np.column_stack((1-monotone[:, 0],
                            monotone[:, :-1]-monotone[:, 1:],
                            monotone[:, -1]))


class CumulativeTrees:
    """Classify four ordered boundaries without fitting continuous returns."""

    def fit(self, x: pd.DataFrame, y: pd.Series) -> "CumulativeTrees":
        index = pd.Categorical(y.astype(str), categories=LABELS).codes
        self.trees = []
        for boundary in range(1, len(LABELS)):
            tree = small_tree()
            tree.fit(x, (index >= boundary).astype(int))
            self.trees.append(tree)
        return self

    def predict_proba(self, x: pd.DataFrame) -> np.ndarray:
        raw = np.column_stack([tree.predict_proba(x)[:, 1]
                               for tree in self.trees])
        return project_cumulative(raw)


def cost_matrix() -> np.ndarray:
    """Penalize overoptimistic distance quadratically, pessimistic linearly."""
    actual = np.arange(len(LABELS))[:, None]
    predicted = np.arange(len(LABELS))[None, :]
    distance = np.abs(predicted-actual)
    return np.where(predicted > actual, distance**2, distance).astype(float)


def score_frame(frame: pd.DataFrame, probability: np.ndarray,
                model_name: str, scope: str, fold_date: str) -> pd.DataFrame:
    scored = frame[["signal_date", "next_date", "symbol6", "name",
                    "entry_1440", "second_high_return", "class"]].copy()
    scored["model"] = model_name
    scored["scope"] = scope
    scored["fold_test_date"] = fold_date
    for index, label in enumerate(LABELS):
        scored[f"p_{label}"] = probability[:, index]
    scored["predicted_class"] = np.asarray(LABELS)[probability.argmax(axis=1)]
    scored["risk_decision"] = np.asarray(LABELS)[
        (probability @ cost_matrix()).argmin(axis=1)]
    scored["p_ge1"] = scored.p_1to3+scored.p_ge3
    scored["utility_score"] = (3*scored.p_ge3+scored.p_1to3
                               -scored.p_m1to0-4*scored.p_ltm1)
    return scored


def top_two(scored: pd.DataFrame, score: str) -> pd.DataFrame:
    ordered = scored.sort_values(
        ["signal_date", score, "symbol6"], ascending=[True, False, True]).copy()
    ordered["selection_rank"] = ordered.groupby("signal_date").cumcount()+1
    return ordered[ordered.selection_rank.le(2)].copy()


def top_two_outcomes(rows: pd.DataFrame) -> dict:
    actual = rows["class"].astype(str)
    return {
        "selected": len(rows), "days_or_train_day_evaluations": int(
            rows.groupby(["fold_test_date", "signal_date"]).ngroups),
        "actual_ge3": int(actual.eq("ge3").sum()),
        "actual_ge1": int(actual.isin(("1to3", "ge3")).sum()),
        "actual_0to1": int(actual.eq("0to1").sum()),
        "actual_m1to0": int(actual.eq("m1to0").sum()),
        "actual_ltm1": int(actual.eq("ltm1").sum()),
        "mean_second_high_return_pct": float(rows.second_high_return.mean()*100),
    }


def probability_metrics(actual: pd.Series, probability: np.ndarray) -> dict:
    true = actual.astype(str).to_numpy()
    pred = np.asarray(LABELS)[probability.argmax(axis=1)]
    decision = np.asarray(LABELS)[(probability @ cost_matrix()).argmin(axis=1)]
    true_index = pd.Categorical(true, categories=LABELS).codes
    pred_index = pd.Categorical(pred, categories=LABELS).codes
    decision_index = pd.Categorical(decision, categories=LABELS).codes
    cost = cost_matrix()
    return {
        "rows": len(actual), "accuracy": float(np.mean(true == pred)),
        "balanced_accuracy": float(balanced_accuracy_score(true, pred)),
        "log_loss": float(-np.log(np.clip(probability[
            np.arange(len(actual)), true_index], 1e-15, 1)).mean()),
        "argmax_mean_ordinal_cost": float(cost[true_index, pred_index].mean()),
        "risk_decision_mean_ordinal_cost": float(cost[true_index, decision_index].mean()),
        "argmax_confusion": confusion_matrix(true, pred, labels=list(LABELS)).tolist(),
        "risk_decision_confusion": confusion_matrix(
            true, decision, labels=list(LABELS)).tolist(),
    }


def run() -> dict:
    data = load_data()
    data["class"] = five_class(data.second_high_return)
    dates = sorted(data.signal_date.unique())
    all_scored, fold_metrics, selections = [], [], []
    for index in range(WINDOW, len(dates)):
        train = data[data.signal_date.isin(dates[index-WINDOW:index])]
        test = data[data.signal_date.eq(dates[index])]
        for name, model in (("nominal_tree", small_tree()),
                            ("cumulative_trees", CumulativeTrees())):
            model.fit(train[list(FEATURES)], train["class"])
            for scope, frame in (("same_fold_train", train),
                                 ("next_day_test", test)):
                probability = model.predict_proba(frame[list(FEATURES)])
                if name == "nominal_tree":
                    probability = probability[:, [list(model.classes_).index(label)
                                                for label in LABELS]]
                scored = score_frame(frame, probability, name, scope, dates[index])
                fold_metrics.append({"fold_test_date": dates[index], "model": name,
                                     "scope": scope,
                                     **probability_metrics(frame["class"], probability)})
                if scope == "next_day_test":
                    all_scored.append(scored)
                for score in ("p_ge3", "utility_score"):
                    top = top_two(scored, score)
                    top["selection_score"] = score
                    selections.append(top)
                fallback = class_priority_fallback(scored)
                fallback["selection_score"] = "class_priority_fallback"
                selections.append(fallback)
        print(f"scored {dates[index]}", flush=True)
    out_predictions = pd.concat(all_scored, ignore_index=True)
    out_metrics = pd.DataFrame(fold_metrics)
    out_selections = pd.concat(selections, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    out_predictions.to_csv(OUT / "predictions.csv.gz", index=False, compression="gzip")
    out_metrics.to_csv(OUT / "fold_metrics.csv", index=False)
    out_selections.to_csv(OUT / "daily_top2.csv.gz", index=False, compression="gzip")
    exit_rows = []
    for (name, score), selected in out_selections[
            out_selections.scope.eq("next_day_test")].groupby(
                ["model", "selection_score"]):
        exits = exit_observations(selected)
        exits["model"] = name
        exits["selection_score"] = score
        exit_rows.append(exits)
    out_exits = pd.concat(exit_rows, ignore_index=True)
    out_exits.to_csv(OUT / "next_day_top2_exits.csv", index=False)
    prior_log_loss = []
    for index in range(WINDOW, len(dates)):
        train = data[data.signal_date.isin(dates[index-WINDOW:index])]
        test = data[data.signal_date.eq(dates[index])]
        prior = train["class"].value_counts(normalize=True).reindex(LABELS).to_numpy()
        true_index = pd.Categorical(test["class"], categories=LABELS).codes
        prior_log_loss.extend(-np.log(prior[true_index]))
    summary = {
        "classes": list(LABELS),
        "boundaries": "<-1%, [-1%,0%), [0%,1%), [1%,3%), >=3%",
        "train_days_per_fold": WINDOW,
        "uniform_random_accuracy": 1/len(LABELS),
        "rolling_prior_next_day_log_loss": float(np.mean(prior_log_loss)),
        "pool_classes": {label: int(data["class"].eq(label).sum())
                         for label in LABELS},
        "cost_matrix_rows_actual_columns_decision": cost_matrix().tolist(),
        "cost_note": "Overprediction uses squared class distance; underprediction uses linear distance. Illustrative prespecified decision cost, not transaction profit.",
        "models": {},
    }
    for (name, scope), subset in out_metrics.groupby(["model", "scope"]):
        matrix = np.asarray(subset.argmax_confusion.tolist()).sum(axis=0)
        risk_matrix = np.asarray(subset.risk_decision_confusion.tolist()).sum(axis=0)
        total = int(matrix.sum())
        result = {
            "rows": total, "correct": int(np.trace(matrix)),
            "accuracy": float(np.trace(matrix)/total),
            "majority_class_accuracy": float(matrix.sum(axis=1).max()/total),
            "best_constant_action_ordinal_cost": float((
                matrix.sum(axis=1) @ cost_matrix()).min()/total),
            "balanced_accuracy_fold_mean": float(subset.balanced_accuracy.mean()),
            "log_loss_weighted_mean": float(np.average(
                subset.log_loss, weights=subset.rows)),
            "argmax_mean_ordinal_cost": float(np.average(
                subset.argmax_mean_ordinal_cost, weights=subset.rows)),
            "risk_decision_mean_ordinal_cost": float(np.average(
                subset.risk_decision_mean_ordinal_cost, weights=subset.rows)),
            "argmax_confusion_rows_actual_columns_predicted": matrix.tolist(),
            "risk_decision_confusion_rows_actual_columns_decision": risk_matrix.tolist(),
            "top2": {
                score: top_two_outcomes(out_selections[
                    out_selections.model.eq(name) & out_selections.scope.eq(scope)
                    & out_selections.selection_score.eq(score)])
                for score in ("p_ge3", "utility_score",
                              "class_priority_fallback")},
        }
        summary["models"].setdefault(name, {})[scope] = result
        if scope == "next_day_test":
            predictions = out_predictions[out_predictions.model.eq(name)]
            for target in ("ltm1", "ge3"):
                event = predictions["class"].astype(str).eq(target)
                score_values = predictions[f"p_{target}"]
                result[f"{target}_auc"] = float(roc_auc_score(event, score_values))
                result[f"{target}_average_precision"] = float(
                    average_precision_score(event, score_values))
            for score, top_summary in result["top2"].items():
                exits = out_exits[out_exits.model.eq(name)
                                  & out_exits.selection_score.eq(score)]
                top_summary["minute_close_exit_mean_return_pct"] = float(
                    exits.exit_return.mean()*100)
                top_summary["minute_close_exit_positive"] = int(
                    exits.exit_return.gt(0).sum())
                top_summary["minute_close_exit_below_minus1"] = int(
                    exits.exit_return.lt(-.01).sum())
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))

"""Inspect four-class sample counts, fit capacity, probabilities and errors."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial.distance import cdist
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (accuracy_score, average_precision_score,
                             balanced_accuracy_score, f1_score, roc_auc_score)

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_second_high_four_class import (
    CLASSES, OUT, four_class, models)
from scripts.experiment_automl_second_high_regression import SECOND_HIGHS


def load_data() -> pd.DataFrame:
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(SECOND_HIGHS, dtype={"symbol6": str})
    data = base.merge(prices[["next_date", "symbol6", "second_high"]],
                      on=["next_date", "symbol6"], validate="one_to_one")
    data["second_high_return"] = data.second_high/data.entry_1440-1
    data["class"] = four_class(data.second_high_return)
    dates = sorted(data.signal_date.unique())
    if len(data) != 14292 or len(dates) != 40 or not data.signal_return.between(
            .03-1e-10, .06+1e-10).all():
        raise ValueError("Expected 40 exact 3%-6% signal-day candidate pools")
    return data


def sample_distribution(data: pd.DataFrame) -> dict:
    dates = sorted(data.signal_date.unique())
    chunks = {"all_40_days": data, "first_20_days_initial_history": data[
        data.signal_date.isin(dates[:20])], "last_20_days_scored": data[
        data.signal_date.isin(dates[20:])]}
    result = {}
    for name, frame in chunks.items():
        result[name] = {"days": frame.signal_date.nunique(), "stocks": len(frame),
                        "date_start": frame.signal_date.min(),
                        "date_end": frame.signal_date.max(),
                        "per_day_min": int(frame.groupby("signal_date").size().min()),
                        "per_day_median": float(frame.groupby("signal_date").size().median()),
                        "per_day_max": int(frame.groupby("signal_date").size().max()),
                        "classes": {label: int(frame["class"].eq(label).sum())
                                    for label in CLASSES}}
    return result


def binary_auc(actual: pd.Series, score: np.ndarray) -> float:
    binary = actual.eq("ge3")
    return float(roc_auc_score(binary, score))


def fit_diagnostics(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = sorted(data.signal_date.unique())
    rows, probability_rows, class_rows = [], [], []
    for index in range(20, len(dates)):
        train = data[data.signal_date.isin(dates[index-20:index])]
        test = data[data.signal_date.eq(dates[index])]
        candidates = models()
        candidates["capacity_probe"] = HistGradientBoostingClassifier(
            max_iter=200, learning_rate=.08, max_leaf_nodes=31,
            min_samples_leaf=20, l2_regularization=1,
            early_stopping=False, random_state=42)
        for name, model in candidates.items():
            model.fit(train[list(FEATURES)], train["class"].astype(str))
            labels = list(model.classes_)
            prior = train["class"].astype(str).value_counts(normalize=True).reindex(
                labels).to_numpy(dtype=float)
            for scope, frame in (("same_fold_train", train), ("next_day_test", test)):
                probability = model.predict_proba(frame[list(FEATURES)])
                predicted = labels[0] if len(labels) == 1 else np.asarray(labels)[
                    probability.argmax(axis=1)]
                actual = frame["class"].astype(str)
                actual_index = pd.Categorical(actual, categories=labels).codes
                p_true = probability[np.arange(len(frame)), actual_index]
                p_ge3 = probability[:, labels.index("ge3")]
                sorted_p = np.sort(probability, axis=1)
                lead = sorted_p[:, -1]-sorted_p[:, -2]
                entropy = -(probability*np.log(np.clip(probability, 1e-15, 1))).sum(
                    axis=1)/np.log(len(labels))
                ge3_event = actual.eq("ge3").to_numpy()
                ge3_picked = np.asarray(predicted) == "ge3"
                rows.append({"test_date": dates[index], "model": name,
                             "scope": scope, "rows": len(frame),
                             "ge3_auc": binary_auc(actual, p_ge3),
                             "balanced_accuracy": float(balanced_accuracy_score(actual, predicted)),
                             "macro_f1": float(f1_score(actual, predicted,
                                                        labels=list(CLASSES), average="macro",
                                                        zero_division=0))})
                probability_rows.append({
                    "test_date": dates[index], "model": name, "scope": scope,
                    "rows": len(frame), "actual_ge3_rate": float(ge3_event.mean()),
                    "train_prior_ge3": float(prior[labels.index("ge3")]),
                    "mean_p_ge3": float(p_ge3.mean()),
                    "p_ge3_median": float(np.median(p_ge3)),
                    "p_ge3_p90": float(np.quantile(p_ge3, .9)),
                    "p_ge3_max": float(p_ge3.max()),
                    "argmax_ge3_count": int(ge3_picked.sum()),
                    "argmax_ge3_rate": float(ge3_picked.mean()),
                    "max_class_probability_median": float(np.median(sorted_p[:, -1])),
                    "max_class_probability_p90": float(np.quantile(sorted_p[:, -1], .9)),
                    "lead_over_second_median": float(np.median(lead)),
                    "lead_over_second_p90": float(np.quantile(lead, .9)),
                    "normalized_entropy_mean": float(entropy.mean()),
                    "log_loss": float(-np.log(np.clip(p_true, 1e-15, 1)).mean()),
                    "train_prior_log_loss": float(-np.log(np.clip(
                        prior[actual_index], 1e-15, 1)).mean()),
                    "mean_p_ge3_given_ge3": float(p_ge3[ge3_event].mean()),
                    "mean_p_ge3_given_lt0": float(p_ge3[actual.eq("lt0")].mean()),
                })
                for true_label in CLASSES:
                    mask = actual.eq(true_label).to_numpy()
                    class_rows.append({
                        "test_date": dates[index], "model": name, "scope": scope,
                        "true_class": true_label, "rows": int(mask.sum()),
                        **{f"mean_p_{label}": float(probability[mask, labels.index(label)].mean())
                           for label in CLASSES},
                    })
        print(f"diagnosed {dates[index]}", flush=True)
    return pd.DataFrame(rows), pd.DataFrame(probability_rows), pd.DataFrame(class_rows)


def prediction_diagnostics(scored: pd.DataFrame) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    summary, calibration, ranks = {}, [], []
    for name, frame in scored.groupby("model"):
        actual = frame["class"].astype(str)
        event = actual.eq("ge3")
        second_ties = []
        for _, day in frame.groupby("signal_date"):
            threshold = day.nlargest(2, "p_ge3").p_ge3.min()
            second_ties.append(int(day.p_ge3.ge(threshold-1e-12).sum()))
        summary[name] = {
            "classes_actual": {label: int(actual.eq(label).sum()) for label in CLASSES},
            "classes_predicted_argmax": {label: int(frame.predicted_class.eq(label).sum())
                                         for label in CLASSES},
            "confusion_actual_by_predicted": {
                true_label: {
                    predicted_label: int((actual.eq(true_label) &
                                          frame.predicted_class.eq(predicted_label)).sum())
                    for predicted_label in CLASSES}
                for true_label in CLASSES},
            "accuracy": float(accuracy_score(actual, frame.predicted_class)),
            "balanced_accuracy": float(balanced_accuracy_score(actual, frame.predicted_class)),
            "macro_f1": float(f1_score(actual, frame.predicted_class,
                                        labels=list(CLASSES), average="macro", zero_division=0)),
            "ge3_auc": binary_auc(actual, frame.p_ge3.to_numpy()),
            "ge3_average_precision": float(average_precision_score(event, frame.p_ge3)),
            "ge3_base_rate": float(event.mean()),
            "ge3_mean_probability": float(frame.p_ge3.mean()),
            "ge3_probability_min_median_max": [float(frame.p_ge3.min()),
                                                  float(frame.p_ge3.median()),
                                                  float(frame.p_ge3.max())],
            "low_return_probability_mean_by_true_class": {
                label: float(frame.loc[actual.eq(label), "p_lt0"].mean())
                for label in CLASSES},
            "ge3_probability_mean_by_true_class": {
                label: float(frame.loc[actual.eq(label), "p_ge3"].mean())
                for label in CLASSES},
            "probability_sum_max_error": float((frame[[f"p_{label}" for label in CLASSES]]
                                                .sum(axis=1)-1).abs().max()),
            "same_day_ge3_auc_mean": float(np.mean([
                roc_auc_score(day["class"].eq("ge3"), day.p_ge3)
                for _, day in frame.groupby("signal_date")
                if day["class"].eq("ge3").nunique() == 2])),
            "score_vs_second_high_spearman": float(spearmanr(
                frame.p_ge3, frame.second_high_return).statistic),
            "second_place_score_tie_max": max(second_ties),
            "second_place_score_tie_days": int(sum(tie > 2 for tie in second_ties)),
        }
        for k in (1, 2, 5, 10, 20, 50):
            selected = frame[frame["rank"].le(k)]
            ranks.append({"model": name, "daily_top_k": k, "selected": len(selected),
                          "ge3": int(selected["class"].eq("ge3").sum()),
                          "lt0": int(selected["class"].eq("lt0").sum()),
                          "mean_return_pct": float(selected.second_high_return.mean()*100),
                          "mean_p_ge3": float(selected.p_ge3.mean())})
        frame = frame.copy()
        frame["probability_decile"] = pd.qcut(frame.p_ge3.rank(method="first"), 10,
                                               labels=False)+1
        for decile, group in frame.groupby("probability_decile"):
            calibration.append({"model": name, "decile_low_to_high": int(decile),
                                "rows": len(group),
                                "mean_predicted_p_ge3": float(group.p_ge3.mean()),
                                "actual_ge3_rate": float(group["class"].eq("ge3").mean()),
                                "actual_lt0_rate": float(group["class"].eq("lt0").mean())})
    return summary, pd.DataFrame(calibration), pd.DataFrame(ranks)


def day_probability_diagnostics(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (model, day), frame in scored.groupby(["model", "signal_date"]):
        rows.append({"model": model, "signal_date": day, "rows": len(frame),
                     "actual_ge3_rate": float(frame["class"].eq("ge3").mean()),
                     "mean_p_ge3": float(frame.p_ge3.mean()),
                     "actual_lt0_rate": float(frame["class"].eq("lt0").mean()),
                     "mean_p_lt0": float(frame.p_lt0.mean()),
                     "argmax_ge3_count": int(frame.predicted_class.eq("ge3").sum())})
    return pd.DataFrame(rows)


def overlap_diagnostic(data: pd.DataFrame) -> dict:
    dates = sorted(data.signal_date.unique())
    test = data[data.signal_date.isin(dates[20:])]
    continuous = list(FEATURES[:4])
    means = data[data.signal_date.isin(dates[:20])][continuous].mean()
    scales = data[data.signal_date.isin(dates[:20])][continuous].std().replace(0, 1)
    distance_rows = []
    local_rows = []
    for day, group in test.groupby("signal_date"):
        high = group[group["class"].eq("ge3")]
        negative = group[group["class"].eq("lt0")]
        if len(high) < 2 or negative.empty:
            continue
        numeric = (group[continuous]-means)/scales
        encoded = np.column_stack([numeric.to_numpy(), group[list(FEATURES[4:])].to_numpy()])
        all_distances = cdist(encoded, encoded)
        np.fill_diagonal(all_distances, np.inf)
        neighbor_count = min(20, len(group)-1)
        neighbors = np.argpartition(all_distances, kth=neighbor_count-1,
                                    axis=1)[:, :neighbor_count]
        high_labels = group["class"].eq("ge3").to_numpy()
        neighbor_high_rates = high_labels[neighbors].mean(axis=1)
        for rate, label in zip(neighbor_high_rates, high_labels):
            local_rows.append({"is_ge3": bool(label),
                               "neighbor_ge3_rate": float(rate),
                               "same_day_ge3_rate": float(high_labels.mean())})
        positions = {index: position for position, index in enumerate(group.index)}
        high_positions = [positions[index] for index in high.index]
        negative_positions = [positions[index] for index in negative.index]
        high_features = encoded[high_positions]
        negative_features = encoded[negative_positions]
        to_negative = cdist(high_features, negative_features).min(axis=1)
        to_same = cdist(high_features, high_features)
        np.fill_diagonal(to_same, np.inf)
        to_same = to_same.min(axis=1)
        for negative_distance, same_distance in zip(to_negative, to_same):
            distance_rows.append({"signal_date": day,
                                  "nearest_lt0_distance": float(negative_distance),
                                  "nearest_ge3_distance": float(same_distance)})
    distances = pd.DataFrame(distance_rows)
    local = pd.DataFrame(local_rows)
    distances.to_csv(OUT / "feature_neighbor_overlap.csv", index=False)
    return {"ge3_samples_with_comparable_neighbors": len(distances),
            "nearest_lt0_distance_median": float(distances.nearest_lt0_distance.median()),
            "nearest_ge3_distance_median": float(distances.nearest_ge3_distance.median()),
            "opposite_class_closer_count": int((distances.nearest_lt0_distance <
                                                distances.nearest_ge3_distance).sum()),
            "ge3_samples_20_neighbor_ge3_rate": float(local.loc[
                local.is_ge3, "neighbor_ge3_rate"].mean()),
            "ge3_samples_same_day_pool_ge3_rate": float(local.loc[
                local.is_ge3, "same_day_ge3_rate"].mean()),
            "definition": "For each >=3% sample on the same test date, compare nearest <0% and >=3% stocks by the seven inputs: first four standardized on initial 20 dates, last three kept as 0/1."}


def score_sensitivity(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for model, group in scored.groupby("model"):
        for score_name, score in (("p_ge3", group.p_ge3),
                                  ("p_ge3_minus_p_lt0", group.p_ge3-group.p_lt0)):
            ranked = group.assign(diagnostic_score=score).sort_values(
                ["signal_date", "diagnostic_score", "symbol6"],
                ascending=[True, False, True])
            ranked["diagnostic_rank"] = ranked.groupby("signal_date").cumcount()+1
            chosen = ranked[ranked.diagnostic_rank.le(2)]
            rows.append({"model": model, "score": score_name, "selected": len(chosen),
                         "ge3": int(chosen["class"].eq("ge3").sum()),
                         "lt0": int(chosen["class"].eq("lt0").sum()),
                         "lt_minus1": int(chosen.second_high_return.lt(-.01).sum()),
                         "mean_return_pct": float(chosen.second_high_return.mean()*100)})
    result = pd.DataFrame(rows)
    result.to_csv(OUT / "score_sensitivity.csv", index=False)
    return result


def write_errors(scored: pd.DataFrame) -> None:
    boosted = scored[scored.model.eq("small_boosted_tree")].copy()
    severe = boosted[(boosted["rank"].le(10)) & boosted["class"].eq("lt0")].sort_values(
        ["p_ge3", "second_high_return"], ascending=[False, True]).head(20)
    missed = boosted[(boosted["class"].eq("ge3")) &
                     boosted.p_ge3.le(boosted.p_ge3.median())].sort_values(
        ["second_high_return", "p_ge3"], ascending=[False, True]).head(20)
    errors = pd.concat([severe.assign(error_type="high_rank_but_lt0"),
                        missed.assign(error_type="ge3_but_below_median_score")])
    errors[["error_type", "signal_date", "symbol6", "name", "rank", "class",
            "second_high_return", *[f"p_{label}" for label in CLASSES],
            *FEATURES]].to_csv(OUT / "error_examples.csv", index=False)


def run() -> dict:
    data = load_data()
    scored = pd.read_csv(OUT / "predictions.csv.gz", dtype={"symbol6": str})
    fit, probability_fit, class_fit = fit_diagnostics(data)
    prediction, calibration, ranks = prediction_diagnostics(scored)
    day_fit = day_probability_diagnostics(scored)
    overlap = overlap_diagnostic(data)
    scores = score_sensitivity(scored)
    write_errors(scored)
    fit.to_csv(OUT / "train_vs_next_day_fit.csv", index=False)
    probability_fit.to_csv(OUT / "probability_fit_by_fold.csv", index=False)
    class_fit.to_csv(OUT / "probability_by_true_class.csv", index=False)
    calibration.to_csv(OUT / "probability_deciles.csv", index=False)
    ranks.to_csv(OUT / "ranking_by_k.csv", index=False)
    day_fit.to_csv(OUT / "probability_by_day.csv", index=False)
    fit_summary = {}
    for (name, scope), frame in fit.groupby(["model", "scope"]):
        fit_summary.setdefault(name, {})[scope] = {
            "mean_ge3_auc_across_20_folds": float(frame.ge3_auc.mean()),
            "mean_balanced_accuracy_across_20_folds": float(frame.balanced_accuracy.mean()),
            "mean_macro_f1_across_20_folds": float(frame.macro_f1.mean()),
            "mean_rows_per_fold": float(frame.rows.mean())}
    probability_summary = {}
    columns = ("actual_ge3_rate", "train_prior_ge3", "mean_p_ge3",
               "p_ge3_median", "p_ge3_p90", "p_ge3_max", "argmax_ge3_count",
               "argmax_ge3_rate", "max_class_probability_median",
               "max_class_probability_p90", "lead_over_second_median",
               "lead_over_second_p90", "normalized_entropy_mean",
               "log_loss", "train_prior_log_loss",
               "mean_p_ge3_given_ge3", "mean_p_ge3_given_lt0")
    for (name, scope), frame in probability_fit.groupby(["model", "scope"]):
        probability_summary.setdefault(name, {})[scope] = {
            key: float(frame[key].mean()) for key in columns}
    day_summary = {}
    for name, frame in day_fit.groupby("model"):
        day_summary[name] = {
            "actual_ge3_rate_min_max": [float(frame.actual_ge3_rate.min()),
                                         float(frame.actual_ge3_rate.max())],
            "mean_p_ge3_min_max": [float(frame.mean_p_ge3.min()),
                                   float(frame.mean_p_ge3.max())],
            "actual_vs_predicted_day_ge3_correlation": float(frame.actual_ge3_rate.corr(
                frame.mean_p_ge3)),
        }
    summary = {"samples": sample_distribution(data),
               "fit": fit_summary, "probability_fit": probability_summary,
               "day_probability_fit": day_summary,
               "out_of_fold_prediction": prediction,
               "feature_overlap": overlap,
               "score_sensitivity": scores.to_dict("records"),
               "interpretation": "Training metrics reuse overlapping 20-day windows and are optimistic fit diagnostics, not an attainable return bound. Test metrics are one next-day fold at a time. Capacity probe is only a diagnostic and was not selected as a strategy."}
    (OUT / "diagnostics.json").write_text(json.dumps(summary, ensure_ascii=False,
                                               indent=2)+"\n")
    return summary


if __name__ == "__main__":
    result = run()
    print(json.dumps({key: result[key] for key in ("samples", "fit", "out_of_fold_prediction",
                                                   "feature_overlap")}, ensure_ascii=False, indent=2))

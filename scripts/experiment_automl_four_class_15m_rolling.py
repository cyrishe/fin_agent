"""Walk forward with a single 15m target across June, July and early August."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix

from scripts.audit_automl_four_class_decisions import class_priority_fallback
from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_second_high_four_class import CLASSES, models


DATA = Path("outputs/stock_automl/sina_15m_rolling/trainable_15m_candidates.csv")
BUILD_SUMMARY = DATA.with_name("build_summary.json")
OUT = Path("docs/stock_automl_runs/20261010_four_class_15m_close_rolling")
WINDOW = 20
JULY_START, JULY_END = "2026-07-01", "2026-07-31"
AUG_END = "2026-08-10"


def forward_folds(rows: pd.DataFrame):
    dates = sorted(rows.signal_date.unique())
    for index, date in enumerate(dates):
        if not JULY_START <= date <= AUG_END:
            continue
        if index < WINDOW:
            raise ValueError(f"Only {index} earlier signal days before {date}")
        prior_dates = dates[index-WINDOW:index]
        train = rows[rows.signal_date.isin(prior_dates) & rows["class"].notna()].copy()
        test = rows[rows.signal_date.eq(date)].copy()
        if (prior_dates[-1] >= date or train.signal_date.nunique() != WINDOW or
                train["class"].nunique() != 4 or test.empty):
            raise ValueError(f"Invalid 20-day forward fold on {date}")
        yield date, prior_dates, train, test


def add_returns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for minute in ("0945", "1000", "1015"):
        result[f"return_{minute}_pct"] = (
            result[f"close_{minute}"] / result.signal_price - 1) * 100
    result["return_0945_from_1500_pct"] = (
        result.close_0945 / result.close_1500 - 1) * 100
    result["high_0945_touch_pct"] = (
        result.high_0945 / result.signal_price - 1) * 100
    return result


def one_period(frame: pd.DataFrame, daily: pd.DataFrame,
               selected: pd.DataFrame) -> dict:
    evaluated = frame[frame["class"].notna()]
    matrix = confusion_matrix(evaluated["class"], evaluated.predicted_class,
                              labels=CLASSES)
    top1 = daily[daily.top1_symbol6.notna()]
    observed_top1 = top1[top1.top1_return_0945_pct.notna()]
    gain_ranked = frame.sort_values(
        ["signal_date", "signal_return", "symbol6"],
        ascending=[True, False, True]).groupby("signal_date", sort=False).head(1)
    gain_ranked_on_model_days = gain_ranked[
        gain_ranked.signal_date.isin(top1.signal_date)]
    period_selected = selected[selected.signal_date.isin(frame.signal_date.unique())]
    observed_selected = period_selected[period_selected.return_0945_pct.notna()]
    probabilities = evaluated[[f"p_{label}" for label in CLASSES]].to_numpy(dtype=float)
    actual_index = evaluated["class"].map(
        {label: index for index, label in enumerate(CLASSES)}).to_numpy(dtype=int)
    return {
        "candidate_days": int(frame.signal_date.nunique()),
        "candidate_rows": len(frame), "labeled_rows": len(evaluated),
        "unlabeled_rows": len(frame)-len(evaluated),
        "class_distribution": {label: int(evaluated["class"].eq(label).sum())
                               for label in CLASSES},
        "predicted_class_distribution": {
            label: int(frame.predicted_class.eq(label).sum()) for label in CLASSES},
        "confusion_rows_actual_columns_predicted": matrix.tolist(),
        "correct": int(np.trace(matrix)),
        "accuracy": float(accuracy_score(evaluated["class"], evaluated.predicted_class)),
        "uniform_random_accuracy": .25,
        "majority_label_accuracy": float(evaluated["class"].value_counts(normalize=True).max()),
        "log_loss": float(-np.log(np.clip(
            probabilities[np.arange(len(evaluated)), actual_index], 1e-15, 1)).mean()),
        "candidate_median_max_probability": float(np.median(
            frame[[f"p_{label}" for label in CLASSES]].max(axis=1))),
        "top1_days": len(top1),
        "top1_median_max_probability": float(top1[
            [f"p_{label}" for label in CLASSES]].max(axis=1).median()),
        "top1_median_p_lt0": float(top1.p_lt0.median()),
        "top1_observed_0945_days": len(observed_top1),
        "top1_missing_0945_days": len(top1)-len(observed_top1),
        "top1_mean_0945_pct": float(observed_top1.top1_return_0945_pct.mean()),
        "top1_mean_per_signal_day_with_cash_pct": (
            float(observed_top1.top1_return_0945_pct.sum()/frame.signal_date.nunique())
            if len(observed_top1) == len(top1) else None),
        "top1_median_0945_pct": float(observed_top1.top1_return_0945_pct.median()),
        "top1_positive_0945": int(observed_top1.top1_return_0945_pct.gt(0).sum()),
        "top1_at_least_1pct_0945": int(observed_top1.top1_return_0945_pct.ge(1).sum()),
        "top1_below_minus1pct_0945": int(observed_top1.top1_return_0945_pct.lt(-1).sum()),
        "top1_observed_1000_days": int(top1.top1_return_1000_pct.notna().sum()),
        "top1_mean_1000_pct": float(top1.top1_return_1000_pct.mean()),
        "top1_observed_1015_days": int(top1.top1_return_1015_pct.notna().sum()),
        "top1_mean_1015_pct": float(top1.top1_return_1015_pct.mean()),
        "top1_observed_1500_entry_days": int(
            top1.top1_return_0945_from_1500_pct.notna().sum()),
        "top1_mean_0945_from_1500_pct": float(
            top1.top1_return_0945_from_1500_pct.mean()),
        "pool_daily_equal_mean_0945_pct": float(daily.pool_return_0945_pct.mean()),
        "highest_signal_gain_mean_0945_pct": float(
            gain_ranked.return_0945_pct.mean()),
        "highest_signal_gain_on_model_days_mean_0945_pct": float(
            gain_ranked_on_model_days.return_0945_pct.mean()),
        "top1_outperformed_pool_days": int(top1.top1_minus_pool_0945_pct.gt(0).sum()),
        "selected_top2_trades": len(period_selected),
        "selected_top2_observed_0945_trades": len(observed_selected),
        "selected_top2_mean_0945_pct": float(observed_selected.return_0945_pct.mean()),
        "selected_top2_positive_0945": int(observed_selected.return_0945_pct.gt(0).sum()),
        "selected_top2_below_minus1pct_0945": int(
            observed_selected.return_0945_pct.lt(-1).sum()),
        "selected_top2_actual_classes": {
            label: int(observed_selected["class"].eq(label).sum())
            for label in CLASSES},
        "selected_top2_by_predicted_class": {
            predicted: {
                "observed_trades": int(observed_selected.predicted_class.eq(predicted).sum()),
                "mean_0945_pct": (float(observed_selected.loc[
                    observed_selected.predicted_class.eq(predicted),
                    "return_0945_pct"].mean()) if observed_selected.predicted_class.eq(
                        predicted).any() else None),
                "actual_classes": {
                    label: int(observed_selected.loc[
                        observed_selected.predicted_class.eq(predicted),
                        "class"].eq(label).sum()) for label in CLASSES},
            } for predicted in ("ge3", "1to3")},
    }


def run() -> dict:
    build_settings = json.loads(BUILD_SUMMARY.read_text())
    rows = pd.read_csv(DATA, dtype={"symbol6": str, "class": str})
    rows["signal_date"] = rows.signal_date.astype(str)
    rows["next_date"] = rows.next_date.astype(str)
    if (rows.duplicated(["signal_date", "symbol6"]).any() or
            rows[list(FEATURES)].isna().any().any() or
            not rows.signal_return.between(.03, .06).all()):
        raise ValueError("Candidate feature data are incomplete or inconsistent")
    pre_outcome = []
    folds = []
    fit_confusions = []
    for date, prior_dates, train, test in forward_folds(rows):
        model = models()["small_boosted_tree"]
        model.fit(train[list(FEATURES)], train["class"])
        fit_probability = model.predict_proba(train[list(FEATURES)])
        probability = model.predict_proba(test[list(FEATURES)])
        labels = list(model.classes_)
        fit_predictions = np.asarray(labels)[fit_probability.argmax(axis=1)]
        fit_confusions.append((date, confusion_matrix(
            train["class"], fit_predictions, labels=CLASSES)))
        scored = test[["signal_date", "next_date", "symbol6", "name",
                       "signal_price", "signal_return", *FEATURES[1:]]].copy()
        for label in CLASSES:
            scored[f"p_{label}"] = probability[:, labels.index(label)]
        scored["predicted_class"] = np.asarray(labels)[probability.argmax(axis=1)]
        scored["train_start"] = prior_dates[0]
        scored["train_end"] = prior_dates[-1]
        pre_outcome.append(scored)
        folds.append({
            "test_date": date, "train_start": prior_dates[0],
            "train_end": prior_dates[-1], "train_days": len(prior_dates),
            "train_rows": len(train), "test_rows": len(test),
            "train_class_counts": {label: int(train["class"].eq(label).sum())
                                   for label in CLASSES},
            "train_accuracy": float(accuracy_score(
                train["class"], fit_predictions)),
            "train_majority_accuracy": float(
                train["class"].value_counts(normalize=True).max()),
            "train_median_max_probability": float(
                np.median(fit_probability.max(axis=1))),
            "test_median_max_probability": float(
                np.median(probability.max(axis=1))),
        })
        print(f"{date}: train {len(train)} / test {len(test)}", flush=True)
    scored = pd.concat(pre_outcome, ignore_index=True)
    if not np.allclose(scored[[f"p_{label}" for label in CLASSES]].sum(axis=1), 1):
        raise ValueError("Four-class probabilities do not sum to one")
    scored["p_ge1"] = scored.p_1to3 + scored.p_ge3
    picks = class_priority_fallback(scored)
    OUT.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT / "predictions_before_outcomes.csv.gz", "wt", encoding="utf8") as file:
        scored.to_csv(file, index=False)
    picks.to_csv(OUT / "selected_before_outcomes.csv", index=False)

    outcomes = rows[["signal_date", "symbol6", "class",
                     "close_1500", "close_0945", "close_1000", "close_1015",
                     "high_0945", "target_0945_close_return",
                     "target_0945_high_return"]]
    evaluated = add_returns(scored.merge(outcomes, on=["signal_date", "symbol6"],
                                         validate="one_to_one"))
    selected = picks[["signal_date", "symbol6", "selection_rank"]].merge(
        evaluated, on=["signal_date", "symbol6"], validate="one_to_one")
    selected.to_csv(OUT / "selected_with_outcomes.csv", index=False)
    with gzip.open(OUT / "predictions_with_outcomes.csv.gz", "wt", encoding="utf8") as file:
        evaluated.to_csv(file, index=False)

    pool = evaluated.groupby("signal_date").agg(
        pool_return_0945_pct=("return_0945_pct", "mean"),
        pool_return_1000_pct=("return_1000_pct", "mean"),
        pool_return_1015_pct=("return_1015_pct", "mean"),
        candidates=("symbol6", "size"), labeled=("class", "count")).reset_index()
    top1 = selected[selected.selection_rank.eq(1)][[
        "signal_date", "next_date", "symbol6", "name", "predicted_class",
        "p_ge3", "p_1to3", "p_ge1", "p_0to1", "p_lt0",
        "signal_return", "signal_price", "close_1500",
        "close_0945", "close_1000", "close_1015", "high_0945",
        "return_0945_pct", "return_1000_pct", "return_1015_pct",
        "return_0945_from_1500_pct", "high_0945_touch_pct", "class"
    ]].rename(columns={
        "symbol6": "top1_symbol6", "name": "top1_name",
        "predicted_class": "top1_predicted_class", "class": "top1_actual_class",
        "signal_return": "top1_signal_return",
        "return_0945_pct": "top1_return_0945_pct",
        "return_1000_pct": "top1_return_1000_pct",
        "return_1015_pct": "top1_return_1015_pct",
        "return_0945_from_1500_pct": "top1_return_0945_from_1500_pct",
        "high_0945_touch_pct": "top1_high_0945_touch_pct"})
    daily = pool.merge(top1, on="signal_date", how="left", validate="one_to_one")
    daily["top1_minus_pool_0945_pct"] = (
        daily.top1_return_0945_pct - daily.pool_return_0945_pct)
    fold_frame = pd.DataFrame(folds).drop(columns="train_class_counts")
    daily = daily.merge(fold_frame, left_on="signal_date", right_on="test_date",
                        validate="one_to_one").drop(columns="test_date")
    daily.to_csv(OUT / "daily_results.csv", index=False)
    (OUT / "folds.json").write_text(json.dumps(folds, ensure_ascii=False, indent=2)+"\n")

    july_scored = evaluated[evaluated.signal_date.between(JULY_START, JULY_END)]
    august_scored = evaluated[evaluated.signal_date.between("2026-08-03", AUG_END)]
    july_daily = daily[daily.signal_date.between(JULY_START, JULY_END)]
    august_daily = daily[daily.signal_date.between("2026-08-03", AUG_END)]
    july_fit_matrix = np.sum([matrix for date, matrix in fit_confusions
                              if JULY_START <= date <= JULY_END], axis=0)
    summary = {
        "model": "unweighted seven-factor four-class small_boosted_tree",
        "model_settings": {"max_iter": 60, "learning_rate": .05,
                           "max_leaf_nodes": 7, "min_samples_leaf": 100,
                           "l2_regularization": 5, "random_state": 42},
        "training": "previous 20 available signal days, refit for every test day",
        "candidate_risk_filter": "; ".join(filter(None, (
            build_settings.get("historical_st_rule"),
            build_settings.get("five_limit_rule"),
            build_settings.get("flat_five_limit_rule")))) or None,
        "target": "next 09:45 15m close / same-day 14:45 close proxy - 1",
        "class_boundaries": "<0%; [0%,1%); [1%,3%); >=3%",
        "selection": "argmax >=3% class first; fallback argmax 1-3%; up to two per day",
        "entry_proxy": "same-day completed 14:45 15m close",
        "exit": "next-day completed 09:45, 10:00, or 10:15 15m close",
        "july": one_period(july_scored, july_daily, selected),
        "august_bridge": one_period(august_scored, august_daily, selected),
        "training_fold_count": len(folds),
        "mean_fold_train_accuracy": float(np.mean(
            [fold["train_accuracy"] for fold in folds])),
        "mean_fold_train_majority_accuracy": float(np.mean(
            [fold["train_majority_accuracy"] for fold in folds])),
        "median_fold_test_max_probability": float(np.median(
            [fold["test_median_max_probability"] for fold in folds])),
        "july_training_fit_aggregate": {
            "note": "Summed fit predictions from July's 23 rolling models; repeated training stock-days are counted in multiple windows.",
            "rows": int(july_fit_matrix.sum()),
            "correct": int(np.trace(july_fit_matrix)),
            "accuracy": float(np.trace(july_fit_matrix)/july_fit_matrix.sum()),
            "confusion_rows_actual_columns_predicted": july_fit_matrix.tolist(),
        },
        "first_fold": folds[0], "last_fold": folds[-1],
        "input_sha256": {str(DATA): hashlib.sha256(DATA.read_bytes()).hexdigest(),
                         str(BUILD_SUMMARY): hashlib.sha256(BUILD_SUMMARY.read_bytes()).hexdigest()},
        "code_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in (Path(__file__),
                                     Path("scripts/build_automl_sina_15m_rolling.py"),
                                     Path("scripts/experiment_automl_second_high_four_class.py"),
                                     Path("scripts/audit_automl_four_class_decisions.py"))},
        "limitations": [
            "July and early-August stock outcomes have already been explored, so this is not an untouched validation set.",
            "14:45 completed-bar close is only an entry-price proxy, not a guaranteed execution.",
            ("Historical risk status uses kcrp_stock_st effective intervals, but its "
             "source records are not frozen at each historical decision time." if
             build_settings.get("exclude_historical_st") else
             "Historical July ST identity was not used; current base names are display-only."),
            "Prices and database daily/valuation records have no frozen as-of revision snapshot.",
            "No commissions, slippage, or fill constraints are included.",
        ],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    DATA = args.data
    BUILD_SUMMARY = DATA.with_name("build_summary.json")
    OUT = args.out
    run()

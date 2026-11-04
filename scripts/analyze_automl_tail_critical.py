"""Review critical errors for 14:40 signals using chronological held-out scores.

This reads local, ignored stock-level data. Only aggregate results are written
to the tracked report; workbook inputs remain in an ignored output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.experiment_automl_tail_three_class import (
    BINARY, COMPACT_FEATURES, day_folds, read_samples,
)


FULL_NAME = "三分类逻辑回归"
COMPACT_NAME = "八因子逻辑回归_探索性"
DETAIL_FEATURES = ("signal_return", "volume_ratio", "turnover_so_far_pct",
                   "float_mv_100m_cny", *BINARY)


def fit_binary_oos(samples):
    numeric = [feature for feature in COMPACT_FEATURES if feature not in BINARY]
    binary = [feature for feature in COMPACT_FEATURES if feature in BINARY]
    predictions = []
    coefficients = []
    for fold, (training, testing) in enumerate(day_folds(samples), 1):
        transform = ColumnTransformer([
            ("numeric", StandardScaler(), numeric),
            ("binary", "passthrough", binary),
        ])
        model = make_pipeline(transform, LogisticRegression(C=0.2, max_iter=2000))
        model.fit(training[list(COMPACT_FEATURES)], training["class"].ne(-1).astype(int))
        fitted = model.named_steps["logisticregression"]
        coefficients.append({
            "fold": fold,
            "train_rows": len(training),
            "coefficients_for_pass": {
                feature: round(float(value), 6)
                for feature, value in zip([*numeric, *binary], fitted.coef_[0])},
        })
        result = testing[["signal_date", "symbol6"]].copy()
        result["fold"] = fold
        result["binary_pass_score"] = model.predict_proba(
            testing[list(COMPACT_FEATURES)])[:, 1]
        predictions.append(result)
    return pd.concat(predictions, ignore_index=True), coefficients


def rank_by_day(rows, score, column):
    ordered = rows.sort_values(["signal_date", score, "symbol6"],
                               ascending=[True, False, True])
    rows[column] = ordered.groupby("signal_date").cumcount().add(1).reindex(rows.index)


def assemble_detail(samples, predictions, candidate_audit, binary_scores):
    tests = predictions[predictions.model.eq(FULL_NAME)][["signal_date", "symbol6"]]
    if tests.duplicated().any():
        raise ValueError("Duplicate scored stock-day rows")
    detail = samples.merge(tests, on=["signal_date", "symbol6"],
                           how="inner", validate="one_to_one")
    if len(detail) != len(tests):
        raise ValueError("Scored rows do not match the standard sample")
    for name, prefix in ((FULL_NAME, "full"), (COMPACT_NAME, "compact")):
        score = predictions[predictions.model.eq(name)][[
            "signal_date", "symbol6", "fold", "p_negative", "p_neutral",
            "p_positive", "predicted_class"]].rename(columns={
                "fold": f"fold_{prefix}", "p_negative": f"negative_score_{prefix}",
                "p_neutral": f"neutral_score_{prefix}",
                "p_positive": f"positive_score_{prefix}",
                "predicted_class": f"predicted_class_{prefix}",
            })
        detail = detail.merge(score, on=["signal_date", "symbol6"],
                              how="left", validate="one_to_one")
    detail = detail.merge(binary_scores, on=["signal_date", "symbol6"],
                          how="left", validate="one_to_one")
    audit = candidate_audit[["signal_date", "symbol6", "entry_1450",
                             "entry_not_near_limit_proxy", "t_final_limit_flag"]]
    detail = detail.merge(audit, on=["signal_date", "symbol6"],
                          how="left", validate="one_to_one")
    if detail[["positive_score_full", "positive_score_compact",
               "binary_pass_score", "entry_1450"]].isna().any().any():
        raise ValueError("Missing model score or execution audit")
    if not detail.fold_full.eq(detail.fold_compact).all() or not detail.fold_full.eq(
            detail.fold).all():
        raise ValueError("Model folds are not aligned")
    detail = detail.rename(columns={"fold": "test_fold"})
    detail["pass"] = detail["class"].ne(-1).astype(int)
    detail["critical"] = detail["class"].eq(-1).astype(int)
    detail["below_entry"] = detail.target_next_high10_return.lt(0).astype(int)
    for score, rank in (("positive_score_full", "rank_full"),
                        ("positive_score_compact", "rank_compact"),
                        ("binary_pass_score", "rank_binary")):
        rank_by_day(detail, score, rank)
    return detail.sort_values(["signal_date", "symbol6"]).reset_index(drop=True)


def rules(rows):
    return {
        "全部候选": pd.Series(True, index=rows.index),
        "20因子三分类_预测1": rows.predicted_class_full.eq(1),
        "8因子三分类_预测1": rows.predicted_class_compact.eq(1),
        "20因子三分类_分数至少0.65": rows.positive_score_full.ge(0.65),
        "20因子三分类_分数至少0.70": rows.positive_score_full.ge(0.70),
        "20因子三分类_分数至少0.75": rows.positive_score_full.ge(0.75),
        "8因子三分类_分数至少0.60": rows.positive_score_compact.ge(0.60),
        "8因子三分类_分数至少0.65": rows.positive_score_compact.ge(0.65),
        "8因子三分类_每日前1": rows.rank_compact.le(1),
        "8因子三分类_每日前2": rows.rank_compact.le(2),
        "8因子三分类_每日前3": rows.rank_compact.le(3),
        "8因子三分类_每日前5": rows.rank_compact.le(5),
        "8因子二分类_每日前1": rows.rank_binary.le(1),
        "8因子二分类_每日前2": rows.rank_binary.le(2),
        "8因子二分类_每日前3": rows.rank_binary.le(3),
        "8因子二分类_每日前5": rows.rank_binary.le(5),
        "8因子二分类_分数至少0.75": rows.binary_pass_score.ge(0.75),
        "8因子二分类_分数至少0.80": rows.binary_pass_score.ge(0.80),
    }


def rule_metrics(rows, selected, day_base):
    group = rows[selected]
    if group.empty:
        return {"n": 0, "days": 0, "weeks": 0, "critical": 0,
                "critical_rate": None, "pass": 0, "pass_rate": None,
                "neutral": 0, "strong": 0, "strong_rate": None,
                "below_entry": 0, "same_day_critical_base": None,
                "critical_difference_pp": None}
    critical_rate = float(group.critical.mean())
    baseline = float(group.signal_date.map(day_base).mean())
    return {
        "n": len(group), "days": group.signal_date.nunique(),
        "weeks": group.signal_date.dt.to_period("W-SUN").nunique(),
        "critical": int(group.critical.sum()), "critical_rate": critical_rate,
        "pass": int(group["pass"].sum()), "pass_rate": float(group["pass"].mean()),
        "neutral": int(group["class"].eq(0).sum()),
        "strong": int(group["class"].eq(1).sum()),
        "strong_rate": float(group["class"].eq(1).mean()),
        "below_entry": int(group.below_entry.sum()),
        "same_day_critical_base": baseline,
        "critical_difference_pp": (critical_rate - baseline) * 100,
    }


def records(frame):
    """Make JSON workbook inputs without changing numeric source values."""
    clean = frame.copy()
    for column in clean.columns:
        if pd.api.types.is_datetime64_any_dtype(clean[column]):
            clean[column] = clean[column].dt.strftime("%Y-%m-%d")
    clean = clean.replace({np.nan: None})
    return clean.to_dict(orient="records")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", default="outputs/stock_automl/tail_standard/trainable_1440.csv")
    parser.add_argument("--predictions", default="outputs/stock_automl/tail_three_class/out_of_sample_predictions.csv")
    parser.add_argument("--candidates", default="outputs/stock_automl/tail_standard/candidates_1440.csv")
    parser.add_argument("--report", default="docs/stock_automl_runs/20261008_tail_critical_review/summary.json")
    parser.add_argument("--workbook-data", default="outputs/stock_automl/tail_critical_review/workbook_data.json")
    args = parser.parse_args()
    sample_path, prediction_path, candidate_path = map(Path, (
        args.samples, args.predictions, args.candidates))
    samples = read_samples(sample_path)
    predictions = pd.read_csv(prediction_path, dtype={"symbol6": str},
                              parse_dates=["signal_date", "next_date"])
    candidates = pd.read_csv(candidate_path, dtype={"symbol6": str},
                             parse_dates=["signal_date"])
    binary, binary_coefficients = fit_binary_oos(samples)
    detail = assemble_detail(samples, predictions, candidates, binary)
    day_base = detail.groupby("signal_date").critical.mean()
    selection = rules(detail)
    rule_summary = [{"rule": name, **rule_metrics(detail, mask, day_base)}
                    for name, mask in selection.items()]
    key_rules = ("20因子三分类_分数至少0.70", "8因子三分类_每日前3",
                 "8因子二分类_每日前2")
    daily = []
    for day, group in detail.groupby("signal_date"):
        item = {"signal_date": day.date().isoformat(), "pool": len(group),
                "pool_critical": int(group.critical.sum()),
                "pool_neutral": int(group["class"].eq(0).sum()),
                "pool_strong": int(group["class"].eq(1).sum()),
                "pool_critical_rate": float(group.critical.mean())}
        for name in key_rules:
            part = group[selection[name].loc[group.index]]
            item[name] = {"n": len(part), "critical": int(part.critical.sum()),
                          "neutral": int(part["class"].eq(0).sum()),
                          "strong": int(part["class"].eq(1).sum()),
                          "below_entry": int(part.below_entry.sum())}
        daily.append(item)
    fold_review = []
    for fold, group in detail.groupby("test_fold"):
        baseline = group.groupby("signal_date").critical.mean()
        for name in key_rules:
            metric = rule_metrics(group, selection[name].loc[group.index], baseline)
            fold_review.append({"fold": int(fold), "rule": name, **metric})
    auc = {
        "full_positive_score_for_pass": float(roc_auc_score(
            detail["pass"], detail.positive_score_full)),
        "compact_positive_score_for_pass": float(roc_auc_score(
            detail["pass"], detail.positive_score_compact)),
        "binary_pass_score_for_pass": float(roc_auc_score(
            detail["pass"], detail.binary_pass_score)),
    }
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pass_definition": "actual class 0 or 1; critical error is actual class -1",
        "target": "T+1 09:31-09:40 maximum high / T 14:50 entry proxy - 1",
        "test_period": [detail.signal_date.min().date().isoformat(),
                        detail.signal_date.max().date().isoformat()],
        "test_days": detail.signal_date.nunique(),
        "test_rows": len(detail),
        "rule_summary": rule_summary,
        "fold_review": fold_review,
        "auc_for_pass": auc,
        "binary_coefficients_by_fold": binary_coefficients,
        "provenance": {
            "samples_sha256": hashlib.sha256(sample_path.read_bytes()).hexdigest(),
            "predictions_sha256": hashlib.sha256(prediction_path.read_bytes()).hexdigest(),
            "candidates_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    }
    output = Path(args.workbook_data)
    output.parent.mkdir(parents=True, exist_ok=True)
    compact_columns = ["signal_date", "next_date", "symbol6", "name", "test_fold",
                       "signal_return", "volume_ratio", "turnover_so_far_pct",
                       "float_mv_100m_cny", *BINARY,
                       "positive_score_full", "negative_score_full",
                       "positive_score_compact", "negative_score_compact",
                       "binary_pass_score", "rank_full", "rank_compact", "rank_binary",
                       "class", "pass", "critical", "below_entry",
                       "target_next_open_return", "target_next_high5_return",
                       "target_next_high10_return", "entry_1450",
                       "entry_not_near_limit_proxy", "t_final_limit_flag"]
    selected_flag_names = ["20因子三分类_分数至少0.70", "20因子三分类_分数至少0.75",
                           "8因子三分类_每日前3", "8因子二分类_每日前2"]
    for name in selected_flag_names:
        detail[name] = selection[name].astype(int)
    workbook_detail = detail[compact_columns + selected_flag_names]
    shortlist = workbook_detail[
        detail[[key_rules[0], key_rules[1], key_rules[2]]].any(axis=1)]
    workbook_data = {"summary": report, "daily": daily,
                     "selected": records(shortlist), "all": records(workbook_detail)}
    output.write_text(json.dumps(workbook_data, ensure_ascii=False, allow_nan=False))
    destination = Path(args.report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"reviewed {len(detail)} scored rows across {report['test_days']} days; "
          f"wrote {len(shortlist)} selected stock-day rows")


if __name__ == "__main__":
    main()

"""Fit the original three training windows once each, then score every other day."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.experiment_automl_tail_three_class import (
    COMPACT_FEATURES, day_folds, read_samples,
)
from scripts.run_automl_tail_today import fitted_model


def outcome(rows: pd.DataFrame) -> dict:
    return {"days": rows.signal_date.nunique(), "stocks": len(rows),
            "critical": int(rows["class"].eq(-1).sum()),
            "neutral": int(rows["class"].eq(0).sum()),
            "strong_over_1pct": int(rows["class"].eq(1).sum())}


def evaluate(original_path: Path, expanded_path: Path, sep30_path: Path,
             output_dir: Path, report_path: Path) -> dict:
    original = read_samples(original_path)
    all_rows = pd.concat([read_samples(expanded_path), read_samples(sep30_path)],
                         ignore_index=True)
    if all_rows.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate stock-day in the expanded test pool")
    if all_rows.signal_date.nunique() != 37:
        raise ValueError("Expected 37 signal days from 2026-08-07 to 2026-09-30")
    original_keys = original[["signal_date", "symbol6"]]
    expanded_keys = all_rows[["signal_date", "symbol6"]]
    if len(original_keys.merge(expanded_keys, how="inner")) != len(original):
        raise ValueError("The original 8,590 stock-days changed in the expanded pool")

    common_dates = sorted(set(all_rows.loc[
        (all_rows.signal_date.le("2026-08-21") |
         all_rows.signal_date.ge("2026-09-22")), "signal_date"]))
    result = []
    ranked_details = []
    for number, (training, assigned_test) in enumerate(day_folds(original), 1):
        model = fitted_model(training)
        test = all_rows.loc[~all_rows.signal_date.isin(training.signal_date.unique())].copy()
        if test.empty or test.signal_date.isin(training.signal_date.unique()).any():
            raise ValueError("Training and scored dates overlap")
        test["score"] = model.predict_proba(test[list(COMPACT_FEATURES)])[:, 1]
        test = test.sort_values(["signal_date", "score", "symbol6"],
                                ascending=[True, False, True])
        test["rank"] = test.groupby("signal_date").cumcount().add(1)
        top2 = test.loc[test["rank"].le(2)].copy()
        top2["model"] = number
        ranked_details.append(top2)
        august = top2.loc[top2.signal_date.between("2026-08-07", "2026-08-21")]
        september = top2.loc[top2.signal_date.between("2026-09-01", "2026-09-30")]
        common = top2.loc[top2.signal_date.isin(common_dates)]
        assigned = top2.loc[top2.signal_date.isin(assigned_test.signal_date.unique())]
        if len(assigned) != 10 or len(august) != 22 or len(common) != 34:
            raise ValueError("Missing top-two choices in a required test segment")
        result.append({
            "model": number,
            "training_signal_period": [training.signal_date.min().date().isoformat(),
                                       training.signal_date.max().date().isoformat()],
            "training_days": training.signal_date.nunique(),
            "training_rows": len(training),
            "all_nontraining_days_top2": outcome(top2),
            "august_07_to_21_top2": outcome(august),
            "september_nontraining_top2": outcome(september),
            "common_17_days_top2": outcome(common),
            "common_late_september_top2": outcome(common.loc[
                common.signal_date.ge("2026-09-22")]),
            "original_assigned_five_days_top2": outcome(assigned),
        })
    details = pd.concat(ranked_details, ignore_index=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    columns = ["model", "signal_date", "next_date", "symbol6", "name", "rank",
               "score", "class", "target_next_high10_return",
               "target_next_open_return", "target_next_high5_return"]
    details[columns].to_csv(output_dir / "three_frozen_folds_top2.csv", index=False)
    report = {"method": "three separate fits; score all dates outside each own training window",
              "available_signal_days": all_rows.signal_date.nunique(),
              "available_candidate_rows": len(all_rows),
              "common_test_dates": [day.date().isoformat() for day in common_dates],
              "models": result,
              "sha256": {"original_samples": hashlib.sha256(
                  original_path.read_bytes()).hexdigest(),
                  "expanded_samples": hashlib.sha256(
                      expanded_path.read_bytes()).hexdigest(),
                  "sep30_samples": hashlib.sha256(sep30_path.read_bytes()).hexdigest()}}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"available_signal_days": report["available_signal_days"],
                      "available_candidate_rows": report["available_candidate_rows"],
                      "models": result}, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, default=Path(
        "outputs/stock_automl/tail_standard/trainable_1440.csv"))
    parser.add_argument("--expanded", type=Path, default=Path(
        "outputs/stock_automl/tail_expanded_august/trainable_1440.csv"))
    parser.add_argument("--sep30", type=Path, default=Path(
        "outputs/stock_automl/tail_sep30_check/trainable_1440.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path(
        "outputs/stock_automl/tail_expanded_august"))
    parser.add_argument("--report", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_august_extension/three_frozen_folds_summary.json"))
    args = parser.parse_args()
    evaluate(args.original, args.expanded, args.sep30, args.output_dir, args.report)

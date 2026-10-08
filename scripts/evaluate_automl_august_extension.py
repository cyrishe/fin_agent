"""Chronological eight-factor test after adding complete August one-minute days."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
from sklearn.metrics import roc_auc_score

from scripts.analyze_automl_tail_critical import fit_binary_oos, rank_by_day
from scripts.experiment_automl_tail_three_class import read_samples


def metrics(group: pd.DataFrame):
    if group.empty:
        return {"n": 0, "days": 0, "critical": 0, "neutral": 0, "strong": 0,
                "critical_rate": None, "strong_rate": None, "pass_rate": None}
    return {"n": len(group), "days": group.signal_date.nunique(),
            "critical": int(group["class"].eq(-1).sum()),
            "neutral": int(group["class"].eq(0).sum()),
            "strong": int(group["class"].eq(1).sum()),
            "critical_rate": float(group["class"].eq(-1).mean()),
            "strong_rate": float(group["class"].eq(1).mean()),
            "pass_rate": float(group["class"].ne(-1).mean())}


def evaluate(samples_path: Path, candidates_path: Path, standard_summary_path: Path,
             output_dir: Path, report_path: Path):
    samples = read_samples(samples_path)
    scores, fold_coefficients = fit_binary_oos(samples)
    test = samples.merge(scores, on=["signal_date", "symbol6"], how="inner",
                         validate="one_to_one")
    rank_by_day(test, "binary_pass_score", "rank_binary")
    audit = pd.read_csv(candidates_path, dtype={"symbol6": str},
                        parse_dates=["signal_date"])[
                            ["signal_date", "symbol6", "entry_not_near_limit_proxy",
                             "t_final_limit_flag", "entry_1450"]]
    test = test.merge(audit, on=["signal_date", "symbol6"],
                      how="left", validate="one_to_one")
    if test[["binary_pass_score", "entry_not_near_limit_proxy",
             "t_final_limit_flag", "entry_1450"]].isna().any().any():
        raise ValueError("Missing score or execution audit")
    top2 = test[test.rank_binary.le(2)].sort_values(
        ["signal_date", "rank_binary", "symbol6"])
    top5 = test[test.rank_binary.le(5)].sort_values(
        ["signal_date", "rank_binary", "symbol6"])
    if not top2.groupby("signal_date").size().eq(2).all() or not top5.groupby(
            "signal_date").size().eq(5).all():
        raise ValueError("Every out-of-sample day must have two/five candidates")
    standard = json.loads(standard_summary_path.read_text())
    audit_days = pd.DataFrame(standard["day_audit"])
    if audit_days.empty:
        raise ValueError("Missing expanded signal-day audit")
    daily = []
    for day, group in test.groupby("signal_date"):
        first = group[group.rank_binary.le(2)]
        daily.append({"signal_date": day.date().isoformat(),
                      "pool": metrics(group), "top2": metrics(first),
                      "top2_min_score": float(first.binary_pass_score.min())})
    by_month = []
    for month, group in test.groupby(test.signal_date.dt.to_period("M")):
        by_month.append({"month": str(month), "pool": metrics(group),
                         "top2": metrics(group[group.rank_binary.le(2)]),
                         "top5": metrics(group[group.rank_binary.le(5)])})
    late_audit = top2[~top2.entry_not_near_limit_proxy.astype(bool) |
                      top2.t_final_limit_flag.ne(0)]
    prior_window = top2[top2.signal_date.between("2026-09-08", "2026-09-29")]
    output = {"training_period": [samples.signal_date.min().date().isoformat(),
                                   samples.signal_date.max().date().isoformat()],
              "all_sample_rows": len(samples),
              "all_sample_days": samples.signal_date.nunique(),
              "test_period": [test.signal_date.min().date().isoformat(),
                              test.signal_date.max().date().isoformat()],
              "test_days": test.signal_date.nunique(),
              "pool": metrics(test), "top2": metrics(top2), "top5": metrics(top5),
              "by_month": by_month, "daily": daily,
              "prior_test_window_top2": metrics(prior_window),
              "auc_for_pass": float(roc_auc_score(
                  test["class"].ne(-1), test.binary_pass_score)),
              "top2_mean_score_by_outcome": {
                  "pass": float(top2.loc[top2["class"].ne(-1),
                                     "binary_pass_score"].mean()),
                  "critical": float(top2.loc[top2["class"].eq(-1),
                                         "binary_pass_score"].mean())},
              "late_execution_issue_top2_count": len(late_audit),
              "fold_coefficients": fold_coefficients,
              "sample_audit": {"requested_days": len(audit_days),
                               "missing_signal_bar_days": audit_days.loc[
                                   audit_days.missing_signal_bar.eq(True), "date"].tolist(),
                               "trainable_min": int(audit_days.trainable.min()),
                               "trainable_max": int(audit_days.trainable.max())},
              "provenance_sha256": {
                  "samples": hashlib.sha256(samples_path.read_bytes()).hexdigest(),
                  "candidates": hashlib.sha256(candidates_path.read_bytes()).hexdigest(),
                  "standard_summary": hashlib.sha256(
                      standard_summary_path.read_bytes()).hexdigest(),
                  "script": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}}
    output_dir.mkdir(parents=True, exist_ok=True)
    export_columns = ["signal_date", "next_date", "symbol6", "name", "rank_binary",
                      "binary_pass_score", "class", "target_next_open_return",
                      "target_next_high5_return", "target_next_high10_return",
                      "entry_1450", "entry_not_near_limit_proxy", "t_final_limit_flag"]
    top2[export_columns].to_csv(output_dir / "top2_detail.csv", index=False)
    top5[export_columns].to_csv(output_dir / "top5_detail.csv", index=False)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in output.items()
                      if key not in ("daily", "fold_coefficients")}, ensure_ascii=False,
                     indent=2))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path("outputs/stock_automl/tail_expanded_august")
    parser.add_argument("--samples", type=Path, default=base / "trainable_1440.csv")
    parser.add_argument("--candidates", type=Path, default=base / "candidates_1440.csv")
    parser.add_argument("--standard-summary", type=Path, default=base / "summary.json")
    parser.add_argument("--output-dir", type=Path, default=base)
    parser.add_argument("--report", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_august_extension/summary.json"))
    args = parser.parse_args()
    evaluate(args.samples, args.candidates, args.standard_summary,
             args.output_dir, args.report)

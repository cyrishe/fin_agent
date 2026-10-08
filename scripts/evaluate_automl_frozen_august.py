"""Score newly backfilled August dates with the previously saved fixed formula."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import expit

from scripts.experiment_automl_tail_three_class import read_samples


def frozen_score(rows: pd.DataFrame, formula: dict) -> np.ndarray:
    log_odds = np.full(len(rows), formula["intercept"], dtype=float)
    for feature in formula["features"]:
        values = rows[feature["name"]].to_numpy(dtype=float)
        if feature["type"] == "numeric_standardized":
            values = (values - feature["training_mean"]) / feature["training_std"]
        log_odds += feature["coefficient"] * values
    return expit(log_odds)


def counts(rows: pd.DataFrame) -> dict:
    return {"stocks": len(rows), "days": rows.signal_date.nunique(),
            "strong_over_1pct": int(rows["class"].eq(1).sum()),
            "neutral_0p5_to_1pct": int(rows["class"].eq(0).sum()),
            "critical_under_0p5pct": int(rows["class"].eq(-1).sum())}


def evaluate(samples_path: Path, candidates_path: Path, formula_path: Path,
             prior_scores_path: Path, output_dir: Path, report_path: Path) -> dict:
    formula = json.loads(formula_path.read_text())
    samples = read_samples(samples_path)
    rows = samples[samples.signal_date.between("2026-08-07", "2026-08-21")].copy()
    if rows.signal_date.nunique() != 11:
        raise ValueError("Expected all 11 backfilled signal days")
    rows["frozen_score"] = frozen_score(rows, formula)
    rows = rows.sort_values(["signal_date", "frozen_score", "symbol6"],
                            ascending=[True, False, True])
    rows["rank"] = rows.groupby("signal_date").cumcount().add(1)
    audit = pd.read_csv(candidates_path, dtype={"symbol6": str},
                        parse_dates=["signal_date"])[
                            ["signal_date", "symbol6", "entry_not_near_limit_proxy",
                             "t_final_limit_flag"]]
    rows = rows.merge(audit, on=["signal_date", "symbol6"],
                      how="left", validate="one_to_one")
    if rows[["entry_not_near_limit_proxy", "t_final_limit_flag"]].isna().any().any():
        raise ValueError("Missing execution proxy for scored rows")
    prior = pd.read_csv(prior_scores_path)
    prior_formula_score = frozen_score(prior, formula)
    max_diff = float(np.max(np.abs(prior_formula_score - prior.binary_pass_score)))
    if max_diff > 1e-12:
        raise ValueError("Saved formula does not reproduce original model scores")
    top2 = rows[rows["rank"].le(2)].copy()
    top5 = rows[rows["rank"].le(5)].copy()
    late_issue = top2[~top2.entry_not_near_limit_proxy.astype(bool) |
                      top2.t_final_limit_flag.ne(0)]
    top2_after_execution_audit = top2.drop(late_issue.index)
    daily = []
    for day, group in top2.groupby("signal_date"):
        daily.append({"date": day.date().isoformat(), **counts(group)})
    output = {
        "mode": "frozen_formula_score_only_no_fit",
        "signal_period": ["2026-08-07", "2026-08-21"],
        "formula_training_period": formula["training_signal_period"],
        "pool": counts(rows), "top2": counts(top2), "top5": counts(top5),
        "top2_after_execution_audit": counts(top2_after_execution_audit),
        "top2_late_execution_proxy_issue_count": len(late_issue),
        "original_score_reconstruction_max_abs_diff": max_diff,
        "daily_top2": daily,
        "sha256": {"samples": hashlib.sha256(samples_path.read_bytes()).hexdigest(),
                   "formula": hashlib.sha256(formula_path.read_bytes()).hexdigest(),
                   "prior_scores": hashlib.sha256(
                       prior_scores_path.read_bytes()).hexdigest()},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    columns = ["signal_date", "next_date", "symbol6", "name", "rank",
               "frozen_score", "class", "target_next_high10_return",
               "target_next_open_return", "target_next_high5_return",
               "entry_not_near_limit_proxy", "t_final_limit_flag"]
    top2[columns].to_csv(output_dir / "frozen_top2_detail.csv", index=False)
    top5[columns].to_csv(output_dir / "frozen_top5_detail.csv", index=False)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in output.items() if k != "daily_top2"},
                     ensure_ascii=False, indent=2))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path("outputs/stock_automl/tail_expanded_august")
    parser.add_argument("--samples", type=Path, default=base / "trainable_1440.csv")
    parser.add_argument("--candidates", type=Path, default=base / "candidates_1440.csv")
    parser.add_argument("--formula", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_score_reliability/model_formula.json"))
    parser.add_argument("--prior-scores", type=Path, default=Path(
        "outputs/stock_automl/tail_live_review/2026-10-08_all_scored.csv"))
    parser.add_argument("--output-dir", type=Path, default=base)
    parser.add_argument("--report", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_august_extension/frozen_formula_summary.json"))
    args = parser.parse_args()
    evaluate(args.samples, args.candidates, args.formula, args.prior_scores,
             args.output_dir, args.report)

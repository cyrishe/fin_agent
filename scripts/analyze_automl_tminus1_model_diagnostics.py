"""Can training metrics or 14:40 probabilities identify T-1 Top1 winners?"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import log_loss

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_four_class_subset_selection import (
    BASE, fit_tree, random_training_subset, replay_top1,
)
from scripts.experiment_automl_recovered_morning_labels import recovered_rows
from scripts.run_automl_four_class_pipeline import (
    PREPARED, generate_training_labels, infer, select_inference_candidates,
    select_top, select_training_samples,
)


PREVIOUS = Path("docs/stock_automl_runs/20261010_four_class_subset_selection")
OUT = Path("docs/stock_automl_runs/20261010_four_class_tminus1_diagnostics")
METRIC_DIRECTIONS = {
    "train_loss": "min", "oob_loss": "min", "all19_loss": "min",
    "top1_p_ge3": "max", "top1_p_good_minus_bad": "max",
    "top1_confidence": "max",
}


def model_loss(model, rows: pd.DataFrame) -> float:
    if rows.empty:
        raise ValueError("Empty diagnostic cohort")
    probability = model.predict_proba(rows[list(FEATURES)])
    labels = generate_training_labels(rows)
    return float(log_loss(labels, probability, labels=list(model.classes_)))


def top1_probabilities(scored: pd.DataFrame) -> dict:
    first = select_top(scored)
    first = first[first.selection_rank.eq(1)]
    if first.empty:
        return {"top1_p_ge3": np.nan, "top1_p_ge1": np.nan,
                "top1_p_lt0": np.nan, "top1_p_good_minus_bad": np.nan,
                "top1_confidence": np.nan, "top1_entropy": np.nan,
                "top1_predicted_class": "NO_TRADE"}
    row = first.iloc[0]
    probabilities = np.array([row.p_lt0, row.p_0to1,
                              row.p_1to3, row.p_ge3], dtype=float)
    good = float(row.p_1to3 + row.p_ge3)
    return {"top1_p_ge3": float(row.p_ge3), "top1_p_ge1": good,
            "top1_p_lt0": float(row.p_lt0),
            "top1_p_good_minus_bad": good - float(row.p_lt0),
            "top1_confidence": float(probabilities.max()),
            "top1_entropy": float(-(probabilities * np.log(
                np.clip(probabilities, 1e-15, 1))).sum()),
            "top1_predicted_class": str(row.predicted_class)}


def within_day_correlations(frame: pd.DataFrame) -> pd.DataFrame:
    records = []
    for metric, direction in METRIC_DIRECTIONS.items():
        for day, group in frame.groupby("test_date"):
            pair = group[[metric, "tminus1_return"]].dropna()
            correlation = (float(spearmanr(pair[metric], pair.tminus1_return).statistic)
                           if pair[metric].nunique() > 1 and
                           pair.tminus1_return.nunique() > 1 else np.nan)
            records.append({"test_date": day, "metric": metric,
                            "direction": direction, "spearman": correlation,
                            "models": len(pair)})
    return pd.DataFrame(records)


def metric_selections(frame: pd.DataFrame) -> pd.DataFrame:
    records = []
    for metric, direction in METRIC_DIRECTIONS.items():
        for day, group in frame.groupby("test_date"):
            ranked = group[group[metric].notna()].sort_values(
                [metric, "fraction", "repeat"],
                ascending=[direction == "min", False, True])
            if ranked.empty:
                continue
            row = ranked.iloc[0]
            records.append({"test_date": day, "metric": metric,
                            "chosen_subset": row.subset,
                            "chosen_metric": float(row[metric]),
                            "tminus1_return": float(row.tminus1_return),
                            "tminus1_symbol6": row.tminus1_symbol6})
    return pd.DataFrame(records)


def run(rows: pd.DataFrame, previous: pd.DataFrame,
        controls: pd.DataFrame, t_returns: pd.DataFrame, output: Path) -> dict:
    days = sorted(rows.signal_date.unique())
    if len(rows) != 14783 or len(days) != 40:
        raise ValueError("Frozen candidate pool changed")
    records = []
    for index in range(20, len(days)):
        day = days[index]
        tminus1 = days[index-1]
        historical = days[index-20:index-1]
        full_training = select_training_samples(rows, historical)
        candidates = select_inference_candidates(rows, tminus1)
        for fraction in (.8, .9):
            for repeat in range(1, 11):
                seed = 20261010 + index*1000 + int(fraction*100)*10 + repeat
                subset = f"random_{int(fraction*100)}_{repeat:02}"
                training = random_training_subset(full_training, fraction, seed)
                oob = full_training.loc[~full_training.index.isin(training.index)]
                model = fit_tree(training, day)
                scored = infer(model, candidates)
                actual = replay_top1(scored, rows, tminus1)
                if actual["status"] == "UNKNOWN":
                    raise ValueError("Unobservable T-1 outcome in diagnostics")
                records.append({"test_date": day, "tminus1_date": tminus1,
                                "subset": subset, "fraction": fraction,
                                "repeat": repeat, "training_rows": len(training),
                                "oob_rows": len(oob),
                                "train_loss": model_loss(model, training),
                                "oob_loss": model_loss(model, oob),
                                "all19_loss": model_loss(model, full_training),
                                "train_accuracy": float(np.mean(
                                    model.predict(training[list(FEATURES)]) ==
                                    generate_training_labels(training))),
                                **top1_probabilities(scored),
                                "tminus1_symbol6": actual["symbol6"],
                                "tminus1_return": actual["return_0940"],
                                "tminus1_status": actual["status"]})
        print(f"diagnostics {day} complete", flush=True)
    frame = pd.DataFrame(records)
    expected = previous.rename(columns={"symbol6": "expected_symbol6",
                                        "return_0940": "expected_return"})
    expected["expected_symbol6"] = expected.expected_symbol6.fillna("")
    checked = frame.merge(expected[["test_date", "subset", "expected_symbol6",
                                    "expected_return"]],
                          on=["test_date", "subset"], validate="one_to_one")
    if (len(checked) != 400 or not checked.tminus1_symbol6.eq(
            checked.expected_symbol6).all() or not np.allclose(
                checked.tminus1_return, checked.expected_return)):
        raise ValueError("Recomputed T-1 decisions differ from the previous run")
    correlations = within_day_correlations(frame)
    selections = metric_selections(frame)
    prospective = selections.rename(columns={"chosen_subset": "subset"}).merge(
        t_returns[["test_date", "subset", "t_return", "t_symbol6"]],
        on=["test_date", "subset"], validate="many_to_one")
    if len(prospective) != len(selections) or prospective.t_return.isna().any():
        raise ValueError("Missing prospective T outcomes for selected models")
    controls = controls.set_index("test_date")
    summary = {"models": len(frame), "days": frame.test_date.nunique(),
               "unique_top1_per_day": {
                   "min": int(frame.groupby("test_date").tminus1_symbol6.nunique().min()),
                   "median": float(frame.groupby("test_date").tminus1_symbol6.nunique().median()),
                   "max": int(frame.groupby("test_date").tminus1_symbol6.nunique().max())},
               "oracle_tminus1_return_pct": float(controls.chosen_tminus1_return.mean()*100),
               "full19_tminus1_return_pct": float(
                   controls.control19_tminus1_return.mean()*100),
               "random_model_tminus1_mean_pct": float(frame.tminus1_return.mean()*100),
               "metrics": {}}
    for metric, direction in METRIC_DIRECTIONS.items():
        selected = selections[selections.metric.eq(metric)].set_index("test_date")
        corr = correlations[correlations.metric.eq(metric)].spearman.dropna()
        delta = selected.tminus1_return - controls.control19_tminus1_return
        second_half = selected.loc[sorted(selected.index)[10:]]
        selected_t = prospective[prospective.metric.eq(metric)].sort_values("test_date")
        summary["metrics"][metric] = {
            "direction": direction,
            "within_day_spearman_mean": float(corr.mean()) if len(corr) else None,
            "within_day_spearman_median": float(corr.median()) if len(corr) else None,
            "positive_correlation_days": int(corr.gt(0).sum()),
            "correlation_days": len(corr),
            "selected_tminus1_mean_pct": float(selected.tminus1_return.mean()*100),
            "selected_last10_mean_pct": float(second_half.tminus1_return.mean()*100),
            "selected_t_mean_pct": float(selected_t.t_return.mean()*100),
            "selected_t_last10_mean_pct": float(selected_t.t_return.iloc[10:].mean()*100),
            "vs_full19_better_days": int(delta.gt(1e-10).sum()),
            "vs_full19_worse_days": int(delta.lt(-1e-10).sum()),
            "vs_full19_same_days": int(delta.abs().le(1e-10).sum()),
        }
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / "model_diagnostics.csv.gz", index=False, compression="gzip")
    correlations.to_csv(output / "within_day_correlations.csv", index=False)
    selections.to_csv(output / "metric_selected_top1.csv", index=False)
    prospective.to_csv(output / "metric_selected_t_results.csv", index=False)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                   indent=2)+"\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, default=PREPARED)
    parser.add_argument("--api", type=Path, default=BASE / "minute_api_label_audit.csv")
    parser.add_argument("--previous", type=Path,
                        default=PREVIOUS / "tminus1_subset_returns.csv")
    parser.add_argument("--controls", type=Path,
                        default=PREVIOUS / "chosen_subsets.csv")
    parser.add_argument("--t-results", type=Path,
                        default=PREVIOUS / "candidate_t_returns.csv")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    original = pd.read_csv(args.prepared, dtype={"symbol6": str})
    rows = recovered_rows(original, pd.read_csv(args.api, dtype={"symbol6": str}))
    previous = pd.read_csv(args.previous, dtype={"symbol6": str})
    controls = pd.read_csv(args.controls)
    t_returns = pd.read_csv(args.t_results, dtype={"t_symbol6": str})
    summary = run(rows, previous, controls, t_returns, args.output)
    summary["sources_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (args.prepared, args.api, args.previous,
                                              args.controls, args.t_results,
                                              Path(__file__))}
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                        indent=2)+"\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

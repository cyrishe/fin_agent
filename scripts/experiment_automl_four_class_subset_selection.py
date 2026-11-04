"""Select one of repeated random historical training subsets by T-1 return."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_automl_recovered_morning_labels import recovered_rows
from scripts.experiment_automl_second_high_four_class import models
from scripts.run_automl_four_class_pipeline import (
    PREPARED, generate_training_features, generate_training_labels, infer,
    select_inference_candidates, select_top, select_training_samples,
    summary_for_picks, verify_next_day,
)


BASE = Path("docs/stock_automl_runs/20261010_four_class_baseline_recent20")
OUT = Path("docs/stock_automl_runs/20261010_four_class_subset_selection")


def random_training_subset(training: pd.DataFrame, fraction: float,
                           seed: int) -> pd.DataFrame:
    """Sample stocks within each historical day to retain its date mix."""
    if fraction not in (.8, .9) or training.signal_date.nunique() != 19:
        raise ValueError("Expected 80% or 90% of 19 labeled historical days")
    rng = np.random.default_rng(seed)
    pieces = [group.sample(frac=fraction, replace=False,
                           random_state=int(rng.integers(0, 2**32 - 1)))
              for _, group in training.groupby("signal_date", sort=True)]
    sampled = pd.concat(pieces).sort_index()
    if sampled.signal_date.nunique() != 19 or len(sampled) >= len(training):
        raise ValueError("Random subset omitted a historical date")
    return sampled


def replay_top1(scored: pd.DataFrame, outcomes: pd.DataFrame,
                signal_date: str) -> dict:
    picks = verify_next_day(select_top(scored), outcomes)
    first = picks[picks.selection_rank.eq(1)]
    if first.empty:
        return {"symbol6": "", "name": "", "return_0940": 0.,
                "status": "NO_TRADE", "predicted_class": ""}
    row = first.iloc[0]
    return {"symbol6": row.symbol6, "name": row["name"],
            "return_0940": float(row.actual_0940_return)
            if pd.notna(row.actual_0940_return) else np.nan,
            "status": "UNKNOWN" if pd.isna(row.actual_0940_return)
            else "OBSERVED", "predicted_class": row.predicted_class}


def choose_winner(results: pd.DataFrame) -> str:
    observed = results[results.return_0940.notna()]
    if observed.empty:
        raise ValueError("No observable T-1 Top1 result for subset selection")
    return str(observed.sort_values(["return_0940", "fraction", "repeat"],
                                    ascending=[False, False, True]).subset.iloc[0])


def fit_tree(training: pd.DataFrame, decision_day: str):
    if not training.next_date.le(decision_day).all():
        raise ValueError("Training label later than the T decision")
    model = models()["small_boosted_tree"]
    model.fit(generate_training_features(training), generate_training_labels(training))
    return model


def paired_summary(picks: pd.DataFrame, baseline: pd.DataFrame) -> dict:
    top1 = picks[picks.selection_rank.eq(1)].set_index("signal_date")
    baseline = baseline.set_index("signal_date")
    delta = top1.actual_0940_return - baseline.actual_0940_return
    return {"top1": summary_for_picks(top1.reset_index()),
            "top2": summary_for_picks(picks),
            "changed_top1_days": int(top1.symbol6.ne(baseline.symbol6).sum()),
            "mean_delta_pct_points": float(delta.mean() * 100),
            "better_days": int(delta.gt(1e-10).sum()),
            "worse_days": int(delta.lt(-1e-10).sum()),
            "same_days": int(delta.abs().le(1e-10).sum())}


def run(rows: pd.DataFrame, baseline: pd.DataFrame, output: Path) -> dict:
    days = sorted(rows.signal_date.unique())
    if len(rows) != 14783 or len(days) != 40 or rows.name.str.contains(
            "ST", case=False, na=False).sum() != 414:
        raise ValueError("Frozen all-ST candidate pool changed")
    tminus1_rows, subset_rows, chosen_rows = [], [], []
    scored_rows, picked_rows, candidate_day_rows = [], [], []
    for index in range(20, len(days)):
        day = days[index]
        tminus1 = days[index-1]
        historical = days[index-20:index-1]  # 19 dates, ending T-2.
        historical_training = select_training_samples(rows, historical)
        tminus1_candidates = select_inference_candidates(rows, tminus1)
        current = select_inference_candidates(rows, day)
        fitted = {}
        current_results = []
        for fraction in (.8, .9):
            for repeat in range(1, 11):
                seed = 20261010 + index*1000 + int(fraction*100)*10 + repeat
                subset = f"random_{int(fraction*100)}_{repeat:02}"
                training = random_training_subset(historical_training, fraction, seed)
                model = fit_tree(training, day)
                fitted[subset] = model
                selection = replay_top1(infer(model, tminus1_candidates), rows, tminus1)
                current_results.append({"test_date": day, "tminus1_date": tminus1,
                                        "subset": subset, "fraction": fraction,
                                        "repeat": repeat, **selection})
                subset_rows.append({"test_date": day, "subset": subset,
                                    "fraction": fraction, "repeat": repeat,
                                    "seed": seed, "historical_dates": "|".join(historical),
                                    "training_rows": len(training),
                                    "training_st_rows": int(training.name.str.contains(
                                        "ST", case=False, na=False).sum())})
        tminus1_rows.extend(current_results)
        winner = choose_winner(pd.DataFrame(current_results))
        control = fit_tree(historical_training, day)
        for record in current_results:
            later = replay_top1(infer(fitted[record["subset"]], current), rows, day)
            candidate_day_rows.append({"test_date": day, "subset": record["subset"],
                                       "fraction": record["fraction"],
                                       "repeat": record["repeat"],
                                       "tminus1_return": record["return_0940"],
                                       "t_return": later["return_0940"],
                                       "t_status": later["status"],
                                       "t_symbol6": later["symbol6"],
                                       "chosen": record["subset"] == winner})
        chosen_rows.append({"test_date": day, "tminus1_date": tminus1,
                            "chosen_subset": winner,
                            "chosen_tminus1_return": next(x["return_0940"] for x in
                                                           current_results if x["subset"] == winner),
                            "control19_tminus1_return": replay_top1(
                                infer(control, tminus1_candidates), rows,
                                tminus1)["return_0940"],
                            "control19_training_rows": len(historical_training),
                            "current_candidates": len(current)})
        for rule, model in (("selected_subset", fitted[winner]),
                            ("full19_control", control)):
            scored = infer(model, current)
            scored["rule"] = rule
            scored_rows.append(scored)
            picks = verify_next_day(select_top(scored), rows)
            picks["rule"] = rule
            picked_rows.append(picks)
        print(f"subset-selection {day}: {winner}", flush=True)
    tminus1 = pd.DataFrame(tminus1_rows)
    subsets = pd.DataFrame(subset_rows)
    chosen = pd.DataFrame(chosen_rows)
    candidate_days = pd.DataFrame(candidate_day_rows)
    scored = pd.concat(scored_rows, ignore_index=True)
    picks = pd.concat(picked_rows, ignore_index=True)
    output.mkdir(parents=True, exist_ok=True)
    tminus1.to_csv(output / "tminus1_subset_returns.csv", index=False)
    subsets.to_csv(output / "training_subsets.csv", index=False)
    chosen.to_csv(output / "chosen_subsets.csv", index=False)
    candidate_days.to_csv(output / "candidate_t_returns.csv", index=False)
    scored.to_csv(output / "all_predictions.csv.gz", index=False, compression="gzip")
    picks.to_csv(output / "top2_verified.csv", index=False)
    summary = {"definition": "19 most recent signal days ending T-2; within each day randomly sample 80% or 90% of labeled stocks, ten repeats each; choose the highest observed T-1 Top1 09:40 return; selected trained model directly scores T without refit",
               "candidate_rows": len(rows), "test_days": len(days)-20,
               "baseline20_top1_mean_pct": float(baseline.actual_0940_return.mean()*100),
               "tminus1_selection": {
                   "chosen_mean_return_pct": float(chosen.chosen_tminus1_return.mean()*100),
                   "control19_mean_return_pct": float(chosen.control19_tminus1_return.mean()*100),
                   "unknown_subset_checks": int(tminus1.status.eq("UNKNOWN").sum()),
                   "no_trade_subset_checks": int(tminus1.status.eq("NO_TRADE").sum()),
                   "winner_counts": chosen.chosen_subset.value_counts().to_dict(),
                   "winner_fractions": chosen.chosen_subset.str.extract(
                       r"random_(\d+)_")[0].value_counts().to_dict()},
               "rules": {rule: paired_summary(group, baseline) for rule, group in
                         picks.groupby("rule", sort=False)}}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                   indent=2)+"\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, default=PREPARED)
    parser.add_argument("--api", type=Path, default=BASE / "minute_api_label_audit.csv")
    parser.add_argument("--baseline", type=Path, default=BASE / "baseline_top1.csv")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    original = pd.read_csv(args.prepared, dtype={"symbol6": str})
    rows = recovered_rows(original, pd.read_csv(args.api, dtype={"symbol6": str}))
    baseline = pd.read_csv(args.baseline, dtype={"symbol6": str})
    summary = run(rows, baseline, args.output)
    summary["sources_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (args.prepared, args.api, args.baseline,
                                              Path(__file__))}
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                        indent=2)+"\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

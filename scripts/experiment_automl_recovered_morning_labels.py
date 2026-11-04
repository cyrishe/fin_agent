"""Recent-20 four-class replay with/without API-recovered morning labels."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_second_high_four_class import CLASSES, four_class
from scripts.run_automl_four_class_pipeline import (
    MARKET, PREPARED, generate_training_features, generate_training_labels,
    infer, select_inference_candidates, select_top, select_training_samples,
    summary_for_picks, train_model, verify_next_day,
)


API = Path("outputs/stock_automl/missing_morning_api/api_missing_morning_audit.csv")
OUT = Path("docs/stock_automl_runs/20261010_recovered_morning_labels")


def recovered_rows(rows: pd.DataFrame, api: pd.DataFrame) -> pd.DataFrame:
    recovered = api[api.status.eq("complete")][
        ["signal_date", "symbol6", "second_high", "close_40"]]
    result = rows.merge(recovered, on=["signal_date", "symbol6"], how="left",
                        suffixes=("", "_api"), validate="one_to_one")
    filling = result.second_high_api.notna()
    if result.loc[filling, ["second_high", "close_40"]].notna().any().any():
        raise ValueError("Recovered labels overlap an existing label")
    result["recovered_from_api"] = filling
    result["second_high"] = result.second_high.fillna(result.second_high_api)
    result["close_40"] = result.close_40.fillna(result.close_40_api)
    result = result.drop(columns=["second_high_api", "close_40_api"])
    return result


def run(original: pd.DataFrame, recovered: pd.DataFrame,
        dates: list[str], output: Path) -> dict:
    if not original[["signal_date", "symbol6"]].equals(
            recovered[["signal_date", "symbol6"]]):
        raise ValueError("The candidate pool changed")
    all_scored, all_picks, folds = [], [], []
    for ix, day in enumerate(dates[20:], start=20):
        prior = dates[ix-20:ix]
        candidates = select_inference_candidates(original, day)
        for arm, training_source in (("exclude_missing", original),
                                     ("recover_53", recovered)):
            training = select_training_samples(training_source, prior)
            if not training.next_date.le(day).all():
                raise ValueError("A training label is later than inference time")
            model = train_model(generate_training_features(training),
                                generate_training_labels(training))
            scored = infer(model, candidates)
            scored["arm"] = arm
            all_scored.append(scored)
            selected = select_top(scored)
            # The same best-observed outcome table verifies both arms.
            picks = verify_next_day(selected, recovered)
            picks["arm"] = arm
            all_picks.append(picks)
            folds.append({"test_date": day, "arm": arm, "training_rows": len(training),
                          "recovered_training_rows": int(training.recovered_from_api.sum())
                          if "recovered_from_api" in training else 0,
                          "test_candidates": len(candidates),
                          "training_dates": "|".join(prior)})
        print(f"recovered-label fold {day} complete", flush=True)
    scored = pd.concat(all_scored, ignore_index=True)
    picks = pd.concat(all_picks, ignore_index=True)
    folds = pd.DataFrame(folds)
    output.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output / "all_predictions.csv.gz", index=False, compression="gzip")
    picks.to_csv(output / "top2_verified.csv", index=False)
    folds.to_csv(output / "folds.csv", index=False)
    outcome = recovered[["signal_date", "symbol6", "signal_price", "second_high"]].rename(
        columns={"signal_price": "outcome_entry"})
    checked = scored.merge(outcome, on=["signal_date", "symbol6"], how="left",
                           validate="many_to_one")
    checked = checked[checked.second_high.notna()].copy()
    actual_return = checked.second_high / checked.outcome_entry - 1
    actual_class = pd.Series(four_class(actual_return).astype(str), index=checked.index)
    summary = {"candidate_rows": len(original),
               "original_known_labels": int(original.second_high.notna().sum()),
               "recovered_labels": int(recovered.recovered_from_api.sum()),
               "remaining_unknown_labels": int(recovered.second_high.isna().sum()),
               "test_dates": dates[20:], "arms": {}}
    for arm, group in picks.groupby("arm", sort=False):
        test = checked[checked.arm.eq(arm)]
        actual = actual_class.loc[test.index]
        summary["arms"][arm] = {
            "top1": summary_for_picks(group[group.selection_rank.eq(1)]),
            "top2": summary_for_picks(group),
            "known_test_candidates": len(test),
            "four_class_accuracy": float(accuracy_score(actual, test.predicted_class)),
            "ge3_auc": float(roc_auc_score(actual.eq("ge3"), test.p_ge3)),
            "mean_training_rows": float(folds.loc[folds.arm.eq(arm), "training_rows"].mean()),
            "mean_recovered_training_rows": float(folds.loc[
                folds.arm.eq(arm), "recovered_training_rows"].mean()),
        }
    original_top1 = picks[(picks.arm.eq("exclude_missing")) &
                          picks.selection_rank.eq(1)].set_index("signal_date")
    recovered_top1 = picks[(picks.arm.eq("recover_53")) &
                           picks.selection_rank.eq(1)].set_index("signal_date")
    delta = recovered_top1.actual_0940_return - original_top1.actual_0940_return
    summary["paired_top1"] = {
        "known_days": int(delta.notna().sum()),
        "changed_symbols": int(recovered_top1.symbol6.ne(original_top1.symbol6).sum()),
        "mean_delta_pct_points": float(delta.mean()*100),
        "better_days": int(delta.gt(0).sum()),
        "worse_days": int(delta.lt(0).sum()),
        "same_days": int(delta.eq(0).sum()),
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, default=PREPARED)
    parser.add_argument("--market", type=Path, default=MARKET)
    parser.add_argument("--api", type=Path, default=API)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    original = pd.read_csv(args.prepared, dtype={"symbol6": str})
    original["recovered_from_api"] = False
    api = pd.read_csv(args.api, dtype={"symbol6": str})
    recovered = recovered_rows(original, api)
    dates = pd.read_csv(args.market).signal_date.tolist()
    summary = run(original, recovered, dates, args.output)
    summary["sources_sha256"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (args.prepared, args.market, args.api, Path(__file__))}
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

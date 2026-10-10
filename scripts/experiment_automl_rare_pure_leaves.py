"""Exploratory rare-leaf tree: very small binary leaves validated on later dates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_automl_recovered_morning_labels import recovered_rows
from scripts.experiment_automl_second_high_four_class import four_class
from scripts.experiment_automl_validated_pure_leaves import (
    API, CLASSES, train_leaf_scores,
)
from scripts.run_automl_four_class_pipeline import (
    PREPARED, select_inference_candidates, select_top, select_training_samples,
    verify_next_day,
)


PRIOR = Path("docs/stock_automl_runs/20261010_four_class_validated_pure_leaves")
OUT = Path("docs/stock_automl_runs/20261010_four_class_rare_pure_leaves")
TREE = {"max_depth": 7, "min_samples_leaf": 10, "random_state": 42}
ARMS = ("baseline19", "rare_only", "rare_priority_all", "rare_priority_class")


def choose_rare(scored: pd.DataFrame, arm: str) -> pd.DataFrame:
    baseline = select_top(scored)
    baseline = baseline[baseline.selection_rank.eq(1)]
    if arm == "baseline19":
        return baseline.copy()
    eligible = scored[scored.rare_strict].copy()
    if arm == "rare_priority_class":
        eligible = eligible[eligible.predicted_class.isin(("ge3", "1to3"))]
    eligible["p_ge1"] = eligible.p_ge3 + eligible.p_1to3
    eligible = eligible.sort_values(["p_ge1", "p_ge3", "symbol6"],
                                    ascending=[False, False, True])
    if not eligible.empty:
        result = eligible.head(1).copy()
        result["selection_rank"] = 1
        return result
    if arm == "rare_only":
        return baseline.iloc[:0].copy()
    return baseline.copy()


def main() -> None:
    rows = recovered_rows(pd.read_csv(PREPARED, dtype={"symbol6": str}),
                          pd.read_csv(API, dtype={"symbol6": str}))
    old_scores = pd.read_csv(PRIOR / "candidate_leaf_scores.csv.gz",
                             dtype={"symbol6": str})
    old_picks = pd.read_csv(PRIOR / "top1_verified.csv", dtype={"symbol6": str})
    days = sorted(rows.signal_date.unique())
    all_scores, all_leaves, all_picks = [], [], []
    for ix in range(20, 40):
        day = days[ix]
        history = days[ix-20:ix-1]
        development = select_training_samples(rows, history[:14])
        validation = select_training_samples(rows, history[14:])
        candidate = select_inference_candidates(rows, day)
        if history[-1] != days[ix-2] or not validation.next_date.le(day).all():
            raise ValueError("Rare tree crosses the prediction date")
        leaf_score, leaves = train_leaf_scores(
            development, validation, candidate, TREE)
        leaf_score = candidate[["signal_date", "symbol6"]].join(
            leaf_score[["leaf", "train_n", "train_rate", "valid_n",
                        "valid_rate", "strict"]])
        leaf_score = leaf_score.rename(columns={column: f"rare_{column}"
                                            for column in leaf_score.columns
                                            if column not in ("signal_date", "symbol6")})
        scored = old_scores[old_scores.signal_date.eq(day)].merge(
            leaf_score, on=["signal_date", "symbol6"], validate="one_to_one")
        if len(scored) != len(candidate):
            raise ValueError("Rare tree did not score every current candidate")
        all_scores.append(scored)
        leaves["test_date"] = day
        all_leaves.append(leaves)
        for arm in ARMS:
            pick = verify_next_day(choose_rare(scored, arm), rows)
            pick["arm"] = arm
            all_picks.append(pick)
        print(f"rare leaf {day} complete", flush=True)
    scores = pd.concat(all_scores, ignore_index=True)
    picks = pd.concat(all_picks, ignore_index=True)
    leaves = pd.concat(all_leaves, ignore_index=True)
    picks["actual_0940_class"] = np.where(
        picks.actual_0940_return.notna(),
        four_class(picks.actual_0940_return).astype(str), "UNKNOWN")
    reference = old_picks[old_picks.arm.eq("baseline19")].set_index("signal_date")
    replay = picks[picks.arm.eq("baseline19")].set_index("signal_date")
    if not replay.symbol6.eq(reference.symbol6).all():
        raise ValueError("Rare tree control differs from full19 baseline")
    outcome = rows[["signal_date", "symbol6", "second_high"]]
    known = scores.merge(outcome, on=["signal_date", "symbol6"],
                         validate="one_to_one")
    known = known[known.second_high.notna()].copy()
    known["ge1_actual"] = (known.second_high / known.signal_price - 1).ge(.01)
    test_counts = scores.groupby(["test_date", "rare_leaf"]).size().rename(
        "test_candidates")
    test_labels = known.groupby(["test_date", "rare_leaf"]).agg(
        test_known=("ge1_actual", "size"), test_ge1=("ge1_actual", "sum"))
    leaves = leaves.merge(test_counts, left_on=["test_date", "leaf"],
                          right_index=True, how="left", validate="one_to_one")
    leaves = leaves.merge(test_labels, left_on=["test_date", "leaf"],
                          right_index=True, how="left", validate="one_to_one")
    for column in ("test_candidates", "test_known", "test_ge1"):
        leaves[column] = leaves[column].fillna(0).astype(int)
    OUT.mkdir(parents=True, exist_ok=True)
    scores.to_csv(OUT / "candidate_rare_leaf_scores.csv.gz", index=False,
                  compression="gzip")
    picks.to_csv(OUT / "top1_verified.csv", index=False)
    leaves.to_csv(OUT / "leaf_audit.csv", index=False)
    strict = known[known.rare_strict]
    summary = {"tree": TREE, "development_dates": 14, "validation_dates": 5,
               "strict_leaf_folds": int(leaves.strict.sum()),
               "strict_leaf_test_candidates": len(strict),
               "strict_leaf_test_ge1": int(strict.ge1_actual.sum()),
               "strict_leaf_test_ge1_fraction": float(strict.ge1_actual.mean())
               if len(strict) else None,
               "pool_ge1_fraction": float(known.ge1_actual.mean()),
               "arms": {}}
    for arm in ARMS:
        group = picks[picks.arm.eq(arm)]
        summary["arms"][arm] = {
            "trade_days": len(group),
            "second_high_counts": {c: int(group.actual_class.eq(c).sum()) for c in CLASSES},
            "sell_0940_counts": {c: int(group.actual_0940_class.eq(c).sum())
                                 for c in CLASSES},
            "same_as_baseline_days": int(group.set_index("signal_date").symbol6.eq(
                reference.symbol6).sum()),
        }
    summary["sources_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (PREPARED, API,
                                              PRIOR / "candidate_leaf_scores.csv.gz",
                                              PRIOR / "top1_verified.csv", Path(__file__))}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

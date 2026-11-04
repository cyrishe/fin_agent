"""Forward test binary positive leaves combined with the frozen four-class model."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeClassifier

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_four_class_tree_leaves import leaf_rule
from scripts.experiment_automl_recovered_morning_labels import recovered_rows
from scripts.experiment_automl_second_high_four_class import four_class
from scripts.run_automl_four_class_pipeline import (
    PREPARED, generate_training_features, generate_training_labels, infer,
    select_inference_candidates, select_top, select_training_samples,
    train_model, verify_next_day,
)


API = Path("docs/stock_automl_runs/20261010_four_class_baseline_recent20/minute_api_label_audit.csv")
PRIOR = Path("docs/stock_automl_runs/20261010_four_class_subset_selection/daily_top1_comparison.csv")
OUT = Path("docs/stock_automl_runs/20261010_four_class_validated_pure_leaves")
CLASSES = ("ge3", "1to3", "0to1", "lt0")
TREE_PARAMETERS = {"max_depth": 5, "min_samples_leaf": 40, "random_state": 42}
VALIDATION_PRIOR = 20


def positive(rows: pd.DataFrame) -> pd.Series:
    return (rows.second_high / rows.signal_price - 1).ge(.01).astype(int)


def train_leaf_scores(development: pd.DataFrame, validation: pd.DataFrame,
                      candidates: pd.DataFrame,
                      parameters: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Tree splits see early labels; leaf evidence is audited on later dates."""
    tree = DecisionTreeClassifier(**(parameters or TREE_PARAMETERS))
    tree.fit(development[list(FEATURES)], positive(development))
    built = pd.DataFrame({"leaf": tree.apply(development[list(FEATURES)]),
                          "positive": positive(development).to_numpy()})
    checked = pd.DataFrame({"leaf": tree.apply(validation[list(FEATURES)]),
                            "positive": positive(validation).to_numpy()})
    early = built.groupby("leaf").agg(train_n=("positive", "size"),
                                       train_positive=("positive", "sum"))
    later = checked.groupby("leaf").agg(valid_n=("positive", "size"),
                                         valid_positive=("positive", "sum"))
    leaves = early.join(later, how="left").fillna(0)
    leaves[["valid_n", "valid_positive"]] = leaves[
        ["valid_n", "valid_positive"]].astype(int)
    leaves["train_rate"] = leaves.train_positive / leaves.train_n
    leaves["valid_rate"] = np.where(leaves.valid_n.gt(0),
                                    leaves.valid_positive / leaves.valid_n, np.nan)
    prior_rate = float(positive(validation).mean())
    leaves["valid_smoothed"] = (
        leaves.valid_positive + VALIDATION_PRIOR * prior_rate) / (
        leaves.valid_n + VALIDATION_PRIOR)
    # Strict means >=70% on both disjoint periods. Moderate only tests whether
    # an above-base-rate pocket has even weak support in the recent period.
    leaves["strict"] = (leaves.train_rate.ge(.70) & leaves.valid_n.ge(5) &
                        leaves.valid_rate.ge(.70))
    leaves["moderate"] = (leaves.train_rate.ge(.60) & leaves.valid_n.ge(10) &
                          leaves.valid_rate.ge(.50))
    leaves["rule"] = [leaf_rule(tree, FEATURES, int(leaf)) for leaf in leaves.index]
    result = pd.DataFrame({"leaf": tree.apply(candidates[list(FEATURES)])},
                          index=candidates.index).join(
                              leaves.drop(columns="rule"), on="leaf",
                              validate="many_to_one")
    if result[["train_n", "valid_n", "valid_smoothed"]].isna().any().any():
        raise ValueError("A candidate did not map to a trained leaf")
    return result, leaves.reset_index()


def choose(scored: pd.DataFrame, arm: str) -> pd.DataFrame:
    """All ranking features are available before the current outcome."""
    baseline = select_top(scored)
    baseline = baseline[baseline.selection_rank.eq(1)]
    if arm == "baseline19":
        return baseline.copy()
    current = scored.copy()
    current["p_ge1"] = current.p_ge3 + current.p_1to3
    if arm in ("strict_only", "moderate_only", "moderate_priority_all",
               "moderate_priority_class"):
        qualifying = current.strict if arm == "strict_only" else current.moderate
        if arm == "moderate_priority_class":
            qualifying = qualifying & current.predicted_class.isin(("ge3", "1to3"))
        eligible = current[qualifying].sort_values(
            ["p_ge1", "p_ge3", "symbol6"], ascending=[False, False, True])
        if not eligible.empty:
            result = eligible.head(1).copy()
            result["selection_rank"] = 1
            return result
        if arm.endswith("only"):
            return baseline.iloc[:0].copy()
        return baseline.copy()
    if arm == "validated_blend30":
        eligible = current[current.predicted_class.isin(("ge3", "1to3"))].copy()
        if eligible.empty:
            return baseline.copy()
        eligible["class_priority"] = np.where(eligible.predicted_class.eq("ge3"), 0, 1)
        base = np.where(eligible.class_priority.eq(0), eligible.p_ge3, eligible.p_ge1)
        eligible["score"] = .7 * base + .3 * eligible.valid_smoothed
        result = eligible.sort_values(
            ["class_priority", "score", "symbol6"],
            ascending=[True, False, True]).head(1).copy()
        result["selection_rank"] = 1
        return result
    raise ValueError(f"Unknown arm: {arm}")


def run(rows: pd.DataFrame, baseline: pd.DataFrame, output: Path) -> dict:
    dates = sorted(rows.signal_date.unique())
    if len(rows) != 14783 or len(dates) != 40:
        raise ValueError("Frozen candidate pool changed")
    arms = ("baseline19", "strict_only", "moderate_only",
            "moderate_priority_all", "moderate_priority_class",
            "validated_blend30")
    scored_rows, pick_rows, leaf_rows, folds = [], [], [], []
    for ix in range(20, 40):
        day = dates[ix]
        history = dates[ix-20:ix-1]  # 19 dates, ending T−2.
        dev_dates, val_dates = history[:14], history[14:]
        if len(dev_dates) != 14 or len(val_dates) != 5 or val_dates[-1] != dates[ix-2]:
            raise ValueError("Tree development/validation chronology changed")
        full = select_training_samples(rows, history)
        development = select_training_samples(rows, dev_dates)
        validation = select_training_samples(rows, val_dates)
        if not full.next_date.le(day).all() or not validation.next_date.le(day).all():
            raise ValueError("Training label is not mature by T 14:40")
        candidate = select_inference_candidates(rows, day)
        model = train_model(generate_training_features(full), generate_training_labels(full))
        scored = infer(model, candidate)
        candidate_leaves, leaves = train_leaf_scores(development, validation, candidate)
        scored = scored.join(candidate_leaves)
        scored["p_ge1"] = scored.p_ge3 + scored.p_1to3
        scored["test_date"] = day
        scored_rows.append(scored)
        leaves["test_date"] = day
        leaf_rows.append(leaves)
        for arm in arms:
            pick = verify_next_day(choose(scored, arm), rows)
            pick["arm"] = arm
            pick_rows.append(pick)
        folds.append({"test_date": day, "full19_dates": "|".join(history),
                      "development_dates": "|".join(dev_dates),
                      "validation_dates": "|".join(val_dates),
                      "full19_rows": len(full), "development_rows": len(development),
                      "validation_rows": len(validation), "candidates": len(candidate),
                      "strict_leaves": int(leaves.strict.sum()),
                      "moderate_leaves": int(leaves.moderate.sum())})
        print(f"validated tree {day} complete", flush=True)
    scored = pd.concat(scored_rows, ignore_index=True)
    picks = pd.concat(pick_rows, ignore_index=True)
    picks["actual_0940_class"] = np.where(
        picks.actual_0940_return.notna(),
        four_class(picks.actual_0940_return).astype(str), "UNKNOWN")
    leaves = pd.concat(leaf_rows, ignore_index=True)
    folds = pd.DataFrame(folds)
    compare = picks[picks.arm.eq("baseline19")].merge(
        baseline[["signal_date", "full19_symbol6", "full19_return"]],
        on="signal_date", validate="one_to_one")
    if (len(compare) != 20 or not compare.symbol6.eq(compare.full19_symbol6).all()
            or not np.allclose(compare.actual_0940_return, compare.full19_return)):
        raise ValueError("Tree study baseline differs from frozen full19 replay")
    output.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output / "candidate_leaf_scores.csv.gz", index=False,
                  compression="gzip")
    picks.to_csv(output / "top1_verified.csv", index=False)
    folds.to_csv(output / "folds.csv", index=False)
    known = scored.merge(rows[["signal_date", "symbol6", "second_high"]],
                         on=["signal_date", "symbol6"], validate="one_to_one")
    known = known[known.second_high.notna()].copy()
    known["ge1_actual"] = (known.second_high / known.signal_price - 1).ge(.01)
    leaf_test_counts = scored.groupby(["test_date", "leaf"]).size().rename(
        "test_candidates")
    leaf_test_outcomes = known.groupby(["test_date", "leaf"]).agg(
        test_known=("ge1_actual", "size"), test_ge1=("ge1_actual", "sum"))
    leaves = leaves.merge(leaf_test_counts, on=["test_date", "leaf"], how="left",
                          validate="one_to_one")
    leaves = leaves.merge(leaf_test_outcomes, on=["test_date", "leaf"], how="left",
                          validate="one_to_one")
    for column in ("test_candidates", "test_known", "test_ge1"):
        leaves[column] = leaves[column].fillna(0).astype(int)
    leaves.to_csv(output / "leaf_audit.csv", index=False)
    summary = {"test_dates": dates[20:], "tree_config": TREE_PARAMETERS,
               "development_dates": 14, "validation_dates": 5,
               "folds": len(folds), "arms": {}, "leaf_groups": {}}
    for group_name in ("strict", "moderate"):
        group = known[known[group_name]]
        summary["leaf_groups"][group_name] = {
            "qualified_leaf_folds": int(leaves[group_name].sum()),
            "days_with_leaf": int(folds[f"{group_name}_leaves"].gt(0).sum()),
            "test_candidates": len(group),
            "test_ge1": int(group.ge1_actual.sum()),
            "test_ge1_fraction": float(group.ge1_actual.mean()) if len(group) else None,
            "all_pool_ge1_fraction": float(known.ge1_actual.mean()),
        }
    for arm in arms:
        group = picks[picks.arm.eq(arm)]
        top1 = group[group.selection_rank.eq(1)]
        counts = {c: int(top1.actual_class.eq(c).sum()) for c in CLASSES}
        summary["arms"][arm] = {
            "trade_days": len(top1), "known": int(top1.actual_class.ne("UNKNOWN").sum()),
            "counts": counts,
            "sell_0940_counts": {c: int(top1.actual_0940_class.eq(c).sum())
                                 for c in CLASSES},
            "mean_0940_return_pct": float(top1.actual_0940_return.mean()*100)
            if len(top1) else None,
            "same_as_baseline_days": int(top1.set_index("signal_date").symbol6.eq(
                compare.set_index("signal_date").symbol6).sum()),
        }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                   indent=2) + "\n")
    return summary


def main() -> None:
    rows = recovered_rows(pd.read_csv(PREPARED, dtype={"symbol6": str}),
                          pd.read_csv(API, dtype={"symbol6": str}))
    baseline = pd.read_csv(PRIOR, dtype={"full19_symbol6": str})
    summary = run(rows, baseline, OUT)
    summary["sources_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (PREPARED, API, PRIOR, Path(__file__))}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Walk-forward decision-tree leaf purity combined with the four-class model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.tree import DecisionTreeClassifier

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_four_class_intraday_factors import DRAWDOWN, EXTRA, VOLUME_TREND
from scripts.experiment_automl_second_high_four_class import CLASSES, four_class, models
from scripts.run_automl_four_class_pipeline import (
    MARKET, PREPARED, generate_training_labels, summary_for_picks, verify_next_day,
)


OUT = Path("docs/stock_automl_runs/20261010_four_class_tree_leaves")
PRIOR_STRENGTH = 100
TREE_CONFIGS = {
    "shallow7": (FEATURES, {"max_depth": 3, "min_samples_leaf": 100}),
    "small7": (FEATURES, {"max_depth": 5, "min_samples_leaf": 25}),
    "small9": ((*FEATURES, DRAWDOWN, VOLUME_TREND),
               {"max_depth": 5, "min_samples_leaf": 25}),
}
ARMS = ("baseline", "shallow7_leaf_only", "small7_leaf_only", "small7_blend25",
        "small7_blend50", "small7_purity_first", "small9_blend25", "small9_purity_first")


def leaf_rule(tree: DecisionTreeClassifier, columns: tuple[str, ...], leaf: int) -> str:
    nodes = tree.tree_

    def find(node: int, conditions: list[str]) -> list[str] | None:
        if node == leaf:
            return conditions
        if nodes.children_left[node] == nodes.children_right[node]:
            return None
        name = columns[nodes.feature[node]]
        threshold = nodes.threshold[node]
        left = find(nodes.children_left[node], [*conditions, f"{name} <= {threshold:.6g}"])
        if left is not None:
            return left
        return find(nodes.children_right[node], [*conditions, f"{name} > {threshold:.6g}"])

    return " AND ".join(find(0, []) or [])


def tree_leaf_scores(train: pd.DataFrame, test: pd.DataFrame,
                     features: tuple[str, ...], parameters: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fit binary >=3% tree; score leaf >=3% and >=1% with prior shrinkage."""
    actual = train.second_high / train.signal_price - 1
    ge3 = actual.ge(.03).astype(int)
    ge1 = actual.ge(.01).astype(int)
    tree = DecisionTreeClassifier(**parameters, random_state=42)
    tree.fit(train[list(features)], ge3)
    assigned_train = tree.apply(train[list(features)])
    stats = pd.DataFrame({"leaf": assigned_train, "ge3": ge3.to_numpy(),
                          "ge1": ge1.to_numpy()}).groupby("leaf").agg(
                              n=("ge3", "size"), ge3=("ge3", "sum"), ge1=("ge1", "sum"))
    stats["raw_ge3"] = stats.ge3 / stats.n
    stats["raw_ge1"] = stats.ge1 / stats.n
    stats["smoothed_ge3"] = (stats.ge3 + PRIOR_STRENGTH * ge3.mean()) / (
        stats.n + PRIOR_STRENGTH)
    stats["smoothed_ge1"] = (stats.ge1 + PRIOR_STRENGTH * ge1.mean()) / (
        stats.n + PRIOR_STRENGTH)
    stats["rule"] = [leaf_rule(tree, features, int(leaf)) for leaf in stats.index]
    scored = pd.DataFrame({"leaf": tree.apply(test[list(features)])}, index=test.index)
    scored = scored.join(stats.drop(columns="rule"), on="leaf", validate="many_to_one")
    return scored, stats.reset_index()


def select_with_leaf(scored: pd.DataFrame, arm: str) -> pd.DataFrame:
    """Keep the four-class >=3/1-3 gate; change only ranking within it."""
    eligible = scored[scored.predicted_class.isin(("ge3", "1to3"))].copy()
    if eligible.empty:
        eligible["selection_rank"] = pd.Series(dtype=int)
        return eligible
    ge3_class = eligible.predicted_class.eq("ge3")
    eligible["class_priority"] = np.where(ge3_class, 0, 1)
    eligible["base_score"] = np.where(ge3_class, eligible.p_ge3,
                                       eligible.p_ge3 + eligible.p_1to3)
    if arm == "baseline":
        eligible["rank_score"] = eligible.base_score
        eligible["branch_priority"] = 1
    else:
        branch = "shallow7" if arm.startswith("shallow7") else (
            "small9" if arm.startswith("small9") else "small7")
        leaf_score = np.where(ge3_class, eligible[f"{branch}_smoothed_ge3"],
                              eligible[f"{branch}_smoothed_ge1"])
        if "leaf_only" in arm:
            weight = 1.0
        elif "blend25" in arm:
            weight = .25
        elif "blend50" in arm:
            weight = .5
        else:
            weight = 0.0
        eligible["rank_score"] = (1-weight)*eligible.base_score + weight*leaf_score
        eligible["branch_priority"] = 1
        if "purity_first" in arm:
            strong = (eligible[f"{branch}_n"].ge(25) &
                      eligible[f"{branch}_raw_ge3"].ge(.35))
            eligible["branch_priority"] = np.where(strong, 0, 1)
            eligible["rank_score"] = np.where(strong,
                                               eligible[f"{branch}_smoothed_ge3"],
                                               eligible.base_score)
    eligible = eligible.sort_values(
        ["branch_priority", "class_priority", "rank_score", "symbol6"],
        ascending=[True, True, False, True])
    eligible["selection_rank"] = np.arange(1, len(eligible)+1)
    return eligible[eligible.selection_rank.le(2)].copy()


def run(rows: pd.DataFrame, extra: pd.DataFrame, dates: list[str], output: Path) -> dict:
    data = rows.merge(extra[["signal_date", "symbol6", DRAWDOWN, VOLUME_TREND]],
                      on=["signal_date", "symbol6"], how="left", validate="one_to_one")
    if len(data) != len(rows) or data[[*FEATURES, DRAWDOWN, VOLUME_TREND]].isna().any().any():
        raise ValueError("Tree experiment needs the same complete 14:40 feature pool")
    scored_rows, picks_rows, leaf_rows, fold_rows = [], [], [], []
    for ix, day in enumerate(dates[20:], start=20):
        past = dates[ix-20:ix]
        train = data[data.signal_date.isin(past) & data.second_high.notna()].copy()
        test = data[data.signal_date.eq(day)].copy()
        if not train.next_date.le(day).all():
            raise ValueError("Unmatured training label")
        model = models()["small_boosted_tree"]
        model.fit(train[list(FEATURES)], generate_training_labels(train))
        probabilities = model.predict_proba(test[list(FEATURES)])
        scored = test[["signal_date", "next_date", "symbol6", "name", "signal_price"]].copy()
        for label in CLASSES:
            scored[f"p_{label}"] = probabilities[:, list(model.classes_).index(label)]
        scored["predicted_class"] = np.asarray(model.classes_)[probabilities.argmax(axis=1)]
        for tree_name, (columns, parameters) in TREE_CONFIGS.items():
            leaf_scored, leaf_stats = tree_leaf_scores(train, test, columns, parameters)
            leaf_scored = leaf_scored.rename(columns={column: f"{tree_name}_{column}"
                                               for column in leaf_scored.columns})
            scored = scored.join(leaf_scored)
            leaf_stats["test_date"] = day
            leaf_stats["tree"] = tree_name
            test_leaf = pd.DataFrame({"leaf": leaf_scored[f"{tree_name}_leaf"].to_numpy(),
                                      "known": test.second_high.notna().to_numpy(),
                                      "ge3": (test.second_high / test.signal_price - 1).ge(.03).to_numpy(),
                                      "close_return": (test.close_40 / test.signal_price - 1).to_numpy()})
            observed = test_leaf.groupby("leaf").agg(
                test_candidates=("leaf", "size"), known=("known", "sum"),
                test_ge3=("ge3", "sum"), mean_close_return=("close_return", "mean"))
            leaf_stats = leaf_stats.merge(observed, on="leaf", how="left", validate="one_to_one")
            leaf_stats[["test_candidates", "known", "test_ge3"]] = leaf_stats[
                ["test_candidates", "known", "test_ge3"]].fillna(0).astype(int)
            leaf_rows.append(leaf_stats)
        scored_rows.append(scored)
        for arm in ARMS:
            picks = select_with_leaf(scored, arm)
            picks = verify_next_day(picks, data)
            picks["arm"] = arm
            picks_rows.append(picks)
        fold_rows.append({"test_date": day, "training_dates": "|".join(past),
                          "training_rows": len(train), "candidates": len(test)})
        print(f"tree {day}: {len(train)} train, {len(test)} candidates", flush=True)
    scored = pd.concat(scored_rows, ignore_index=True)
    picks = pd.concat(picks_rows, ignore_index=True)
    leaves = pd.concat(leaf_rows, ignore_index=True)
    output.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output / "all_candidate_leaf_scores.csv.gz", index=False, compression="gzip")
    picks.to_csv(output / "top2_verified.csv", index=False)
    leaves.to_csv(output / "leaf_audit.csv", index=False)
    pd.DataFrame(fold_rows).to_csv(output / "folds.csv", index=False)
    outcomes = data[["signal_date", "symbol6", "signal_price", "second_high"]].rename(
        columns={"signal_price": "outcome_entry"})
    checked = scored.merge(outcomes, on=["signal_date", "symbol6"],
                           how="left", validate="one_to_one")
    checked = checked[checked.second_high.notna()].copy()
    ge3_actual = (checked.second_high / checked.outcome_entry - 1).ge(.03)
    summary = {"candidate_rows": len(data), "test_dates": dates[20:],
               "known_test_candidates": len(checked), "model_gate_candidates": int(
                   scored.predicted_class.isin(("ge3", "1to3")).sum()),
               "arms": {}, "tree_quality": {}}
    for arm, group in picks.groupby("arm", sort=False):
        summary["arms"][arm] = {"top1": summary_for_picks(group[group.selection_rank.eq(1)]),
                                "top2": summary_for_picks(group)}
    baseline = picks[(picks.arm.eq("baseline")) & picks.selection_rank.eq(1)].set_index(
        "signal_date").actual_0940_return
    for arm in ARMS[1:]:
        group = picks[(picks.arm.eq(arm)) & picks.selection_rank.eq(1)].set_index("signal_date")
        difference = group.actual_0940_return - baseline
        summary["arms"][arm]["paired_top1"] = {
            "known_days": int(difference.notna().sum()),
            "mean_delta_pct_points": float(difference.mean()*100),
            "better_days": int(difference.gt(0).sum()),
            "worse_days": int(difference.lt(0).sum()),
            "same_days": int(difference.eq(0).sum()),
            "same_symbol_days": int(group.symbol6.eq(picks[(picks.arm.eq("baseline")) &
                                   picks.selection_rank.eq(1)].set_index("signal_date").symbol6).sum()),
        }
    for tree_name in TREE_CONFIGS:
        s = leaves[leaves.tree.eq(tree_name)]
        qualifying = s[s.raw_ge3.ge(.35) & s.n.ge(25)]
        c = checked[checked[f"{tree_name}_raw_ge3"].ge(.35) &
                    checked[f"{tree_name}_n"].ge(25)]
        summary["tree_quality"][tree_name] = {
            "leaf_observations_across_folds": len(s),
            "high_purity_leaf_observations": len(qualifying),
            "high_purity_training_examples": int(qualifying.n.sum()),
            "high_purity_training_ge3": int(qualifying.ge3.sum()),
            "high_purity_test_candidates": len(c),
            "high_purity_test_ge3": int((c.second_high / c.outcome_entry - 1).ge(.03).sum()),
            "high_purity_test_ge3_rate": float((c.second_high / c.outcome_entry - 1).ge(.03).mean()) if len(c) else None,
            "test_ge3_auc_smoothed": float(roc_auc_score(
                ge3_actual, checked[f"{tree_name}_smoothed_ge3"])),
        }
    summary["boosted_ge3_auc"] = float(roc_auc_score(ge3_actual, checked.p_ge3))
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, default=PREPARED)
    parser.add_argument("--market", type=Path, default=MARKET)
    parser.add_argument("--extra", type=Path, default=EXTRA)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    rows = pd.read_csv(args.prepared, dtype={"symbol6": str}, low_memory=False)
    extra = pd.read_csv(args.extra, dtype={"symbol6": str})
    dates = pd.read_csv(args.market).signal_date.tolist()
    summary = run(rows, extra, dates, args.output)
    summary["sources_sha256"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (args.prepared, args.market, args.extra, Path(__file__))}
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Compare fixed eight-factor models trained on prespecified signal-day designs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.experiment_automl_tail_three_class import COMPACT_FEATURES, read_samples
from scripts.run_automl_tail_today import fitted_model


def counts(rows: pd.DataFrame) -> dict:
    n = len(rows)
    strong = int(rows["class"].eq(1).sum())
    neutral = int(rows["class"].eq(0).sum())
    critical = int(rows["class"].eq(-1).sum())
    return {"days": int(rows.signal_date.nunique()), "selections": n,
            "strong": strong, "neutral": neutral, "critical": critical,
            "strong_rate": strong / n if n else None,
            "pass_rate": (strong + neutral) / n if n else None,
            "critical_rate": critical / n if n else None}


def evaluate(expanded: Path, sep30: Path, output: Path) -> dict:
    rows = pd.concat([read_samples(expanded), read_samples(sep30)], ignore_index=True)
    if rows.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate stock-day")
    dates = sorted(rows.signal_date.unique())
    training_pool = [d for d in dates if pd.Timestamp("2026-08-25") <= d <= pd.Timestamp("2026-09-21")]
    common_test = [d for d in dates if d not in training_pool]
    if len(training_pool) != 20 or len(common_test) != 17:
        raise ValueError("Expected 20 training-design days and 17 common test days")
    size = rows.groupby("signal_date").size().loc[training_pool]
    # Label-free 14:40 breadth proxy: number of eligible 3%-6% candidates.
    # Take five dates from each tercile, spaced through each ordered tercile.
    ordered = list(size.sort_values(kind="stable").index)
    breadth_days = sorted(group[i] for group in (ordered[:7], ordered[7:13], ordered[13:])
                          for i in ([0, 1, 3, 5, 6] if len(group) == 7 else [0, 1, 2, 4, 5]))
    plans = {"全部20日": training_pool, "前15日": training_pool[:15],
             "后15日": training_pool[-15:], "市场宽度分层15日": breadth_days}
    if len(plans["市场宽度分层15日"]) != 15:
        raise ValueError("Breadth selection is not 15 days")
    reports = []
    details = []
    common = rows[rows.signal_date.isin(common_test)]
    common_baseline = counts(common)
    for name, train_dates in plans.items():
        training = rows[rows.signal_date.isin(train_dates)]
        testing = rows[~rows.signal_date.isin(train_dates)].copy()
        if set(train_dates) & set(testing.signal_date.unique()):
            raise ValueError("Training and testing dates overlap")
        model = fitted_model(training)
        testing["score"] = model.predict_proba(testing[list(COMPACT_FEATURES)])[:, 1]
        testing = testing.sort_values(["signal_date", "score", "symbol6"],
                                      ascending=[True, False, True]).copy()
        testing["rank"] = testing.groupby("signal_date").cumcount() + 1
        testing["experiment"] = name
        testing["test_group"] = testing.signal_date.isin(common_test).map(
            {True: "共同17日", False: "训练池余下日期"})
        details.append(testing[testing["rank"].le(5)][
            ["experiment", "test_group", "signal_date", "symbol6", "name", "rank",
             "score", "class", "target_next_high10_return"]])
        test_common = testing[testing.signal_date.isin(common_test)]
        per_day = {day: {"top2": counts(group[group["rank"].le(2)]),
                         "top5": counts(group)}
                   for day, group in testing[testing["rank"].le(5)].groupby("signal_date")}
        reports.append({"experiment": name,
                        "training_dates": [d.date().isoformat() for d in train_dates],
                        "training_rows": len(training),
                        "training_day_candidate_counts": {d.date().isoformat(): int(size.loc[d])
                                                          for d in train_dates},
                        "common_17_days": {"top2": counts(test_common[test_common["rank"].le(2)]),
                                           "top5": counts(test_common[test_common["rank"].le(5)])},
                        "all_nontraining_days": {
                            "top2": counts(testing[testing["rank"].le(2)]),
                            "top5": counts(testing[testing["rank"].le(5)])},
                        "daily": {d.date().isoformat(): v for d, v in per_day.items()}})
    output.mkdir(parents=True, exist_ok=True)
    pd.concat(details, ignore_index=True).to_csv(output / "selected_top5.csv", index=False)
    result = {"label": "T+1 09:31-09:40 high / T 14:50 proxy - 1; >1%=strong, <0.5%=critical",
              "model": "eight-factor logistic regression C=0.2; class 0 or 1 is pass",
              "common_test_dates": [d.date().isoformat() for d in common_test],
              "common_test_candidate_baseline": common_baseline,
              "training_pool_candidate_counts": {d.date().isoformat(): int(n)
                                                 for d, n in size.items()},
              "experiments": reports,
              "input_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in (expanded, sep30)}}
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expanded", type=Path, default=Path(
        "outputs/stock_automl/tail_expanded_august/trainable_1440.csv"))
    parser.add_argument("--sep30", type=Path, default=Path(
        "outputs/stock_automl/tail_sep30_check/trainable_1440.csv"))
    parser.add_argument("--output", type=Path, default=Path(
        "outputs/stock_automl/training_day_designs"))
    args = parser.parse_args()
    result = evaluate(args.expanded, args.sep30, args.output)
    for item in result["experiments"]:
        print(item["experiment"], item["training_rows"], item["common_17_days"])

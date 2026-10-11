"""Append next-morning labels and 09:40 price outcomes to a saved 14:40 decision."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from scripts.run_automl_four_class_pipeline import summary_for_picks, verify_next_day


def run(decision_path: Path, prepared_path: Path, output: Path) -> dict:
    decision = json.loads(decision_path.read_text())
    day = decision["signal_date"]
    rows = pd.read_csv(prepared_path, dtype={"symbol6": str}, low_memory=False)
    if day not in set(rows.signal_date):
        raise ValueError("The signal date is absent from refreshed historical candidates")
    groups = []
    for method, stocks in decision["selections"].items():
        for stock in stocks:
            groups.append({"signal_date": day, "method": method, **stock})
    picks = pd.DataFrame(groups)
    if picks.empty:
        summary = {"signal_date": day, "selected": 0, "message": "No stock was selected"}
    else:
        if picks.duplicated(["method", "symbol6"]).any():
            raise ValueError("Duplicate selected stock")
        result = verify_next_day(picks, rows)
        output.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(output, index=False)
        summary = {"signal_date": day, "selected": len(result),
                   "methods": {method: {
                       "top1": summary_for_picks(group[group.selection_rank.eq(1)]),
                       "top2": summary_for_picks(group)}
                       for method, group in result.groupby("method")}}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.decision, arguments.prepared,
                         arguments.output), ensure_ascii=False, indent=2))

"""Count actual four-class outcomes of the existing 80/90% model selections."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_automl_four_class_subset_selection import fit_tree, replay_top1
from scripts.experiment_automl_recovered_morning_labels import recovered_rows
from scripts.experiment_automl_second_high_four_class import four_class
from scripts.run_automl_four_class_pipeline import (
    PREPARED, infer, select_inference_candidates, select_training_samples,
)


BASE = Path("docs/stock_automl_runs/20261010_four_class_baseline_recent20")
PRIOR = Path("docs/stock_automl_runs/20261010_four_class_subset_selection")
OUT = Path("docs/stock_automl_runs/20261010_four_class_tminus1_diagnostics")
CLASS_ORDER = ("ge3", "1to3", "0to1", "lt0")


def labeled_picks(events: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    result = events.merge(labels, on=["signal_date", "symbol6"], how="left",
                          validate="many_to_one")
    ratio = result.second_high / result.signal_price - 1
    result["actual_class"] = np.where(result.second_high.notna(),
                                      four_class(ratio).astype(str), "UNKNOWN")
    if len(result) != len(events) or result.actual_class.eq("UNKNOWN").any():
        raise ValueError("Selected Top1 lacks an observable four-class label")
    return result


def main() -> None:
    original = pd.read_csv(PREPARED, dtype={"symbol6": str})
    rows = recovered_rows(original, pd.read_csv(BASE / "minute_api_label_audit.csv",
                                                dtype={"symbol6": str}))
    labels = rows[["signal_date", "symbol6", "signal_price", "second_high"]]
    diagnostics = pd.read_csv(OUT / "model_diagnostics.csv.gz",
                              dtype={"tminus1_symbol6": str})
    t_results = pd.read_csv(PRIOR / "candidate_t_returns.csv",
                            dtype={"t_symbol6": str})
    previous = pd.read_csv(PRIOR / "daily_top1_comparison.csv",
                           dtype={"selected_symbol6": str, "full19_symbol6": str,
                                  "baseline20_symbol6": str})
    controls = pd.read_csv(PRIOR / "chosen_subsets.csv")

    eligible = diagnostics[diagnostics.top1_p_ge3.notna()].copy()
    by_fraction = eligible.sort_values(
        ["test_date", "fraction", "top1_p_ge3", "repeat"],
        ascending=[True, True, False, True]).drop_duplicates(
            ["test_date", "fraction"])
    by_fraction["strategy"] = by_fraction.fraction.map(
        {.8: "80%组内最高P3", .9: "90%组内最高P3"})
    overall = eligible.sort_values(
        ["test_date", "top1_p_ge3", "fraction", "repeat"],
        ascending=[True, False, False, True]).drop_duplicates("test_date")
    overall["strategy"] = "20模型最高P3"
    chosen = pd.concat([by_fraction, overall], ignore_index=True)
    if len(chosen) != 60 or chosen.groupby("strategy").size().ne(20).any():
        raise ValueError("Expected one selected model per strategy and date")

    tminus1 = chosen[["test_date", "tminus1_date", "subset", "strategy",
                      "tminus1_symbol6", "top1_p_ge3"]].rename(columns={
                          "tminus1_date": "signal_date",
                          "tminus1_symbol6": "symbol6"})
    tminus1["stage"] = "T−1"
    t = chosen[["test_date", "subset", "strategy", "top1_p_ge3"]].merge(
        t_results[["test_date", "subset", "t_symbol6"]],
        on=["test_date", "subset"], validate="many_to_one")
    t = t.rename(columns={"test_date": "signal_date", "t_symbol6": "symbol6"})
    t["stage"] = "T"
    t["test_date"] = t.signal_date
    t["top1_p_ge3"] = np.nan  # This is a prior-day model-selection score.

    controls_t = []
    for strategy, symbol_column, class_column in (
        ("全量19日模型", "full19_symbol6", "full19_actual_class"),
        ("滚动20日基准", "baseline20_symbol6", "baseline20_actual_class"),
        ("按T−1已实现收益选模型", "selected_symbol6", "selected_actual_class"),
    ):
        frame = previous[["signal_date", symbol_column, class_column]].rename(
            columns={symbol_column: "symbol6", class_column: "saved_class"})
        frame["strategy"] = strategy
        frame["stage"] = "T"
        frame["test_date"] = frame.signal_date
        controls_t.append(frame)
    controls_t = pd.concat(controls_t, ignore_index=True)

    full19_tminus1 = []
    days = sorted(rows.signal_date.unique())
    for index in range(20, len(days)):
        test_date, signal_date = days[index], days[index - 1]
        historical = days[index - 20:index - 1]
        training = select_training_samples(rows, historical)
        model = fit_tree(training, signal_date)
        actual = replay_top1(infer(model, select_inference_candidates(
            rows, signal_date)), rows, signal_date)
        expected = controls.loc[controls.test_date.eq(test_date),
                                "control19_tminus1_return"].iloc[0]
        if actual["status"] != "OBSERVED" or not np.isclose(
                actual["return_0940"], expected, atol=1e-12):
            raise ValueError(f"Full19 T−1 replay differs on {test_date}")
        full19_tminus1.append({"test_date": test_date, "signal_date": signal_date,
                              "symbol6": actual["symbol6"], "strategy": "全量19日模型",
                              "stage": "T−1"})

    events = pd.concat([tminus1, t, controls_t,
                        pd.DataFrame(full19_tminus1)], ignore_index=True)
    result = labeled_picks(events, labels)
    saved = result[result.saved_class.notna()]
    if not saved.saved_class.eq(saved.actual_class).all():
        raise ValueError("Saved T classes differ from frozen label source")
    counts = (result.groupby(["stage", "strategy", "actual_class"]).size()
              .unstack(fill_value=0).reindex(columns=CLASS_ORDER, fill_value=0))
    counts["total"] = counts.sum(axis=1)
    if counts.total.ne(20).any():
        raise ValueError("Each strategy and stage must have 20 picks")
    OUT.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT / "selected_four_class_details.csv", index=False)
    counts.to_csv(OUT / "selected_four_class_counts.csv")
    print(counts.to_string())


if __name__ == "__main__":
    main()

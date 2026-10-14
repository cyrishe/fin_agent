"""Offline rolling experiment for minute-low-above-VWAP share on the same candidate cohorts."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import FEATURES, db_connection
from scripts.experiment_automl_intraday_vwap import (
    LATE_MARKET, OUT, db_day, load_cohorts, mk_day,
)
from scripts.experiment_automl_second_high_four_class import four_class, models
from scripts.run_automl_four_class_pipeline import select_top


MONTHS = {"mk": ("2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"),
          "db": ("2026-09",)}


def required_days(calendar: list[str], months: tuple[str, ...]) -> list[str]:
    indices = [i for i, day in enumerate(calendar) if day[:7] in months]
    if not indices or min(indices) < 20:
        raise ValueError(f"No mature rolling window for {months}")
    return sorted({day for i in indices for day in calendar[i-20:i+1]})


def extract(source: str, output: Path = OUT) -> None:
    early, late = load_cohorts()
    cohort = early if source == "mk" else late
    folder = output / "low_factor_days"
    folder.mkdir(parents=True, exist_ok=True)
    calendar = (sorted(cohort.signal_date.unique()) if source == "mk" else
                pd.read_csv(LATE_MARKET).trade_date.astype(str).tolist())
    days = [day for day in required_days(calendar, MONTHS[source])
            if day in set(cohort.signal_date)]
    if source == "db":
        with db_connection(Path(".env")) as db:
            for day in days:
                codes = sorted(cohort.loc[cohort.signal_date.eq(day), "symbol6"].unique())
                path = folder / f"{day}.csv"
                if path.exists():
                    continue
                pieces = [db_day(db, day, codes[i:i+1000])
                          for i in range(0, len(codes), 1000)]
                frame = pd.concat(pieces, ignore_index=True)
                if set(frame.symbol6) != set(codes):
                    raise ValueError(f"Low VWAP DB coverage incomplete: {day}")
                frame.to_csv(path, index=False)
                print("low-factor", source, day, len(frame), flush=True)
    else:
        for day in days:
            path = folder / f"{day}.csv"
            if path.exists():
                continue
            codes = sorted(cohort.loc[cohort.signal_date.eq(day), "symbol6"].unique())
            frame = mk_day(day, codes)
            if set(frame.symbol6) != set(codes):
                raise ValueError(f"Low VWAP archive coverage incomplete: {day}")
            frame.to_csv(path, index=False)
            print("low-factor", source, day, len(frame), flush=True)


def replay_segment(cohort: pd.DataFrame, calendar: list[str], segment: str,
                   months: tuple[str, ...]) -> pd.DataFrame:
    picks = []
    labeled = set(cohort.loc[cohort.second_high.notna(), "signal_date"])
    features = (*FEATURES, "low_vwap_level")
    for i in range(20, len(calendar)):
        day = calendar[i]
        if day[:7] not in months or (segment == "feb_jul" and day == "2026-07-31"):
            continue
        previous = calendar[i-20:i]
        if not set(previous).issubset(labeled):
            continue
        train = cohort[cohort.signal_date.isin(previous) & cohort.second_high.notna() &
                       cohort.next_date.le(day)]
        current = cohort[cohort.signal_date.eq(day)]
        if current.empty:
            continue
        if train.signal_date.nunique() != 20:
            raise ValueError(f"Immature training labels: {day}")
        if train.low_vwap_level.isna().any() or current.low_vwap_level.isna().any():
            raise ValueError(f"Low VWAP factor incomplete in rolling window: {day}")
        labels = four_class(train.second_high / train.signal_price - 1).astype(str)
        model = models()["small_boosted_tree"]
        model.fit(train[list(features)], labels)
        probabilities = model.predict_proba(current[list(features)])
        scored = current[["signal_date", "next_date", "symbol6", "name", "signal_price",
                          "low_above_ratio", "low_vwap_level"]].copy()
        for label in ("lt0", "0to1", "1to3", "ge3"):
            scored[f"p_{label}"] = probabilities[:, list(model.classes_).index(label)]
        scored["predicted_class"] = np.asarray(model.classes_)[probabilities.argmax(axis=1)]
        top = select_top(scored)
        top = top[top.selection_rank.eq(1)].copy()
        top = top.merge(current[["signal_date", "symbol6", "second_high", "close_40"]],
                        on=["signal_date", "symbol6"], validate="one_to_one")
        top["actual_second_high_return"] = top.second_high / top.signal_price - 1
        top["actual_0940_return"] = top.close_40 / top.signal_price - 1
        top["actual_class"] = np.where(top.second_high.notna(),
                                        four_class(top.actual_second_high_return).astype(str),
                                        "UNKNOWN")
        top["segment"] = segment
        top["arm"] = "low_vwap8"
        picks.append(top)
        print("low-model", segment, day, "train", len(train), "candidates", len(current), flush=True)
    return pd.concat(picks, ignore_index=True)


def run(segment: str, output: Path = OUT) -> None:
    early, late = load_cohorts()
    files = sorted((output / "low_factor_days").glob("2026-*.csv"))
    if not files:
        raise ValueError("No all-candidate low VWAP data")
    factor = pd.concat([pd.read_csv(path, dtype={"signal_date": str, "symbol6": str})
                        for path in files], ignore_index=True)
    if factor.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate low VWAP factor")
    cohort, source = (early, "mk") if segment == "feb_jul" else (late, "db")
    calendar = (sorted(early.signal_date.unique()) if source == "mk" else
                pd.read_csv(LATE_MARKET).trade_date.astype(str).tolist())
    needed = required_days(calendar, MONTHS[source])
    combined = cohort[cohort.signal_date.isin(needed)].copy()
    combined["segment"] = segment
    combined = combined.merge(factor[["signal_date", "symbol6", "low_above_minutes",
                                      "low_above_ratio", "low_vwap_level"]],
                              on=["signal_date", "symbol6"], how="left", validate="one_to_one")
    coverage = combined.groupby("segment").agg(candidates=("symbol6", "size"),
                                                factor_rows=("low_vwap_level", "count"))
    coverage.to_csv(output / f"low_vwap_coverage_{segment}.csv")
    if combined.low_vwap_level.isna().any():
        raise ValueError("Low VWAP factor missing on candidate")
    combined.low_vwap_level = combined.low_vwap_level.astype(int)
    result = replay_segment(combined, calendar, segment, MONTHS[source])
    result.to_csv(output / f"low_vwap_top1_{segment}.csv", index=False)
    parts = [pd.read_csv(path, dtype={"symbol6": str, "signal_date": str})
             for path in (output / "low_vwap_top1_feb_jul.csv",
                          output / "low_vwap_top1_sep_oct.csv") if path.exists()]
    result = pd.concat(parts, ignore_index=True)
    result.to_csv(output / "low_vwap_top1.csv", index=False)
    baseline = pd.read_csv(output / "paired_top1.csv", dtype={"symbol6": str,
                                                               "signal_date": str})
    available_months = tuple(result.signal_date.str[:7].unique())
    baseline = baseline[baseline.arm.eq("baseline7") &
                        baseline.signal_date.str[:7].isin(available_months)]
    rows = []
    for frame, name in ((baseline, "baseline7"), (result, "low_vwap8")):
        for segment, group in frame.groupby("segment"):
            rows.append({"segment": segment, "arm": name, "picks": len(group),
                         "target_ge3": int(group.actual_second_high_return.ge(.03).sum()),
                         "target_lt0": int(group.actual_second_high_return.lt(0).sum()),
                         "sale_ge3": int(group.actual_0940_return.ge(.03).sum()),
                         "sale_negative": int(group.actual_0940_return.lt(0).sum()),
                         "mean_sale_pct": float(group.actual_0940_return.mean()*100)})
    summary = pd.DataFrame(rows)
    summary.to_csv(output / "low_vwap_model_summary.csv", index=False)
    scripts = [Path(__file__), Path("scripts/experiment_automl_intraday_vwap.py"),
               Path("scripts/backtest_automl_low_vwap_gate.py")]
    manifest = {"git_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip(),
        "prediction_months": [*MONTHS["mk"], *MONTHS["db"]],
        "model_parameters": models()["small_boosted_tree"].get_params(),
        "factor_formula": "fraction of 220 minute lows strictly above same-minute cumulative VWAP",
        "level_thresholds": {"0": "<85%", "1": "85%-95%", "2": ">95%"},
        "code_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in scripts},
        "factor_days_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in files}}
    (output / "low_vwap_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    print(summary.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("extract-mk", "extract-db", "run-sep", "run-early"))
    args = parser.parse_args()
    if args.command.startswith("run"):
        run("sep_oct" if args.command == "run-sep" else "feb_jul")
    else:
        extract(args.command.split("-")[1])


if __name__ == "__main__":
    main()

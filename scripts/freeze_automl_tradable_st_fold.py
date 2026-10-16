"""Freeze and verify an exact historical 20-day tradable-ST model fold.

This archives one fitted model from the rolling *backtest*. It is not a live
model for dates after its recorded signal date.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_four_class_subset_selection import fit_tree
from scripts.experiment_automl_mk_1m_rolling import evaluate
from scripts.experiment_automl_second_high_four_class import four_class
from scripts.run_automl_four_class_pipeline import select_training_samples


SOURCE = Path("outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz")
REFERENCE = Path("docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top2_verified.csv")
EXPECTED_SOURCE_SHA256 = "d160e906712f8d9f0d7b1f052079c023fd2e8d7def5611fd71c31c738e4b90f2"
TRAIN_COLUMNS = ("signal_date", "next_date", "symbol6", "signal_price", "second_high", *FEATURES)
SOURCE_SCRIPTS = (
    "scripts/build_automl_mk_1m_candidates.py",
    "scripts/build_automl_tail_standard.py",
    "scripts/prepare_automl_mk_1m_tradable_st.py",
    "scripts/audit_automl_mk_1m_entry_limit.py",
    "scripts/experiment_automl_second_high_four_class.py",
    "scripts/experiment_automl_four_class_subset_selection.py",
    "scripts/run_automl_four_class_pipeline.py",
    "scripts/experiment_automl_mk_1m_rolling20.py",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def training_slice(rows: pd.DataFrame, signal_date: str) -> tuple[pd.DataFrame, list[str]]:
    dates = sorted(rows.signal_date.unique())
    if signal_date not in dates or dates.index(signal_date) < 20:
        raise ValueError("Signal date needs 20 preceding dates in the frozen cohort")
    index = dates.index(signal_date)
    training_dates = dates[index - 20:index]
    training = select_training_samples(rows, training_dates)
    if training.signal_date.nunique() != 20 or not training.next_date.le(signal_date).all():
        raise ValueError("Incomplete or immature training labels")
    if training[list(FEATURES)].isna().any().any():
        raise ValueError("Incomplete training features")
    return training, training_dates


def check_saved_picks(model, rows: pd.DataFrame, signal_date: str) -> dict:
    _, picks = evaluate(model, rows, signal_date, "rolling20_tminus1")
    saved = pd.read_csv(REFERENCE, dtype={"signal_date": str, "symbol6": str})
    expected = saved[saved.signal_date.eq(signal_date)].sort_values("selection_rank")
    actual = picks.sort_values("selection_rank")
    if (actual.symbol6.tolist() != expected.symbol6.tolist() or
            actual.predicted_class.tolist() != expected.predicted_class.tolist()):
        raise ValueError("Frozen model selections differ from archived backtest")
    for column in ("p_lt0", "p_0to1", "p_1to3", "p_ge3"):
        if not np.allclose(actual[column], expected[column], rtol=0, atol=1e-12):
            raise ValueError(f"Frozen model {column} differs from archived backtest")
    return {"candidate_rows": int(rows.signal_date.eq(signal_date).sum()),
            "selected_symbols": actual.symbol6.tolist()}


def freeze(signal_date: str, output: Path, source: Path = SOURCE) -> dict:
    if digest(source) != EXPECTED_SOURCE_SHA256:
        raise ValueError("Frozen candidate cohort SHA-256 mismatch")
    rows = pd.read_csv(source, dtype={"symbol6": str, "signal_date": str,
                                          "next_date": str}, low_memory=False)
    training, dates = training_slice(rows, signal_date)
    output.mkdir(parents=True, exist_ok=True)
    training_file = output / "training_rows.csv.gz"
    model_file = output / "model.joblib"
    training[list(TRAIN_COLUMNS)].to_csv(training_file, index=False,
                                         compression="gzip", float_format="%.17g")
    model = fit_tree(training, signal_date)
    checks = check_saved_picks(model, rows, signal_date)
    joblib.dump(model, model_file, compress=3)
    manifest = {
        "kind": "historical_rolling20_backtest_fold", "signal_date": signal_date,
        "training_dates": dates, "training_rows": len(training),
        "training_label_counts": four_class(training.second_high / training.signal_price - 1)
            .value_counts().to_dict(),
        "features_ordered": list(FEATURES), "target": "four_class(second_high_0931_0940 / signal_price_1440 - 1)",
        "source_file": str(source), "source_sha256": digest(source),
        "training_file": training_file.name, "training_sha256": digest(training_file),
        "model_file": model_file.name, "model_sha256": digest(model_file),
        "reference_file": str(REFERENCE), "reference_sha256": digest(REFERENCE),
        "source_scripts_sha256": {name: digest(Path(name)) for name in SOURCE_SCRIPTS},
        "estimator": type(model).__name__, "model_params": model.get_params(deep=False),
        "sklearn_version": sklearn.__version__, **checks,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False,
                                                       indent=2) + "\n")
    return manifest


def verify(output: Path, source: Path = SOURCE) -> dict:
    manifest = json.loads((output / "manifest.json").read_text())
    for key, path in (("source_sha256", source),
                      ("training_sha256", output / manifest["training_file"]),
                      ("model_sha256", output / manifest["model_file"]),
                      ("reference_sha256", REFERENCE)):
        if digest(path) != manifest[key]:
            raise ValueError(f"{key} mismatch")
    for name, expected_sha in manifest["source_scripts_sha256"].items():
        if digest(Path(name)) != expected_sha:
            raise ValueError(f"Source script changed: {name}")
    rows = pd.read_csv(source, dtype={"symbol6": str, "signal_date": str,
                                          "next_date": str}, low_memory=False)
    training, dates = training_slice(rows, manifest["signal_date"])
    saved_training = pd.read_csv(output / manifest["training_file"],
                                 dtype={"symbol6": str, "signal_date": str,
                                        "next_date": str}, float_precision="round_trip")
    if (dates != manifest["training_dates"] or len(training) != manifest["training_rows"] or
            not saved_training.symbol6.equals(training.symbol6.reset_index(drop=True)) or
            not np.allclose(saved_training[list(FEATURES)],
                            training[list(FEATURES)], rtol=0, atol=1e-12) or
            not np.allclose(saved_training.second_high, training.second_high, rtol=0, atol=1e-12)):
        raise ValueError("Saved training rows differ from the frozen candidate cohort")
    model = joblib.load(output / manifest["model_file"])
    refitted = fit_tree(saved_training, manifest["signal_date"])
    current = rows[rows.signal_date.eq(manifest["signal_date"])]
    if not np.allclose(model.predict_proba(current[list(FEATURES)]),
                       refitted.predict_proba(current[list(FEATURES)]),
                       rtol=0, atol=1e-12):
        raise ValueError("Saved model cannot be reproduced from saved training rows")
    checks = check_saved_picks(model, rows, manifest["signal_date"])
    if checks != {key: manifest[key] for key in checks}:
        raise ValueError("Saved selection metadata differs")
    return {"signal_date": manifest["signal_date"], "training_rows": len(training),
            "model_sha256": manifest["model_sha256"], **checks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify(args.output) if args.verify else freeze(args.signal_date, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

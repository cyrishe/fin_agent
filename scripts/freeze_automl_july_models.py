"""Reconstruct and verify model 3; freeze it and the new exact-trained 7-factor fit."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import pandas as pd
import sklearn

from scripts.evaluate_automl_sina_15m_july import fitted
from scripts.experiment_automl_tail_three_class import COMPACT_FEATURES, read_samples


def freeze(expanded: Path, sep30: Path, archived: Path, output: Path) -> dict:
    all_rows = pd.concat([read_samples(expanded), read_samples(sep30)], ignore_index=True)
    training = all_rows[all_rows.signal_date.between("2026-08-25", "2026-09-21")].copy()
    if len(training) != 7488 or training.signal_date.nunique() != 20:
        raise ValueError("Model 3 training window changed")
    old = fitted(training, tuple(COMPACT_FEATURES))
    test = all_rows[~all_rows.signal_date.isin(training.signal_date.unique())].copy()
    test["score"] = old.predict_proba(test[list(COMPACT_FEATURES)])[:, 1]
    test = test.sort_values(["signal_date", "score", "symbol6"],
                            ascending=[True, False, True])
    test["rank"] = test.groupby("signal_date").cumcount() + 1
    current = test[test["rank"].le(2)][["signal_date", "symbol6", "rank", "score"]]
    prior = pd.read_csv(archived, dtype={"symbol6": str}, parse_dates=["signal_date"])
    prior = prior[prior.model.eq(3)][["signal_date", "symbol6", "rank", "score"]]
    joined = current.merge(prior, on=["signal_date", "symbol6", "rank"],
                           suffixes=("_now", "_archived"))
    matched = len(joined)
    if matched != 34 or len(current) != 34 or len(prior) != 34:
        raise ValueError("Reconstructed old model does not reproduce all 34 archived picks")
    max_score_diff = float((joined.score_now - joined.score_archived).abs().max())
    if max_score_diff > 1e-12:
        raise ValueError("Reconstructed old model scores differ from archive")
    seven_features = tuple(f for f in COMPACT_FEATURES if f != "price_above_all_ma")
    seven = fitted(training, seven_features)
    output.mkdir(parents=True, exist_ok=True)
    old_path = output / "frozen_model3_binary_pass.joblib"
    seven_path = output / "exact_trained_seven_factor.joblib"
    for model, path in ((old, old_path), (seven, seven_path)):
        temp = path.with_suffix(".joblib.tmp")
        joblib.dump(model, temp)
        if path.exists() and hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(temp.read_bytes()).digest():
            temp.unlink()
            raise ValueError(f"Existing frozen artifact differs: {path}")
        temp.replace(path)
    manifest = {"training_start": "2026-08-25", "training_end": "2026-09-21",
                "training_days": 20, "training_rows": len(training),
                "old_model_reproduction": f"{matched}/34 archived top-two stock-date-rank rows",
                "max_archived_score_difference": max_score_diff,
                "seven_features": seven_features, "sklearn_version": sklearn.__version__,
                "artifacts_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in (old_path, seven_path)},
                "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (expanded, sep30, archived)}}
    (output / "frozen_models_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--expanded", type=Path, default=Path(
        "outputs/stock_automl/tail_expanded_august/trainable_1440.csv"))
    p.add_argument("--sep30", type=Path, default=Path(
        "outputs/stock_automl/tail_sep30_check/trainable_1440.csv"))
    p.add_argument("--archived", type=Path, default=Path(
        "outputs/stock_automl/tail_expanded_august/three_frozen_folds_top2.csv"))
    p.add_argument("--output", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_sina_15m_july"))
    a = p.parse_args()
    freeze(a.expanded, a.sep30, a.archived, a.output)

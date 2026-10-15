"""Fit a comparison runtime from the exact saved 14:40 training rows."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
import sklearn

from scripts.run_automl_1440_strategy import FEATURES, ROOT, SHANGHAI
from scripts.run_automl_four_class_pipeline import (
    generate_training_features, generate_training_labels, train_model,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--source-cache", type=Path, required=True)
    parser.add_argument("--target-cache", type=Path, required=True)
    args = parser.parse_args()
    day = args.date
    source = args.source_cache
    target = args.target_cache
    if source.resolve() == target.resolve():
        parser.error("source and target caches must differ")
    source_meta = json.loads((source / f"{day}_model.json").read_text())
    training_file = source / f"{day}_training.csv.gz"
    if (source_meta["signal_date"] != day or
            source_meta["feature_names"] != list(FEATURES) or
            source_meta["pipeline_sha256"] != sha256(ROOT / "scripts/run_automl_1440_strategy.py") or
            source_meta["training_sha256"] != sha256(training_file)):
        raise ValueError("Source training cache failed identity check")
    training = pd.read_csv(training_file, dtype={"symbol6": str},
                           float_precision="round_trip")
    if len(training) != source_meta["training_rows"]:
        raise ValueError("Source training row count changed")
    model = train_model(generate_training_features(training),
                        generate_training_labels(training))
    target.mkdir(parents=True, exist_ok=True)
    target_training = target / training_file.name
    shutil.copy2(training_file, target_training)
    for amount_file in source.glob("amounts_*.csv.gz"):
        shutil.copy2(amount_file, target / amount_file.name)
    target_model = target / f"{day}_model.joblib"
    joblib.dump(model, target_model)
    meta = {**source_meta, "sklearn_version": sklearn.__version__,
            "model_sha256": sha256(target_model),
            "prepared_at": datetime.now(SHANGHAI).isoformat()}
    (target / f"{day}_model.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"signal_date": day, "sklearn_version": sklearn.__version__,
                      "training_rows": len(training),
                      "training_sha256": meta["training_sha256"],
                      "model_sha256": meta["model_sha256"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

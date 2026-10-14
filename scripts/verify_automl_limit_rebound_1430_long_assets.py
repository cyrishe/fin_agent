"""Inventory the C1430-B models and training rows saved by the training run."""
from __future__ import annotations

import hashlib
import json

import joblib
import pandas as pd
import sklearn

from scripts.experiment_automl_limit_rebound_1430_long import OUT


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> dict:
    summary = json.loads((OUT / "summary.json").read_text())
    features = list(summary["models"]["full35"]["features"])
    assets = {}
    for name, expected in (("train", summary["splits"]["train"]),
                           ("refit", summary["splits"]["refit"])):
        sample = OUT / f"full35_{name}_training.csv.gz"
        fitted = OUT / f"full35_{name}_model.joblib"
        rows = pd.read_csv(sample, dtype={"symbol": str})
        model = joblib.load(fitted)
        if len(rows) != expected["rows"] or int(rows.label_next3.sum()) != expected["positive"]:
            raise ValueError(f"Training row mismatch: {name}")
        if list(model.feature_names_in_) != features:
            raise ValueError(f"Feature order mismatch: {name}")
        assets[name] = {"rows": len(rows), "positive": int(rows.label_next3.sum()),
                        "start": rows.d1_date.min(), "end": rows.d1_date.max(),
                        "training_file": sample.name, "training_sha256": sha(sample),
                        "model_file": fitted.name, "model_sha256": sha(fitted)}
    result = {"experiment": "C1430-B-LONG", "sklearn": sklearn.__version__,
              "features": features, "label": "label_next3",
              "panel_sha256": sha(OUT / "panel.csv.gz"), "assets": assets,
              "score_files": {name: sha(OUT / f"full35_{name}.csv.gz")
                              for name in ("train", "valid", "test")}}
    if result["panel_sha256"] != summary["source"]["panel_sha256"]:
        raise ValueError("Panel SHA mismatch")
    (OUT / "training_asset_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


if __name__ == "__main__":
    main()

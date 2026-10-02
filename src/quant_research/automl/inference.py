"""Score a new local market panel with a model produced by this runner."""
from pathlib import Path
import json

import joblib

from .config import ResearchSpec
from .features import build_panel, sample_mask


def predict_latest(run_directory, daily, events=()):
    root = Path(run_directory)
    spec = ResearchSpec.from_dict(json.loads((root / "spec.json").read_text()))
    candidate = json.loads((root / "selection.json").read_text())["candidate"]
    model = joblib.load(root / "model.joblib")
    panel = build_panel(daily, events)
    # One common as-of date; stale suspended-company rows are not presented as current signals.
    latest = panel[(panel.date == panel.date.max()) & panel.history_ready & panel.volume.gt(0)].copy()
    latest = latest[sample_mask(latest, candidate["sampler"], spec)]
    if latest.empty:
        return latest[["symbol", "date"]]
    latest["prediction"] = model.predict(latest)
    latest["horizon"] = candidate["horizon"]
    latest["target_return"] = spec.target_return
    latest["prediction_kind"] = "probability_above_target" if candidate["task"] == "classification" else "expected_return"
    return latest[["symbol", "date", "horizon", "target_return", "prediction_kind", "prediction"]].sort_values("prediction", ascending=False)

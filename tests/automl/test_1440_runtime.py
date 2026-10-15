"""The 14:40 cache must have been fitted by the active sklearn runtime."""
import hashlib
import json

import pytest

from scripts import run_automl_1440_strategy as strategy


def test_model_cache_accepts_matching_runtime_and_rejects_version_drift(
        tmp_path, monkeypatch):
    day = "2026-10-14"
    training = tmp_path / f"{day}_training.csv.gz"
    model = tmp_path / f"{day}_model.joblib"
    training.write_bytes(b"same training rows")
    model.write_bytes(b"model bytes")
    dates = [f"day-{i}" for i in range(20)]
    monkeypatch.setattr(strategy, "database_calendar", lambda _db, _day: dates)
    monkeypatch.setattr(strategy.joblib, "load", lambda _path: "loaded")
    meta = {"signal_date": day, "feature_names": list(strategy.FEATURES),
            "training_dates": dates,
            "pipeline_sha256": hashlib.sha256(strategy.Path(strategy.__file__).read_bytes()).hexdigest(),
            "training_sha256": hashlib.sha256(training.read_bytes()).hexdigest(),
            "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
            "sklearn_version": strategy.sklearn.__version__}
    manifest = tmp_path / f"{day}_model.json"
    manifest.write_text(json.dumps(meta))
    assert strategy.model_for_day(None, day, None, tmp_path)[0] == "loaded"
    meta["sklearn_version"] = "1.9.1" if strategy.sklearn.__version__ != "1.9.1" else "1.7.1"
    manifest.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="缓存模型"):
        strategy.model_for_day(None, day, None, tmp_path)
    del meta["sklearn_version"]
    manifest.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="缓存模型"):
        strategy.model_for_day(None, day, None, tmp_path)

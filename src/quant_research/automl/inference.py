"""Score a new local market panel with a model produced by this runner."""
import json

import joblib

from .config import ResearchSpec
from .assets import frozen_policy, resolve_strategy_directory
from .evaluation import selected
from .features import build_panel, sample_mask


def predict_latest(run_directory, daily, events=(), *, strategy_id=None):
    root = resolve_strategy_directory(run_directory, strategy_id)
    spec = ResearchSpec.from_dict(json.loads((root / "spec.json").read_text()))
    selection = json.loads((root / "selection.json").read_text())
    candidate = selection["candidate"]
    policy = frozen_policy(selection, spec)
    model = joblib.load(root / "model.joblib")
    panel = build_panel(daily, events)
    # One common as-of date; stale suspended-company rows are not presented as current signals.
    latest = panel[(panel.date == panel.date.max()) & panel.history_ready & panel.volume.gt(0)].copy()
    latest = latest[sample_mask(latest, candidate["sampler"], spec)]
    if spec.symbols:
        latest = latest[latest.symbol.isin(spec.symbols)]
    columns = ["symbol", "date", "horizon", "target_return", "prediction_kind", "prediction", "selected"]
    if latest.empty:
        empty = latest.reindex(columns=columns)
        empty["selected"] = False
        return empty
    latest["prediction"] = model.predict(latest)
    latest["horizon"] = candidate["horizon"]
    latest["target_return"] = spec.target_return
    latest["prediction_kind"] = "probability_above_target" if candidate["task"] == "classification" else "expected_return"
    picks = selected(latest, candidate["task"], spec, policy=policy)
    latest["selected"] = latest.index.isin(picks.index)
    return latest[columns].sort_values(["selected", "prediction", "symbol"], ascending=[False, False, True])


def predict_strategy(strategy_directory, daily, events=(), *, strategy_id=None):
    """Reuse a saved strategy, or an explicitly named member of a saved study."""
    return predict_latest(strategy_directory, daily, events, strategy_id=strategy_id)

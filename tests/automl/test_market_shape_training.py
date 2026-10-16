import pandas as pd

from scripts import experiment_automl_market_shape_train as experiment


def test_market_shape_uses_only_previous_completed_sessions(monkeypatch):
    dates = pd.date_range("2026-08-01", periods=7, freq="D")
    source = pd.DataFrame({"trade_date": dates,
                           "market_return": [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.90],
                           "breadth": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.9]})
    monkeypatch.setattr(experiment, "query", lambda *_: source.copy())
    signal = dates[-1]
    states = experiment.market_states(None, [signal])
    assert len(states) == 1
    assert states.iloc[0].prior_market_return == 0.06
    assert abs(states.iloc[0].prior_market_return_5d - 0.20) < 1e-12
    assert abs(states.iloc[0].prior_breadth_5d - 0.4) < 1e-12

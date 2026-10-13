"""Chronology and economic invariants for the isolated research experiment."""
import numpy as np
import pandas as pd
import pytest

from scripts.research_automl_close_open_objectives import (
    CONTEXT_FEATURES, PATH_FEATURES, build_paths, history_for, meta_history,
    record, utility,
)


def daily_fixture():
    dates = pd.bdate_range("2025-01-01", periods=14).strftime("%Y-%m-%d").tolist()
    rows = []
    for symbol in ("000001.SZ", "600001.SH"):
        for i, date in enumerate(dates):
            rows.append(dict(trade_date=date, stk_code=symbol, preclose=10+i*.1,
                             open=10.01+i*.1, high=10.4+i*.1, low=9.9+i*.1,
                             close=10.1+i*.1, volume=1000+i*50, turn_ratio=1+i*.1))
    candidates = pd.DataFrame([dict(signal_date=dates[10], symbol6="000001", signal_price=11.4,
                                    close_40=11.5, signal_return=.04, turnover_so_far_pct=1.6,
                                    ma5=11, ma20=10.5)])
    return pd.DataFrame(rows), candidates, dates


def test_same_day_close_and_future_data_cannot_change_features():
    raw, candidate, dates = daily_fixture()
    expected = build_paths(raw, candidate)
    altered = raw.copy()
    mask = altered.trade_date.ge(dates[10])
    for name in ("high", "low", "close", "volume", "turn_ratio"):
        altered.loc[mask, name] *= 7
    actual = build_paths(altered, candidate)
    pd.testing.assert_frame_equal(expected[list(PATH_FEATURES+CONTEXT_FEATURES)],
                                  actual[list(PATH_FEATURES+CONTEXT_FEATURES)])
    altered = raw.copy()
    altered.loc[altered.trade_date.eq(dates[9]), "close"] *= 1.01
    assert build_paths(altered, candidate).lag_return.iloc[0] != expected.lag_return.iloc[0]


def test_suspended_history_is_not_silently_compressed():
    raw, candidate, dates = daily_fixture()
    raw = raw[~(raw.stk_code.eq("000001.SZ") & raw.trade_date.eq(dates[8]))]
    built = build_paths(raw, candidate)
    assert np.isnan(built.prior5_return.iloc[0])
    assert np.isnan(built.prior5_contract_fraction.iloc[0])


def test_training_and_meta_reject_unmatured_labels():
    dates = pd.bdate_range("2025-01-01", periods=22).strftime("%Y-%m-%d").tolist()
    rows = pd.DataFrame(dict(signal_date=dates[:-1], next_date=dates[1:], actual=.01))
    assert len(history_for(rows, dates, 20)) == 20
    invalid = rows.copy()
    invalid.loc[19, "next_date"] = dates[21]
    with pytest.raises(ValueError, match="immature"):
        history_for(invalid, dates, 20)
    previous = [rows.iloc[[i]].copy() for i in range(20)]
    assert len(meta_history(previous, dates[20])) == 20
    previous[-1]["signal_date"] = dates[20]
    with pytest.raises(ValueError, match="current/future"):
        meta_history(previous, dates[20])


def test_utility_caps_windfalls_but_penalizes_large_losses():
    u = utility(np.array([.10, .20, -.03, -.04]))
    assert u[0] == u[1] == .05
    assert .019 < u[2]-u[3] < .021
    assert u[2] < -.03


def test_unknown_outcome_is_not_cash_and_gate_uses_predictions():
    d = pd.DataFrame(dict(symbol6=["000001", "000002"], actual=[np.nan, .5]))
    pick = record("2025", "2025-02-01", d, "example", np.array([.02, .01]))
    assert pick["traded"] and pick["symbol6"] == "000001" and np.isnan(pick["return"])
    cash = record("2025", "2025-02-01", d, "example", np.array([-.01, -.02]), gate=True)
    assert not cash["traded"] and cash["return"] == 0

from __future__ import annotations

import pandas as pd
import pytest

from scripts.benchmark_automl_1440_inference import (
    FEATURES, frozen_score, make_features, model_universe_eligible,
    select_candidates, verify_model,
)


def test_candidate_boundary_uses_preclose_and_keeps_three_to_six_percent():
    quotes = pd.DataFrame({"symbol6": ["000001", "000002", "000003", "000004"],
                           "signal_price": [10.3, 10.6, 10.299, 10.601],
                           "preclose": [10.0] * 4})
    assert set(select_candidates(quotes).symbol6) == {"000001", "000002"}
    assert model_universe_eligible("000001", "平安银行")
    assert not model_universe_eligible("000001", "*ST测试")
    assert not model_universe_eligible("777777", "测试")


def test_features_use_prior_history_and_quote_cumulative_volume():
    dates = pd.bdate_range("2026-09-01", periods=21)
    history = pd.DataFrame({"trade_date": dates, "stk_code": "000001.SZ",
                            "close": [10.0 + i * .1 for i in range(21)],
                            "adjclose": [10.0 + i * .1 for i in range(21)],
                            "adjpreclose": [10.0] + [10.0 + i * .1 for i in range(20)],
                            "volume": [100_000 + i * 1000 for i in range(21)]})
    quote = pd.DataFrame([{"symbol6": "000001", "name": "test",
                           "signal_price": 12.6, "preclose": 12.0,
                           "volume_hands": 1200, "low_so_far": 12.2,
                           "signal_return": .05}])
    value = pd.DataFrame([{"stk_code": "000001.SZ", "float_mv": 2e10,
                           "float_share": 1e9}])
    rows, exclusions = make_features(quote, history, value, dates)
    assert len(rows) == 1
    row = rows.iloc[0]
    assert row.turnover_so_far_pct == pytest.approx(.012)
    assert row.volume_ratio == pytest.approx(120_000 * 240 / (220 * 118_000))
    assert row.float_mv_100m_cny == 200
    assert row.volume_4of5_increasing == 1
    assert row.ma_bull_5_10_20 == 1
    assert row.all_intraday_lows_above_ma == 1
    assert sum(exclusions.values()) == 0
    incomplete, reasons = make_features(quote, history.iloc[:-1], value, dates)
    assert incomplete.empty and reasons["incomplete_history"] == 1


def test_frozen_model_matches_archived_scores():
    model, validation = verify_model()
    assert validation["archived_score_max_difference"] == 0
    assert len(FEATURES) == 7
    assert callable(frozen_score)

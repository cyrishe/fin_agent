import numpy as np
import pandas as pd
from contextlib import nullcontext

import scripts.experiment_automl_next_open_high1 as high1
from scripts.experiment_automl_next_open_high1 import same_session_price_features


def test_daily_price_features_ignore_cross_date_adjustment_rebase():
    rows = []
    previous = 10.0
    for day, date in enumerate(pd.date_range("2026-08-01", periods=25)):
        close = previous * 1.01
        rows.append({"symbol": "600612.SH", "date": date,
                     "preclose": previous, "open": previous * 1.005,
                     "close": close, "adjclose": close * (10 if day >= 10 else 1)})
        previous = close
    frame = same_session_price_features(pd.DataFrame(rows))
    jump = frame.iloc[10]
    assert jump.adjclose / frame.iloc[9].adjclose - 1 > 9
    assert np.isclose(jump.clean_price_return_1, .01)
    assert np.isclose(frame.iloc[14].clean_price_return_5, 1.01**5 - 1)
    assert np.isclose(frame.iloc[24].clean_price_return_20, 1.01**20 - 1)
    assert np.isclose(frame.iloc[24].clean_price_gap, .005)
    assert np.isclose(frame.iloc[24].clean_price_volatility_20, 0, atol=1e-12)


def test_next_open_label_uses_next_session_preclose_even_when_old_gap_is_rebased(monkeypatch):
    calendar = pd.date_range("2026-08-01", periods=23)
    rows = []
    previous = 10.0
    for index, date in enumerate(calendar):
        close = previous * 1.01
        rows.append({"symbol": "600612.SH", "date": date, "preclose": previous,
                     "open": previous * (1.015 if index == 22 else 1.005),
                     "close": close, "is_limit_price": 0})
        previous = close
    monkeypatch.setattr(high1, "kingdom_connection", lambda: nullcontext())
    monkeypatch.setattr(high1, "_load_batches", lambda *_args: pd.DataFrame(rows))
    old = pd.DataFrame([{"symbol": "600612.SH", "signal_date": calendar[21],
                         "gap": 9.0, "price_amount_ratio_5": 1.0,
                         "flow_main_ratio_1": 0.0, "flow_main_ratio_5": 0.0,
                         "sector_return_1": 0.0, "csi300_return_1": 0.0}])
    fixed = high1.attach_limit_audit(old, ["600612.SH"], calendar).iloc[0]
    assert np.isclose(fixed.gap, .015)
    assert fixed.crossdate_gap == 9.0
    assert np.isclose(fixed.price_return_5, 1.01**5 - 1)
    assert fixed.prior5_limit_count == 0
    for features, _, _ in high1.model_candidates("exclude_prior5_limit").values():
        assert "current_final_limit_flag" not in features

import pandas as pd
import pytest

from scripts.build_automl_tail_standard import TRAIN_EXPORT, assemble


def test_standard_1440_features_need_complete_bars_but_no_ingestion_timestamps():
    day, following = pd.Timestamp("2026-09-22"), pd.Timestamp("2026-09-23")
    signal = pd.DataFrame([{
        "signal_date": day, "next_date": following, "symbol6": "000001",
        "name": "平安银行", "signal_price": 10.4, "t_reference_preclose": 10.0,
        "prior_close": 10.0, "prior_adjclose": 20.0,
        "history_complete": True, "value_complete": True,
        "avg_volume5_shares": 240_000.0, "float_mv": 10_000_000.0,
        "float_share": 1_000_000.0, "ma5_adj": 21.0, "ma10_adj": 20.0,
        "ma20_adj": 19.0, "signal_fallback": 0, "signal_finalized": 1,
        **{f"volume_tminus{i}": 240_000.0 for i in range(1, 6)},
    }])
    minute = pd.DataFrame([{"symbol6": "000001", "minute_bars": 220,
                            "minute_volume_hands": 2200, "min_low_so_far": 9.8,
                            "minute_fallback": 0, "minute_finalized": 1,
                            "exact_source_bars": 220}])
    entry = pd.DataFrame([{"signal_date": day, "symbol6": "000001",
                           "entry_1450": 10.6, "entry_fallback": 0,
                           "entry_finalized": 1}])
    morning = pd.DataFrame([{"next_date": following, "symbol6": "000001",
                             "next_open": 11.0, "next_high5": 11.2,
                             "next_high10": 11.4, "bars5": 5, "bars10": 10,
                             "volume5": 100, "morning_fallback": 0,
                             "morning_finalized": 1}])

    row = assemble(signal, minute, entry, morning).iloc[0]

    assert row.feature_complete
    assert row.label_complete
    assert row.volume_ratio == pytest.approx(1.0)
    assert row.turnover_so_far_pct == pytest.approx(22.0)
    assert row.ma_bull_5_10_20
    assert not row.all_intraday_lows_above_ma
    assert row.target_next_high5_return == pytest.approx(11.2 / 10.6 - 1)
    assert row.target_next_high10_return == pytest.approx(11.4 / 10.6 - 1)
    assert "entry_1450" not in TRAIN_EXPORT
    assert "t_final_limit_flag" not in TRAIN_EXPORT
    assert "entry_not_near_limit_proxy" not in TRAIN_EXPORT

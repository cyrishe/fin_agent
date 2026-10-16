import pandas as pd
import pytest

from scripts.build_automl_tail_sample import assemble


def test_intraday_units_and_late_1440_bar_do_not_enter_strict_training():
    day = pd.Timestamp("2026-09-22")
    following = pd.Timestamp("2026-09-23")
    rows = pd.DataFrame([{
        "signal_date": day, "next_date": following, "symbol6": "000001",
        "name": "平安银行",
        "signal_time": clock, "signal_price": price, "prior_close": 10.0,
        "prior_adjclose": 20.0, "history_ready": True, "value_ready": True,
        "avg_volume5_shares": 240_000.0, "float_share": 1_000_000.0,
        "float_mv": 10_000_000.0, "free_float_mv": 8_000_000.0,
        "ma5_adj": 21.0, "ma10_adj": 20.0, "ma20_adj": 19.0,
        **{f"volume_tminus{i}": 240_000.0 for i in range(1, 6)},
        "is_fallback": 0, "is_finalized": 1,
        "source_snapshot_time": f"2026-09-22 {clock}:00",
        "fetch_time": fetch, "created_at": fetch, "updated_at": fetch,
    } for clock, price, fetch in [
        ("14:30", 10.4, "2026-09-22 14:40:05"),
        ("14:40", 10.5, "2026-09-22 14:50:05"),
    ]])
    minute = pd.DataFrame([{
        "symbol6": "000001", "bars_1430": 210, "bars_1440": 220,
        "minute_volume_1430": 2100, "minute_volume_1440": 2200,
        "min_low_1430": 9.8, "min_low_1440": 9.8,
        "fallback_1430": 0, "fallback_1440": 0,
        "finalized_1430": 1, "finalized_1440": 1,
        "arrived_1430": 210, "arrived_1440": 210,
    }])
    entry = pd.DataFrame([{"signal_date": day, "symbol6": "000001",
                           "entry_1450": 10.6, "entry_fallback": 0,
                           "entry_finalized": 1}])
    morning = pd.DataFrame([{"next_date": following, "symbol6": "000001",
                             "next_open": 11.0, "next_high5": 11.2,
                             "next_high10": 11.4, "bars5": 5, "bars10": 10,
                             "morning_volume5": 100, "morning_fallback": 0,
                             "morning_finalized": 1}])
    final = pd.DataFrame([{"signal_date": day, "symbol6": "000001",
                           "t_final_limit_flag": 0}])

    result = assemble(rows, minute, entry, morning, final).set_index("signal_time")

    assert result.loc["14:30", "feature_ready_1449"]
    assert result.loc["14:30", "entry_not_near_limit_proxy"]
    assert not result.loc["14:40", "feature_ready_1449"]
    assert result.loc["14:40", "minute_complete"]
    assert result.loc["14:30", "volume_ratio"] == pytest.approx(1.0)
    assert result.loc["14:40", "volume_ratio"] == pytest.approx(1.0)
    assert result.loc["14:30", "turnover_so_far_pct"] == pytest.approx(21.0)
    assert result.loc["14:40", "target_next_high10_return"] == pytest.approx(11.4 / 10.6 - 1)

import pandas as pd
import pytest

from scripts.build_automl_tail_standard import (
    TRAIN_EXPORT, assemble, four_of_five_volume_increasing, prior_profile,
)


def test_standard_1440_features_need_complete_bars_but_no_ingestion_timestamps():
    day, following = pd.Timestamp("2026-09-22"), pd.Timestamp("2026-09-23")
    signal = pd.DataFrame([{
        "signal_date": day, "next_date": following, "symbol6": "000001",
        "name": "平安银行", "signal_price": 10.4, "t_reference_preclose": 10.0,
        "prior_close": 10.0, "prior_adjclose": 20.0,
        "history_complete": True, "adj_price_break_20d": False,
        "value_complete": True,
        "avg_volume5_shares": 240_000.0, "float_mv": 10_000_000.0,
        "volume_4of5_increasing": 1,
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
    assert row.volume_4of5_increasing == 1
    assert row.turnover_so_far_pct == pytest.approx(22.0)
    assert row.float_mv_100m_cny == pytest.approx(0.1)
    assert row.ma_bull_5_10_20
    assert not row.all_intraday_lows_above_ma
    assert row.target_next_high5_return == pytest.approx(11.2 / 10.6 - 1)
    assert row.target_next_high10_return == pytest.approx(11.4 / 10.6 - 1)
    assert "entry_1450" not in TRAIN_EXPORT
    assert "t_final_limit_flag" not in TRAIN_EXPORT
    assert "entry_not_near_limit_proxy" not in TRAIN_EXPORT
    assert "adj_price_break_20d" not in TRAIN_EXPORT
    assert {"volume_4of5_increasing", "turnover_so_far_pct",
            "float_mv_100m_cny"}.issubset(TRAIN_EXPORT)

    signal.loc[0, "adj_price_break_20d"] = True
    break_row = assemble(signal, minute, entry, morning).iloc[0]
    assert not break_row.feature_complete
    assert pd.isna(break_row.ma5)
    assert break_row.volume_4of5_increasing == 1


@pytest.mark.parametrize(("series", "expected"), [
    ([1, 2, 3, 4, 5], 1),
    ([1, 3, 2, 4, 5], 1),
    ([1, 2, 3, 4, 1], 1),
    ([5, 4, 3, 2, 1], 0),
    ([1, 2, 2, 3, 1], 0),
])
def test_four_of_five_volume_increasing_examples(series, expected):
    columns = {f"volume_tminus{5-i}": [v] for i, v in enumerate(series)}
    assert four_of_five_volume_increasing(pd.DataFrame(columns)).iloc[0] == expected


def test_adj_price_break_in_prior_twenty_days_excludes_training_feature_row():
    dates = pd.bdate_range("2026-07-20", periods=23)
    price = pd.DataFrame({
        "date": dates, "symbol6": "000001", "preclose": 10.0,
        "close": 10.0, "adjpreclose": 10.0, "adjclose": 10.0,
        "volume": 1000.0,
    })
    # The later adjusted prices rebase by 10x despite flat raw prices.
    price.loc[20:, ["adjpreclose", "adjclose"]] = 100.0
    price.loc[17:21, "volume"] = [1000, 3000, 2000, 4000, 5000]
    value = pd.DataFrame({
        "date": dates, "symbol6": "000001", "float_mv": 1e8,
        "float_share": 1e7,
    })
    profile, _ = prior_profile(price, value)
    row = profile.loc[profile.signal_date.eq(dates[22])].iloc[0]
    assert row.adj_price_break_20d
    assert row.history_complete
    assert row.volume_4of5_increasing == 1

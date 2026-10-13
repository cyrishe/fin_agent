"""Decision-time and source-coverage checks for the cooling-day feature panel."""
from __future__ import annotations

import pandas as pd
import pytest

from scripts.build_automl_limit_rebound_features import (
    FEATURES, KEY, build_panel, load_cached_minute_features, minute_features,
)


def fixture():
    days = pd.bdate_range("2025-12-22", periods=16).strftime("%Y-%m-%d").tolist()
    stock = []
    for i, day in enumerate(days):
        up = i in (5, 6, 10)
        close = 11 if up else 10 + (i - 6) * .08 if i in (7, 8, 9) else 10
        stock.append({"trade_date": day, "stk_code": "000001.SZ", "preclose": 10,
                      "open": 10, "high": max(10, close) + .1,
                      "low": 9.9, "close": close, "volume": 100 + i * 2,
                      "amount": 1000 + i * 20, "turn_ratio": 2 + i * .1,
                      "is_limit_price": int(up)})
    other = [{**row, "stk_code": "600001.SH", "is_limit_price": 0,
              "close": 10.1} for row in stock]
    event = pd.DataFrame([{"symbol": "000001", "first_limit_date": days[5],
                           "streak_end_date": days[6], "streak_length": 2}])
    return pd.DataFrame(stock + other), event, days


def test_each_cooling_close_is_a_separate_asof_decision():
    daily, events, days = fixture()
    features, labels = build_panel(daily, events)
    assert features.cooling_day.tolist() == [1, 2, 3]
    assert features.asof_date.tolist() == days[7:10]
    assert labels.label_next_limit_up.tolist() == [False, False, True]
    assert labels.label_next3_any_limit_up.tolist() == [True, True, True]
    assert features.loc[0, "path_up_close_days"] == 1
    assert features.loc[2, "path_up_close_days"] == 3
    assert features.loc[0, "day_high_return"] > features.loc[0, "day_return"]
    assert features.loc[0, "market_prev_limit_up_count"] >= 0
    assert features.loc[0, "first_minute_complete"] == 0
    assert set(features.columns) == set(KEY) | {name for group in FEATURES.values() for name in group}
    assert not any(name.startswith("label_") for name in features.columns)

    changed = daily.copy()
    changed.loc[(changed.stk_code == "000001.SZ") & (changed.trade_date == days[10]),
                ["close", "is_limit_price"]] = [9.8, 0]
    early, changed_labels = build_panel(changed, events)
    pd.testing.assert_frame_equal(features, early)
    assert changed_labels.label_next_limit_up.tolist() == [False, False, False]


def test_missing_quote_and_market_calendar_do_not_become_negative_samples():
    daily, events, days = fixture()
    daily = daily[~((daily.stk_code == "000001.SZ") & (daily.trade_date == days[8]))]
    features, labels = build_panel(daily, events)
    assert features.cooling_day.tolist() == [1]
    assert labels.label_next_observed.tolist() == [False]
    assert pd.isna(labels.label_next_limit_up.iloc[0])


def test_max_cooling_is_at_most_one_trading_week():
    daily, events, days = fixture()
    with pytest.raises(ValueError, match="1..5"):
        build_panel(daily, events, max_cooling=6)
    daily.loc[(daily.stk_code == "000001.SZ") & daily.trade_date.eq(days[10]),
              ["close", "is_limit_price"]] = [10.1, 0]
    features, _ = build_panel(daily, events, max_cooling=5)
    assert features.cooling_day.tolist() == [1, 2, 3, 4, 5]


def test_right_edge_is_unknown_not_failure():
    daily, _, days = fixture()
    daily.loc[(daily.stk_code == "000001.SZ") & daily.trade_date.eq(days[-2]),
              ["close", "is_limit_price"]] = [11, 1]
    event = pd.DataFrame([{"symbol": "000001", "first_limit_date": days[-2],
                           "streak_end_date": days[-2], "streak_length": 1}])
    features, labels = build_panel(daily, event)
    assert features.cooling_day.tolist() == [1]
    assert labels.label_next_observed.tolist() == [False]
    assert pd.isna(labels.label_next_limit_up.iloc[0])


def test_first_board_minute_shape_requires_full_raw_day():
    times = pd.date_range("2026-01-01 09:31", periods=120, freq="min").append(
        pd.date_range("2026-01-01 13:01", periods=120, freq="min"))
    bars = pd.DataFrame({"time": times, "open": [10.5] * 240,
                         "high": [10.8] * 240, "low": [10.4] * 240,
                         "close": [10.8] * 240, "volume": [100] * 240,
                         "turnover": [1000] * 240})
    bars.loc[50:51, ["high", "close"]] = 11
    bars.loc[52, "high"] = 11
    bars.loc[53:, ["high", "close"]] = 11
    bars.loc[53:, "low"] = 11
    result = minute_features(bars, 11)
    assert result["first_minute_complete"] == 1
    assert result["first_touch_minute"] == 51
    assert result["first_reseal_count"] == 2
    assert result["first_last_reseal_minute"] == 54
    assert result["first_below_cap_after_touch_minutes"] == 3
    assert result["first_amount_first_hour_fraction"] == .25
    assert minute_features(bars.iloc[:-1], 11)["first_minute_complete"] == 0


def test_cached_minute_features_must_agree_for_repeated_cooling_rows(tmp_path):
    minute = minute_features(None, 11)
    rows = pd.DataFrame([{"symbol": "000001", "first_limit_date": "2026-01-05", **minute}] * 2)
    path = tmp_path / "features.csv.gz"
    rows.to_csv(path, index=False)
    assert len(load_cached_minute_features(path)) == 1
    rows.loc[1, "first_minute_complete"] = 1
    rows.to_csv(path, index=False)
    with pytest.raises(ValueError, match="disagree"):
        load_cached_minute_features(path)

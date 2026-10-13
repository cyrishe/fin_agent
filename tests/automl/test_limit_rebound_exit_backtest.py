from types import SimpleNamespace

import pandas as pd

from scripts.backtest_automl_limit_rebound_may_july import (
    cap_price, make_trade_paths, resolve_down_limit_exits, trigger_exit,
)


def test_entry_day_upper_limit_trigger_respects_t_plus_one():
    dates = ["2026-05-06", "2026-05-07", "2026-05-08", "2026-05-11"]
    daily = pd.DataFrame([
        {"trade_date": dates[0], "symbol": "000001", "preclose": 10., "open": 10.1,
         "high": 11., "close": 11., "is_limit_price": 1},
        {"trade_date": dates[1], "symbol": "000001", "preclose": 11., "open": 11.5,
         "high": 11.6, "close": 11.4, "is_limit_price": 0},
    ])
    row = SimpleNamespace(entry_date=dates[0], symbol="000001", rate=.1)
    result = trigger_exit(row, "first_limit_touch", daily, {}, dates,
                          {day: i for i, day in enumerate(dates)})
    assert result["exit_date"] == dates[1]
    assert result["exit_price"] == 11.5
    assert result["exit_reason"] == "t_plus_one_after_trigger"


def test_continue_sealed_streak_then_leave_after_first_unsealed_close():
    dates = ["2026-05-06", "2026-05-07", "2026-05-08", "2026-05-11"]
    daily = pd.DataFrame([
        {"trade_date": dates[0], "symbol": "000001", "preclose": 10., "open": 10.1,
         "high": 11., "close": 11., "is_limit_price": 1},
        {"trade_date": dates[1], "symbol": "000001", "preclose": 11., "open": 12.,
         "high": 12.1, "close": 12.1, "is_limit_price": 1},
        {"trade_date": dates[2], "symbol": "000001", "preclose": 12.1, "open": 12.,
         "high": 12., "close": 11.8, "is_limit_price": 0},
        {"trade_date": dates[3], "symbol": "000001", "preclose": 11.8, "open": 11.7,
         "high": 11.8, "close": 11.6, "is_limit_price": 0},
    ])
    row = SimpleNamespace(entry_date=dates[0], symbol="000001", rate=.1)
    result = trigger_exit(row, "hold_until_unsealed", daily, {}, dates,
                          {day: i for i, day in enumerate(dates)})
    assert result["exit_date"] == dates[3]
    assert result["exit_price"] == 11.7
    assert result["exit_reason"] == "first_unsealed_next_open"


def test_rounds_exchange_price_to_cent():
    assert cap_price(10.05, .1) == 11.06


def test_ex_rights_factor_preserves_position_value():
    dates = ["2026-05-06", "2026-05-07", "2026-05-08", "2026-05-11"]
    daily = pd.DataFrame([
        {"trade_date": dates[0], "symbol": "000001", "preclose": 10., "open": 10.,
         "high": 10.1, "close": 10., "is_limit_price": 0, "action_factor": 1.},
        {"trade_date": dates[1], "symbol": "000001", "preclose": 5., "open": 5.,
         "high": 5.1, "close": 5., "is_limit_price": 0, "action_factor": 2.},
        {"trade_date": dates[2], "symbol": "000001", "preclose": 5., "open": 5.,
         "high": 5.1, "close": 5., "is_limit_price": 0, "action_factor": 1.},
        {"trade_date": dates[3], "symbol": "000001", "preclose": 5., "open": 5.,
         "high": 5.1, "close": 5., "is_limit_price": 0, "action_factor": 1.},
    ])
    entries = pd.DataFrame([{"signal_date": "2026-04-30", "entry_date": dates[0],
                             "symbol": "000001", "first_limit_date": "2026-04-29",
                             "cooling_day": 1, "rank": 1, "score": .2, "rate": .1,
                             "entry_price": 10.}])
    paths = make_trade_paths(entries, daily, {})
    fixed = paths[paths.policy.eq("fixed_3day")].iloc[0]
    assert fixed.adjustment_factor == 2
    assert fixed.gross_return == 0


def test_one_price_lower_limit_does_not_fill_sell_at_open():
    dates = ["2026-05-06", "2026-05-07", "2026-05-08"]
    daily = pd.DataFrame([
        {"trade_date": dates[0], "symbol": "000001", "preclose": 10., "open": 10.,
         "high": 10.2, "close": 10., "action_factor": 1.},
        {"trade_date": dates[1], "symbol": "000001", "preclose": 10., "open": 9.,
         "high": 9., "close": 9., "action_factor": 1.},
        {"trade_date": dates[2], "symbol": "000001", "preclose": 9., "open": 9.3,
         "high": 9.5, "close": 9.4, "action_factor": 1.},
    ])
    path = pd.DataFrame([{"signal_date": "2026-04-30", "entry_date": dates[0],
                          "symbol": "000001", "first_limit_date": "2026-04-29",
                          "cooling_day": 1, "rank": 1, "score": .2, "rate": .1,
                          "entry_price": 10., "policy": "first_limit_touch",
                          "exit_date": dates[1], "exit_price": 9.,
                          "exit_reason": "three_day_timeout", "minute_used": False,
                          "adjustment_factor": 1., "gross_return": -.1}])
    actual = resolve_down_limit_exits(path, daily, {}).iloc[0]
    assert actual.exit_date == dates[2]
    assert actual.exit_price == 9.3
    assert actual.exit_reason == "down_limit_retry_next_open"

"""Two-year event and as-of enrichment contracts."""
import pandas as pd

from scripts.build_automl_limit_rebound_2y import add_size_proxy, events_from_daily
from scripts.enrich_automl_limit_rebound_industry import attach_industry
from scripts.experiment_automl_limit_rebound_2y import split as split_next_day
from scripts.experiment_automl_limit_rebound_3day import split as split_three_day


def test_event_detection_breaks_on_missing_market_day():
    daily = pd.DataFrame({
        "symbol": ["000001"] * 3 + ["000002"] * 3,
        "trade_date": ["2024-10-08", "2024-10-09", "2024-10-11"] * 2,
        "up": [True, True, True, False, False, False],
    })
    # The market traded October 10, but this stock has no row that day.
    daily.loc[len(daily)] = ["000002", "2024-10-10", False]
    events = events_from_daily(daily)
    assert events[["first_limit_date", "streak_end_date", "streak_length"]].values.tolist() == [
        ["2024-10-08", "2024-10-09", 2], ["2024-10-11", "2024-10-11", 1]]


def test_size_proxy_uses_only_same_day_amount_and_turnover():
    daily = pd.DataFrame({"trade_date": ["2026-01-05"], "symbol": ["000001"],
                          "amount": [2e8], "turn_ratio": [2.]})
    features = pd.DataFrame({"asof_date": ["2026-01-05"], "symbol": ["000001"]})
    result = add_size_proxy(features, daily)
    assert result.float_mv_proxy_100m.iloc[0] == 100


def test_future_created_industry_membership_cannot_enter_old_decision():
    daily = pd.DataFrame([
        dict(trade_date=date, stk_code=symbol, preclose=10., open=10., high=11.,
             low=9., close=close, volume=100., amount=1000., turn_ratio=1.,
             is_limit_price=0)
        for date in ("2026-01-05", "2026-01-06")
        for symbol, close in (("000001.SZ", 10.5), ("000002.SZ", 10.2))
    ])
    features = pd.DataFrame({"asof_date": ["2026-01-05", "2026-01-06"],
                             "symbol": ["000001", "000001"]})
    membership = pd.DataFrame([
        dict(stk_code="000001.SZ", begin_date="2020-01-01", end_date=None,
             create_time="2026-01-06 00:00:00", industry_code="A"),
        dict(stk_code="000002.SZ", begin_date="2020-01-01", end_date=None,
             create_time="2026-01-05 00:00:00", industry_code="A"),
    ])
    result = attach_industry(daily, features, membership)
    assert pd.isna(result.industry_member_count.iloc[0])
    assert result.industry_member_count.iloc[1] == 2


def test_three_day_split_embargoes_boundary_and_purges_repeated_event():
    rows = pd.DataFrame([
        dict(symbol="000001", first_limit_date="2026-03-24", asof_date="2026-03-26"),
        dict(symbol="000002", first_limit_date="2026-03-27", asof_date="2026-03-30"),
        dict(symbol="000003", first_limit_date="2026-03-29", asof_date="2026-04-01"),
        dict(symbol="000004", first_limit_date="2026-06-20", asof_date="2026-06-25"),
        dict(symbol="000004", first_limit_date="2026-06-20", asof_date="2026-07-01"),
    ])
    train, valid, refit, test = split_three_day(rows)
    assert train.symbol.tolist() == ["000001"]
    assert valid.symbol.tolist() == ["000003", "000004"]
    assert set(refit.symbol) == {"000001", "000002", "000003"}
    assert test.symbol.tolist() == ["000004"]


def test_next_day_two_year_split_keeps_events_on_one_side():
    rows = pd.DataFrame([
        dict(symbol="000001", first_limit_date="2026-03-27", asof_date="2026-03-30"),
        dict(symbol="000001", first_limit_date="2026-03-27", asof_date="2026-04-01"),
        dict(symbol="000002", first_limit_date="2026-03-29", asof_date="2026-03-30"),
        dict(symbol="000003", first_limit_date="2026-06-28", asof_date="2026-06-29"),
        dict(symbol="000003", first_limit_date="2026-06-28", asof_date="2026-07-01"),
    ])
    train, valid, refit, test = split_next_day(rows)
    assert train.symbol.tolist() == ["000002"]
    assert set(valid.symbol) == {"000001", "000003"}
    assert set(refit.symbol) == {"000001", "000002"}
    assert test.symbol.tolist() == ["000003"]

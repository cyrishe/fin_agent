from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from src.backtest import BacktestError, Bar, InMemoryMarketData, Instrument
from src.services.buy_and_hold_backtest_service import (
    AshareDailyMarketDataLoader,
    BuyAndHoldBacktestService,
    LoadedAshareMarketData,
    ResolvedBacktestStock,
)


D1 = dt.date(2026, 1, 5)
D2 = dt.date(2026, 1, 6)
D3 = dt.date(2026, 1, 7)
D4 = dt.date(2026, 1, 8)


def _row(date: dt.date, code: str, price: str, *, volume: str = "1000", limit=False):
    value = Decimal(price)
    return {
        "trade_date": date.isoformat(),
        "stock_code": code,
        "open": value,
        "high": value + Decimal("1"),
        "low": value - Decimal("1"),
        "close": value + Decimal("0.5"),
        "volume": Decimal(volume),
        "is_limit_price": limit,
    }


class _FakeKlineService:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def query_daily(self, **kwargs):
        self.calls.append(kwargs)
        return self.payloads[kwargs["subject"]]


class _FakeCursor:
    def __init__(self, dates):
        self.dates = dates
        self.sql = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return [{"calendar_date": date} for date in self.dates]


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *_args, **_kwargs):
        return self._cursor


class _FakeDb:
    def __init__(self, dates):
        self.cursor = _FakeCursor(dates)
        self.conn = _FakeConnection(self.cursor)
        self.closed = False

    def close_db(self):
        self.closed = True


def test_real_market_loader_uses_trade_calendar_and_maps_daily_bars():
    fake_db = _FakeDb([D1, D2, D3])
    kline = _FakeKlineService(
        {
            "贵州茅台": {
                "stock_code": "600519.SH",
                "stock_name": "贵州茅台",
                "rows": [
                    _row(D1, "600519.SH", "100"),
                    _row(D2, "600519.SH", "101"),
                    _row(D3, "600519.SH", "102", limit=True),
                ],
            },
            "五粮液": {
                "stock_code": "000858.SZ",
                "stock_name": "五粮液",
                "rows": [
                    _row(D1, "000858.SZ", "50"),
                    _row(D2, "000858.SZ", "51", volume="0"),
                    _row(D3, "000858.SZ", "52"),
                ],
            },
        }
    )
    loader = AshareDailyMarketDataLoader(
        kline_service=kline,
        db_factory=lambda: fake_db,
    )

    loaded = loader.load(
        subjects=["贵州茅台", "五粮液"],
        start_date=D1,
        end_date=D3,
    )

    assert loaded.market_data.calendar == (D1, D2, D3)
    assert loaded.market_data.instrument("600519.SH").lot_size == 100
    assert loaded.market_data.bar("000858.SZ", D2).tradable is False
    assert loaded.stocks[0].code == "600519.SH"
    assert loaded.stocks[1].code == "000858.SZ"
    assert "aiia_trade_calendar" in fake_db.cursor.sql
    assert fake_db.cursor.params == ("CN_A", D1, D3)
    assert fake_db.closed is True
    assert all(call["limit"] == 1000 for call in kline.calls)
    assert any("涨跌停" in warning for warning in loaded.warnings)


def test_real_market_loader_starts_when_all_selected_stocks_have_a_bar():
    fake_db = _FakeDb([D1, D2, D3, D4])
    kline = _FakeKlineService(
        {
            "A": {
                "stock_code": "600001.SH",
                "stock_name": "A",
                "rows": [_row(date, "600001.SH", "10") for date in (D1, D2, D3, D4)],
            },
            "B": {
                "stock_code": "000002.SZ",
                "stock_name": "B",
                "rows": [_row(date, "000002.SZ", "20") for date in (D2, D3, D4)],
            },
        }
    )

    loaded = AshareDailyMarketDataLoader(
        kline_service=kline,
        db_factory=lambda: fake_db,
    ).load(subjects=["A", "B"], start_date=D1, end_date=D4)

    assert loaded.effective_start == D2
    assert loaded.market_data.calendar == (D2, D3, D4)
    assert any("实际从 2026-01-06 开始" in warning for warning in loaded.warnings)


def test_real_market_loader_rejects_duplicate_symbol_date_rows():
    fake_db = _FakeDb([D1, D2])
    duplicate = _row(D1, "600001.SH", "10")
    loader = AshareDailyMarketDataLoader(
        kline_service=_FakeKlineService(
            {
                "A": {
                    "stock_code": "600001.SH",
                    "stock_name": "A",
                    "rows": [duplicate, dict(duplicate), _row(D2, "600001.SH", "11")],
                }
            }
        ),
        db_factory=lambda: fake_db,
    )

    with pytest.raises(BacktestError) as caught:
        loader.load(subjects=["A"], start_date=D1, end_date=D2)

    assert caught.value.code == "duplicate_bar"
    assert caught.value.details == {
        "symbol": "600001.SH",
        "trade_date": D1.isoformat(),
    }


def _bar(date, symbol, open_price, close_price):
    open_value = Decimal(str(open_price))
    close_value = Decimal(str(close_price))
    return Bar(
        date=date,
        symbol=symbol,
        open=open_value,
        high=max(open_value, close_value) + Decimal("1"),
        low=min(open_value, close_value) - Decimal("1"),
        close=close_value,
        volume=Decimal("10000"),
    )


class _FixtureLoader:
    def __init__(self):
        self.data = InMemoryMarketData(
            [
                _bar(D1, "600001.SH", 10, 10),
                _bar(D1, "000002.SZ", 20, 20),
                _bar(D2, "600001.SH", 10, 11),
                _bar(D2, "000002.SZ", 20, 18),
                _bar(D3, "600001.SH", 11, 12),
                _bar(D3, "000002.SZ", 18, 21),
                _bar(D4, "600001.SH", 12, 13),
                _bar(D4, "000002.SZ", 21, 22),
            ],
            instruments=[
                Instrument("600001.SH", lot_size=100),
                Instrument("000002.SZ", lot_size=100),
            ],
            calendar=[D1, D2, D3, D4],
            source_name="fixture.real_adapter",
        )

    def load(self, *, subjects, start_date, end_date):
        assert tuple(subjects) == ("A", "B")
        return LoadedAshareMarketData(
            market_data=self.data,
            stocks=(
                ResolvedBacktestStock("A", "600001.SH", "股票A", 4, 0),
                ResolvedBacktestStock("B", "000002.SZ", "股票B", 4, 0),
            ),
            requested_start=start_date,
            requested_end=end_date,
            effective_start=D1,
            effective_end=D4,
            session_count=4,
            warnings=(),
        )


def test_buy_and_hold_service_buys_once_and_holds_to_the_end():
    service = BuyAndHoldBacktestService(loader=_FixtureLoader())

    result = service.run(
        {
            "stocks": ["A", "B"],
            "start_date": D1.isoformat(),
            "end_date": D4.isoformat(),
            "initial_cash": "100000",
        }
    )

    assert result["strategy"]["name"] == "买入并持有"
    assert [stock["target_weight"] for stock in result["stocks"]] == [0.5, 0.5]
    assert result["summary"]["trade_count"] == 2
    assert {trade["action"] for trade in result["trades"]} == {"买入"}
    assert {trade["date"] for trade in result["trades"]} == {D2.isoformat()}
    assert len(result["equity_curve"]) == 4
    assert len(result["ending_holdings"]) == 2
    assert result["summary"]["final_value"] != result["summary"]["initial_cash"]
    assert result["evidence"]["run_fingerprint"].startswith("sha256:")


def test_buy_and_hold_service_uses_supplied_weights_and_keeps_remainder_as_cash():
    service = BuyAndHoldBacktestService(loader=_FixtureLoader())

    result = service.run(
        {
            "stocks": ["A", "B"],
            "weights": ["0.6", "0.3"],
            "start_date": D1.isoformat(),
            "end_date": D4.isoformat(),
            "initial_cash": "100000",
        }
    )

    assert result["strategy"]["allocation"] == "specified_weight"
    assert [stock["target_weight"] for stock in result["stocks"]] == [0.6, 0.3]
    assert result["summary"]["trade_count"] == 2


def test_buy_and_hold_service_rejects_weight_sum_above_one():
    with pytest.raises(BacktestError) as caught:
        BuyAndHoldBacktestService(loader=_FixtureLoader()).run(
            {
                "stocks": ["A", "B"],
                "weights": ["0.7", "0.4"],
                "start_date": D1.isoformat(),
                "end_date": D4.isoformat(),
            }
        )

    assert caught.value.code == "weight_sum_exceeds_one"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"stocks": [], "start_date": "2026-01-01", "end_date": "2026-02-01"}, "invalid_stocks"),
        ({"stocks": ["A", "A"], "start_date": "2026-01-01", "end_date": "2026-02-01"}, "duplicate_stock"),
        ({"stocks": ["A"], "start_date": "bad", "end_date": "2026-02-01"}, "invalid_date"),
        ({"stocks": ["A"], "start_date": "2026-02-01", "end_date": "2026-01-01"}, "invalid_date_range"),
        ({"stocks": ["A"], "start_date": "2020-01-01", "end_date": "2026-01-01"}, "period_too_long"),
    ],
)
def test_buy_and_hold_service_rejects_only_non_executable_inputs(payload, code):
    with pytest.raises(BacktestError) as caught:
        BuyAndHoldBacktestService(loader=_FixtureLoader()).run(payload)

    assert caught.value.code == code

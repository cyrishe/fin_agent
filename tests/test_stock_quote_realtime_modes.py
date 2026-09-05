from __future__ import annotations

from datetime import date

import pytest

from src.experiments.staged_data_protocol.phase2 import api_runner, intraday_quote_provider
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
from src.experiments.staged_data_protocol.phase2.intraday_quote_provider import (
    _latest_quote_limit_policy,
    _minute_bar_cte,
    _trade_date_predicate,
)
from src.experiments.staged_data_protocol.phase2.models import ApiCall


@pytest.mark.parametrize(
    ("mode", "expected_provider", "expected_latest_only"),
    [(0, "daily", None), (1, "minute", False), (2, "minute", True)],
)
def test_stock_quote_routes_modes(
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    expected_provider: str,
    expected_latest_only: bool | None,
) -> None:
    calls: list[tuple[str, bool | None]] = []

    def fake_daily(*, subject, args, outputs):
        calls.append(("daily", None))
        return {"status": "ok", "columns": ["code"], "rows": []}

    def fake_minute(*, args, outputs, latest_only=False):
        calls.append(("minute", latest_only))
        return {"status": "ok", "columns": ["code"], "rows": []}

    monkeypatch.setattr(api_runner, "execute_quote_api", fake_daily)
    monkeypatch.setattr(api_runner, "execute_intraday_quote_api", fake_minute)

    result = api_runner.execute_api_call(ApiCall("r1", "stock.quote", {"mode": mode}, ["code"], ""))

    assert result.data["status"] == "ok"
    assert calls == [(expected_provider, expected_latest_only)]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, 0), (1, 1), (2, 2), ("history", 0), ("minute", 1), ("latest", 2), (False, 0), (True, 2)],
)
def test_stock_quote_realtime_mode_compatibility(raw: object, expected: int) -> None:
    resolved = resolve_api("stock.quote")
    assert resolved is not None
    assert api_runner._quote_realtime_mode({"realtime": raw}, resolved) == expected


def test_stock_quote_t_minus_one_day_k_rejects_today_exact_date() -> None:
    validation = validate_call(
        ApiCall(
            "r1",
            "stock.quote",
            {"filter": f"code = 300308.SZ and tradedate = {date.today().isoformat()}", "mode": 0},
            ["code", "tradedate", "close"],
            "",
        ),
        previous_results={},
    )
    assert validation.ok is False
    assert any("mode=0 only returns day K" in item for item in validation.errors)


def test_stock_quote_native_30_minute_period_is_valid() -> None:
    validation = validate_call(
        ApiCall("r1", "stock.quote", {"mode": 1, "period": "30m", "count": 20}, ["code", "close"], ""),
        previous_results={},
    )
    assert validation.ok is True


def test_stock_quote_rejects_unsupported_minute_period() -> None:
    validation = validate_call(
        ApiCall("r1", "stock.quote", {"mode": 1, "period": 2}, ["code", "close"], ""),
        previous_results={},
    )
    assert validation.ok is False
    assert any("1, 3, 5, 10, 15, 30, 60" in item for item in validation.errors)


def test_native_minute_kline_query_uses_period_type_and_never_resamples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return [
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "tradedate": date(2026, 8, 6),
                    "bar_start_time": "2026-08-06 14:00:00",
                    "bar_end_time": "2026-08-06 15:00:00",
                    "open": 1310,
                    "high": 1314,
                    "low": 1300,
                    "close": 1309,
                    "amount": 123,
                    "volumn": 456,
                    "is_finalized": False,
                    "source_bar_count": 52,
                }
            ]

    class FakeDb:
        class Conn:
            def cursor(self, *_args, **_kwargs):
                return FakeCursor()

        conn = Conn()

        def close_db(self):
            return None

    monkeypatch.setattr(intraday_quote_provider, "StockInfoDbUtils", lambda **_kwargs: FakeDb())
    result = intraday_quote_provider.execute_intraday_quote_api(
        args={"mode": 1, "period": 60, "count": 1, "filter": "code = 600519.SH"},
        outputs=["code", "tradedate", "bar_start_time", "bar_end_time", "close", "amount", "is_finalized"],
    )

    assert result["status"] == "ok"
    assert result["rows"][0]["close"] == 1309
    assert result["sql_shape"]["source"] == "stored_fixed_period_kline"
    sql = str(captured["sql"])
    assert "s.kline_type = '60m'" in sql
    assert "s.period_minutes = 60" in sql
    assert "LAG(" not in sql
    assert "snapshot差分" not in sql


def test_latest_quote_uses_native_running_daily_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return [{"code": "600519", "tradedate": date(2026, 8, 6), "close": 1308.96, "amount": 3184000000}]

    class FakeDb:
        class Conn:
            def cursor(self, *_args, **_kwargs):
                return FakeCursor()

        conn = Conn()

        def close_db(self):
            return None

    monkeypatch.setattr(intraday_quote_provider, "StockInfoDbUtils", lambda **_kwargs: FakeDb())
    result = intraday_quote_provider.execute_intraday_quote_api(
        args={"mode": 2, "filter": "code = 600519.SH"},
        outputs=["code", "tradedate", "close", "amount"],
        latest_only=True,
    )

    assert result["status"] == "ok"
    assert result["sql_shape"]["selection"] == "latest_daily_bar_per_code"
    assert "s.kline_type = '1d'" in str(captured["sql"])
    assert result["rows"][0]["amount"] == 3184000000


def test_same_minute_cte_is_limited_to_native_one_minute_rows() -> None:
    sql = _minute_bar_cte(date_predicate="s.trade_date = %s")
    assert "s.kline_type = '1m'" in sql
    assert "s.period_minutes = 1" in sql
    assert "LAG(" not in sql


def test_minute_date_range_is_applied_before_the_per_code_count_window() -> None:
    predicate, params = _trade_date_predicate(
        intraday_quote_provider._filters_from_args(  # noqa: SLF001
            {"filter": "tradedate >= 2026-08-01 and tradedate <= 2026-08-06"}
        )
    )
    assert predicate == "s.trade_date >= %s AND s.trade_date <= %s"
    assert params == ["2026-08-01", "2026-08-06"]


def test_latest_quote_explicit_list_uses_list_size_without_global_default() -> None:
    policy = _latest_quote_limit_policy(
        args={"codes": ["600519.SH", "000858.SZ"]},
        filters=intraday_quote_provider._filters_from_args({"codes": ["600519.SH", "000858.SZ"]}),  # noqa: SLF001
    )
    assert policy["business_limit"] == 2
    assert policy["fetch_limit"] == 2

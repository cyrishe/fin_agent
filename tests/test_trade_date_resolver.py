from __future__ import annotations

from datetime import date

from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.models import ApiCall, ResultHandle
from src.experiments.staged_data_protocol.phase2.trade_date_resolver import TradeDateResolver
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class _Db:
    def __init__(self, rows):
        self.conn = self
        self.cursor_obj = _Cursor(rows)

    def cursor(self):
        return self.cursor_obj

    def close_db(self):
        return None


def _resolver():
    rows = [(date(2026, 7, 21),), (date(2026, 7, 22),), (date(2026, 7, 23),), (date(2026, 7, 24),), (date(2026, 7, 27),), (date(2026, 7, 28),)]
    return TradeDateResolver(
        db_factory=lambda **_: _Db(rows),
        today=lambda: date(2026, 7, 28),
    )


def test_exact_non_trade_date_falls_back_and_warns() -> None:
    call = parse_api_call(
        'r1 = stock.quote(filter = "code = 600519.SH", date = "2026-07-26", realtime = 0) '
        "-> code, tradedate, close"
    )

    resolved = _resolver().resolve(call)

    assert resolved.call.args["date"] == "2026-07-24"
    assert resolved.warnings == [
        "请求日期 2026-07-26 不是交易日，已使用前一个交易日 2026-07-24 的数据。"
    ]


def test_relative_offset_uses_calendar_sequence() -> None:
    call = parse_api_call(
        'r1 = stock.quote(filter = "code = 600519.SH and tradedate = -1", realtime = 0) '
        "-> code, tradedate, close"
    )

    resolved = _resolver().resolve(call)

    assert resolved.call.args["filter"] == "code = 600519.SH and tradedate = '2026-07-27'"
    assert resolved.warnings == []


def test_range_endpoints_are_each_floored_to_trade_days() -> None:
    call = parse_api_call(
        'r1 = stock.quote(start = "2026-07-21", end = "2026-07-26", realtime = 0) '
        "-> code, tradedate, close"
    )

    resolved = _resolver().resolve(call)

    assert resolved.call.args["start"] == "2026-07-21"
    assert resolved.call.args["end"] == "2026-07-24"
    assert resolved.warnings == [
        "请求日期 2026-07-26 不是交易日，已使用前一个交易日 2026-07-24 的数据。"
    ]


def test_non_market_report_date_is_not_rewritten() -> None:
    call = parse_api_call(
        'r1 = stock.financial_3_table(filter = "code = 600519.SH and report_period = 2026-07-26") '
        "-> code, report_period, revenue"
    )

    resolved = _resolver().resolve(call)

    assert resolved.call.args == call.args
    assert resolved.warnings == []


def test_runtime_exposes_date_adjustment_warning(monkeypatch) -> None:
    import src.services.finance_data_tool_runtime_service as runtime_module

    def fake_execute(call: ApiCall, previous_results):
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=["code", "tradedate"],
            data={"status": "ok", "rows": [{"code": "600519.SH", "tradedate": call.args["date"]}]},
        )

    monkeypatch.setattr(runtime_module, "execute_api_call", fake_execute)
    service = FinanceDataToolRuntimeService(trade_date_resolver=_resolver())
    result = service.execute_request(
        request=(
            'r1 = stock.quote(filter = "code = 600519.SH", date = "2026-07-26", '
            "realtime = 0) -> code, tradedate"
        )
    )

    assert result["ok"] is True
    assert result["call"]["args"]["date"] == "2026-07-24"
    assert result["execution"]["warnings"] == [
        "请求日期 2026-07-26 不是交易日，已使用前一个交易日 2026-07-24 的数据。"
    ]

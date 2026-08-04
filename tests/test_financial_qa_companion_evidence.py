from src.scenarios.financial_qa.companion_evidence import (
    FinancialQaCompanionEvidenceService,
)
from src.scenarios.financial_qa.service import FinancialQaCcService


def _quote_ref(*, code: str = "600519", name: str = "贵州茅台") -> dict:
    return {
        "api": "stock.quote",
        "goal": "查询最新行情",
        "row_count": 1,
        "schema": {
            "columns": [
                {"name": "code"},
                {"name": "name"},
                {"name": "close"},
                {"name": "pct"},
            ]
        },
        "sample": {
            "rows": [{
                "code": code,
                "name": name,
                "close": 1338.0,
                "pct": -1.74,
            }]
        },
    }


class _Runtime:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def execute_request(self, *, request: str):
        self.requests.append(request)
        return {
            "ok": True,
            "result": {
                "api": "stock.quote",
                "columns": [
                    "code", "name", "tradedate", "open", "high", "low",
                    "close", "volumn", "amount", "pct",
                ],
                "data": {
                    "rows": [
                        {
                            "code": "600519",
                            "name": "贵州茅台",
                            "tradedate": "2026-07-30",
                            "open": 1348.0,
                            "high": 1352.0,
                            "low": 1335.0,
                            "close": 1340.0,
                            "volumn": 100,
                        },
                        {
                            "code": "600519",
                            "name": "贵州茅台",
                            "tradedate": "2026-07-29",
                            "open": 1330.0,
                            "high": 1345.0,
                            "low": 1328.0,
                            "close": 1342.0,
                            "volumn": 90,
                        },
                    ],
                    "row_count": 2,
                },
            },
        }


def test_single_stock_quote_gets_deterministic_monthly_kline_companion() -> None:
    runtime = _Runtime()
    service = FinancialQaCompanionEvidenceService(finance_runtime=runtime)

    refs = service.build([_quote_ref()])

    assert len(runtime.requests) == 1
    assert 'filter = "code = 600519"' in runtime.requests[0]
    assert 'order = "tradedate desc"' in runtime.requests[0]
    assert "limit = 22" in runtime.requests[0]
    assert "realtime = 0" in runtime.requests[0]
    assert refs[0]["display_title"] == "近一个月日 K 走势"
    assert [row["tradedate"] for row in refs[0]["sample"]["rows"]] == [
        "2026-07-29",
        "2026-07-30",
    ]
    assert refs[0]["meta"] == {
        "origin": "automatic_companion",
        "presentation_only": True,
    }


def test_companion_is_not_added_for_multi_stock_or_existing_ohlc_series() -> None:
    runtime = _Runtime()
    service = FinancialQaCompanionEvidenceService(finance_runtime=runtime)
    multi_stock = {
        **_quote_ref(),
        "row_count": 2,
        "sample": {
            "rows": [
                _quote_ref(code="600519")["sample"]["rows"][0],
                _quote_ref(code="000001", name="平安银行")["sample"]["rows"][0],
            ]
        },
    }
    existing_series = {
        "api": "stock.quote",
        "sample": {
            "rows": [
                {"tradedate": "2026-07-29", "open": 1, "high": 2, "low": 1, "close": 2},
                {"tradedate": "2026-07-30", "open": 2, "high": 3, "low": 2, "close": 3},
            ]
        },
    }

    assert service.build([multi_stock]) == []
    assert service.build([existing_series]) == []
    assert runtime.requests == []


def test_close_only_history_for_the_same_stock_still_gets_ohlc_companion() -> None:
    runtime = _Runtime()
    service = FinancialQaCompanionEvidenceService(finance_runtime=runtime)
    close_history = {
        "api": "stock.quote",
        "row_count": 2,
        "sample": {
            "rows": [
                {"code": "600519", "name": "贵州茅台", "tradedate": "2026-07-29", "close": 1342},
                {"code": "600519", "name": "贵州茅台", "tradedate": "2026-07-30", "close": 1340},
            ]
        },
    }

    refs = service.build([_quote_ref(), close_history])

    assert len(refs) == 1
    assert len(runtime.requests) == 1


class _Session:
    def run_turn(self, **_kwargs):
        return {
            "session_id": "cc-session",
            "duration_ms": 12,
            "result": "贵州茅台最新价为 1338 元。",
            "result_refs": [_quote_ref()],
        }

    def close(self):
        return None


class _Companion:
    def build(self, _result_refs):
        return [{
            "api": "stock.quote",
            "display_title": "近一个月日 K 走势",
            "row_count": 2,
            "sample_complete": True,
            "schema": {
                "columns": [
                    "code", "name", "tradedate", "open", "high", "low", "close",
                ]
            },
            "sample": {
                "rows": [
                    {
                        "code": "600519",
                        "name": "贵州茅台",
                        "tradedate": "2026-07-29",
                        "open": 1,
                        "high": 2,
                        "low": 1,
                        "close": 2,
                    },
                    {
                        "code": "600519",
                        "name": "贵州茅台",
                        "tradedate": "2026-07-30",
                        "open": 2,
                        "high": 3,
                        "low": 2,
                        "close": 3,
                    },
                ]
            },
        }]


def test_financial_qa_surface_keeps_companion_out_of_answer_evidence_refs() -> None:
    service = FinancialQaCcService(
        enabled=True,
        session_service=_Session(),
        companion_evidence_service=_Companion(),
    )
    result = service.answer(
        thread_id=7,
        turn_id=8,
        owner_id="owner-a",
        user_text="贵州茅台现在多少钱？",
        dispatch_plan={
            "selected_agent": "investment_analyst",
            "turn_mode": "normal_qa",
            "entry": "agent_route",
        },
    )

    assert [block["block_type"] for block in result["surface_blocks"]] == [
        "narrative",
        "status",
        "data",
        "data",
    ]
    assert result["surface_blocks"][1]["data"]["role"] == "process"
    assert result["surface_blocks"][-1]["semantic"] == "finance.ohlcv"
    assert result["result_refs"] == [_quote_ref()]

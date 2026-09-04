from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from src.finance_api.models import FinanceQueryRequest
from src.finance_api.service import FinanceApiGateway


class _Engine:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def answer(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "summary": "贵州茅台最近三条行情已经取得。",
            "message": "贵州茅台最近三条行情已经取得。",
            "model_name": "deepseek-v4-flash",
            "financial_qa": {
                "duration_ms": 1234,
                "reasoning_effort": "low",
                "tool_calls": [{"tool": "finance_query"}],
                "result_refs": [
                    {
                        "api": "stock.quote",
                        "row_count": 3,
                        "result_ref": "dataref_should_not_be_public",
                    }
                ],
                "error": "",
            },
            "data": {
                "format": "row-dict",
                "results": [
                    {
                        "result_name": "r1",
                        "goal": "查询行情",
                        "api": "stock.quote",
                        "data_type": "table",
                        "schema": {"columns": [{"name": "close"}]},
                        "row_count": 3,
                        "result_ref": "dataref_should_not_be_public",
                        "rows": [
                            {"close": 1},
                            {"close": 2},
                            {"close": 3},
                        ],
                    }
                ],
            },
        }

    def close(self) -> None:
        self.closed = True


def test_gateway_data_mode_returns_rows_without_summary_and_caps_output() -> None:
    engine = _Engine()
    gateway = FinanceApiGateway(
        engine=engine,
        default_runtime="dsh",
        max_concurrency=1,
    )
    response = asyncio.run(
        gateway.execute(
            FinanceQueryRequest(
                query="贵州茅台最近行情",
                response_mode="data",
                max_rows=2,
                conversation_id="conversation-1",
            ),
            principal_id="client-a",
        )
    )

    assert response.ok is True
    assert response.summary is None
    assert response.data is not None
    assert response.data.results[0].rows == [{"close": 1}, {"close": 2}]
    assert response.data.results[0].truncated is True
    assert response.execution.truncated is True
    assert engine.calls[0]["data_only"] is True
    assert engine.calls[0]["isolated_request"] is False
    assert engine.calls[0]["include_response_data"] is True
    assert engine.calls[0]["response_data_max_rows"] == 2
    assert engine.calls[0]["runtime"] == "dsh"
    assert engine.calls[0]["owner_id"] == "finance-api:client-a"
    assert "dataref_should_not_be_public" not in response.model_dump_json()


def test_gateway_summary_mode_omits_structured_rows_and_isolates_principals() -> None:
    engine = _Engine()
    gateway = FinanceApiGateway(engine=engine, default_runtime="cc")

    first = asyncio.run(
        gateway.execute(
            FinanceQueryRequest(
                query="解释贵州茅台行情",
                response_mode="summary",
                conversation_id="same-public-id",
            ),
            principal_id="client-a",
        )
    )
    second = asyncio.run(
        gateway.execute(
            FinanceQueryRequest(
                query="继续",
                response_mode="summary",
                conversation_id="same-public-id",
            ),
            principal_id="client-b",
        )
    )

    assert first.summary
    assert first.data is None
    assert first.execution.result_count == 1
    assert first.execution.total_rows == 3
    assert first.execution.returned_rows == 0
    assert first.execution.apis == ["stock.quote"]
    assert engine.calls[0]["data_only"] is False
    assert engine.calls[0]["include_response_data"] is False
    assert engine.calls[0]["response_data_max_rows"] == 100
    assert engine.calls[0]["thread_id"] != engine.calls[1]["thread_id"]
    assert second.runtime == "cc"


def test_gateway_close_closes_shared_execution_core() -> None:
    engine = _Engine()
    gateway = FinanceApiGateway(engine=engine)
    gateway.close()
    assert engine.closed is True


def test_gateway_admits_ten_isolated_requests_concurrently() -> None:
    class _ConcurrentEngine(_Engine):
        def __init__(self) -> None:
            super().__init__()
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def answer(self, **kwargs: Any) -> dict[str, Any]:
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.05)
            try:
                return super().answer(**kwargs)
            finally:
                with self.lock:
                    self.active -= 1

    async def execute_all(gateway: FinanceApiGateway):
        return await asyncio.gather(
            *[
                gateway.execute(
                    FinanceQueryRequest(
                        query=f"独立查询 {index}",
                        response_mode="data",
                    ),
                    principal_id="client-a",
                )
                for index in range(10)
            ]
        )

    engine = _ConcurrentEngine()
    gateway = FinanceApiGateway(
        engine=engine,
        default_runtime="dsh",
        max_concurrency=10,
    )
    responses = asyncio.run(execute_all(gateway))

    assert engine.max_active == 10
    assert len({item["thread_id"] for item in engine.calls}) == 10
    assert len({item["turn_id"] for item in engine.calls}) == 10
    assert all(item["isolated_request"] is True for item in engine.calls)
    assert all(response.conversation_id is None for response in responses)

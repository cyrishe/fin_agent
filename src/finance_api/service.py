from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import datetime, timezone
from functools import partial
from typing import Any, Mapping

import anyio

from src.finance_api.models import FinanceQueryRequest, FinanceQueryResponse
from src.scenarios.financial_qa import FinancialQaCcService
from src.scenarios.financial_qa.runtime import normalize_financial_qa_runtime


def _trim(value: Any) -> str:
    return str(value or "").strip()


class FinanceApiGateway:
    """Public projection over the existing financial-QA execution core."""

    def __init__(
        self,
        *,
        engine: FinancialQaCcService | None = None,
        default_runtime: str | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self.engine = engine or FinancialQaCcService()
        self.default_runtime = normalize_financial_qa_runtime(
            default_runtime
            or os.environ.get("FINANCE_API_DEFAULT_RUNTIME")
            or "dsh"
        )
        self.max_concurrency = max(
            1,
            int(
                max_concurrency
                if max_concurrency is not None
                else os.environ.get("FINANCE_API_MAX_CONCURRENCY") or 10
            ),
        )
        self._semaphore: asyncio.Semaphore | None = None

    async def execute(
        self,
        request: FinanceQueryRequest,
        *,
        principal_id: str,
    ) -> FinanceQueryResponse:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
        async with self._semaphore:
            return await anyio.to_thread.run_sync(
                partial(
                    self._execute_sync,
                    request=request,
                    principal_id=principal_id,
                )
            )

    def _execute_sync(
        self,
        *,
        request: FinanceQueryRequest,
        principal_id: str,
    ) -> FinanceQueryResponse:
        request_id = f"fq_{uuid.uuid4().hex}"
        runtime = normalize_financial_qa_runtime(
            request.runtime or self.default_runtime
        )
        public_conversation_id = request.conversation_id
        scope_value = public_conversation_id or request_id
        scope_digest = hashlib.sha256(
            f"{principal_id}:{scope_value}".encode("utf-8")
        ).hexdigest()[:32]
        raw = self.engine.answer(
            thread_id=f"finance-api-{scope_digest}",
            turn_id=request_id,
            owner_id=f"finance-api:{principal_id}",
            user_text=request.query,
            dispatch_plan={
                "selected_agent": "investment_analyst",
                "turn_mode": "normal_qa",
                "entry": "agent_route",
                "semantic_turn": {
                    "ori_question": request.query,
                    "resolved_question": request.query,
                },
            },
            research_mode=request.research_mode,
            runtime=runtime,
            data_only=request.response_mode == "data",
            isolated_request=public_conversation_id is None,
            include_response_data=request.response_mode in {"data", "both"},
            response_data_max_rows=request.max_rows,
        )
        return self._public_response(
            raw,
            request=request,
            request_id=request_id,
            runtime=runtime,
            conversation_id=public_conversation_id,
        )

    @staticmethod
    def _public_response(
        raw: Mapping[str, Any],
        *,
        request: FinanceQueryRequest,
        request_id: str,
        runtime: str,
        conversation_id: str | None,
    ) -> FinanceQueryResponse:
        finance_meta = (
            raw.get("financial_qa")
            if isinstance(raw.get("financial_qa"), Mapping)
            else {}
        )
        include_summary = request.response_mode in {"summary", "both"}
        include_data = request.response_mode in {"data", "both"}
        raw_data = raw.get("data") if isinstance(raw.get("data"), Mapping) else {}
        projected_results: list[dict[str, Any]] = []
        for item in (raw_data.get("results") or []) if include_data else []:
            if not isinstance(item, Mapping):
                continue
            rows = [
                dict(row)
                for row in item.get("rows") or []
                if isinstance(row, Mapping)
            ][: request.max_rows]
            row_count = int(item.get("row_count") or len(rows))
            projected_results.append(
                {
                    "result_name": _trim(item.get("result_name")),
                    "goal": _trim(item.get("goal")),
                    "api": _trim(item.get("api")),
                    "data_type": _trim(item.get("data_type")) or "table",
                    "schema": (
                        dict(item.get("schema"))
                        if isinstance(item.get("schema"), Mapping)
                        else {}
                    ),
                    "row_count": row_count,
                    "rows_returned": len(rows),
                    "truncated": len(rows) < row_count,
                    "rows": rows,
                }
            )

        error_text = _trim(finance_meta.get("error"))
        summary = _trim(raw.get("summary") or raw.get("message"))
        result_metadata = projected_results or [
            dict(item)
            for item in finance_meta.get("result_refs") or []
            if isinstance(item, Mapping)
        ]
        total_rows = sum(
            int(item.get("row_count") or 0)
            for item in result_metadata
        )
        returned_rows = (
            sum(int(item["rows_returned"]) for item in projected_results)
            if include_data
            else 0
        )
        api_names = list(
            dict.fromkeys(
                _trim(item.get("api"))
                for item in result_metadata
                if _trim(item.get("api"))
            )
        )
        return FinanceQueryResponse.model_validate(
            {
                "id": request_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "ok": not bool(error_text),
                "query": request.query,
                "response_mode": request.response_mode,
                "runtime": runtime,
                "conversation_id": conversation_id,
                "summary": summary if include_summary and summary else None,
                "data": (
                    {"format": "row-dict", "results": projected_results}
                    if include_data
                    else None
                ),
                "execution": {
                    "duration_ms": int(finance_meta.get("duration_ms") or 0),
                    "worker_index": finance_meta.get("worker_index"),
                    "queue_wait_ms": int(finance_meta.get("queue_wait_ms") or 0),
                    "model_name": _trim(raw.get("model_name")),
                    "reasoning_effort": _trim(finance_meta.get("reasoning_effort")),
                    "tool_call_count": len(finance_meta.get("tool_calls") or []),
                    "result_count": len(result_metadata),
                    "total_rows": total_rows,
                    "returned_rows": returned_rows,
                    "truncated": (
                        any(bool(item["truncated"]) for item in projected_results)
                        if include_data
                        else False
                    ),
                    "apis": api_names,
                },
                "error": (
                    {"code": "finance_query_failed", "message": error_text}
                    if error_text
                    else None
                ),
            }
        )

    def close(self) -> None:
        self.engine.close()

    def prewarm(self) -> dict[str, Any]:
        if self.default_runtime != "dsh":
            return {"ok": True, "runtime": self.default_runtime, "skipped": True}
        service = getattr(self.engine, "dsh_session_service", None)
        if service is None or not bool(getattr(service, "enabled", False)):
            return {"ok": False, "runtime": "dsh", "enabled": False}
        return dict(service.prewarm())

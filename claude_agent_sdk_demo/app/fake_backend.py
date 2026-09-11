from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import uuid

from .contracts import BackendRequest
from .event_stream import BackendEvent


class FakeAgentBackend:
    """Deterministic offline backend used to verify SSE and harness behavior."""

    async def stream(self, request: BackendRequest) -> AsyncIterator[BackendEvent]:
        session_id = request.session_id or f"fake_{uuid.uuid4().hex[:12]}"
        yield BackendEvent("session.started", {"session_id": session_id, "model": "fake-model"}, source="fake")
        await asyncio.sleep(0)
        if request.enable_web_search:
            yield BackendEvent(
                "tool.started",
                {"tool_use_id": "fake_search_1", "tool_name": "web_search"},
                source="fake",
            )
            await asyncio.sleep(0)
            yield BackendEvent(
                "tool.completed",
                {"tool_use_id": "fake_search_1", "tool_name": "web_search", "is_error": False},
                source="fake",
            )
        text = (
            "[FAKE BACKEND] 已收到问题："
            f"{request.question}。启用 skills：{', '.join(request.skill_names) or '无'}。"
        )
        for chunk in (text[: max(1, len(text) // 2)], text[max(1, len(text) // 2) :]):
            await asyncio.sleep(0)
            if chunk:
                yield BackendEvent("assistant.delta", {"text": chunk}, source="fake")
        yield BackendEvent(
            "backend.result",
            {
                "status": "completed",
                "subtype": "success",
                "result": text,
                "structured_output": (
                    {"answer": text, "facts": [], "uncertainties": ["fake backend has no external evidence"]}
                    if request.output_mode == "research_json"
                    else None
                ),
                "session_id": session_id,
                "num_turns": 1,
                "duration_ms": 1,
                "total_cost_usd": 0.0,
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "stop_reason": "end_turn",
            },
            source="fake",
        )

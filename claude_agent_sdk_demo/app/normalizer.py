from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from typing import Any

from .event_stream import BackendEvent


class ClaudeEventNormalizer:
    """Translate Claude SDK messages into provider-neutral harness events."""

    def __init__(self) -> None:
        self._tool_by_index: dict[int, dict[str, str]] = {}
        self._tool_by_id: dict[str, str] = {}

    def normalize(self, message: Any) -> list[BackendEvent]:
        class_name = type(message).__name__
        if class_name == "StreamEvent":
            return self._stream_events(getattr(message, "event", {}) or {})
        if class_name == "SystemMessage":
            return self._system_events(message)
        if class_name == "AssistantMessage":
            return self._assistant_events(message)
        if class_name == "UserMessage":
            return self._user_events(message)
        if class_name == "ResultMessage":
            return self._result_events(message)
        if class_name == "RateLimitEvent":
            return [BackendEvent("provider.rate_limit", _safe_mapping(message), channel="diagnostic")]
        return [
            BackendEvent(
                "provider.message",
                {"message_type": class_name},
                channel="diagnostic",
            )
        ]

    def _stream_events(self, raw: dict[str, Any]) -> list[BackendEvent]:
        event_type = str(raw.get("type") or "")
        index = int(raw.get("index") or 0)
        if event_type == "content_block_start":
            block = raw.get("content_block") if isinstance(raw.get("content_block"), dict) else {}
            if block.get("type") in {"tool_use", "server_tool_use"}:
                tool_id = str(block.get("id") or "")
                tool_name = str(block.get("name") or "")
                self._tool_by_index[index] = {"id": tool_id, "name": tool_name}
                if tool_id:
                    self._tool_by_id[tool_id] = tool_name
                return [
                    BackendEvent(
                        "tool.started",
                        {"tool_use_id": tool_id, "tool_name": tool_name},
                    )
                ]
        if event_type == "content_block_delta":
            delta = raw.get("delta") if isinstance(raw.get("delta"), dict) else {}
            if delta.get("type") == "text_delta":
                text = str(delta.get("text") or "")
                return [BackendEvent("assistant.delta", {"text": text})] if text else []
            if delta.get("type") == "input_json_delta":
                tool = self._tool_by_index.get(index, {})
                partial_json = str(delta.get("partial_json") or "")
                return [
                    BackendEvent(
                        "tool.input.delta",
                        {
                            "tool_use_id": tool.get("id", ""),
                            "tool_name": tool.get("name", ""),
                            "chunk_bytes": len(partial_json.encode("utf-8")),
                        },
                        channel="diagnostic",
                    )
                ]
        if event_type == "content_block_stop" and index in self._tool_by_index:
            tool = self._tool_by_index[index]
            return [BackendEvent("tool.input.completed", dict(tool), channel="diagnostic")]
        return []

    def _system_events(self, message: Any) -> list[BackendEvent]:
        subtype = str(getattr(message, "subtype", "") or "")
        raw = getattr(message, "data", {})
        data = raw if isinstance(raw, dict) else {}
        if subtype == "init":
            return [
                BackendEvent(
                    "session.started",
                    {
                        "session_id": str(data.get("session_id") or data.get("sessionId") or ""),
                        "model": str(data.get("model") or ""),
                    },
                )
            ]
        return [BackendEvent("session.event", {"subtype": subtype}, channel="diagnostic")]

    def _assistant_events(self, message: Any) -> list[BackendEvent]:
        events: list[BackendEvent] = []
        for block in _iter_content(message):
            class_name = type(block).__name__
            if class_name == "ToolUseBlock":
                tool_id = str(getattr(block, "id", "") or "")
                tool_name = str(getattr(block, "name", "") or "")
                if tool_id:
                    self._tool_by_id[tool_id] = tool_name
                events.append(
                    BackendEvent(
                        "tool.requested",
                        {
                            "tool_use_id": tool_id,
                            "tool_name": tool_name,
                            "input_keys": sorted(
                                str(key)
                                for key in (
                                    getattr(block, "input", {})
                                    if isinstance(getattr(block, "input", {}), dict)
                                    else {}
                                )
                            ),
                        },
                        channel="diagnostic",
                    )
                )
        return events

    def _user_events(self, message: Any) -> list[BackendEvent]:
        events: list[BackendEvent] = []
        for block in _iter_content(message):
            if type(block).__name__ != "ToolResultBlock":
                continue
            tool_id = str(getattr(block, "tool_use_id", "") or "")
            events.append(
                BackendEvent(
                    "tool.completed",
                    {
                        "tool_use_id": tool_id,
                        "tool_name": self._tool_by_id.get(tool_id, ""),
                        "is_error": bool(getattr(block, "is_error", False)),
                    },
                )
            )
        return events

    @staticmethod
    def _result_events(message: Any) -> list[BackendEvent]:
        usage = getattr(message, "usage", None)
        return [
            BackendEvent(
                "backend.result",
                {
                    "status": "failed" if bool(getattr(message, "is_error", False)) else "completed",
                    "subtype": str(getattr(message, "subtype", "") or ""),
                    "result": str(getattr(message, "result", "") or ""),
                    "structured_output": getattr(message, "structured_output", None),
                    "session_id": str(getattr(message, "session_id", "") or ""),
                    "num_turns": int(getattr(message, "num_turns", 0) or 0),
                    "duration_ms": int(getattr(message, "duration_ms", 0) or 0),
                    "total_cost_usd": getattr(message, "total_cost_usd", None),
                    "usage": usage if isinstance(usage, dict) else {},
                    "stop_reason": str(getattr(message, "stop_reason", "") or ""),
                },
            )
        ]


def _iter_content(message: Any) -> Iterable[Any]:
    content = getattr(message, "content", [])
    return content if isinstance(content, list) else []


def _safe_mapping(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return {"value_type": type(value).__name__}

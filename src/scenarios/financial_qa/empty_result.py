"""The existing public zero-row response, shared with DSH's terminal handoff."""

from __future__ import annotations

import uuid
from typing import Any, Mapping


EMPTY_RESULT_PLUGIN = "fin-agent:empty-result"


def all_zero_result_summary(result_refs: list[dict[str, Any]]) -> str:
    if not result_refs or any(item.get("row_count") is None for item in result_refs):
        return ""
    try:
        if any(int(item.get("row_count")) != 0 for item in result_refs):
            return ""
    except (TypeError, ValueError):
        return ""
    goals = list(dict.fromkeys(
        str(item.get("goal") or "").strip()
        for item in result_refs
        if str(item.get("goal") or "").strip()
    ))
    scope = "；".join(goals[:2])
    prefix = f"本次已查询：{scope}。" if scope else "本次查询已完成。"
    return (
        f"{prefix}当前数据范围与查询条件下返回 0 条记录，"
        "因此无法从现有结果给出所问数值。"
    )


def empty_result_context(result_refs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prepare a native plugin message, not a fabricated model answer.

    This stays in the internal MCP trace until the loop's normal final boundary
    accepts it. The loop does not maintain a second copy of the response text.
    """
    summary = all_zero_result_summary(result_refs)
    if not summary:
        return None
    return {
        "id": str(uuid.uuid4()),
        "role": "user",
        "source": {"kind": "plugin", "plugin": EMPTY_RESULT_PLUGIN},
        "content": [{"type": "text", "text": summary}],
    }


def has_empty_result_handoff(events: list[dict[str, Any]], summary: str) -> bool:
    """Only an actually logged host response can authorize a blocked finish."""
    if not summary:
        return False
    for event in reversed(events):
        if event.get("type") != "user/message":
            continue
        message = event.get("data")
        if not isinstance(message, Mapping):
            continue
        if message.get("source") == {"kind": "plugin", "plugin": EMPTY_RESULT_PLUGIN}:
            return message.get("content") == [{"type": "text", "text": summary}]
    return False

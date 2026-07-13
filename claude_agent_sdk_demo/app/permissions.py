from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any


LOGGER = logging.getLogger("claude_demo.permissions")


@dataclass
class ToolAuditLog:
    records: list[dict[str, Any]] = field(default_factory=list)

    def add(self, event: str, tool_name: str, details: dict[str, Any] | None = None) -> None:
        record = {
            "event": event,
            "tool_name": tool_name,
            "details": _redact(details or {}),
        }
        self.records.append(record)
        LOGGER.info("tool_audit %s", json.dumps(record, ensure_ascii=False, default=str))


@dataclass(frozen=True)
class ToolPolicy:
    allowed_tools: frozenset[str]

    def check(self, tool_name: str, tool_input: dict[str, Any]) -> tuple[bool, str]:
        if tool_name not in self.allowed_tools:
            return False, f"tool is outside this run's capability set: {tool_name}"
        if tool_name.endswith("__web_search") or tool_name == "WebSearch":
            query = str(tool_input.get("query") or tool_input.get("search_term") or "").strip()
            if len(query) > 500:
                return False, "web search query exceeds 500 characters"
        return True, ""


def build_hooks(policy: ToolPolicy, audit: ToolAuditLog) -> dict[str, list[Any]]:
    """Create SDK hooks lazily so fake/offline mode needs no Claude dependency."""
    from claude_agent_sdk import HookMatcher

    async def pre_tool_use(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        tool_name = str(input_data.get("tool_name") or "")
        tool_input = input_data.get("tool_input") if isinstance(input_data.get("tool_input"), dict) else {}
        allowed, reason = policy.check(tool_name, tool_input)
        audit.add("pre_tool_use", tool_name, {"tool_use_id": tool_use_id, "input": tool_input, "allowed": allowed})
        if allowed:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    async def post_tool_failure(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        audit.add(
            "post_tool_failure",
            str(input_data.get("tool_name") or ""),
            {"tool_use_id": tool_use_id, "error": str(input_data.get("error") or "")[:1_000]},
        )
        return {}

    return {
        "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use])],
        "PostToolUseFailure": [HookMatcher(matcher=None, hooks=[post_tool_failure])],
    }


def _redact(value: Any) -> Any:
    secret_fragments = {"authorization", "api_key", "apikey", "token", "secret", "password"}
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if any(fragment in str(key).lower() for fragment in secret_fragments) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str) and len(value) > 4_000:
        return value[:4_000] + "…"
    return value

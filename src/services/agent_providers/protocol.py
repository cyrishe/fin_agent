from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Protocol, runtime_checkable


AgentEventSink = Callable[[Dict[str, Any]], None]
AGENT_RUN_PROTOCOL_VERSION = "agent.run.v1"


class AgentEventCoalescer:
    """Coalesce character-sized model deltas without changing event semantics."""

    DELTA_TYPES = {"agent_delta", "reasoning_delta", "reasoning_summary_delta", "plan_delta"}

    def __init__(self, *, min_chars: int = 256) -> None:
        self.min_chars = max(1, int(min_chars))
        self.pending: Optional[Dict[str, Any]] = None

    def push(self, event: Mapping[str, Any]) -> list[Dict[str, Any]]:
        current = dict(event)
        if str(current.get("type") or "") not in self.DELTA_TYPES:
            return [*self.flush(), current]
        if self.pending is not None and not self._same_stream(self.pending, current):
            ready = self.flush()
        else:
            ready = []
        if self.pending is None:
            self.pending = current
        else:
            self.pending["content"] = str(self.pending.get("content") or "") + str(current.get("content") or "")
        if len(str(self.pending.get("content") or "")) >= self.min_chars:
            ready.extend(self.flush())
        return ready

    def flush(self) -> list[Dict[str, Any]]:
        if self.pending is None:
            return []
        event = self.pending
        self.pending = None
        return [event]

    @staticmethod
    def _same_stream(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        left_meta = left.get("metadata") if isinstance(left.get("metadata"), Mapping) else {}
        right_meta = right.get("metadata") if isinstance(right.get("metadata"), Mapping) else {}
        return (
            str(left.get("source") or "") == str(right.get("source") or "")
            and str(left.get("type") or "") == str(right.get("type") or "")
            and str(left_meta.get("stage") or "") == str(right_meta.get("stage") or "")
            and str(left_meta.get("item_id") or "") == str(right_meta.get("item_id") or "")
        )


def normalize_agent_run_result(
    value: Mapping[str, Any] | None,
    *,
    provider: str,
    stage: str,
    session_id: str = "",
) -> Dict[str, Any]:
    """Return the stable execution envelope shared by every provider.

    ``final`` remains the existing Skill Output Schema. This function only
    normalizes runtime diagnostics and never changes business content.
    """
    source = dict(value or {})
    events = [dict(item) for item in source.get("events") or [] if isinstance(item, Mapping)]
    final = dict(source.get("final") or {}) if isinstance(source.get("final"), Mapping) else {}
    context_bundle = (
        dict(source.get("context_bundle") or {})
        if isinstance(source.get("context_bundle"), Mapping)
        else {}
    )
    llm_usage = dict(source.get("llm_usage") or {}) if isinstance(source.get("llm_usage"), Mapping) else {}
    return {
        **source,
        "protocol_version": AGENT_RUN_PROTOCOL_VERSION,
        "provider": str(provider or "").strip(),
        "stage": str(stage or "").strip(),
        "ok": bool(source.get("ok")),
        "error": str(source.get("error") or "").strip(),
        "timeout": bool(source.get("timeout")),
        "timeout_kind": str(source.get("timeout_kind") or "").strip(),
        "timeout_after_seconds": int(source.get("timeout_after_seconds") or 0),
        "events": events,
        "final": final,
        "session_id": str(source.get("session_id") or session_id or "").strip(),
        "provider_session_id": str(source.get("provider_session_id") or "").strip(),
        "raw_stdout": str(source.get("raw_stdout") or ""),
        "raw_stderr": str(source.get("raw_stderr") or ""),
        "last_message": str(source.get("last_message") or ""),
        "llm_usage": llm_usage,
        "context_bundle": context_bundle,
        "duration_ms": int(source.get("duration_ms") or 0),
    }


@runtime_checkable
class AgentSkillHarness(Protocol):
    """Provider-neutral contract used by slow, tool-capable agent runtimes."""

    provider_name: str

    def available(self) -> bool: ...

    def run_skill(
        self,
        *,
        skill_path: str,
        output_schema_path: str = "",
        user_request: str,
        context: Optional[Mapping[str, Any]] = None,
        session_id: str = "",
        stage: str = "",
        event_sink: Optional[AgentEventSink] = None,
    ) -> Dict[str, Any]: ...

    def run_turn(
        self,
        *,
        prompt: str,
        developer_instructions: str,
        output_schema: Mapping[str, Any],
        stage: str,
        event_sink: Optional[AgentEventSink] = None,
    ) -> Dict[str, Any]: ...

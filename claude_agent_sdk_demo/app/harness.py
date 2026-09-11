from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import re
import time

from .backend import AgentBackend
from .config import Settings
from .contracts import BackendRequest, RunRequest
from .event_stream import EventFactory, StreamEvent


class AgentHarness:
    """Provider-neutral run boundary: validation, deadlines, events, and finalization."""

    def __init__(self, backend: AgentBackend, settings: Settings) -> None:
        self.backend = backend
        self.settings = settings

    def available_skills(self) -> list[str]:
        skill_root = self.settings.workspace_dir / ".claude" / "skills"
        if not skill_root.exists():
            return []
        return sorted(path.parent.name for path in skill_root.glob("*/SKILL.md") if path.is_file())

    def validate_request(self, request: RunRequest) -> None:
        if len(request.question) > self.settings.max_question_chars:
            raise ValueError("question exceeds configured size limit")
        available = set(self.available_skills())
        unknown = sorted(set(request.skill_names) - available)
        if unknown:
            raise ValueError(f"unknown skills: {', '.join(unknown)}")
        if request.session_id and not self.settings.allow_session_resume:
            raise ValueError("session resume is disabled until the host application enforces session ownership")
        if request.enable_web_search and self.settings.web_search_backend == "disabled":
            raise ValueError("web search is disabled by server configuration")

    async def stream(self, request: RunRequest, *, run_id: str) -> AsyncIterator[StreamEvent]:
        factory = EventFactory(run_id)
        started_at = time.monotonic()
        self.validate_request(request)
        yield factory.make(
            "run.started",
            {
                "client_request_id": request.client_request_id,
                "skills": request.skill_names,
                "web_search": request.enable_web_search,
                "output_mode": request.output_mode,
            },
        )
        backend_request = BackendRequest(
            run_id=run_id,
            question=request.question,
            session_id=request.session_id,
            skill_names=request.skill_names,
            enable_web_search=request.enable_web_search,
            output_mode=request.output_mode,
            client_request_id=request.client_request_id,
        )
        iterator = self.backend.stream(backend_request).__aiter__()
        finished = False
        try:
            while True:
                elapsed = time.monotonic() - started_at
                remaining = self.settings.hard_timeout_seconds - elapsed
                if remaining <= 0:
                    raise TimeoutError("hard timeout exceeded")
                wait_seconds = min(float(self.settings.idle_timeout_seconds), remaining)
                try:
                    backend_event = await asyncio.wait_for(iterator.__anext__(), timeout=wait_seconds)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    raise TimeoutError("provider stream idle timeout exceeded") from exc
                if backend_event.type == "backend.result":
                    finished = True
                    final_type = "run.failed" if backend_event.data.get("status") == "failed" else "run.completed"
                    yield factory.make(
                        final_type,
                        {
                            **backend_event.data,
                            "harness_duration_ms": int((time.monotonic() - started_at) * 1000),
                        },
                        source=backend_event.source,
                    )
                    continue
                yield factory.make(
                    backend_event.type,
                    backend_event.data,
                    source=backend_event.source,
                    channel=backend_event.channel,
                )
            if not finished:
                yield factory.make(
                    "run.failed",
                    {
                        "error_code": "missing_final_result",
                        "message": "provider stream ended without a final result",
                        "harness_duration_ms": int((time.monotonic() - started_at) * 1000),
                    },
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            yield factory.make(
                "run.failed",
                {
                    "error_code": "timeout",
                    "message": str(exc),
                    "harness_duration_ms": int((time.monotonic() - started_at) * 1000),
                },
            )
        except Exception as exc:
            yield factory.make(
                "run.failed",
                {
                    "error_code": type(exc).__name__,
                    "message": _safe_error_message(exc),
                    "harness_duration_ms": int((time.monotonic() - started_at) * 1000),
                },
            )


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return type(exc).__name__
    message = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", message)
    message = re.sub(
        r"(?i)(authorization|api[_-]?key|token|secret|password)(\s*[:=]\s*)([^\s,;}]+)",
        r"\1\2[REDACTED]",
        message,
    )
    return message[:1_000]

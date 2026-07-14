from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import uuid
from typing import Any


@dataclass(frozen=True)
class BackendEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "claude"
    channel: str = "user"


@dataclass(frozen=True)
class StreamEvent:
    version: str
    event_id: str
    run_id: str
    seq: int
    type: str
    source: str
    channel: str
    timestamp: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventFactory:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._seq = 0

    def make(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        source: str = "harness",
        channel: str = "user",
    ) -> StreamEvent:
        self._seq += 1
        return StreamEvent(
            version="agent_stream.v1",
            event_id=f"evt_{uuid.uuid4().hex}",
            run_id=self.run_id,
            seq=self._seq,
            type=event_type,
            source=source,
            channel=channel,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            data=data or {},
        )


def encode_sse(event: StreamEvent) -> str:
    payload = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"), default=str)
    return f"id: {event.event_id}\nevent: {event.type}\ndata: {payload}\n\n"


def encode_sse_comment(comment: str = "heartbeat") -> str:
    safe = comment.replace("\r", " ").replace("\n", " ")
    return f": {safe}\n\n"

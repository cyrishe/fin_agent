from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class CustomToolRunTrace:
    """Append-only, human-readable diagnostic trace for one custom-tool turn."""

    SECRET_MARKERS = ("password", "secret", "token", "api_key", "apikey", "authorization", "cookie")
    SECTION_LABELS = {
        "request": "请求与会话上下文",
        "prepare": "运行准备",
        "design": "Design：需求理解与规格形成",
        "coding": "Coding：动态模块实现",
        "test": "测试与校验",
        "final": "结果与状态写回",
        "error": "错误与中断",
    }

    def __init__(self, *, run_id: str, root_dir: str = "data/runtime_traces/custom_tool") -> None:
        self.run_id = str(run_id or "unknown").strip() or "unknown"
        self.started_at = time.time()
        self.sequence = 0
        self.current_section = ""
        self.lock = threading.Lock()
        day = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        self.path = Path(root_dir) / day / f"{self.run_id}.txt"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "# Custom Tool Agent 全链路记录\n\n"
            f"- run_id: {self.run_id}\n"
            f"- started_at: {self._timestamp()}\n"
            "- format: 按业务阶段分段；每条记录包含绝对时间、相对耗时、来源和事件类型。\n"
            "- security: 密钥、令牌、Cookie 等字段自动脱敏。\n",
            encoding="utf-8",
        )

    def snapshot(self, name: str, payload: Any, *, section: str = "request", source: str = "system") -> None:
        self.record(
            {
                "source": source,
                "type": "snapshot",
                "content": str(name or "snapshot"),
                "metadata": {"payload": payload},
            },
            section=section,
        )

    def record(self, event: Mapping[str, Any], *, section: str = "") -> None:
        event_payload = dict(event or {})
        event_section = section or self._infer_section(event_payload)
        source = str(event_payload.get("source") or "unknown").strip() or "unknown"
        event_type = str(event_payload.get("type") or "event").strip() or "event"
        content = event_payload.get("content")
        metadata = event_payload.get("metadata")
        with self.lock:
            self.sequence += 1
            chunks: list[str] = []
            if event_section != self.current_section:
                self.current_section = event_section
                label = self.SECTION_LABELS.get(event_section, event_section or "其他事件")
                chunks.append(f"\n## {label}\n")
            elapsed_ms = int((time.time() - self.started_at) * 1000)
            chunks.append(
                f"\n### {self.sequence:06d} | {self._timestamp()} | +{elapsed_ms}ms | {source}/{event_type}\n"
            )
            if content not in (None, ""):
                chunks.append("\n**content**\n\n")
                chunks.append(self._format_value(content))
                chunks.append("\n")
            remaining = {
                key: value
                for key, value in event_payload.items()
                if key not in {"source", "type", "content", "metadata"}
            }
            if metadata not in (None, {}, []):
                remaining["metadata"] = metadata
            if remaining:
                chunks.append("\n**data**\n\n```json\n")
                chunks.append(json.dumps(self._redact(remaining), ensure_ascii=False, indent=2, default=str))
                chunks.append("\n```\n")
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write("".join(chunks))
                handle.flush()

    def finish(self, result: Any, *, error: str = "") -> None:
        if error:
            self.record(
                {"source": "system", "type": "run_failed", "content": error},
                section="error",
            )
        else:
            self.snapshot("final_result", result, section="final")
        elapsed_ms = int((time.time() - self.started_at) * 1000)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## 记录结束\n\n"
                f"- finished_at: {self._timestamp()}\n"
                f"- elapsed_ms: {elapsed_ms}\n"
                f"- event_count: {self.sequence}\n"
            )

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")

    @classmethod
    def _redact(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            result = {}
            for key, item in value.items():
                normalized = str(key).lower()
                result[str(key)] = "***REDACTED***" if any(marker in normalized for marker in cls.SECRET_MARKERS) else cls._redact(item)
            return result
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        if isinstance(value, tuple):
            return [cls._redact(item) for item in value]
        return value

    @classmethod
    def _format_value(cls, value: Any) -> str:
        if isinstance(value, (Mapping, list, tuple)):
            return "```json\n" + json.dumps(cls._redact(value), ensure_ascii=False, indent=2, default=str) + "\n```"
        return str(value)

    @staticmethod
    def _infer_section(event: Mapping[str, Any]) -> str:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        stage = str(metadata.get("stage") or event.get("stage") or "").strip().lower()
        event_type = str(event.get("type") or "").strip().lower()
        content = str(event.get("content") or "").lower()
        if event_type == "error" or "failed" in event_type:
            return "error"
        if stage in {"design", "coding"}:
            if event_type in {"test_result", "command_output"} or "test" in content or "测试" in content:
                return "test"
            return stage
        if event_type in {"stage_start", "context_ready", "tool_call", "turn_started"}:
            return "prepare"
        if event_type in {"stage_result", "turn_completed", "final"}:
            return "final"
        return "prepare"

import json
from pathlib import Path

from src.services.custom_tool_run_trace_service import CustomToolRunTrace


def test_trace_writes_timed_business_sections_and_all_events(tmp_path: Path) -> None:
    trace = CustomToolRunTrace(run_id="run_demo", root_dir=str(tmp_path))
    trace.snapshot("request", {"text": "创建工具", "api_token": "secret"})
    trace.record({"source": "harness", "type": "stage_start", "content": "coding", "metadata": {"stage": "coding"}})
    trace.record({"source": "codex", "type": "agent_delta", "content": "partial module", "metadata": {"stage": "coding"}})
    trace.record({"source": "model", "type": "test_result", "content": "测试完成", "metadata": {"stage": "coding"}})
    trace.finish({"status": "code_ready"})

    text = trace.path.read_text(encoding="utf-8")
    assert "请求与会话上下文" in text
    assert "Coding：动态模块实现" in text
    assert "测试与校验" in text
    assert "partial module" in text
    assert "api_token" in text
    assert "secret" not in text
    assert "+" in text and "ms" in text
    assert "event_count: 5" in text


def test_trace_formats_structured_payload_as_json(tmp_path: Path) -> None:
    trace = CustomToolRunTrace(run_id="json_demo", root_dir=str(tmp_path))
    trace.record({"source": "system", "type": "snapshot", "content": {"a": 1}}, section="request")

    text = trace.path.read_text(encoding="utf-8")
    assert json.dumps({"a": 1}, ensure_ascii=False, indent=2) in text

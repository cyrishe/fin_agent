import json
from pathlib import Path
import time

import fastjsonschema

from src.services.codex_exec_skill_harness import CodexCustomToolCoder, CodexSdkSkillHarness


SKILL_DIR = Path("src/skills/financial-tool-coding-v1")


def test_coding_skill_contract_uses_dynamic_modules_not_files() -> None:
    schema = json.loads((SKILL_DIR / "schema.json").read_text(encoding="utf-8"))
    properties = schema["properties"]

    assert "files" not in properties
    assert "implementation" in properties
    modules_schema = properties["implementation"]["properties"]["modules"]
    module_properties = modules_schema["items"]["properties"]
    assert {"module_id", "entrypoint", "functions", "source_code"}.issubset(module_properties)
    assert "maxItems" not in modules_schema
    assert "pattern" not in module_properties["module_id"]
    assert "maxItems" not in properties["tests"]


def test_coding_skill_uses_api_contract_without_database_prerequisites() -> None:
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    assert "Design、API Catalog 和 SDK 契约" in skill_text
    assert "不连接数据库、不验证数据库凭据" in skill_text
    assert "数据库连接或凭据故障属于运行环境问题，不能退回 Design" in skill_text
    assert "BUSINESS_DB_URL" not in skill_text


def test_coding_schema_is_compatible_with_strict_structured_output() -> None:
    schema = json.loads((SKILL_DIR / "schema.json").read_text(encoding="utf-8"))

    def inspect(node):
        if not isinstance(node, dict):
            return
        if "const" in node or "enum" in node:
            assert "type" in node, node
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, node
            properties = node.get("properties") or {}
            assert set(node.get("required") or []) == set(properties), node
        for value in node.values():
            if isinstance(value, dict):
                inspect(value)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)

    inspect(schema)
    assert schema["properties"]["sample_input_json"]["type"] == "string"
    test_properties = schema["properties"]["tests"]["items"]["properties"]
    assert test_properties["input_json"]["type"] == "string"
    assert test_properties["expected_json"]["type"] == "string"


def test_coding_skill_sample_matches_schema() -> None:
    payload = {
        "source": "model",
        "type": "final",
        "status": "code_ready",
        "message": "实现草稿已生成，等待系统测试。",
        "implementation": {
            "summary": "实现求和。",
            "entry_module": "main",
            "modules": [{
                "module_id": "main",
                "role": "动态执行入口",
                "language": "python",
                "entrypoint": "run",
                "functions": [
                    {"name": "sum_values", "responsibility": "计算合计。"},
                    {"name": "run", "responsibility": "执行入口。"},
                ],
                "source_code": "def run(inputs: dict) -> dict:\n    return {'total': sum(inputs['values'])}\n",
            }],
        },
        "tests": [{
            "test_id": "basic",
            "category": "happy_path",
            "status": "proposed",
            "input_json": "{\"values\":[1,2]}",
            "expected_json": "{\"total\":3}",
            "purpose": "验证求和。",
        }],
        "sample_input_json": "{\"values\":[1,2]}",
        "implementation_notes": [],
        "design_issues": [],
        "risks": [],
    }

    fastjsonschema.compile(json.loads((SKILL_DIR / "schema.json").read_text(encoding="utf-8")))(payload)


def test_coder_defaults_to_versioned_coding_skill_and_schema() -> None:
    coder = CodexCustomToolCoder()

    assert coder.skill_path == "src/skills/financial-tool-coding-v1/SKILL.md"
    assert coder.output_schema_path == "src/skills/financial-tool-coding-v1/schema.json"


def test_coder_distinguishes_runtime_failure_from_design_fix() -> None:
    class HarnessStub:
        def run_skill(self, **kwargs):
            return {"ok": False, "error": "stream disconnected", "events": [], "final": {}}

    result = CodexCustomToolCoder(harness=HarnessStub()).code({"tool_name": "demo"})

    assert result["status"] == "coding_failed"
    assert result["message"] == "实现未完成：Coding 连接中断，未取得有效结果。 当前设计已保留，可以重试。"
    assert result["error"] == {
        "code": "coding_connection_failed",
        "summary": "Coding 连接中断，未取得有效结果。",
    }
    assert result["error_detail"] == "stream disconnected"


def test_coder_summarizes_invalid_schema_without_exposing_raw_sdk_payload() -> None:
    detail = "Invalid schema for response_format codex_output_schema: In context=('properties', 'source'), schema must have a 'type' key. invalid_json_schema"

    result = CodexCustomToolCoder._failure_summary(detail)

    assert result == {
        "code": "coding_schema_invalid",
        "summary": "字段 source 缺少严格 Schema 所需的类型声明。",
    }


def test_sdk_idle_timeout_interrupts_even_when_stream_has_no_notification(tmp_path, monkeypatch) -> None:
    import openai_codex
    import openai_codex.types

    skill = tmp_path / "SKILL.md"
    schema = tmp_path / "schema.json"
    skill.write_text("Return a final object.", encoding="utf-8")
    schema.write_text(json.dumps({
        "type": "object",
        "additionalProperties": False,
        "required": ["source", "type"],
        "properties": {
            "source": {"type": "string", "enum": ["model"]},
            "type": {"type": "string", "enum": ["final"]},
        },
    }), encoding="utf-8")

    class FakeTurn:
        def __init__(self):
            self.interrupted = False

        def stream(self):
            while not self.interrupted:
                time.sleep(0.02)
            if False:
                yield None

        def interrupt(self):
            self.interrupted = True

    fake_turn = FakeTurn()

    class FakeThread:
        def turn(self, *args, **kwargs):
            return fake_turn

    class FakeCodex:
        def __init__(self, config=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def thread_start(self, **kwargs):
            return FakeThread()

    class Values:
        deny_all = "deny_all"
        read_only = "read_only"
        workspace_write = "workspace_write"
        full_access = "full_access"

    class Config:
        def __init__(self, **kwargs):
            pass

    class ReasoningSummary:
        @staticmethod
        def model_validate(value):
            return value

    class ContextBundle:
        def build(self, **kwargs):
            return {"bundle_dir": str(tmp_path)}

    monkeypatch.setattr(openai_codex, "ApprovalMode", Values)
    monkeypatch.setattr(openai_codex, "Sandbox", Values)
    monkeypatch.setattr(openai_codex, "Codex", FakeCodex)
    monkeypatch.setattr(openai_codex, "CodexConfig", Config)
    monkeypatch.setattr(openai_codex.types, "Personality", type("Personality", (), {"pragmatic": "pragmatic"}))
    monkeypatch.setattr(openai_codex.types, "ReasoningSummary", ReasoningSummary)
    harness = CodexSdkSkillHarness(
        cwd=str(tmp_path),
        timeout_seconds=1,
        hard_timeout_seconds=5,
        sandbox="read-only",
        context_bundle_service=ContextBundle(),
    )

    started = time.time()
    result = harness.run_skill(
        skill_path=str(skill),
        output_schema_path=str(schema),
        user_request="test",
        stage="coding",
    )

    assert result["ok"] is False
    assert result["timeout"] is True
    assert result["timeout_kind"] == "idle timeout"
    assert "after 1s" in result["error"]
    assert fake_turn.interrupted is True
    assert time.time() - started < 3

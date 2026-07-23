import json
from pathlib import Path
import time

import fastjsonschema

from src.services.codex_exec_skill_harness import CodexCustomToolCoder, CodexSdkSkillHarness


SKILL_DIR = Path("src/skills/financial-tool-development")
IMPLEMENTATION_DIR = SKILL_DIR / "skills" / "financial-tool-implementation"
CODING_PLAYBOOK = IMPLEMENTATION_DIR / "references" / "coding.md"


def test_coding_skill_contract_uses_dynamic_modules_not_files() -> None:
    schema = json.loads((IMPLEMENTATION_DIR / "schema.json").read_text(encoding="utf-8"))
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
    skill_text = (IMPLEMENTATION_DIR / "SKILL.md").read_text(encoding="utf-8") + CODING_PLAYBOOK.read_text(encoding="utf-8")

    assert "CONTEXT.design" in skill_text
    assert "API Catalog" in skill_text
    assert "custom_tool_sdk.finance_query" in skill_text
    assert "直接连接数据库" in skill_text
    assert "BUSINESS_DB_URL" not in skill_text


def test_coding_skill_defines_dynamic_module_instead_of_user_file() -> None:
    skill_text = (IMPLEMENTATION_DIR / "SKILL.md").read_text(encoding="utf-8") + CODING_PLAYBOOK.read_text(encoding="utf-8")
    contract_text = (IMPLEMENTATION_DIR / "references" / "coding-output-contract.md").read_text(encoding="utf-8")

    assert "不是 `.py` 文件" in skill_text
    assert "动态执行" in skill_text
    assert "系统保存并动态加载" in contract_text
    assert "source_code" in contract_text
    assert "不要在最终 JSON 中重复整份" in skill_text


def test_coding_schema_is_compatible_with_strict_structured_output() -> None:
    schema = json.loads((IMPLEMENTATION_DIR / "schema.json").read_text(encoding="utf-8"))

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
    assert test_properties["result"]["type"] == "string"
    assert test_properties["error"]["type"] == "string"


def test_coding_skill_sample_matches_schema() -> None:
    payload = {
        "message": "实现草稿已生成，等待系统测试。",
        "tool_contract": {
            "tool_name": "sum_values",
            "display_name": "求和工具",
            "description": "计算数字数组的合计。",
            "inputs": [{
                "name": "values",
                "type": "array",
                "required": True,
                "description": "待求和的数字数组。",
            }],
            "outputs": [{
                "name": "total",
                "type": "number",
                "required": True,
                "description": "合计值。",
            }],
        },
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
        "implementation_explanation": {
            "summary": "读取数字数组并返回合计。",
            "core_flow": ["读取 values", "计算合计", "组装输出"],
            "key_modules": ["run：动态入口与结果组装"],
        },
        "implementation_review": {
            "conclusion": "matches",
            "requirement_alignment": ["实现数字求和目标"],
            "design_alignment": ["输入 values 与输出 total 保持一致"],
            "deviations": [],
        },
        "technical_summary": {
            "status": "complete",
            "conclusion": "入口与样例技术运行通过。",
            "verified": ["动态入口可加载", "样例输出可序列化"],
            "unresolved": [],
        },
        "tests": [{
            "test_id": "basic",
            "input_json": "{\"values\":[1,2]}",
            "actual_output_json": "{\"total\":3}",
            "result": "passed",
            "checks": ["run 返回 dict", "total 等于 3"],
            "evidence": ["total=3"],
            "error": "",
            "purpose": "验证求和。",
        }],
        "sample_input_json": "{\"values\":[1,2]}",
        "implementation_notes": [],
        "issues": [],
    }

    fastjsonschema.compile(json.loads((IMPLEMENTATION_DIR / "schema.json").read_text(encoding="utf-8")))(payload)


def test_coder_defaults_to_focused_implementation_skill_and_schema() -> None:
    coder = CodexCustomToolCoder()

    assert coder.skill_path.endswith("skills/financial-tool-implementation/SKILL.md")
    assert coder.output_schema_path.endswith("skills/financial-tool-implementation/schema.json")


def test_coder_passes_confirmed_design_once_as_context() -> None:
    class HarnessStub:
        def __init__(self):
            self.kwargs = {}

        def run_skill(self, **kwargs):
            self.kwargs = kwargs
            return {
                "ok": True,
                "events": [],
                "final": {
                    "status": "need_design_fix",
                    "message": "缺少必要数据能力。",
                },
            }

    harness = HarnessStub()
    design = {"tool_name": "demo", "rules": [{"name": "核心规则"}]}
    coder = CodexCustomToolCoder(harness=harness)

    coder.code(design, requirement_text="一段不应重复注入的原始需求")

    assert harness.kwargs["context"] == {"design": design}
    assert "CONTEXT.design" in harness.kwargs["user_request"]
    assert "一段不应重复注入的原始需求" not in harness.kwargs["user_request"]
    assert json.dumps(design, ensure_ascii=False) not in harness.kwargs["user_request"]


def test_coder_distinguishes_runtime_failure_from_design_fix() -> None:
    class HarnessStub:
        def run_skill(self, **kwargs):
            return {"ok": False, "error": "stream disconnected", "events": [], "final": {}}

    result = CodexCustomToolCoder(harness=HarnessStub()).code({"tool_name": "demo"})

    assert result["ok"] is False
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


def test_sdk_resumes_provider_thread_with_minimal_followup_prompt(tmp_path, monkeypatch) -> None:
    import openai_codex

    skill = tmp_path / "SKILL.md"
    schema = tmp_path / "schema.json"
    skill.write_text("Return a final object.", encoding="utf-8")
    schema.write_text(json.dumps({
        "type": "object",
        "additionalProperties": False,
        "required": ["source", "type", "status", "message"],
        "properties": {
            "source": {"type": "string", "enum": ["model"]},
            "type": {"type": "string", "enum": ["final"]},
            "status": {"type": "string", "enum": ["ok"]},
            "message": {"type": "string"},
        },
    }), encoding="utf-8")
    calls = []

    class Payload:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Notification:
        def __init__(self, method, payload):
            self.method = method
            self.payload = payload

    class FakeTurn:
        def stream(self):
            yield Notification(
                "item/agentMessage/delta",
                Payload(delta='{"source":"model","type":"final","status":"ok","message":"done"}', item_id="answer"),
            )
            yield Notification("turn/completed", Payload())

    class FakeThread:
        def __init__(self, thread_id):
            self.id = thread_id

        def turn(self, turn_input, **kwargs):
            calls.append({"kind": "turn", "input": turn_input})
            return FakeTurn()

    class FakeCodex:
        def __init__(self, config=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def thread_start(self, **kwargs):
            calls.append({"kind": "start"})
            return FakeThread("provider-thread-1")

        def thread_resume(self, thread_id, **kwargs):
            calls.append({"kind": "resume", "thread_id": thread_id})
            return FakeThread(thread_id)

    class Config:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(openai_codex, "Codex", FakeCodex)
    monkeypatch.setattr(openai_codex, "CodexConfig", Config)
    monkeypatch.setenv("CUSTOM_TOOL_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))
    harness = CodexSdkSkillHarness(
        cwd=str(tmp_path),
        capabilities=None,
    )

    first = harness.run_skill(
        skill_path=str(skill),
        output_schema_path=str(schema),
        user_request="首次实现",
        context={"coding_feedback": "首次实现"},
        session_id="coding-session-1",
        stage="coding",
    )
    second = harness.run_skill(
        skill_path=str(skill),
        output_schema_path=str(schema),
        user_request="继续实现",
        context={
            "coding_feedback": "根据真实错误只修复相关函数",
            "design": {"tool_name": "should_not_inline_on_resume"},
            "_provider_session_id": first["provider_session_id"],
        },
        session_id="coding-session-1",
        stage="coding",
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["provider_session_id"] == "provider-thread-1"
    assert second["provider_session_id"] == "provider-thread-1"
    assert [item["kind"] for item in calls if item["kind"] != "turn"] == ["start", "resume"]
    second_turn_input = [item for item in calls if item["kind"] == "turn"][1]["input"]
    assert len(second_turn_input) == 1
    assert "根据真实错误只修复相关函数" in second_turn_input[0].text
    assert "# CONTEXT" not in second_turn_input[0].text
    assert "should_not_inline_on_resume" not in second_turn_input[0].text
    assert "首次实现" not in second_turn_input[0].text

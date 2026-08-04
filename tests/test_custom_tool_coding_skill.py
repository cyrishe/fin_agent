import json
from pathlib import Path
import time

import fastjsonschema

from src.services.codex_exec_skill_harness import CodexCustomToolCoder, CodexExecSkillHarness, CodexSdkSkillHarness


SKILL_DIR = Path("src/skills/financial-tool-development")
IMPLEMENTATION_DIR = SKILL_DIR / "skills" / "financial-tool-implementation"
CODING_PLAYBOOK = IMPLEMENTATION_DIR / "references" / "coding.md"


def test_coding_skill_contract_keeps_only_runtime_contract_and_natural_language_results() -> None:
    schema = json.loads((IMPLEMENTATION_DIR / "schema.json").read_text(encoding="utf-8"))
    properties = schema["properties"]

    assert set(properties) == {
        "tool_contract",
        "implementation_summary",
        "finance_tool_profile",
        "strategy_runtime_profile",
        "selection_output_profile",
    }
    assert properties["implementation_summary"]["type"] == "string"
    assert "implementation" not in properties
    assert "tests" not in properties


def test_coding_skill_uses_api_contract_without_database_prerequisites() -> None:
    skill_text = (IMPLEMENTATION_DIR / "SKILL.md").read_text(encoding="utf-8") + CODING_PLAYBOOK.read_text(encoding="utf-8")

    assert "Design" in skill_text
    assert "API Catalog" in skill_text
    assert "custom_tool_sdk.finance_query" in skill_text
    assert "BUSINESS_DB_URL" not in skill_text


def test_coding_prompt_always_contains_finance_api_call_contract() -> None:
    prompt = CodexExecSkillHarness()._build_prompt(
        skill_text="coding instructions",
        user_request="实现动态工具",
        context={"context_bundle": {"bundle_dir": "/tmp/bundle"}},
        structured_output=True,
        stage="coding",
    )

    assert "# REQUIRED FINANCE API CALL CONTRACT" in prompt
    assert "request=..." not in prompt
    assert "result_name" in prompt
    assert "不使用 `r1/r2`" not in prompt
    assert "## 五类通用 API" in prompt
    assert "def run(inputs: dict) -> dict:" not in prompt
    assert "api_catalog/subjects/<subject>/<dataview>.json" in prompt
    assert "stock.quote.kd_minute_volumn_avg" not in prompt
    assert 'tradedate = -1' not in prompt


def test_coding_skill_defines_dynamic_module_instead_of_user_file() -> None:
    skill_text = (IMPLEMENTATION_DIR / "SKILL.md").read_text(encoding="utf-8") + CODING_PLAYBOOK.read_text(encoding="utf-8")
    contract_text = (IMPLEMENTATION_DIR / "references" / "coding-output-contract.md").read_text(encoding="utf-8")

    assert "不是交付给用户的代码文件" in skill_text
    assert "动态执行" in skill_text
    assert "外层系统从隔离工作区回收" in contract_text
    assert "`code`" in contract_text


def test_coding_schema_does_not_require_display_only_execution_examples() -> None:
    schema = json.loads((IMPLEMENTATION_DIR / "schema.json").read_text(encoding="utf-8"))
    assert "execution_examples" not in schema["properties"]
    assert "execution_examples" not in schema["required"]


def test_coding_skill_sample_matches_schema() -> None:
    payload = {
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
            }, {
                "name": "key_process_info",
                "type": "object",
                "required": True,
                "description": "解释结果的核心中间信息。",
            }],
        },
        "implementation_summary": "读取数字数组，由 run 调用求和函数并返回 total。",
    }

    fastjsonschema.compile(json.loads((IMPLEMENTATION_DIR / "schema.json").read_text(encoding="utf-8")))(payload)


def test_coding_profile_keeps_family_small_and_old_payload_compatible() -> None:
    schema = json.loads((IMPLEMENTATION_DIR / "schema.json").read_text(encoding="utf-8"))
    validate = fastjsonschema.compile(schema)
    base = {
        "tool_contract": {
            "tool_name": "market_strength",
            "display_name": "大盘强度",
            "description": "计算大盘强度指标。",
            "inputs": [],
            "outputs": [],
        },
        "implementation_summary": "计算并验证大盘强度指标。",
    }

    validate(base)
    validate({
        **base,
        "finance_tool_profile": {
            "protocol": "finance_tool_profile.v1",
            "family": "analytics",
            "execution_shape": "aggregate_context",
            "output_semantic": "metric",
            "summary": "计算大盘强度，不输出投资决策。",
        },
    })
    assert schema["properties"]["finance_tool_profile"]["properties"]["family"]["enum"] == [
        "information",
        "analytics",
        "strategy",
        "action",
    ]
    assert schema["properties"]["finance_tool_profile"]["properties"]["execution_shape"]["enum"] == [
        "aggregate_context",
        "entity_local",
        "cross_sectional",
        "portfolio_stateful",
    ]
    assert schema["properties"]["finance_tool_profile"]["properties"]["output_semantic"]["enum"] == [
        "facts",
        "metric",
        "series",
        "assessment",
        "ranked_selection",
        "signal",
        "portfolio_target",
        "action_receipt",
    ]
    assert "finance_tool_profile" not in schema["required"]


def test_coding_skill_requires_compact_core_process_evidence_and_alignment_explanation() -> None:
    skill_text = (IMPLEMENTATION_DIR / "SKILL.md").read_text(encoding="utf-8")
    playbook_text = CODING_PLAYBOOK.read_text(encoding="utf-8")
    contract_text = (
        IMPLEMENTATION_DIR / "references" / "coding-output-contract.md"
    ).read_text(encoding="utf-8")
    combined = "\n".join((skill_text, playbook_text, contract_text))

    assert "DYNAMIC_TOOL_TEMPLATE.py" in combined
    assert "工具输出遵循 Design，并包含 `key_process_info`" in combined
    assert "核心中间结构、指标名称和值" in combined
    assert "静态检查" in combined
    assert "输入、输出、核心逻辑与数据范围" in combined
    assert "只有两项" in combined
    assert "不判断策略是否有效" in combined
    assert "当前 Coding 会话和同一工作区" in combined
    assert "一次性输出最新版结果" in combined


def test_coder_defaults_to_focused_implementation_skill_and_schema() -> None:
    coder = CodexCustomToolCoder()

    assert coder.skill_path.endswith("skills/financial-tool-implementation/SKILL.md")
    assert coder.output_schema_path.endswith("skills/financial-tool-implementation/schema.json")
    assert coder.harness.provider_name == "codex"


def test_exec_harness_resolves_schema_before_switching_to_bundle_cwd() -> None:
    harness = CodexExecSkillHarness(cwd=".")
    schema = harness._resolve_output_schema_file(
        skill_file=IMPLEMENTATION_DIR / "SKILL.md",
        output_schema_path=str(IMPLEMENTATION_DIR / "schema.json"),
    )

    assert schema is not None
    assert schema.is_absolute()


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

    coder.code(design, requirement_text="计算输入数字之和")

    assert harness.kwargs["context"] == {
        "design": design,
        "requirement_brief": "计算输入数字之和",
    }
    assert "CONTEXT.design" in harness.kwargs["user_request"]
    assert "计算输入数字之和" not in harness.kwargs["user_request"]
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


def test_coder_explains_turn_limit_and_last_permission_failure() -> None:
    result = CodexCustomToolCoder._failure_summary(
        "Claude structured output failed: error_max_turns; "
        "tool permission denied: only isolated Python compile/test commands are allowed"
    )

    assert result == {
        "code": "coding_turn_limit",
        "summary": "Coding Agent 的测试命令超出隔离执行范围，因此在最大执行轮次内没有完成。",
    }


def test_coder_uses_terminal_failure_kind_before_recoverable_tool_denial() -> None:
    result = CodexCustomToolCoder._failure_summary(
        "tool permission denied: only isolated Python compile/test commands are allowed",
        failure_kind="error_max_turns",
    )

    assert result == {
        "code": "coding_turn_limit",
        "summary": "Coding Agent 的测试命令超出隔离执行范围，因此在最大执行轮次内没有完成。",
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

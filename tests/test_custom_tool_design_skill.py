import json
from pathlib import Path

import pytest

from src.services.codex_exec_skill_harness import (
    CodexCustomToolDesigner,
    CodexExecSkillHarness,
    _SKILL_EXECUTION_DEVELOPER_INSTRUCTIONS,
)
from src.skill_runtime.schema_validator import SchemaValidationError, SchemaValidator


SKILL_ROOT = Path("src/skills/financial-tool-development")
DESIGN_SKILL = SKILL_ROOT / "skills/financial-tool-design/SKILL.md"
DESIGN_SCHEMA = SKILL_ROOT / "schema.json"


def _valid_design_result() -> dict:
    return {
        "design": "## 输入\n指定股票。\n\n## 流程\n1. 读取日线价格。\n2. 计算金叉并返回信号。",
    }


def test_design_skill_is_one_of_five_focused_skills() -> None:
    catalog = json.loads((SKILL_ROOT / "skills/catalog.json").read_text(encoding="utf-8"))
    names = [item["name"] for item in catalog["skills"]]

    assert names == [
        "financial-tool-requirement",
        "financial-tool-design",
        "financial-tool-flowchart",
        "financial-tool-implementation",
        "financial-tool-test-execution",
    ]
    assert DESIGN_SKILL.exists()
    assert not (SKILL_ROOT / "SKILL.md").exists()


def test_design_skill_only_defines_module_and_process_design() -> None:
    text = DESIGN_SKILL.read_text(encoding="utf-8")

    assert "结构化自然语言" in text
    assert "内部函数或模块" in text
    assert "未确定的格式、默认参数、边界处理和技术依赖不要自行定义" in text
    assert "不读取或指定具体 API" in text
    assert "不要绘制流程图、生成源代码或执行测试" in text
    assert "design_scenario" not in text
    assert "need_design_fix" not in text


def test_design_schema_accepts_complete_design_without_workflow_status() -> None:
    schema = json.loads(DESIGN_SCHEMA.read_text(encoding="utf-8"))
    payload = _valid_design_result()

    SchemaValidator().validate(payload, schema)
    assert "status" not in schema["properties"]
    assert "questions" not in schema["properties"]
    assert "change_summary" not in schema["properties"]
    assert schema["properties"]["design"]["type"] == "string"
    assert schema["required"] == ["design"]


def test_design_schema_accepts_optional_strict_finance_tool_profile() -> None:
    schema = json.loads(DESIGN_SCHEMA.read_text(encoding="utf-8"))
    payload = {
        **_valid_design_result(),
        "finance_tool_profile": {
            "protocol": "finance_tool_profile.v1",
            "family": "analytics",
            "execution_shape": "aggregate_context",
            "output_semantic": "metric",
            "summary": "计算大盘强度指标。",
        },
    }

    SchemaValidator().validate(payload, schema)
    profile = schema["properties"]["finance_tool_profile"]
    assert profile["additionalProperties"] is False
    assert "finance_tool_profile" not in schema["required"]

    payload["finance_tool_profile"]["internal_runtime_state"] = "leak"
    with pytest.raises(SchemaValidationError, match="unexpected field"):
        SchemaValidator().validate(payload, schema)


def test_structured_design_prompt_does_not_repeat_transport_or_api_catalog() -> None:
    prompt = CodexExecSkillHarness()._build_prompt(
        skill_text="design instructions",
        user_request="设计一个工具",
        context={},
        structured_output=True,
        stage="design",
    )

    assert "NDJSON" not in prompt
    assert "api_catalog/index.json" not in prompt
    assert "不要读取 API Catalog" in prompt


def test_sdk_skill_execution_is_scoped_to_selected_resources() -> None:
    assert "SKILL.md content is already present" in _SKILL_EXECUTION_DEVELOPER_INSTRUCTIONS
    assert "Do not inspect AGENTS.md, memory files, unrelated skills" in _SKILL_EXECUTION_DEVELOPER_INSTRUCTIONS


def test_designer_continues_to_design_when_requirement_has_no_questions() -> None:
    class Harness:
        def __init__(self) -> None:
            self.stages = []

        def run_skill(self, **kwargs):
            stage = kwargs["stage"]
            self.stages.append(stage)
            if stage == "design":
                return {
                    "ok": True,
                    "events": [],
                    "final": {
                        "design": "## 流程\n计算并返回金叉信号。",
                        "finance_tool_profile": {
                            "protocol": "finance_tool_profile.v1",
                            "family": "strategy",
                            "execution_shape": "entity_local",
                            "output_semantic": "signal",
                            "summary": "逐股识别金叉信号。",
                        },
                    },
                }
            if stage == "flowchart":
                return {
                    "ok": True,
                    "events": [],
                    "final": {"mermaid": "flowchart TD\nA[输入] --> B[输出]"},
                }
            return {
                "ok": True,
                "events": [],
                "final": {
                    "summary": "按收盘价金叉理解，是否按此实现？",
                    "requirement_brief": "识别指定股票的收盘价金叉并返回信号日期。",
                    "questions": [],
                },
            }

    harness = Harness()
    result = CodexCustomToolDesigner(harness=harness).design("创建一个金叉工具")

    assert harness.stages == ["requirement", "design", "flowchart"]
    assert result["ok"] is True
    assert "金叉信号" in result["design"]["document"]
    assert result["design"]["finance_tool_profile"] == {
        "protocol": "finance_tool_profile.v1",
        "family": "strategy",
        "execution_shape": "entity_local",
        "output_semantic": "signal",
        "summary": "逐股识别金叉信号。",
    }


def test_designer_surfaces_provider_failure_without_business_fallback() -> None:
    class FailedHarness:
        def run_skill(self, **kwargs):
            return {"ok": False, "error": "MaaS connection failed", "events": []}

    result = CodexCustomToolDesigner(harness=FailedHarness()).design("创建一个工具")

    assert result["ok"] is False
    assert result["error"] == "MaaS connection failed"

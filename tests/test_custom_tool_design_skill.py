import json
from pathlib import Path

import pytest

from src.services.codex_exec_skill_harness import CodexCustomToolDesigner, CodexExecSkillHarness
from src.services.custom_tool_service import CustomToolAgentService
from src.skill_runtime.schema_validator import SchemaValidationError, SchemaValidator


SKILL_DIR = Path("src/skills/financial-tool-requirement-design-v3")


def _design_result() -> dict:
    return json.loads((SKILL_DIR / "assets/sample-review.json").read_text(encoding="utf-8"))


def test_design_skill_bundle_has_paired_schema() -> None:
    skill_path = SKILL_DIR / "SKILL.md"
    schema_path = SKILL_DIR / "schema.json"
    harness = CodexExecSkillHarness(cwd=".")

    assert skill_path.exists()
    assert harness._resolve_output_schema_file(skill_file=skill_path) == schema_path
    assert json.loads(schema_path.read_text(encoding="utf-8"))["additionalProperties"] is False


def test_design_output_schema_accepts_valid_business_result() -> None:
    schema = json.loads((SKILL_DIR / "schema.json").read_text(encoding="utf-8"))

    SchemaValidator().validate(_design_result(), schema)


def test_design_output_schema_avoids_unsupported_composition_keywords() -> None:
    schema = json.loads((SKILL_DIR / "schema.json").read_text(encoding="utf-8"))
    unsupported = {"allOf", "not", "if", "then", "else", "dependentRequired", "dependentSchemas"}

    def collect_keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(collect_keys(item) for item in value.values()), set())
        if isinstance(value, list):
            return set().union(*(collect_keys(item) for item in value), set())
        return set()

    assert not unsupported & collect_keys(schema)


def test_design_output_schema_rejects_unknown_status_and_fields() -> None:
    schema = json.loads((SKILL_DIR / "schema.json").read_text(encoding="utf-8"))
    invalid_status = _design_result()
    invalid_status["status"] = "confirmed"
    extra_field = _design_result()
    extra_field["render_blocks"] = []

    with pytest.raises(SchemaValidationError, match="expected one of"):
        SchemaValidator().validate(invalid_status, schema)
    with pytest.raises(SchemaValidationError, match="unexpected field 'render_blocks'"):
        SchemaValidator().validate(extra_field, schema)


def test_structured_skill_prompt_does_not_repeat_transport_or_schema_instructions() -> None:
    prompt = CodexExecSkillHarness()._build_prompt(
        skill_text="design instructions",
        user_request="设计一个工具",
        context={},
        structured_output=True,
    )

    assert "NDJSON" not in prompt
    assert "output_schema" not in prompt
    assert "render_blocks" not in prompt
    assert "source=model,type=final" not in prompt


def test_custom_tool_designer_defaults_to_paired_skill_bundle() -> None:
    designer = CodexCustomToolDesigner(harness=CodexExecSkillHarness())

    assert designer.skill_path == str(SKILL_DIR / "SKILL.md")
    assert designer.output_schema_path == str(SKILL_DIR / "schema.json")


def test_structured_final_is_wrapped_as_internal_model_event() -> None:
    final = CodexExecSkillHarness._final_from_text(json.dumps(_design_result(), ensure_ascii=False))

    assert final["source"] == "model"
    assert final["type"] == "final"
    assert final["status"] == "review"


class _DesignResultStub:
    def __init__(self, result: dict) -> None:
        self.result = result

    def design(self, requirement_text, context=None, event_sink=None):
        return dict(self.result)


def test_custom_tool_flow_maps_clarification_to_collect_requirement() -> None:
    questions = [{
        "id": "Q1",
        "question": "输出候选列表还是排名？",
        "reason": "影响输出协议",
        "answer_type": "single_choice",
        "required": True,
        "options": [],
        "allow_custom": True,
    }]
    agent = CustomToolAgentService(
        designer=_DesignResultStub({
            "status": "clarification",
            "message": "需要确认输出形式。",
            "understanding": {"goal": "设计选股工具"},
            "questions": questions,
            "design": {"tool_name": "selector"},
            "existing_analysis": {},
        }),
        use_codex=False,
    )

    result = agent.start_create("设计选股工具")

    assert result["state"]["status"] == "collect_requirement"
    assert result["state"]["design_revision"] == 1
    assert result["design_status"] == "clarification"
    assert result["questions"] == questions


def test_custom_tool_flow_maps_review_to_trusted_confirmation_gate() -> None:
    design = _design_result()["design"]
    agent = CustomToolAgentService(
        designer=_DesignResultStub({
            "status": "review",
            "message": "设计已形成。",
            "understanding": _design_result()["understanding"],
            "questions": [],
            "design": design,
            "existing_analysis": _design_result()["existing_analysis"],
        }),
        use_codex=False,
    )

    result = agent.start_create("设计突破信号")

    assert result["state"]["status"] == "awaiting_design_confirmation"
    assert result["state"]["design_contract"] == design
    assert result["state"]["design_revision"] == 1
    assert result["design_artifact"]["design_artifact_id"]
    assert result["design_status"] == "review"

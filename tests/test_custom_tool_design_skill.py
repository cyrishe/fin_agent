import json
from pathlib import Path

import pytest

from src.services.codex_exec_skill_harness import CodexCustomToolDesigner, CodexExecSkillHarness
from src.services.custom_tool_service import CustomToolAgentService, CustomToolError
from src.services.custom_tool_design_protocol_service import (
    CustomToolDesignProtocolError,
    CustomToolDesignProtocolService,
)
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
    assert (SKILL_DIR / "revision-schema.json").exists()


def test_design_skill_uses_api_catalog_without_database_prerequisites() -> None:
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    assert "API Catalog" in skill_text
    assert "Design 不连接数据库、不读取凭据" in skill_text
    assert "不能成为向用户追问数据库信息的理由" in skill_text
    assert "BUSINESS_DB_URL" not in skill_text


def test_design_output_schema_accepts_valid_business_result() -> None:
    schema = json.loads((SKILL_DIR / "schema.json").read_text(encoding="utf-8"))

    SchemaValidator().validate(_design_result(), schema)


def test_golden_cross_design_is_a_valid_complete_protocol_snapshot() -> None:
    schema = json.loads((SKILL_DIR / "schema.json").read_text(encoding="utf-8"))
    payload = json.loads(Path("tests/fixtures/golden_cross_30_60_design.json").read_text(encoding="utf-8"))

    SchemaValidator().validate(payload, schema)
    assert payload["status"] == "review"
    assert {item["name"] for item in payload["design"]["modules"]} == {
        "日线数据适配", "金叉计算", "结果合成",
    }
    assert payload["design"]["data_requirements"][0]["source_ref"] == "stock.quote"


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
    assert designer.revision_schema_path == str(SKILL_DIR / "revision-schema.json")


def test_design_revision_schema_accepts_changed_fields_without_full_design() -> None:
    schema = json.loads((SKILL_DIR / "revision-schema.json").read_text(encoding="utf-8"))
    change_properties = schema["$defs"]["change"]["properties"]
    assert set(change_properties) == {"path", "value_json"}
    assert "maxItems" not in schema["properties"]["changes"]
    payload = {
        "status": "review",
        "change_summary": "将分析窗口改为 60 个交易日。",
        "questions": [],
        "changes": [{
            "path": "design.rules",
            "value_json": json.dumps([{
                "name": "观察窗口",
                "logic": "最近 60 个完整交易日内出现信号即为命中。",
                "parameters": ["lookback_days=60"],
                "on_missing_data": "不足时返回数据不足。",
            }], ensure_ascii=False),
        }],
    }

    SchemaValidator().validate(payload, schema)
    assert "design" not in payload
    assert "feedback" not in payload


def test_design_skill_declares_minimum_core_first_round_policy() -> None:
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    examples_text = (SKILL_DIR / "references/examples.md").read_text(encoding="utf-8")

    assert "最小核心闭环" in skill_text
    assert "最多 3 个必答问题、3 个模块、5 条核心规则" in skill_text
    assert "不主动增加排名、评分体系、回测、预警、推荐、交易动作" in skill_text
    assert "模糊形态识别" in examples_text
    assert "给一个股票，判断近期是否出现 M 顶特征" in examples_text


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


class _ContextRecordingDesigner(_DesignResultStub):
    def __init__(self, result: dict) -> None:
        super().__init__(result)
        self.calls = []

    def design(self, requirement_text, context=None, event_sink=None):
        self.calls.append({
            "requirement_text": requirement_text,
            "context": dict(context or {}),
        })
        return super().design(requirement_text, context=context, event_sink=event_sink)


class _RevisionFieldsDesigner(_ContextRecordingDesigner):
    pass


def test_create_first_round_is_isolated_and_labeled_for_codex_backend_and_ui() -> None:
    payload = _design_result()
    payload["existing_analysis"] = {
        "analyzed": True,
        "current_behavior": ["不应继承"],
        "gaps": ["不应继承"],
        "affected_areas": ["旧工具"],
        "evidence": [{"location": "old.py", "finding": "旧状态"}],
    }
    designer = _ContextRecordingDesigner(payload)
    agent = CustomToolAgentService(designer=designer, use_codex=False)

    result = agent.start_create(
        "创建一个放量突破识别工具",
        owner_id="user_a",
        state={
            "status": "awaiting_design_confirmation",
            "design_round": 9,
            "design_revision": 7,
            "design_artifact_id": "old_artifact",
        },
    )

    assert designer.calls == [{
        "requirement_text": "创建一个放量突破识别工具",
        "context": {
            "owner_id": "user_a",
            "state": {},
            "design_scenario": "create_first_round",
            "design_round": 1,
            "design_policy": {
                "scope_mode": "minimum_viable_core",
                "progressive_expansion": True,
                "implicit_adjacent_features": False,
                "first_round_budget": {
                    "required_questions": 3,
                    "modules": 3,
                    "rules": 5,
                    "outputs": 5,
                    "exceptions": 3,
                    "acceptance": 5,
                    "flow_steps": 7,
                },
            },
        },
    }]
    assert result["design_context"] == {
        "scenario": "create_first_round",
        "round": 1,
        "is_first_round": True,
    }
    assert result["state"]["design_scenario"] == "create_first_round"
    assert result["state"]["design_round"] == 1
    assert result["state"]["design_revision"] == 1
    assert result["state"]["design_artifact_id"] != "old_artifact"
    assert result["existing_analysis"] == {
        "analyzed": False,
        "current_behavior": [],
        "gaps": [],
        "affected_areas": [],
        "evidence": [],
    }


def test_create_first_round_requires_a_real_requirement() -> None:
    agent = CustomToolAgentService(designer=_DesignResultStub(_design_result()), use_codex=False)

    with pytest.raises(CustomToolError, match="创建工具时请先描述"):
        agent.start_create("  ")


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
    assert result["design_context"]["scenario"] == "create_first_round"
    assert result["design_context"]["round"] == 1


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


def test_revision_fields_are_merged_by_system_and_preserve_untouched_design() -> None:
    first_payload = _design_result()
    first = CustomToolAgentService(
        designer=_DesignResultStub(first_payload),
        use_codex=False,
    ).start_create("设计市场与个股突破信号", owner_id="user_a", turn_id=101)
    patch_designer = _RevisionFieldsDesigner({
        "status": "review",
        "protocol_mode": "revision_fields",
        "change_summary": "成交量倍数改为 2。",
        "questions": [],
        "changes": [{
            "path": "design.rules",
            "value_json": json.dumps([
                first_payload["design"]["rules"][0],
                {
                    "name": "个股放量突破规则",
                    "logic": "收盘价突破前 20 日高点且成交量大于前 20 日均量的 2 倍。",
                    "parameters": ["突破窗口=20 个交易日", "成交量倍数=2"],
                    "on_missing_data": "有效数据不足 21 个交易日时返回数据不足。",
                },
            ], ensure_ascii=False),
        }],
    })
    agent = CustomToolAgentService(designer=patch_designer, use_codex=False)

    revised = agent.continue_flow(
        "成交量倍数改成 2，其他不变",
        state=first["state"],
        owner_id="user_a",
        turn_id=102,
    )

    assert patch_designer.calls[0]["requirement_text"] == "成交量倍数改成 2，其他不变"
    context = patch_designer.calls[0]["context"]
    assert "state" not in context
    assert "feedback_ledger" not in context
    assert context["canonical_design"] == first_payload["design"]
    assert revised["design"]["inputs"] == first_payload["design"]["inputs"]
    assert revised["design"]["outputs"] == first_payload["design"]["outputs"]
    assert revised["design"]["rules"][1]["parameters"][-1] == "成交量倍数=2"
    assert revised["state"]["requirement_text"] == "设计市场与个股突破信号"
    assert [item["turn_id"] for item in revised["state"]["feedback_ledger"]] == [101, 102]
    assert revised["state"]["design_revision"] == 2


def test_design_protocol_only_rejects_a_change_that_cannot_be_parsed() -> None:
    protocol = CustomToolDesignProtocolService()
    canonical = _design_result()
    with pytest.raises(CustomToolDesignProtocolError, match="invalid value_json"):
        protocol.apply_revision(canonical, {
            "status": "review",
            "questions": [],
            "changes": [{
                "path": "design.tool_name",
                "value_json": '{broken',
            }],
        })


def test_design_protocol_accepts_multi_field_revision_without_patch_operations() -> None:
    protocol = CustomToolDesignProtocolService()
    canonical = _design_result()
    confirmed = canonical["understanding"]["confirmed_requirements"] + ["成交额按元读取并换算为万元"]
    exceptions = canonical["design"]["exceptions"] + [{"case": "成交额缺失", "behavior": "返回数据不足。"}]

    merged = protocol.apply_revision(canonical, {
        "status": "review",
        "questions": [],
        "changes": [
            {"path": "understanding.confirmed_requirements", "value_json": json.dumps(confirmed, ensure_ascii=False)},
            {"path": "design.exceptions", "value_json": json.dumps(exceptions, ensure_ascii=False)},
        ],
    })

    assert merged["understanding"]["confirmed_requirements"] == confirmed
    assert merged["design"]["exceptions"] == exceptions
    assert merged["design"]["inputs"] == canonical["design"]["inputs"]

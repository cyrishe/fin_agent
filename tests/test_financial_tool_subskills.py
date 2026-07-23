import json
from pathlib import Path

import fastjsonschema


ROOT = Path("src/skills/financial-tool-development")
SUBSKILLS_ROOT = ROOT / "skills"
EXPECTED_ORDER = [
    "financial-tool-requirement",
    "financial-tool-design",
    "financial-tool-flowchart",
    "financial-tool-implementation",
    "financial-tool-test-execution",
]


def test_financial_tool_subskill_catalog_has_only_the_five_core_nodes() -> None:
    catalog = json.loads((SUBSKILLS_ROOT / "catalog.json").read_text(encoding="utf-8"))
    entries = catalog["skills"]

    assert [item["name"] for item in entries] == EXPECTED_ORDER
    assert {item["default_complexity"] for item in entries} <= {"fastest", "fast", "mid", "high"}
    assert all("provider" not in item and "model" not in item for item in entries)
    assert all((SUBSKILLS_ROOT / item["path"]).exists() for item in entries)


def test_each_financial_tool_subskill_is_focused_and_loadable() -> None:
    for skill_name in EXPECTED_ORDER:
        skill_dir = SUBSKILLS_ROOT / skill_name
        skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        metadata = (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")

        assert f"name: {skill_name}" in skill_text
        assert "## 专业背景" in skill_text
        assert "TODO" not in skill_text
        assert "外层总控" not in skill_text
        assert "allow_implicit_invocation: false" in metadata

        schema_path = ROOT / "schema.json" if skill_name == "financial-tool-design" else skill_dir / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["type"] == "object"
        if skill_name == "financial-tool-design":
            assert schema["additionalProperties"] is True
        else:
            assert schema["additionalProperties"] is False
        fastjsonschema.compile(schema)


def test_model_protocols_do_not_emit_workflow_status_or_business_gate() -> None:
    schemas = [ROOT / "schema.json"] + [
        SUBSKILLS_ROOT / name / "schema.json"
        for name in EXPECTED_ORDER
        if name != "financial-tool-design"
    ]
    for schema_path in schemas:
        text = schema_path.read_text(encoding="utf-8")
        top = json.loads(text)
        assert "status" not in top["properties"]
        assert "gate_passed" not in text
        assert "business_ok" not in text


def test_requirement_questions_are_advisory_not_flow_gates() -> None:
    schema = json.loads((SUBSKILLS_ROOT / "financial-tool-requirement" / "schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["notice"]["items"]["type"] == "string"
    question = schema["properties"]["questions"]["items"]
    assert "required" not in question["properties"]
    assert set(question["properties"]) == {"question", "candidate"}
    assert question["properties"]["candidate"]["minItems"] == 1


def test_flowchart_is_independent_from_module_design() -> None:
    design_schema = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))
    flow_schema = json.loads((SUBSKILLS_ROOT / "financial-tool-flowchart" / "schema.json").read_text(encoding="utf-8"))

    assert design_schema["properties"]["design"]["type"] == "string"
    assert {"flow", "mermaid"} <= set(flow_schema["properties"])


def test_implementation_and_test_keep_technical_facts_separate_from_user_judgement() -> None:
    implementation = json.loads((SUBSKILLS_ROOT / "financial-tool-implementation" / "schema.json").read_text(encoding="utf-8"))
    test_plan = json.loads((SUBSKILLS_ROOT / "financial-tool-test-execution" / "schema.json").read_text(encoding="utf-8"))

    assert "status" not in implementation["properties"]
    assert "tool_contract" in implementation["required"]
    assert "issues" in implementation["properties"]
    assert set(test_plan["required"]) == {"summary", "next_action", "assessment", "cases", "presentation"}
    assert test_plan["properties"]["next_action"]["enum"] == ["run_tests", "finish"]
    assert set(test_plan["properties"]["cases"]["items"]["required"]) == {"name", "purpose", "request"}
    assert "execution_ok" not in test_plan["properties"]["cases"]["items"]["properties"]

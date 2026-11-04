import json
from pathlib import Path

import fastjsonschema

from src.services.codex_exec_skill_harness import (
    CodexCustomToolEditPlanner,
    CodexExecSkillHarness,
)


SKILL_DIR = Path(
    "src/skills/financial-tool-development/skills/financial-tool-edit-planning"
)


def _assets() -> tuple[dict, dict, dict]:
    manifest = {
        "tool_name": "ct_bottom_five_red_scan",
        "display_name": "底部五连阳温和放量选股",
        "description": "按既定规则筛选股票。",
        "revision": 3,
    }
    design = {
        "document": (
            "## 筛选规则\n"
            "最近5个交易日累计涨幅不低于3%，且不高于15%。\n"
            "换手率保持在1%至10%。\n\n"
            "## 输出\n返回命中股票及核心指标。"
        ),
        "mermaid": "flowchart TD\nA[读取行情] --> B[按既定阈值筛选] --> C[返回结果]",
    }
    schema = {
        "inputs": [],
        "outputs": [{"name": "matches", "type": "array"}],
    }
    return manifest, design, schema


class _Harness:
    def __init__(self, final: dict, *, ok: bool = True) -> None:
        self.final = final
        self.ok = ok
        self.calls = []

    def run_skill(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "ok": self.ok,
            "error": "provider unavailable" if not self.ok else "",
            "events": [{"source": "harness", "type": "stage_start"}],
            "final": self.final,
        }


def _local_threshold_plan() -> dict:
    return {
        "route": "local_patch",
        "affected_assets": ["design", "implementation"],
        "impact_summary": "只调整现有累计涨幅下限，公开输入输出和主流程不变。",
        "metadata_patch": {"display_name": None, "description": None},
        "design_replacements": [{
            "before": "最近5个交易日累计涨幅不低于3%，且不高于15%。",
            "after": "最近5个交易日累计涨幅不低于3.5%，且不高于15%。",
            "reason": "同步新的累计涨幅下限。",
        }],
        "implementation_instruction": (
            "只把累计涨幅下限从3%调整为3.5%，保持输入输出和其他规则不变；"
            "用3.4%与3.5%的合成样例验证边界。"
        ),
    }


def test_edit_planning_skill_and_schema_define_a_narrow_semantic_router() -> None:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    schema = json.loads((SKILL_DIR / "schema.json").read_text(encoding="utf-8"))

    assert "必须基于完整语义判断" in text
    assert "不得按词语、关键词或固定句式分类" in text
    assert "数据范围发生变化" in text
    assert "核心计算、筛选、组合或处理流程发生变化" in text
    assert "合成的符合/不符合样例" in text
    assert set(schema["properties"]) == {
        "route",
        "affected_assets",
        "impact_summary",
        "metadata_patch",
        "design_replacements",
        "implementation_instruction",
    }
    fastjsonschema.compile(schema)(_local_threshold_plan())


def test_planner_passes_system_resolved_assets_to_one_shot_edit_stage() -> None:
    harness = _Harness(_local_threshold_plan())
    planner = CodexCustomToolEditPlanner(harness=harness)
    manifest, design, schema = _assets()

    result = planner.plan(
        "把涨幅下限改成3.5%",
        manifest=manifest,
        design=design,
        schema=schema,
        context={
            "_workspace_identity": {"owner_id": "u1"},
            "existing_manifest": {"tool_name": "wrong_tool"},
        },
        run_id="edit-run-1",
    )

    assert result["ok"] is True
    assert result["route"] == "local_patch"
    assert len(harness.calls) == 1
    call = harness.calls[0]
    assert call["stage"] == "edit_plan"
    assert call["session_id"] == "edit-run-1"
    assert call["context"]["existing_manifest"] == manifest
    assert call["context"]["current_design"] == design
    assert call["context"]["existing_schema"] == schema
    assert call["context"]["_workspace_identity"] == {"owner_id": "u1"}


def test_exact_design_replacement_preserves_every_unaffected_asset() -> None:
    _, design, _ = _assets()
    original = json.loads(json.dumps(design, ensure_ascii=False))

    patched = CodexCustomToolEditPlanner.apply_design_replacements(
        design,
        _local_threshold_plan()["design_replacements"],
    )

    assert patched["document"].count("3.5%") == 1
    assert "不高于15%" in patched["document"]
    assert "换手率保持在1%至10%" in patched["document"]
    assert patched["mermaid"] == design["mermaid"]
    assert design == original


def test_non_unique_design_replacement_conservatively_uses_full_revision() -> None:
    plan = _local_threshold_plan()
    plan["design_replacements"] = [{
        "before": "既定",
        "after": "更新后的",
        "reason": "这段文字在 Design 中不唯一。",
    }]
    harness = _Harness(plan)
    planner = CodexCustomToolEditPlanner(harness=harness)
    manifest, design, schema = _assets()
    design["document"] += "\n继续使用既定处理方式。"

    result = planner.plan(
        "更新这条规则",
        manifest=manifest,
        design=design,
        schema=schema,
    )

    assert result["ok"] is True
    assert result["route"] == "full_revision"
    assert result["design_replacements"] == []
    assert "matched 2 locations" in result["fallback_reason"]


def test_local_contract_change_is_forced_to_full_revision() -> None:
    plan = _local_threshold_plan()
    plan["affected_assets"] = ["design", "implementation", "contract"]
    harness = _Harness(plan)
    planner = CodexCustomToolEditPlanner(harness=harness)
    manifest, design, schema = _assets()

    result = planner.plan(
        "再增加一个必填公开参数",
        manifest=manifest,
        design=design,
        schema=schema,
    )

    assert result["route"] == "full_revision"
    assert result["affected_assets"] == ["design", "implementation", "contract"]
    assert result["metadata_patch"] == {"display_name": None, "description": None}
    assert "公开契约" in result["fallback_reason"]


def test_metadata_only_edit_does_not_request_coding() -> None:
    plan = {
        "route": "local_patch",
        "affected_assets": ["metadata"],
        "impact_summary": "只改变用户看到的名称。",
        "metadata_patch": {"display_name": "五连阳筛选", "description": None},
        "design_replacements": [],
        "implementation_instruction": "无需修改代码。",
    }
    harness = _Harness(plan)
    planner = CodexCustomToolEditPlanner(harness=harness)
    manifest, design, schema = _assets()

    result = planner.plan(
        "把展示名称缩短为五连阳筛选",
        manifest=manifest,
        design=design,
        schema=schema,
    )

    assert result["ok"] is True
    assert result["route"] == "local_patch"
    assert result["affected_assets"] == ["metadata"]
    assert result["metadata_patch"] == {
        "display_name": "五连阳筛选",
        "description": None,
    }
    assert result["design_replacements"] == []
    assert result["implementation_instruction"] == ""


def test_ambiguous_business_change_stays_on_full_revision_route() -> None:
    plan = {
        "route": "full_revision",
        "affected_assets": ["design", "implementation", "contract"],
        "impact_summary": "更智能未说明筛选目标、输出口径和规则变化，需重新确认。",
        "metadata_patch": {"display_name": None, "description": None},
        "design_replacements": [],
        "implementation_instruction": "",
    }
    harness = _Harness(plan)
    planner = CodexCustomToolEditPlanner(harness=harness)
    manifest, design, schema = _assets()

    result = planner.plan(
        "让这个工具更智能一些",
        manifest=manifest,
        design=design,
        schema=schema,
    )

    assert result["ok"] is True
    assert result["route"] == "full_revision"
    assert "更智能" in result["impact_summary"]
    assert "fallback_reason" not in result


def test_provider_failure_does_not_fabricate_an_edit_plan() -> None:
    harness = _Harness({}, ok=False)
    planner = CodexCustomToolEditPlanner(harness=harness)
    manifest, design, schema = _assets()

    result = planner.plan(
        "修改阈值",
        manifest=manifest,
        design=design,
        schema=schema,
    )

    assert result["ok"] is False
    assert result["error"] == {
        "code": "edit_plan_failed",
        "summary": "provider unavailable",
    }


def test_edit_plan_prompt_reads_only_existing_assets() -> None:
    prompt = CodexExecSkillHarness()._build_prompt(
        skill_text="edit planning instructions",
        user_request="修改阈值",
        context={"context_bundle": {"bundle_dir": "/tmp/bundle"}},
        structured_output=True,
        stage="edit_plan",
    )

    assert "CONTEXT.design_ref" in prompt
    assert "existing_manifest" in prompt
    assert "existing_schema" in prompt
    assert "不要读取 API Catalog 或实现源码" in prompt

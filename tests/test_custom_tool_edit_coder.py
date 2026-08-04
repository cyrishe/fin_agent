from pathlib import Path

from src.services.codex_exec_skill_harness import (
    CodexCustomToolEditCoder,
    CodexExecSkillHarness,
)
from src.services.custom_tool_context_bundle_service import CustomToolContextBundleService


SKILL_PATH = Path(
    "src/skills/financial-tool-development/skills/financial-tool-edit-implementation/SKILL.md"
)


OLD_SOURCE = """def passes(value):
    return value >= 3.0


def run(inputs):
    value = float(inputs.get("value") or 0)
    return {"ok": True, "matched": passes(value), "key_process_info": {"value": value}}
"""

NEW_SOURCE = OLD_SOURCE.replace(">= 3.0", ">= 3.5")


def _design() -> dict:
    return {
        "tool_name": "ct_threshold_demo",
        "document": "累计值不低于3.5时命中，公开输入输出保持不变。",
    }


def _implementation(source: str = OLD_SOURCE) -> dict:
    return {
        "revision": 7,
        "modules": [{
            "module_id": "main",
            "role": "dynamic_entry",
            "language": "python",
            "entrypoint": "run",
            "source_code": source,
        }],
        "last_test": {"ok": True},
    }


def _final(source: str = NEW_SOURCE, *, case_count: int = 2) -> dict:
    cases = [
        {
            "input": {"value": 3.5},
            "actual": {
                "ok": True,
                "matched": True,
                "key_process_info": {"value": 3.5},
            },
            "status": "passed",
        },
        {
            "input": {"value": 3.4},
            "actual": {
                "ok": True,
                "matched": False,
                "key_process_info": {"value": 3.4},
            },
            "status": "passed",
        },
    ][:case_count]
    return {
        "tool_contract": {
            "tool_name": "ct_threshold_demo",
            "display_name": "阈值示例",
            "description": "阈值示例。",
            "inputs": [],
            "outputs": [],
        },
        "implementation_summary": "只调整阈值，并以3.5和3.4两个合成边界样例验证。",
        "implementation": {
            "entry_module": "main",
            "modules": [{"module_id": "main", "source_code": source}],
        },
        "code": source,
        "coding_test_evidence": {"cases": cases, "summary": "passed"},
    }


class _Harness:
    def __init__(self, final: dict, *, ok: bool = True) -> None:
        self.final = final
        self.ok = ok
        self.calls = []

    def run_skill(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "ok": self.ok,
            "error": "provider failed" if not self.ok else "",
            "events": [],
            "final": self.final,
            "provider_session_id": "provider-edit-1",
        }


def test_edit_coder_is_drop_in_and_uses_fastest_profile_hint() -> None:
    coder = CodexCustomToolEditCoder()

    assert coder.default_complexity == "fastest"
    assert coder.skill_path.endswith("financial-tool-edit-implementation/SKILL.md")
    assert coder.output_schema_path.endswith("financial-tool-implementation/schema.json")


def test_edit_coder_sends_only_locked_local_assets_to_edit_coding_stage() -> None:
    harness = _Harness(_final())
    coder = CodexCustomToolEditCoder(harness=harness)

    result = coder.code(
        _design(),
        requirement_text="很长的原始创建需求，不应再次发送。",
        context={
            "coding_feedback": "只把运行阈值从3.0改成3.5，用3.5和3.4验证。",
            "current_implementation": _implementation(),
            "_workspace_identity": {"owner_id": "u1", "tool_name": "ct_threshold_demo"},
            "selected_skills": ["unrelated-skill"],
            "test_feedback": {"unrelated": True},
            "requirement_brief": "不应进入局部 Coding。",
            "_agent_runtime": {
                "session_id": "edit-session-1",
                "provider_session_id": "provider-edit-existing",
            },
        },
    )

    assert result["ok"] is True
    assert len(harness.calls) == 1
    call = harness.calls[0]
    assert call["stage"] == "edit_coding"
    assert call["session_id"] == "edit-session-1"
    assert set(call["context"]) == {
        "design",
        "current_implementation",
        "implementation_instruction",
        "_provider_session_id",
        "_workspace_identity",
    }
    assert call["context"]["implementation_instruction"].startswith("只把运行阈值")
    assert call["context"]["_provider_session_id"] == "provider-edit-existing"
    assert "原始创建需求" not in str(call)
    assert "unrelated-skill" not in str(call)


def test_edit_coder_requires_an_existing_implementation_before_model_call() -> None:
    harness = _Harness(_final())
    result = CodexCustomToolEditCoder(harness=harness).code(
        _design(),
        implementation_instruction="修改阈值。",
        context={"current_implementation": {"revision": 0, "modules": []}},
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "edit_coding_implementation_missing"
    assert harness.calls == []


def test_edit_coder_rejects_a_no_op_result() -> None:
    harness = _Harness(_final(source=OLD_SOURCE))
    result = CodexCustomToolEditCoder(harness=harness).code(
        _design(),
        implementation_instruction="把阈值改成3.5。",
        context={"current_implementation": _implementation()},
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "edit_coding_no_change"


def test_edit_coder_accepts_one_batched_case_containing_multiple_boundary_samples() -> None:
    final = _final(case_count=1)
    final["coding_test_evidence"]["cases"][0] = {
        "input": {
            "stocks": [
                {"name": "反例", "value": 3.4},
                {"name": "正例", "value": 3.5},
            ],
        },
        "actual": {
            "ok": True,
            "matches": ["正例"],
            "key_process_info": {"threshold": 3.5, "checked": 2},
        },
        "status": "passed",
    }
    harness = _Harness(final)
    result = CodexCustomToolEditCoder(harness=harness).code(
        _design(),
        implementation_instruction="把阈值改成3.5，并构造正反例。",
        context={"current_implementation": _implementation()},
    )

    assert result["ok"] is True


def test_edit_coder_restores_system_owned_tool_identity_in_model_summary() -> None:
    final = _final()
    final["tool_contract"]["tool_name"] = "ct_other_tool"
    harness = _Harness(final)
    result = CodexCustomToolEditCoder(harness=harness).code(
        _design(),
        implementation_instruction="把阈值改成3.5，并构造正反例。",
        context={"current_implementation": _implementation()},
    )

    assert result["ok"] is True
    assert result["final"]["tool_contract"]["tool_name"] == "ct_threshold_demo"


def test_edit_coder_requires_at_least_one_recoverable_execution_case() -> None:
    harness = _Harness(_final(case_count=0))
    result = CodexCustomToolEditCoder(harness=harness).code(
        _design(),
        implementation_instruction="把阈值改成3.5，并构造正反例。",
        context={"current_implementation": _implementation()},
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "edit_coding_evidence_missing"


def test_edit_coding_bundle_reuses_workspace_without_materializing_api_catalog(tmp_path: Path) -> None:
    service = CustomToolContextBundleService(
        catalog_path=str(tmp_path / "missing-catalog.json"),
        root_dir=str(tmp_path / "bundles"),
    )
    bundle = service.build(
        stage="edit_coding",
        user_request="局部修改已有实现",
        context={
            "design": _design(),
            "current_implementation": _implementation(),
            "implementation_instruction": "把阈值从3.0改成3.5。",
            "_workspace_identity": {"owner_id": "u1"},
        },
        run_id="edit-coding-bundle-1",
    )

    bundle_dir = Path(bundle["bundle_dir"])
    prompt_context = service.prompt_context(bundle, {})
    assert bundle["coding_workspace"]["editable"] is True
    assert bundle["coding_workspace"]["module_files"]
    assert prompt_context["design_ref"] == "design.json"
    assert prompt_context["implementation_instruction"] == "把阈值从3.0改成3.5。"
    assert "api_index" not in bundle
    assert not (bundle_dir / "api_catalog").exists()
    assert (bundle_dir / "CODING_WORKSPACE.md").is_file()
    assert (bundle_dir / "dev_runtime" / "test_support.py").is_file()


def test_edit_coding_prompt_omits_full_api_research_contract() -> None:
    prompt = CodexExecSkillHarness()._build_prompt(
        skill_text="local edit coding instructions",
        user_request="局部修改",
        context={
            "context_bundle": {
                "bundle_dir": "/tmp/edit-bundle",
                "coding_workspace": {"editable": True, "module_files": ["implementation/main.py"]},
            }
        },
        structured_output=True,
        stage="edit_coding",
    )

    assert "implementation_instruction" in prompt
    assert "不要读取 API Catalog" in prompt
    assert "# REQUIRED FINANCE API CALL CONTRACT" not in prompt
    assert "api_catalog/CODING_GUIDE.md" not in prompt


def test_edit_implementation_skill_requires_minimal_change_and_synthetic_proof() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "不得重新理解需求、重做 Design" in text
    assert "不要扫描仓库或读取 API Catalog" in text
    assert "至少包含一个满足修订后规则的样例和一个不满足的样例" in text
    assert "不得顺手重构" in text

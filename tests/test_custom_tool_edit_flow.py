from pathlib import Path

import pytest

from src.services.custom_tool_service import (
    CustomToolAgentService,
    CustomToolError,
    CustomToolStoreService,
)


OWNER = "edit_owner"
TOOL = "ct_synthetic_momentum"


def _active_strategy(store: CustomToolStoreService) -> dict:
    design = {
        "manifest": {
            "tool_name": TOOL,
            "display_name": "合成动量筛选",
            "description": "筛选五日累计涨幅不低于3%的股票。",
            "visibility": "personal",
            "runtime": {
                "kind": "python_sandbox",
                "backend": "local_dev",
                "timeout_ms": 2000,
            },
        },
        "input_schema": {
            "type": "object",
            "required": ["stocks"],
            "properties": {"stocks": {"type": "array"}},
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "required": ["matches"],
            "properties": {"matches": {"type": "array"}},
        },
        "code": (
            "MIN_RETURN = 3.0\n\n"
            "def run(inputs: dict) -> dict:\n"
            "    rows = inputs.get('stocks') or []\n"
            "    return {'matches': [row for row in rows if row['return_5d'] >= MIN_RETURN]}\n"
        ),
        "modules": [{
            "module_id": "main",
            "language": "python",
            "entrypoint": "run",
            "source_code": (
                "MIN_RETURN = 3.0\n\n"
                "def run(inputs: dict) -> dict:\n"
                "    rows = inputs.get('stocks') or []\n"
                "    return {'matches': [row for row in rows if row['return_5d'] >= MIN_RETURN]}\n"
            ),
        }],
        "design_contract": {
            "tool_name": TOOL,
            "display_name": "合成动量筛选",
            "description": "筛选五日累计涨幅不低于3%的股票。",
            "document": "最近5个交易日累计涨幅不低于3%，返回命中股票。",
        },
    }
    store.save_draft(design, owner_id=OWNER)
    store.record_test(TOOL, {"ok": True, "execution_ok": True, "contract_ok": True})
    store.commit(TOOL, owner_ids=[OWNER])
    return store.load(TOOL)


class _Planner:
    def __init__(self, plan: dict) -> None:
        self.plan_value = plan
        self.calls = []

    def plan(self, request, **kwargs):
        self.calls.append({"request": request, **kwargs})
        return {
            "ok": True,
            "events": [{"source": "harness", "type": "stage_start", "content": "分析修改范围"}],
            **self.plan_value,
        }


class _ThresholdCoder:
    def __init__(self, *, business_ok: bool = True) -> None:
        self.business_ok = business_ok
        self.calls = []

    def code(self, design, *, requirement_text="", context=None, event_sink=None):
        self.calls.append({
            "design": dict(design),
            "requirement_text": requirement_text,
            "context": dict(context or {}),
        })
        passing_output = {
            "matches": [{"name": "符合样本", "return_5d": 3.6}],
            "key_process_info": {"threshold": 3.5, "checked": 2},
        }
        failing_output = {
            "ok": False,
            "error": "synthetic business failure",
            "matches": [],
        }
        code = (
            "MIN_RETURN = 3.5\n\n"
            "def run(inputs: dict) -> dict:\n"
            "    rows = inputs.get('stocks') or []\n"
            "    return {'matches': [row for row in rows if row['return_5d'] >= MIN_RETURN], "
            "'key_process_info': {'threshold': MIN_RETURN, 'checked': len(rows)}}\n"
        )
        return {
            "ok": True,
            "message": "局部实现和构造样本验证完成。",
            "events": [],
            "final": {
                "message": "局部实现完成。",
                # Deliberately drift every model-owned identity/contract field;
                # orchestration must restore the system-owned target contract.
                "tool_contract": {
                    "tool_name": "ct_wrong_new_tool",
                    "display_name": "错误的新工具",
                    "description": "错误改写",
                    "inputs": [{"name": "wrong", "type": "string", "required": True}],
                    "outputs": [{"name": "wrong", "type": "string", "required": True}],
                },
                "implementation": {
                    "summary": "只调整累计涨幅下限。",
                    "entry_module": "main",
                    "modules": [{
                        "module_id": "main",
                        "language": "python",
                        "entrypoint": "run",
                        "source_code": code,
                    }],
                },
                "implementation_explanation": {"summary": "累计涨幅下限由3%调整为3.5%。"},
                "implementation_review": {"conclusion": "matches"},
                "coding_test_evidence": {
                    "cases": [{
                        "input": {
                            "stocks": [
                                {"name": "不符合样本", "return_5d": 3.4},
                                {"name": "符合样本", "return_5d": 3.6},
                            ]
                        },
                        "actual": passing_output if self.business_ok else failing_output,
                        "status": "passed",
                    }]
                },
                "tests": [],
                "execution_examples": [],
                "implementation_notes": [],
                "issues": [],
            },
        }


def _local_plan() -> dict:
    return {
        "route": "local_patch",
        "affected_assets": ["design", "implementation"],
        "impact_summary": "只调整五日累计涨幅下限，工具身份和公开契约不变。",
        "metadata_patch": {"display_name": None, "description": None},
        "design_replacements": [{
            "before": "最近5个交易日累计涨幅不低于3%，返回命中股票。",
            "after": "最近5个交易日累计涨幅不低于3.5%，返回命中股票。",
            "reason": "同步新的累计涨幅下限。",
        }],
        "implementation_instruction": (
            "只把累计涨幅下限从3%改为3.5%；用3.4%的反例和3.6%的正例验证。"
        ),
    }


def test_local_edit_builds_verified_candidate_without_replacing_active_tool(tmp_path: Path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    active = _active_strategy(store)
    planner = _Planner(_local_plan())
    coder = _ThresholdCoder()
    service = CustomToolAgentService(
        store=store,
        edit_planner=planner,
        edit_coder=coder,
        use_codex=False,
    )

    result = service.start_edit(
        TOOL,
        "把累计涨幅下限从3%改为3.5%",
        owner_id=OWNER,
        owner_ids=[OWNER],
    )

    assert len(planner.calls) == 1
    assert len(coder.calls) == 1
    assert coder.calls[0]["context"]["coding_feedback"].startswith("只把累计涨幅下限")
    assert "3.5%" in coder.calls[0]["design"]["document"]
    assert result["coding_status"] == "implemented"
    assert result["test_result"]["execution_ok"] is True
    assert result["test_result"]["evidence_source"] == "isolated_synthetic_fixture"
    assert result["edit_summary"]["base_revision"] == 1
    assert result["edit_summary"]["candidate_revision"] == 2
    assert result["edit_summary"]["verification"]["status"] == "passed"
    candidate = store.load_revision(TOOL, 2)
    assert candidate["manifest"]["tool_name"] == TOOL
    assert candidate["manifest"]["display_name"] == active["manifest"]["display_name"]
    assert candidate["input_schema"] == active["input_schema"]
    assert candidate["output_schema"] == active["output_schema"]
    assert "MIN_RETURN = 3.5" in candidate["code"]
    assert store.load(TOOL)["manifest"]["current_revision"] == 1
    assert "MIN_RETURN = 3.0" in store.load(TOOL)["code"]

    activated = service.continue_flow_action(
        "custom_tool.activate_draft",
        state=result["state"],
        expected_revision=2,
        owner_id=OWNER,
    )

    assert activated["activation"]["implementation_revision"] == 2
    assert "MIN_RETURN = 3.5" in store.load(TOOL)["code"]


def test_metadata_only_edit_skips_coding_and_preserves_code(tmp_path: Path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    active = _active_strategy(store)
    planner = _Planner({
        "route": "local_patch",
        "affected_assets": ["metadata"],
        "impact_summary": "只缩短展示名称。",
        "metadata_patch": {"display_name": "动量筛选", "description": None},
        "design_replacements": [],
        "implementation_instruction": "",
    })
    service = CustomToolAgentService(
        store=store,
        edit_planner=planner,
        edit_coder=None,
        use_codex=False,
    )

    result = service.start_edit(
        TOOL,
        "把名称改短一些，叫动量筛选",
        owner_id=OWNER,
        owner_ids=[OWNER],
    )

    assert result["test_result"]["evidence_source"] == "edit_invariant_checks"
    assert result["tool"]["manifest"]["display_name"] == "动量筛选"
    assert result["tool"]["code"] == active["code"]
    assert store.load(TOOL)["manifest"]["display_name"] == "合成动量筛选"


def test_failed_synthetic_business_result_cannot_be_activated(tmp_path: Path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    _active_strategy(store)
    service = CustomToolAgentService(
        store=store,
        edit_planner=_Planner(_local_plan()),
        edit_coder=_ThresholdCoder(business_ok=False),
        use_codex=False,
    )

    result = service.start_edit(
        TOOL,
        "把累计涨幅下限从3%改为3.5%",
        owner_id=OWNER,
        owner_ids=[OWNER],
    )

    assert result["test_result"]["execution_ok"] is False
    assert result["edit_summary"]["verification"]["status"] == "failed"
    with pytest.raises(CustomToolError, match="尚未通过聚焦验证"):
        service.continue_flow_action(
            "custom_tool.activate_draft",
            state=result["state"],
            expected_revision=2,
            owner_id=OWNER,
        )
    assert store.load(TOOL)["manifest"]["current_revision"] == 1


def test_full_revision_plan_reuses_design_confirmation_flow_with_edit_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    _active_strategy(store)
    service = CustomToolAgentService(
        store=store,
        edit_planner=_Planner({
            "route": "full_revision",
            "affected_assets": ["design", "implementation", "contract"],
            "impact_summary": "新增公开输入并改变整体策略，需要完整确认。",
            "metadata_patch": {"display_name": None, "description": None},
            "design_replacements": [],
            "implementation_instruction": "",
        }),
        use_codex=False,
    )
    captured = {}

    def fake_start_create(requirement_text, **kwargs):
        captured["requirement_text"] = requirement_text
        captured.update(kwargs)
        return {"message": "进入完整设计", "state": kwargs["state"], "events": []}

    monkeypatch.setattr(service, "start_create", fake_start_create)

    result = service.start_edit(
        TOOL,
        "增加风险偏好输入并重做组合规则",
        owner_id=OWNER,
        owner_ids=[OWNER],
    )

    assert result["edit_plan"]["route"] == "full_revision"
    assert captured["state"]["edit_target"]["tool_name"] == TOOL
    assert captured["state"]["edit_target"]["base_revision"] == 1
    assert captured["requirement_text"] == "增加风险偏好输入并重做组合规则"

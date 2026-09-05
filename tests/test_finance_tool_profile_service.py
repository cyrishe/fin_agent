from pathlib import Path

import pytest

from src.services.custom_tool_service import (
    CustomToolAgentService,
    CustomToolFinanceProfileError,
    CustomToolRuntimeService,
    CustomToolStoreService,
)
from src.services.finance_tool_profile_service import (
    ACTION_EXECUTION_POLICY,
    FINANCE_TOOL_PROFILE_PROTOCOL,
    FinanceToolProfileError,
    FinanceToolProfileService,
)


ANALYTICS_PROFILE = {
    "family": "analytics",
    "execution_shape": "aggregate_context",
    "output_semantic": "assessment",
    "summary": "计算并解释大盘强度。",
}


def _bundle(*, profile=None) -> dict:
    result = {
        "manifest": {
            "tool_name": "ct_market_strength",
            "display_name": "大盘强度",
            "description": "计算大盘强度。",
            "visibility": "personal",
            "capabilities": ["custom_tool"],
        },
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
        "code": "def run(inputs): return {'strength': 60}",
    }
    if profile is not None:
        result["finance_tool_profile"] = profile
    return result


def test_analytics_profile_is_canonical_but_does_not_create_capability() -> None:
    normalized = FinanceToolProfileService().normalize(
        {**ANALYTICS_PROFILE, "ignored_display_hint": "blue"}
    )

    assert normalized == {
        "protocol": FINANCE_TOOL_PROFILE_PROTOCOL,
        **ANALYTICS_PROFILE,
    }
    assert "capabilities" not in normalized


def test_strategy_profile_does_not_require_optional_runtime_companion() -> None:
    strategy_profile = {
        "family": "strategy",
        "execution_shape": "cross_sectional",
        "output_semantic": "signal",
    }

    normalized = FinanceToolProfileService().normalize(strategy_profile)
    assert normalized["family"] == "strategy"
    assert normalized["output_semantic"] == "signal"


def test_runtime_companion_requires_strategy_profile() -> None:
    with pytest.raises(FinanceToolProfileError, match="family=strategy"):
        FinanceToolProfileService().normalize(
            ANALYTICS_PROFILE,
            strategy_runtime_profile={"protocol": "strategy_runtime_profile.v1"},
        )


def test_selection_companion_canonicalizes_ranked_selection_semantic() -> None:
    normalized = FinanceToolProfileService().normalize(
        {
            "family": "strategy",
            "execution_shape": "cross_sectional",
            "output_semantic": "signal",
        },
        strategy_runtime_profile={"protocol": "strategy_runtime_profile.v1"},
        selection_output_profile={
            "candidate_path": "selected",
            "symbol_field": "symbol",
        },
    )

    assert normalized["output_semantic"] == "ranked_selection"


def test_missing_profile_keeps_legacy_revision_compatible() -> None:
    assert (
        FinanceToolProfileService().normalize(
            None,
            strategy_runtime_profile={"protocol": "strategy_runtime_profile.v1"},
        )
        is None
    )


def test_runtime_keeps_structured_error_for_missing_legacy_tool(
    tmp_path: Path,
) -> None:
    store = CustomToolStoreService(
        root_dir=str(tmp_path / "tools"), backend="filesystem"
    )
    runtime = CustomToolRuntimeService(
        store=store,
        runtime_root=str(tmp_path / "runtime"),
    )

    result = runtime.run("ct_missing", {}, owner_ids=["user_a"])

    assert result["ok"] is False
    assert result["meta"]["failure_kind"] == "permission_or_lifecycle_error"
    assert "custom tool not found" in result["error"]


def test_action_is_canonical_planned_but_never_implementable() -> None:
    profile = FinanceToolProfileService().normalize(
        {
            "family": "action",
            "execution_shape": "portfolio_stateful",
            "output_semantic": "action_receipt",
            "execution_policy": "executable",
        }
    )

    assert profile["execution_policy"] == ACTION_EXECUTION_POLICY
    with pytest.raises(FinanceToolProfileError, match="design-only"):
        FinanceToolProfileService.assert_implementation_allowed(profile)


def test_filesystem_revision_persists_profile_and_rejects_action(tmp_path: Path) -> None:
    store = CustomToolStoreService(
        root_dir=str(tmp_path / "tools"), backend="filesystem"
    )
    saved = store.save_draft(_bundle(profile=ANALYTICS_PROFILE), owner_id="user_a")

    assert saved["finance_tool_profile"] == {
        "protocol": FINANCE_TOOL_PROFILE_PROTOCOL,
        **ANALYTICS_PROFILE,
    }
    assert store.load_revision("ct_market_strength", 1)[
        "finance_tool_profile"
    ] == saved["finance_tool_profile"]

    with pytest.raises(CustomToolFinanceProfileError, match="design-only"):
        store.save_draft(
            _bundle(
                profile={
                    "family": "action",
                    "execution_shape": "portfolio_stateful",
                    "output_semantic": "action_receipt",
                }
            ),
            owner_id="user_a",
        )


def test_coding_uses_design_profile_and_action_cannot_cross_boundary(
    tmp_path: Path,
) -> None:
    service = CustomToolAgentService(
        store=CustomToolStoreService(
            root_dir=str(tmp_path / "tools"), backend="filesystem"
        ),
        use_codex=False,
    )
    final = {
        "tool_contract": {
            "tool_name": "ct_market_strength",
            "display_name": "大盘强度",
            "description": "计算大盘强度。",
            "inputs": [],
            "outputs": [{"name": "strength", "type": "number"}],
        },
        "implementation": {
            "summary": "计算强度。",
            "modules": [
                {
                    "module_id": "main",
                    "source_code": "def run(inputs): return {'strength': 60}",
                }
            ],
        },
        "finance_tool_profile": {
            "family": "analytics",
            "execution_shape": "entity_local",
            "output_semantic": "metric",
        },
    }

    analytics = service._bundle_from_coding_final(
        {"document": "计算大盘强度。", "finance_tool_profile": ANALYTICS_PROFILE},
        final,
    )
    assert analytics["finance_tool_profile"]["family"] == "analytics"
    assert analytics["finance_tool_profile"]["execution_shape"] == "aggregate_context"
    assert analytics["manifest"]["capabilities"] == ["custom_tool"]

    with pytest.raises(CustomToolFinanceProfileError, match="design-only"):
        service._bundle_from_coding_final(
            {
                "document": "执行下单。",
                "finance_tool_profile": {
                    "family": "action",
                    "execution_shape": "portfolio_stateful",
                    "output_semantic": "action_receipt",
                },
            },
            final,
        )


def test_action_design_stops_before_starting_coder(tmp_path: Path) -> None:
    class RecordingCoder:
        def __init__(self) -> None:
            self.calls = []

        def code(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError("action design must not start Coding")

    coder = RecordingCoder()
    service = CustomToolAgentService(
        store=CustomToolStoreService(
            root_dir=str(tmp_path / "tools"), backend="filesystem"
        ),
        coder=coder,
        use_codex=False,
    )
    state = {
        "requirement_text": "设计一个下单工具。",
        "design_contract": {
            "document": "形成订单草案并等待人工确认。",
            "mermaid": "flowchart TD\nA --> B",
            "finance_tool_profile": {
                "protocol": "finance_tool_profile.v1",
                "family": "action",
                "execution_shape": "portfolio_stateful",
                "output_semantic": "action_receipt",
            },
        },
    }

    result = service._confirm_and_code(state=state, owner_id="user_a")

    assert "暂不进入 Coding" in result["message"]
    assert result["state"]["design_contract"] == state["design_contract"]
    assert coder.calls == []


def test_strategy_coding_profile_and_companions_form_one_consistent_revision(
    tmp_path: Path,
) -> None:
    service = CustomToolAgentService(
        store=CustomToolStoreService(
            root_dir=str(tmp_path / "tools"), backend="filesystem"
        ),
        use_codex=False,
    )
    bundle = service._bundle_from_coding_final(
        {
            "document": "在完整股票池中排序并返回入选股票。",
            "finance_tool_profile": {
                "family": "strategy",
                "execution_shape": "cross_sectional",
                "output_semantic": "signal",
            },
        },
        {
            "tool_contract": {
                "tool_name": "ct_ranked_selection",
                "display_name": "横截面选股",
                "description": "在完整股票池中排序。",
                "inputs": [
                    {
                        "name": "universe",
                        "type": "array",
                        "items_type": "string",
                        "required": True,
                    }
                ],
                "outputs": [{"name": "selected", "type": "array"}],
            },
            "implementation": {
                "modules": [
                    {
                        "module_id": "main",
                        "source_code": "def run(inputs): return {'selected': []}",
                    }
                ]
            },
            "strategy_runtime_profile": {
                "protocol": "strategy_runtime_profile.v1",
                "binding": {"field": "universe"},
                "required_history_sessions": 20,
                "default_run_sessions": 100,
                "market_code": "CN_A",
            },
            "selection_output_profile": {
                "candidate_path": "selected",
                "symbol_field": "symbol",
            },
        },
    )

    assert bundle["finance_tool_profile"]["family"] == "strategy"
    assert bundle["finance_tool_profile"]["output_semantic"] == "ranked_selection"
    assert bundle["manifest"]["capabilities"] == ["custom_tool", "strategy"]


def test_local_patch_preserves_profile_but_full_revision_does_not(
    tmp_path: Path,
) -> None:
    store = CustomToolStoreService(
        root_dir=str(tmp_path / "tools"), backend="filesystem"
    )
    active = store.save_draft(_bundle(profile=ANALYTICS_PROFILE), owner_id="user_a")
    store.commit("ct_market_strength", owner_ids=["user_a"])
    service = CustomToolAgentService(store=store, use_codex=False)
    target = {
        "tool_name": "ct_market_strength",
        "base_revision": 1,
        "base_code_hash": active["manifest"]["code_hash"],
    }

    local = service._lock_edit_candidate_bundle(
        _bundle(),
        state={
            "edit_target": target,
            "edit_plan": {"route": "local_patch", "affected_assets": ["implementation"]},
            "design_contract": {"document": "局部修改。"},
        },
        owner_id="user_a",
    )
    assert local["finance_tool_profile"]["family"] == "analytics"

    full = service._lock_edit_candidate_bundle(
        _bundle(),
        state={
            "edit_target": target,
            "edit_plan": {"route": "full_revision", "affected_assets": ["design"]},
            "design_contract": {"document": "完整重设计。"},
        },
        owner_id="user_a",
    )
    assert "finance_tool_profile" not in full

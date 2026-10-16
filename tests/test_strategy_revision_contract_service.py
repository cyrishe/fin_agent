from __future__ import annotations

import json
from pathlib import Path

import fastjsonschema
import pytest

from src.services.custom_tool_service import (
    CustomToolAgentService,
    CustomToolStoreService,
)
from src.services.strategy_revision_contract_service import (
    StrategyRevisionContractError,
    StrategyRevisionContractService,
)


def _runtime_profile(field: str = "universe") -> dict:
    return {
        "protocol": "strategy_runtime_profile.v1",
        "binding": {"field": field},
        "required_history_sessions": 20,
        "default_run_sessions": 100,
        "default_universe_ref": {"type": "all_a_share"},
        "market_code": "CN_A",
    }


def _selection_profile() -> dict:
    return {
        "candidate_path": "data.selected_stocks",
        "symbol_field": "stock_code",
        "output_date_path": "data.as_of_date",
    }


def _array_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "universe": {
                "type": "array",
                "items": {"type": "string"},
            }
        },
    }


def _bundle(*, field_type: str = "array", items_type: str = "string") -> dict:
    input_field = {
        "name": "universe" if field_type == "array" else "stock_code",
        "type": field_type,
        "required": True,
        "description": "由系统 Wrapper 注入的证券范围。",
    }
    if field_type == "array":
        input_field["items_type"] = items_type
    binding = input_field["name"]
    return {
        "tool_contract": {
            "tool_name": "ct_rank_selector",
            "display_name": "横截面排名策略",
            "description": "对点时股票范围进行排序。",
            "inputs": [input_field],
            "outputs": [
                {
                    "name": "selected_stocks",
                    "type": "array",
                    "required": True,
                    "description": "有序选股结果。",
                },
                {
                    "name": "as_of_date",
                    "type": "string",
                    "required": True,
                    "description": "本次选股的数据截止日。",
                },
            ],
        },
        "implementation_summary": "已完成横截面排序和受控样例验证。",
        "strategy_runtime_profile": _runtime_profile(binding),
        "selection_output_profile": _selection_profile(),
        "code": (
            "def run(inputs):\n"
            "    return {'selected_stocks': [], 'as_of_date': '2026-07-24', "
            "'key_process_info': {}}"
        ),
    }


def test_ordinary_tool_can_omit_strategy_companions() -> None:
    contracts = StrategyRevisionContractService().normalize(
        runtime_profile=None,
        selection_output_profile=None,
        input_schema={"type": "object", "properties": {}},
    )

    assert contracts.to_bundle_fields() == {}


def test_native_ranked_strategy_contract_is_canonical_and_assessed() -> None:
    service = StrategyRevisionContractService()
    contracts = service.normalize(
        runtime_profile=_runtime_profile(),
        selection_output_profile=_selection_profile(),
        input_schema=_array_schema(),
    )

    assert contracts.runtime_profile == _runtime_profile()
    assert contracts.selection_output_profile == _selection_profile()
    assert service.assess(
        runtime_profile=contracts.runtime_profile,
        selection_output_profile=contracts.selection_output_profile,
        input_schema=_array_schema(),
    ) == {
        "strategy_wrapper_ready": True,
        "portfolio_backtest_contract_ready": True,
        "execution_shape": "native_universe",
        "summary": (
            "已具备一次性点时选股结果契约；历史回放仍需运行主机完成权限、"
            "固定修订号和 point-in-time 预检。"
        ),
    }


def test_entity_local_array_strategy_is_assessed_as_independent_dispatch() -> None:
    service = StrategyRevisionContractService()

    assessment = service.assess(
        runtime_profile=_runtime_profile(),
        selection_output_profile=None,
        input_schema=_array_schema(),
        execution_shape="entity_local",
    )

    assert assessment == {
        "strategy_wrapper_ready": True,
        "portfolio_backtest_contract_ready": False,
        "execution_shape": "independent_entities",
        "summary": (
            "逐股独立策略可由 Wrapper 有界并行运行；当前输出不能直接作为"
            "共享组合的每日排名。"
        ),
    }


def test_scalar_strategy_cannot_claim_native_portfolio_backtest_output() -> None:
    with pytest.raises(
        StrategyRevisionContractError,
        match="one native universe invocation",
    ):
        StrategyRevisionContractService().normalize(
            runtime_profile=_runtime_profile("stock_code"),
            selection_output_profile=_selection_profile(),
            input_schema={
                "type": "object",
                "properties": {"stock_code": {"type": "string"}},
            },
        )


def test_selection_profile_must_start_from_a_declared_output() -> None:
    with pytest.raises(
        StrategyRevisionContractError,
        match="candidate_path root is absent",
    ):
        StrategyRevisionContractService().normalize(
            runtime_profile=_runtime_profile(),
            selection_output_profile=_selection_profile(),
            input_schema=_array_schema(),
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "object"}},
            },
        )


def test_selection_output_date_path_rejects_array_index_with_actionable_error() -> None:
    profile = {
        **_selection_profile(),
        "output_date_path": "data.selected_stocks[0].as_of_date",
    }

    with pytest.raises(
        StrategyRevisionContractError,
        match="one scalar date without array indexes",
    ):
        StrategyRevisionContractService().normalize(
            runtime_profile=_runtime_profile(),
            selection_output_profile=profile,
            input_schema=_array_schema(),
            output_schema={
                "type": "object",
                "properties": {
                    "selected_stocks": {"type": "array"},
                    "as_of_date": {"type": "string"},
                },
            },
        )


def test_selection_profile_paths_are_canonicalized_to_the_host_data_envelope() -> None:
    contracts = StrategyRevisionContractService().normalize(
        runtime_profile=_runtime_profile(),
        selection_output_profile={
            "candidate_path": "selected_stocks",
            "symbol_field": "stock_code",
            "output_date_path": "as_of_date",
        },
        input_schema=_array_schema(),
        output_schema={
            "type": "object",
            "properties": {
                "selected_stocks": {"type": "array"},
                "as_of_date": {"type": "string"},
            },
        },
    )

    assert contracts.selection_output_profile == {
        "candidate_path": "data.selected_stocks",
        "symbol_field": "stock_code",
        "output_date_path": "data.as_of_date",
    }


def test_coding_bundle_derives_strategy_capability_and_array_item_schema(
    tmp_path,
) -> None:
    service = CustomToolAgentService(
        store=CustomToolStoreService(root_dir=str(tmp_path)),
        use_codex=False,
    )

    bundle = service._bundle_from_coding_final(
        {"tool_name": "ct_rank_selector", "document": "按因子横截面排序。"},
        _bundle(),
    )

    assert bundle["manifest"]["capabilities"] == ["custom_tool", "strategy"]
    assert bundle["input_schema"]["properties"]["universe"]["items"] == {
        "type": "string"
    }
    assert bundle["strategy_runtime_profile"] == _runtime_profile()
    assert bundle["selection_output_profile"] == _selection_profile()


def test_ordinary_coding_bundle_keeps_existing_capability_and_shape(tmp_path) -> None:
    service = CustomToolAgentService(
        store=CustomToolStoreService(root_dir=str(tmp_path)),
        use_codex=False,
    )
    final = _bundle()
    final.pop("strategy_runtime_profile")
    final.pop("selection_output_profile")

    bundle = service._bundle_from_coding_final(
        {"tool_name": "ct_rank_selector", "document": "普通计算。"},
        final,
    )
    saved = service.store.save_draft(bundle, owner_id="owner-a")

    assert bundle["manifest"]["capabilities"] == ["custom_tool"]
    assert "strategy_runtime_profile" not in bundle
    assert "selection_output_profile" not in bundle
    assert "strategy_runtime_profile" not in saved
    assert "selection_output_profile" not in saved


def test_strategy_companions_are_revision_scoped_and_local_patch_preserves_them(
    tmp_path,
) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path))
    service = CustomToolAgentService(store=store, use_codex=False)
    bundle = service._bundle_from_coding_final(
        {"tool_name": "ct_rank_selector", "document": "按因子横截面排序。"},
        _bundle(),
    )
    active = store.save_draft(bundle, owner_id="owner-a")
    manifest = active["manifest"]
    state = {
        "edit_target": {
            "tool_name": "ct_rank_selector",
            "base_revision": manifest["current_revision"],
            "base_code_hash": manifest["code_hash"],
        },
        "edit_plan": {
            "route": "local_patch",
            "affected_assets": ["implementation"],
        },
        "design_contract": {"tool_name": "ct_rank_selector", "document": "仅改阈值。"},
    }

    candidate = service._lock_edit_candidate_bundle(
        {
            **bundle,
            "strategy_runtime_profile": {},
            "selection_output_profile": {},
        },
        state=state,
        owner_id="owner-a",
    )

    assert candidate["strategy_runtime_profile"] == _runtime_profile()
    assert candidate["selection_output_profile"] == _selection_profile()
    assert candidate["manifest"]["capabilities"] == ["custom_tool", "strategy"]
    saved = store.save_candidate_revision(
        candidate,
        owner_id="owner-a",
        tool_name="ct_rank_selector",
    )
    assert saved["strategy_runtime_profile"] == _runtime_profile()
    assert saved["selection_output_profile"] == _selection_profile()
    assert store.load("ct_rank_selector")["manifest"]["current_revision"] == 1


def test_local_edit_build_inherits_system_owned_strategy_companions_before_validation(
    tmp_path,
) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path))
    service = CustomToolAgentService(store=store, use_codex=False)
    finance_profile = {
        "protocol": "finance_tool_profile.v1",
        "family": "strategy",
        "execution_shape": "cross_sectional",
        "output_semantic": "ranked_selection",
        "summary": "按点时股票池生成有序选股结果。",
    }
    active_bundle = service._bundle_from_coding_final(
        {
            "tool_name": "ct_rank_selector",
            "document": "按因子横截面排序。",
            "finance_tool_profile": finance_profile,
        },
        _bundle(),
    )
    active = store.save_draft(active_bundle, owner_id="owner-a")
    local_edit_final = _bundle()
    local_edit_final.pop("strategy_runtime_profile")
    local_edit_final.pop("selection_output_profile")

    candidate = service._bundle_from_coding_final(
        {
            "tool_name": "ct_rank_selector",
            "document": "仅修改分钟K数据读取方式。",
            "finance_tool_profile": finance_profile,
        },
        local_edit_final,
        preserved_revision=active,
    )

    assert candidate["strategy_runtime_profile"] == _runtime_profile()
    assert candidate["selection_output_profile"] == _selection_profile()
    assert candidate["finance_tool_profile"]["family"] == "strategy"
    assert candidate["manifest"]["capabilities"] == ["custom_tool", "strategy"]


def test_full_redesign_does_not_inherit_stale_strategy_companions(tmp_path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path))
    service = CustomToolAgentService(store=store, use_codex=False)
    bundle = service._bundle_from_coding_final(
        {"tool_name": "ct_rank_selector", "document": "按因子横截面排序。"},
        _bundle(),
    )
    active = store.save_draft(bundle, owner_id="owner-a")
    manifest = active["manifest"]

    candidate = service._lock_edit_candidate_bundle(
        {
            "manifest": dict(bundle["manifest"]),
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object", "properties": {}},
            "code": "def run(inputs): return {'key_process_info': {}}",
        },
        state={
            "edit_target": {
                "tool_name": "ct_rank_selector",
                "base_revision": manifest["current_revision"],
                "base_code_hash": manifest["code_hash"],
            },
            "edit_plan": {"route": "full_redesign", "affected_assets": []},
            "design_contract": {"tool_name": "ct_rank_selector", "document": "改为普通计算。"},
        },
        owner_id="owner-a",
    )

    assert "strategy_runtime_profile" not in candidate
    assert "selection_output_profile" not in candidate
    assert candidate["manifest"]["capabilities"] == ["custom_tool"]


def test_coding_output_schema_keeps_only_tool_contract_and_summary() -> None:
    schema = json.loads(
        Path(
            "src/skills/financial-tool-development/skills/financial-tool-implementation/schema.json"
        ).read_text(encoding="utf-8")
    )
    validate = fastjsonschema.compile(schema)
    validate(
        {
            "tool_contract": {
                "tool_name": "ct_sum",
                "display_name": "求和",
                "description": "求和。",
                "inputs": [],
                "outputs": [],
            },
            "implementation_summary": "普通工具仍保持原输出。",
        }
    )
    assert set(schema["properties"]) == {"tool_contract", "implementation_summary"}


def test_requirement_design_and_coding_share_the_entity_list_runtime_boundary() -> None:
    root = Path("src/skills/financial-tool-development/skills")
    requirement = (root / "financial-tool-requirement" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    design = (root / "financial-tool-design" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    coding = (root / "financial-tool-implementation" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "不需要追问用户选择“单个还是批量”" in requirement
    assert "完整集合共同参与计算" in requirement
    assert "公开输入默认使用一个 `array<string>` 列表" in design
    assert "不要在方案或流程图中描述遍历、线程、并发、分片" in design
    assert "Coding 保持这一个列表输入" in coding
    assert "同一种数据的查询次数不应随着目标数量增长" in coding
    assert "没有必要再增加线程池、异步调度或分片" in coding

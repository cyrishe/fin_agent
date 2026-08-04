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


def test_coding_output_schema_accepts_strategy_metadata_and_legacy_payload() -> None:
    schema = json.loads(
        Path(
            "src/skills/financial-tool-development/skills/financial-tool-implementation/schema.json"
        ).read_text(encoding="utf-8")
    )
    validate = fastjsonschema.compile(schema)
    strategy_payload = _bundle()
    strategy_payload.pop("code")
    validate(strategy_payload)
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


def test_requirement_design_and_coding_share_the_strategy_wrapper_boundary() -> None:
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

    assert "不询问“单只股票 / 股票列表 / 全市场”" in requirement
    assert "横截面/组合共同判断" in requirement
    assert "不询问或设计 `single/list/market`" in design
    assert "单次快照判断 + 外围调度" in design
    assert "strategy_runtime_profile.v1" in coding
    assert "逐股 `string` 策略" in coding
    assert "不得输出该字段" in coding

from src.services.conversation_mainline_contract_service import ConversationMainlineContractService


def _build(path_type: str, *, work_items=None, selected_tools=None, target=None):
    return ConversationMainlineContractService().build_contract(
        normalized_input={"text": "用阿布规则判断 600519 今天能不能买", "has_attachments": False},
        dispatch_plan={
            "entry": path_type,
            "target": target or {"type": "tool_group", "name": "direct_tools"},
        },
        execution_plan={
            "selected_path": {"type": path_type, "target": target or {"type": "tool_group", "name": "direct_tools"}},
            "work_items": work_items or [],
            "selected_tools": selected_tools or [],
        },
        work_context={"thread_active_skill_canonical_name": "abu_buy_decision"},
    )


def test_mainline_contract_keeps_compiled_skill_run_separate_from_authoring() -> None:
    contract = _build(
        "skill_run",
        target={"type": "skill", "name": "abu_buy_decision"},
        work_items=[{"step_id": "step_1", "type": "skill", "name": "abu_buy_decision"}],
    )

    assert contract["schema_version"] == "conversation_mainline_contract.v1"
    assert contract["chat_task_mode"] == "compiled_skill_run"
    assert contract["runtime_lane"] == "skill_runtime"
    assert contract["chat_boundaries"]["skill_authoring_visible"] is False
    assert contract["chat_boundaries"]["skill_persistence_visible"] is False
    assert "do_not_reinterpret_compiled_skill_body" in contract["responsibilities"]["planner"]
    assert "load_compiled_skill_contract" in contract["responsibilities"]["resolver"]


def test_mainline_contract_marks_planned_or_multi_tool_chat_as_temporary_loop() -> None:
    contract = _build(
        "planned_run",
        work_items=[
            {"step_id": "step_1", "type": "tool", "name": "get_hot_industries_and_leaders"},
            {"step_id": "step_2", "type": "tool", "name": "financial_news_search", "depends_on": ["step_1"]},
        ],
    )

    assert contract["chat_task_mode"] == "temporary_multi_step_task"
    assert contract["runtime_lane"] == "tool_plan_runtime_loop"
    assert contract["runtime_contract"]["requires_runtime_loop"] is True
    assert contract["runtime_contract"]["work_item_count"] == 2
    assert contract["runtime_contract"]["tool_count"] == 2


def test_mainline_contract_keeps_effect_tuning_surfaces_explicit_and_deferred() -> None:
    contract = _build("tool_plan_run", selected_tools=["stock_quote"])

    assert contract["chat_task_mode"] == "direct_tool_plan"
    assert contract["tuning_boundary"]["status"] == "separated_not_tuned"
    assert contract["tuning_boundary"]["do_not_optimize_for_case_pass_rate"] is True
    surfaces = {item["surface"]: item for item in contract["tuning_boundary"]["surfaces"]}
    assert "assistant.round_task_synthesizer" not in surfaces
    assert surfaces["assistant.conversation_task_finalizer"]["phase"] == "conversation_task_finalization"
    assert surfaces["agent_runtime.planner"]["status"] == "defer_effect_tuning"
    assert surfaces["agent_runtime.plan_compiler"]["phase"] == "tool_dag_compilation"
    assert surfaces["tool_argument_compiler"]["kind"] == "service"

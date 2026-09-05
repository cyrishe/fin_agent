from src.services.assistant_dispatch_planner import AssistantDispatchPlanner


class _StubPreprocessService:
    def preprocess(self, **_: object) -> dict:
        return {
            "domain": "business",
            "interaction_mode": "execute_business_task",
            "execution_path": "planned_run",
            "task_domain": "business_dialog",
            "capability_family": "business_analysis",
            "dispatch_plan": {
                "entry": "planned_run",
                "turn_mode": "normal_qa",
                "selected_agent": "investment_analyst",
                "execution_plan_preview": {
                    "plan_type": "planned_run",
                    "selected_tools": ["theme_leaders", "stock_funds"],
                    "work_items": [
                        {"step_id": "step_1", "type": "tool", "name": "theme_leaders"},
                        {"step_id": "step_2", "type": "tool", "name": "stock_funds"},
                    ],
                },
            },
            "execution_plan_preview": {
                "plan_type": "planned_run",
                "selected_tools": ["theme_leaders", "stock_funds"],
                "work_items": [
                    {"step_id": "step_1", "type": "tool", "name": "theme_leaders"},
                    {"step_id": "step_2", "type": "tool", "name": "stock_funds"},
                ],
            },
            "interaction": {
                "agent_name": "investment_analyst",
                "turn_mode": "normal_qa",
                "analize": "这是一个需要先找龙头再补资金面的业务分析问题。",
                "domain_hint": "business",
                "agent_hint": "investment_analyst",
                "needs_reference_resolution": False,
            },
            "context_resolution": {
                "analize": "",
                "resolved_items": [],
                "resolution_summary": "",
            },
            "normalized_request": {
                "round_task_desc": "先找机器人概念前两只龙头，再查看资金面。",
            },
            "runtime_modules": [
                {"module": "conversation_management", "status": "completed", "nodes": ["interaction_preprocess"]},
            ],
            "runtime_node_results": [
                {"node": "interaction_preprocess", "module": "conversation_management", "status": "completed"},
            ],
            "runtime_feedback_protocol": {
                "version": "runtime_feedback.v1",
            },
            "conversation_mainline": {
                "schema_version": "conversation_mainline_contract.v1",
                "chat_task_mode": "temporary_multi_step_task",
                "runtime_lane": "tool_plan_runtime_loop",
            },
            "work_context": {},
            "normalized_input": {"text": "机器人概念前两只龙头看下资金面"},
            "llm_usage": {},
        }


def test_assistant_dispatch_planner_builds_planning_task_state():
    planner = AssistantDispatchPlanner(preprocess_service=_StubPreprocessService())

    result = planner.plan_free_chat(
        text="机器人概念前两只龙头看下资金面",
        attachments=[],
        thread_context={},
        application_context={},
    )

    task_state = result["task_state"]
    assert task_state["job"]["current_stage"] == "planning_completed"
    assert task_state["job"]["plan_mode"] == "planning"
    assert len(task_state["steps"]) == 3
    assert task_state["steps"][0]["title"] == "理解问题"
    assert task_state["steps"][1]["title"] == "整理任务"
    assert task_state["steps"][2]["title"] == "生成执行方案"
    assert result["runtime_modules"][0]["module"] == "conversation_management"
    assert result["runtime_node_results"][0]["node"] == "interaction_preprocess"
    assert result["runtime_feedback_protocol"]["version"] == "runtime_feedback.v1"
    assert result["conversation_mainline"]["chat_task_mode"] == "temporary_multi_step_task"

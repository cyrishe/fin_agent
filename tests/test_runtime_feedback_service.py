from src.services.runtime_feedback_service import RuntimeFeedbackService


def test_runtime_feedback_marks_final_risk_as_partial_add_step():
    service = RuntimeFeedbackService()

    feedback = service.build_task_feedback(
        execution_plan={"objective": "查询大单和特大单"},
        tool_runs=[
            {
                "step_id": "step_1",
                "tool_name": "stock_capital_flow_query",
                "status": "completed",
                "reason": "ok",
                "result": {"ok": True, "data": {"rows": [{"main_net_inflow_wan": 1}]}},
            }
        ],
        final_output={
            "summary": "已查到主力资金。",
            "risks": [{"type": "数据覆盖不足", "description": "缺失特大单和大单明细。"}],
        },
    )

    assert feedback["status"] == "partial"
    assert feedback["reason_code"] == "coverage_insufficient"
    assert feedback["suggested_action"] == "add_step"
    assert "缺失特大单和大单明细" in feedback["message"]


def test_runtime_feedback_routes_missing_binding_to_stage_replan():
    service = RuntimeFeedbackService()

    step_feedback = service.build_step_feedback(
        {
            "step_id": "step_2",
            "tool_name": "analysis_python",
            "status": "failed",
            "reason": "missing_upstream_binding",
            "failure_kind": "missing_upstream_binding",
            "error": "missing ${step_1.rows}",
        }
    )

    assert step_feedback["status"] == "failed"
    assert step_feedback["reason_code"] == "tool_schema_mismatch"
    assert step_feedback["suggested_action"] == "replan_stage"


def test_runtime_feedback_invalid_input_asks_user():
    service = RuntimeFeedbackService()

    step_feedback = service.build_step_feedback(
        {
            "step_id": "step_1",
            "tool_name": "stock_history_kline",
            "status": "failed",
            "reason": "tool_failed",
            "error": "证券代码无法识别",
        }
    )

    assert step_feedback["reason_code"] == "invalid_input"
    assert step_feedback["suggested_action"] == "ask_user"


def test_runtime_feedback_unsupported_field_routes_to_replan():
    service = RuntimeFeedbackService()

    step_feedback = service.build_step_feedback(
        {
            "step_id": "step_2",
            "tool_name": "security_universe_query",
            "status": "failed",
            "reason": "tool_failed",
            "error": "unsupported sort field: turnover",
        }
    )

    assert step_feedback["reason_code"] == "tool_schema_mismatch"
    assert step_feedback["suggested_action"] == "replan_stage"


def test_runtime_feedback_unsupported_sort_by_routes_to_replan():
    service = RuntimeFeedbackService()

    step_feedback = service.build_step_feedback(
        {
            "step_id": "step_1",
            "tool_name": "plate_member_query",
            "status": "failed",
            "reason": "tool_failed",
            "error": "unsupported sort_by: turnover",
        }
    )

    assert step_feedback["reason_code"] == "tool_schema_mismatch"
    assert step_feedback["suggested_action"] == "replan_stage"


def test_runtime_feedback_task_prefers_replan_over_add_step():
    service = RuntimeFeedbackService()

    feedback = service.build_task_feedback(
        execution_plan={"objective": "按成交额排序"},
        tool_runs=[],
        final_output={},
        step_feedback=[
            {
                "status": "failed",
                "message": "tool A 缺少数据。",
                "scope": "step",
                "reason_code": "data_unavailable",
                "suggested_action": "add_step",
                "evidence": [],
            },
            {
                "status": "failed",
                "message": "tool B 参数字段不支持。",
                "scope": "step",
                "reason_code": "tool_schema_mismatch",
                "suggested_action": "replan_stage",
                "evidence": [],
            },
        ],
    )

    assert feedback["reason_code"] == "tool_schema_mismatch"
    assert feedback["suggested_action"] == "replan_stage"


def test_runtime_feedback_final_invalid_subject_asks_user():
    service = RuntimeFeedbackService()

    feedback = service.build_task_feedback(
        execution_plan={"objective": "查询不存在标的"},
        tool_runs=[
            {"step_id": "step_1", "tool_name": "lookup", "status": "completed", "reason": "ok"}
        ],
        final_output={
            "summary": "无法查询。",
            "risks": [{"type": "数据覆盖不足", "description": "由于标的不存在，无法继续执行分析。"}],
        },
    )

    assert feedback["status"] == "needs_user_input"
    assert feedback["reason_code"] == "invalid_input"
    assert feedback["suggested_action"] == "ask_user"

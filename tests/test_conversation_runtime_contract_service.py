from src.services.conversation_runtime_contract_service import ConversationRuntimeContractService


def test_feedback_protocol_declares_two_loop_levels_and_limited_reason_codes():
    service = ConversationRuntimeContractService()

    protocol = service.feedback_protocol()

    assert protocol["version"] == "runtime_feedback.v1"
    assert protocol["loop_levels"] == ["node_internal_loop", "module_feedback_loop"]
    assert "tool_schema_mismatch" in protocol["reason_codes"]
    assert "user_clarification_required" in protocol["reason_codes"]
    assert protocol["default_retry_policy"]["max_module_retry"] == 1


def test_execution_contract_maps_missing_binding_failure_to_planner_feedback():
    service = ConversationRuntimeContractService()

    contract = service.build_execution_contract(
        execution_plan={"objective": "测试执行协议"},
        tool_runs=[
            {
                "step_id": "step_1",
                "tool_name": "analysis_python",
                "status": "failed",
                "failure_kind": "missing_upstream_binding",
                "reason": "missing ${step_0.rows}",
            }
        ],
        final_output={},
        render_payload={},
        runtime_trace={"local_events": []},
    )

    nodes = {item["node"]: item for item in contract["node_results"]}
    feedback = nodes["runtime_execute"]["feedback"]

    assert contract["phase"] == "execution"
    assert contract["modules"][0]["module"] == "execution_runtime"
    assert nodes["runtime_execute"]["status"] == "failed"
    assert feedback["from_node"] == "runtime_execute"
    assert feedback["to_node"] == "agent_runtime_planning"
    assert feedback["reason_code"] == "tool_schema_mismatch"


from __future__ import annotations

from typing import Any, Dict, List, Optional


class ConversationRuntimeContractService:
    """Builds a stable protocol view over the existing conversation runtime.

    This service does not make decisions. It only groups already-computed
    outputs into the four module / eight core-node contract used by tracing,
    evaluation, and future feedback-loop handling.
    """

    MODULES: List[Dict[str, Any]] = [
        {
            "module": "conversation_management",
            "title": "对话管理",
            "nodes": [
                "context_resolution",
                "interaction_preprocess",
                "conversation_task_finalization",
                "conversation_state_update",
            ],
        },
        {
            "module": "task_analysis",
            "title": "任务分析",
            "nodes": ["dispatch_planning"],
        },
        {
            "module": "capability_selection",
            "title": "核心工具和技能选择",
            "nodes": ["agent_runtime_planning"],
        },
        {
            "module": "execution_runtime",
            "title": "执行 Runtime",
            "nodes": ["runtime_execute", "observe_present_writeback"],
        },
    ]

    NODE_MODULE: Dict[str, str] = {
        node: str(module["module"])
        for module in MODULES
        for node in module["nodes"]
    }

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _source(payload: Any) -> str:
        return str(payload.get("source") or "") if isinstance(payload, dict) else ""

    def build_preprocess_contract(
        self,
        *,
        normalized_input: Dict[str, Any],
        interaction: Dict[str, Any],
        context_resolution: Dict[str, Any],
        normalized_request: Dict[str, Any],
        task_domain_decision: Dict[str, Any],
        capability_family_decision: Dict[str, Any],
        selected_agent: str,
        dispatch_plan: Dict[str, Any],
        execution_plan_preview: Dict[str, Any],
        interaction_frame: Dict[str, Any],
        conversation_state: Dict[str, Any],
        context_write_policy: Dict[str, Any],
        thread_context_patch_preview: Dict[str, Any],
    ) -> Dict[str, Any]:
        node_results = [
            self._node(
                node="interaction_preprocess",
                status="completed",
                output={
                    "agent_name": self._trim(interaction.get("agent_name") or interaction.get("agent_hint")),
                    "turn_mode": self._trim(interaction.get("turn_mode")),
                    # Compatibility fields retained for existing trace consumers.
                    "domain_hint": self._trim(interaction.get("domain_hint")),
                    "agent_hint": self._trim(interaction.get("agent_hint")),
                    "needs_reference_resolution": bool(interaction.get("needs_reference_resolution")),
                    "info_ready": bool(interaction.get("info_ready")),
                },
                trace={
                    "source": self._source(interaction) or "unknown",
                    "reason": self._trim(interaction.get("reason") or interaction.get("analize")),
                    "confidence": interaction.get("confidence") if isinstance(interaction.get("confidence"), (int, float)) else None,
                    "input_refs": ["normalized_input.text", "thread_context", "application_context"],
                    "output_refs": ["interaction.agent_name", "interaction.turn_mode"],
                },
            ),
            self._node(
                node="context_resolution",
                status="skipped" if self._source(context_resolution) == "skipped" else "completed",
                output={
                    "ori_question": self._trim(context_resolution.get("ori_question")),
                    "resolved_question": self._trim(context_resolution.get("resolved_question")),
                    "context_refs": context_resolution.get("context_refs") if isinstance(context_resolution.get("context_refs"), list) else [],
                    "resolved_count": len(context_resolution.get("resolved_items") or []) if isinstance(context_resolution.get("resolved_items"), list) else 0,
                    "resolution_summary": self._trim(context_resolution.get("resolution_summary")),
                },
                trace={
                    "source": self._source(context_resolution) or "unknown",
                    "reason": "needs_reference_resolution=false" if self._source(context_resolution) == "skipped" else self._trim(context_resolution.get("analize")),
                    "confidence": None,
                    "input_refs": ["context_window", "thread_state", "preprocessing_signals", "interaction"],
                    "output_refs": ["context_resolution.resolved_question", "context_resolution.context_refs"],
                },
            ),
            self._node(
                node="conversation_task_finalization",
                status="completed",
                output={
                    "resolved_question": self._trim(normalized_request.get("resolved_question")),
                    "round_task_desc": self._trim(normalized_request.get("round_task_desc")),
                    "task_split_count": len(normalized_request.get("task_splitd") or []) if isinstance(normalized_request.get("task_splitd"), list) else 0,
                    "context_relation": self._trim(normalized_request.get("context_relation")),
                },
                trace={
                    "source": self._source(normalized_request) or "unknown",
                    "reason": self._trim(normalized_request.get("analize")),
                    "confidence": None,
                    "input_refs": ["normalized_input.text", "interaction", "context_resolution", "preprocessing_signals"],
                    "output_refs": ["normalized_request.round_task_desc"],
                },
            ),
            self._node(
                node="conversation_state_update",
                status="completed",
                output={
                    "interaction_mode": self._trim(interaction_frame.get("interaction_mode")),
                    "active_focus_type": self._trim(interaction_frame.get("active_focus_type")),
                    "active_focus_id": self._trim(interaction_frame.get("active_focus_id")),
                    "conversation_state": self._trim(conversation_state.get("state")),
                    "has_write_policy": bool(context_write_policy),
                },
                trace={
                    "source": "derived",
                    "reason": self._trim(conversation_state.get("reason")) or "derived_from_interaction_frame_and_dispatch",
                    "confidence": None,
                    "input_refs": ["interaction_frame", "dispatch_plan", "execution_plan_preview"],
                    "output_refs": ["conversation_state", "context_write_policy", "thread_context_patch_preview"],
                },
            ),
            self._node(
                node="dispatch_planning",
                status="completed",
                output={
                    "task_domain": self._trim(task_domain_decision.get("value")),
                    "capability_family": self._trim(capability_family_decision.get("value")),
                    "selected_agent": self._trim(selected_agent),
                    "entry": self._trim(dispatch_plan.get("entry")),
                    "target": dispatch_plan.get("target") if isinstance(dispatch_plan.get("target"), dict) else {},
                },
                trace={
                    "source": "rule",
                    "reason": self._trim(task_domain_decision.get("reason")) or self._trim(capability_family_decision.get("reason")),
                    "confidence": None,
                    "input_refs": ["normalized_request.domain", "normalized_request.focus", "execution_plan_preview.selected_path"],
                    "output_refs": ["task_domain", "capability_family", "dispatch_plan"],
                },
            ),
            self._node(
                node="agent_runtime_planning",
                status="completed" if execution_plan_preview else "skipped",
                output={
                    "selected_path": execution_plan_preview.get("selected_path") if isinstance(execution_plan_preview.get("selected_path"), dict) else {},
                    "work_item_count": len(execution_plan_preview.get("work_items") or []) if isinstance(execution_plan_preview.get("work_items"), list) else 0,
                    "planner_source": self._trim(execution_plan_preview.get("planner_result_source") or execution_plan_preview.get("planner_type")),
                },
                trace={
                    "source": self._trim(execution_plan_preview.get("planner_result_source") or execution_plan_preview.get("planner_type")) or ("derived" if execution_plan_preview else "skipped"),
                    "reason": self._trim((execution_plan_preview.get("selected_path") or {}).get("reason") if isinstance(execution_plan_preview.get("selected_path"), dict) else ""),
                    "confidence": None,
                    "input_refs": ["normalized_request.round_task_desc", "work_context", "application_context"],
                    "output_refs": ["execution_plan_preview", "dispatch_plan.execution_plan_preview"],
                },
            ),
            self._node(
                node="runtime_execute",
                status="pending",
                output={},
                trace={
                    "source": "not_started_in_preprocess",
                    "reason": "runtime execution starts after dispatch",
                    "confidence": None,
                    "input_refs": ["dispatch_plan", "execution_plan_preview"],
                    "output_refs": ["tool_results", "artifacts", "runtime_trace"],
                },
            ),
            self._node(
                node="observe_present_writeback",
                status="pending",
                output={
                    "thread_context_patch_preview": bool(thread_context_patch_preview),
                },
                trace={
                    "source": "not_started_in_preprocess",
                    "reason": "final observation, presentation, and durable writeback happen after execution",
                    "confidence": None,
                    "input_refs": ["runtime_result", "context_write_policy"],
                    "output_refs": ["answer", "presentation_blocks", "thread_context_patch"],
                },
            ),
        ]
        node_order = [
            "context_resolution",
            "interaction_preprocess",
            "conversation_task_finalization",
            "conversation_state_update",
            "dispatch_planning",
            "agent_runtime_planning",
            "runtime_execute",
            "observe_present_writeback",
        ]
        node_results.sort(key=lambda item: node_order.index(str(item.get("node") or "")))
        return {
            "version": "conversation_runtime_contract.v1",
            "phase": "preprocess",
            "modules": self._module_view(node_results),
            "node_results": node_results,
            "feedback_protocol": self.feedback_protocol(),
        }

    def feedback_protocol(self) -> Dict[str, Any]:
        return {
            "version": "runtime_feedback.v1",
            "loop_levels": ["node_internal_loop", "module_feedback_loop"],
            "statuses": ["completed", "blocked", "needs_user_input", "failed", "partial", "skipped", "pending"],
            "reason_codes": [
                "missing_required_input",
                "invalid_contract",
                "low_confidence",
                "upstream_state_not_actionable",
                "ambiguous_reference",
                "tool_schema_mismatch",
                "execution_failed",
                "data_unavailable",
                "coverage_insufficient",
                "goal_misaligned",
                "invalid_input",
                "user_clarification_required",
            ],
            "feedback_envelope": {
                "version": "runtime_feedback.v2",
                "required_fields": ["status", "message", "scope", "reason_code", "suggested_action", "evidence"],
                "suggested_actions": ["continue", "retry_step", "add_step", "replan_stage", "restart_task", "ask_user"],
            },
            "default_retry_policy": {
                "max_module_retry": 1,
                "max_node_internal_retry": 1,
                "fallback": "ask_user_or_report_failure",
            },
        }

    def build_execution_contract(
        self,
        *,
        execution_plan: Dict[str, Any],
        tool_runs: List[Dict[str, Any]],
        final_output: Any,
        render_payload: Dict[str, Any],
        runtime_trace: Dict[str, Any],
        runtime_feedback: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        failed_count = len([item for item in tool_runs if self._trim(item.get("status")) == "failed"])
        skipped_count = len([item for item in tool_runs if self._trim(item.get("status")) == "skipped"])
        completed_count = len([item for item in tool_runs if self._trim(item.get("status")) == "completed"])
        feedback_status = self._trim(runtime_feedback.get("status")) if isinstance(runtime_feedback, dict) else ""
        runtime_status = "failed" if failed_count else (feedback_status if feedback_status and feedback_status != "completed" else "completed")
        node_results = [
            self._node(
                node="runtime_execute",
                status=runtime_status,
                output={
                    "completed_count": completed_count,
                    "failed_count": failed_count,
                    "skipped_count": skipped_count,
                    "runtime_event_count": len(runtime_trace.get("local_events") or []) if isinstance(runtime_trace.get("local_events"), list) else 0,
                    "runtime_feedback_status": feedback_status,
                },
                trace={
                    "source": "tool_plan_runtime",
                    "reason": self._trim(execution_plan.get("objective")),
                    "confidence": None,
                    "input_refs": ["execution_plan.work_items"],
                    "output_refs": ["tool_runs", "runtime_trace"],
                },
                feedback=self._execution_feedback(tool_runs, runtime_feedback=runtime_feedback)
                if (failed_count or skipped_count or feedback_status not in {"", "completed"})
                else None,
            ),
            self._node(
                node="observe_present_writeback",
                status="completed",
                output={
                    "has_final_output": bool(final_output),
                    "has_render_payload": bool(render_payload),
                    "section_count": len(render_payload.get("sections") or []) if isinstance(render_payload.get("sections"), list) else 0,
                },
                trace={
                    "source": "tool_plan_runtime",
                    "reason": "final output and render payload built from runtime results",
                    "confidence": None,
                    "input_refs": ["tool_runs", "final_output"],
                    "output_refs": ["task_result.final_output", "task_result.render_payload"],
                },
            ),
        ]
        return {
            "version": "conversation_runtime_contract.v1",
            "phase": "execution",
            "modules": self._module_view(node_results, include_empty=False),
            "node_results": node_results,
            "feedback_protocol": self.feedback_protocol(),
        }

    def _execution_feedback(
        self,
        tool_runs: List[Dict[str, Any]],
        *,
        runtime_feedback: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if isinstance(runtime_feedback, dict) and self._trim(runtime_feedback.get("status")) not in {"", "completed"}:
            suggested_action = self._trim(runtime_feedback.get("suggested_action"))
            to_node = self._feedback_target_node(suggested_action)
            return {
                **runtime_feedback,
                "from_node": "runtime_execute",
                "to_node": to_node,
                "severity": "medium" if self._trim(runtime_feedback.get("status")) != "failed" else "high",
                "depends_on": ["execution_plan.work_items", "runtime_step.result", "final_output.risks"],
                "instruction": self._trim(runtime_feedback.get("message")) or "请根据 runtime feedback 决定补步骤、重做阶段或向用户澄清。",
                "retry_policy": {
                    "max_retry": 1,
                    "fallback": "ask_user_or_report_failure",
                },
            }
        failed = next((item for item in tool_runs if self._trim(item.get("status")) == "failed"), None)
        if not isinstance(failed, dict):
            return None
        failure_kind = self._trim(failed.get("failure_kind"))
        if failure_kind in {"missing_upstream_binding", "tool_schema_mismatch"}:
            reason_code = "tool_schema_mismatch"
            to_node = "agent_runtime_planning"
            instruction = "请根据工具 schema、依赖绑定和用户原始需求重新生成执行计划或工具参数。"
        else:
            reason_code = "execution_failed"
            to_node = "runtime_execute"
            instruction = "请检查运行时错误；长任务可按 checkpoint 策略重试，普通任务应向用户说明失败原因。"
        return {
            "from_node": "runtime_execute",
            "to_node": to_node,
            "status": "failed",
            "message": self._trim(failed.get("error") or failed.get("reason")) or "运行时步骤失败。",
            "scope": "step",
            "reason_code": reason_code,
            "suggested_action": "replan_stage" if to_node == "agent_runtime_planning" else "retry_step",
            "severity": "medium",
            "depends_on": ["execution_plan.work_items", "runtime_step.result"],
            "evidence": [
                f"step_id={self._trim(failed.get('step_id'))}",
                f"name={self._trim(failed.get('tool_name') or failed.get('name'))}",
                f"reason={self._trim(failed.get('reason') or failed.get('error'))}",
            ],
            "instruction": instruction,
            "retry_policy": {
                "max_retry": 1,
                "fallback": "ask_user_or_report_failure",
            },
        }

    def _feedback_target_node(self, suggested_action: str) -> str:
        if suggested_action in {"add_step", "replan_stage"}:
            return "agent_runtime_planning"
        if suggested_action == "restart_task":
            return "conversation_task_finalization"
        if suggested_action == "ask_user":
            return "interaction_preprocess"
        return "runtime_execute"

    def _node(
        self,
        *,
        node: str,
        status: str,
        output: Dict[str, Any],
        trace: Dict[str, Any],
        feedback: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "node": node,
            "module": self.NODE_MODULE.get(node, ""),
            "status": status,
            "output": output,
            "feedback": feedback,
            "trace": {
                "source": self._trim(trace.get("source")),
                "reason": self._trim(trace.get("reason")),
                "confidence": trace.get("confidence") if isinstance(trace.get("confidence"), (int, float)) else None,
                "input_refs": [self._trim(item) for item in (trace.get("input_refs") or []) if self._trim(item)],
                "output_refs": [self._trim(item) for item in (trace.get("output_refs") or []) if self._trim(item)],
            },
        }

    def _module_view(self, node_results: List[Dict[str, Any]], *, include_empty: bool = True) -> List[Dict[str, Any]]:
        by_node = {self._trim(item.get("node")): item for item in node_results if isinstance(item, dict)}
        rows: List[Dict[str, Any]] = []
        for module in self.MODULES:
            node_ids = [self._trim(item) for item in (module.get("nodes") or []) if self._trim(item)]
            nodes = [by_node[node_id] for node_id in node_ids if node_id in by_node]
            if not include_empty and not nodes:
                continue
            rows.append(
                {
                    "module": self._trim(module.get("module")),
                    "title": self._trim(module.get("title")),
                    "nodes": node_ids,
                    "status": self._module_status(nodes),
                }
            )
        return rows

    def _module_status(self, nodes: List[Dict[str, Any]]) -> str:
        statuses = {self._trim(item.get("status")) for item in nodes if isinstance(item, dict)}
        if "failed" in statuses:
            return "failed"
        if "blocked" in statuses:
            return "blocked"
        if "needs_user_input" in statuses:
            return "needs_user_input"
        if "pending" in statuses:
            return "pending"
        if statuses and statuses.issubset({"completed", "skipped"}):
            return "completed"
        return "pending"

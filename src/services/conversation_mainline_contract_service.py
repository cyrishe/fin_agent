from __future__ import annotations

from typing import Any, Dict, List


class ConversationMainlineContractService:
    """Builds the chat-facing mainline contract without changing routing logic.

    The contract keeps effect-tuning surfaces explicit and separate from the
    functional runtime path. It is intentionally descriptive: planner/runtime
    code remains the source of execution behavior.
    """

    SCHEMA_VERSION = "conversation_mainline_contract.v1"

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def build_contract(
        self,
        *,
        normalized_input: Dict[str, Any],
        dispatch_plan: Dict[str, Any],
        execution_plan: Dict[str, Any],
        work_context: Dict[str, Any],
        conversation_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        selected_path = execution_plan.get("selected_path") if isinstance(execution_plan.get("selected_path"), dict) else {}
        entry = self._trim(dispatch_plan.get("entry")) or self._trim(selected_path.get("type")) or "agent_route"
        path_type = self._trim(selected_path.get("type")) or entry
        work_items = execution_plan.get("work_items") if isinstance(execution_plan.get("work_items"), list) else []
        selected_tools = execution_plan.get("selected_tools") if isinstance(execution_plan.get("selected_tools"), list) else []
        target = dispatch_plan.get("target") if isinstance(dispatch_plan.get("target"), dict) else {}

        task_mode = self._chat_task_mode(path_type=path_type, work_items=work_items, selected_tools=selected_tools)
        runtime_lane = self._runtime_lane(path_type=path_type, task_mode=task_mode)
        return {
            "schema_version": self.SCHEMA_VERSION,
            "phase": "chat_dispatch",
            "entry": entry,
            "path_type": path_type,
            "chat_task_mode": task_mode,
            "runtime_lane": runtime_lane,
            "target": {
                "type": self._trim(target.get("type")),
                "name": self._trim(target.get("name")),
            },
            "chat_boundaries": {
                "skill_authoring_visible": False,
                "skill_persistence_visible": False,
                "allowed_skill_actions": ["run_existing_skill"],
                "allowed_dynamic_actions": ["temporary_multi_step_task", "direct_tool_plan", "agent_route"],
            },
            "responsibilities": self._responsibilities(runtime_lane=runtime_lane),
            "runtime_contract": {
                "work_item_count": len(work_items),
                "tool_count": len([item for item in work_items if self._trim((item or {}).get("type")) == "tool"])
                if work_items
                else len([item for item in selected_tools if self._trim(item)]),
                "has_planned_dag": bool(execution_plan.get("planned_dag")),
                "requires_runtime_loop": task_mode in {"temporary_multi_step_task", "compiled_skill_run"},
                "requires_user_clarification": bool(execution_plan.get("clarification_needed")),
            },
            "tuning_boundary": {
                "status": "separated_not_tuned",
                "current_policy": "functional_contract_first",
                "do_not_optimize_for_case_pass_rate": True,
                "surfaces": self._tuning_surfaces(),
            },
            "inputs": {
                "text_present": bool(self._trim(normalized_input.get("text"))),
                "has_attachments": bool(normalized_input.get("has_attachments")),
                "active_skill_name": self._trim(
                    work_context.get("thread_active_skill_canonical_name")
                    or work_context.get("thread_active_skill_name")
                    or work_context.get("active_skill_canonical_name")
                    or work_context.get("active_skill_name")
                ),
                "conversation_state": self._trim(conversation_state.get("state")),
            },
        }

    def _chat_task_mode(self, *, path_type: str, work_items: List[Any], selected_tools: List[Any]) -> str:
        if path_type == "skill_run":
            return "compiled_skill_run"
        if path_type == "planned_run":
            return "temporary_multi_step_task"
        if path_type == "tool_plan_run":
            tool_item_count = len([item for item in work_items if isinstance(item, dict) and self._trim(item.get("type")) == "tool"])
            if tool_item_count > 1 or len([item for item in selected_tools if self._trim(item)]) > 1:
                return "temporary_multi_step_task"
            return "direct_tool_plan"
        if path_type == "vision_intake":
            return "vision_task"
        if path_type in {"asset_open", "catalog_browse"}:
            return "system_navigation"
        return "agent_route"

    def _runtime_lane(self, *, path_type: str, task_mode: str) -> str:
        if task_mode == "compiled_skill_run":
            return "skill_runtime"
        if task_mode == "temporary_multi_step_task":
            return "tool_plan_runtime_loop"
        if task_mode == "direct_tool_plan":
            return "tool_plan_runtime"
        if path_type == "vision_intake":
            return "vision_runtime"
        if path_type in {"asset_open", "catalog_browse"}:
            return "system_runtime"
        return "assistant_agent_runtime"

    def _responsibilities(self, *, runtime_lane: str) -> Dict[str, List[str]]:
        planner = [
            "classify_round_task",
            "select_existing_skill_or_capability_family",
            "produce_high_level_execution_plan",
        ]
        resolver = [
            "bind_parameters",
            "resolve_tool_or_skill_capabilities",
            "report_missing_inputs_without_fabrication",
        ]
        runtime = [
            "execute_steps_by_dependency",
            "record_trace_and_artifacts",
            "surface_runtime_failures",
        ]
        judge = [
            "decide_answer_or_continue_or_ask_user",
            "compose_evidence_based_answer",
        ]
        if runtime_lane == "skill_runtime":
            planner.append("do_not_reinterpret_compiled_skill_body")
            resolver.append("load_compiled_skill_contract")
        return {
            "planner": planner,
            "resolver": resolver,
            "runtime": runtime,
            "judge": judge,
        }

    def _tuning_surfaces(self) -> List[Dict[str, str]]:
        return [
            {
                "surface": "assistant.interaction_preprocess",
                "kind": "prompt",
                "phase": "intent_and_context",
                "status": "defer_effect_tuning",
            },
            {
                "surface": "assistant.conversation_task_finalizer",
                "kind": "prompt",
                "phase": "conversation_task_finalization",
                "status": "defer_effect_tuning",
            },
            {
                "surface": "agent_runtime.planner",
                "kind": "prompt",
                "phase": "high_level_planning",
                "status": "defer_effect_tuning",
            },
            {
                "surface": "agent_runtime.plan_compiler",
                "kind": "prompt",
                "phase": "tool_dag_compilation",
                "status": "defer_effect_tuning",
            },
            {
                "surface": "tool_candidate_rerank",
                "kind": "service",
                "phase": "tool_selection",
                "status": "defer_effect_tuning",
            },
            {
                "surface": "tool_argument_compiler",
                "kind": "service",
                "phase": "argument_binding",
                "status": "defer_effect_tuning",
            },
        ]

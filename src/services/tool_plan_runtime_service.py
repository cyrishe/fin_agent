from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import re
from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.services.code_work_item_runner import CodeWorkItemRunner
from src.services.conversation_runtime_contract_service import ConversationRuntimeContractService
from src.services.result_presentation_service import ResultPresentationService
from src.services.runtime_execution_service import RuntimeExecutionService
from src.services.runtime_feedback_service import RuntimeFeedbackService
from src.services.tool_runtime_preflight_service import ToolRuntimePreflightService
from src.skill_runtime.context_retention import reduce_tool_result_for_runtime
from src.tools.registry import normalize_tool_args_for_definition, run_tool
from src.utils.ai_service import DEFAULT_CHAT_MODEL, chat_qwen_flash_json, chat_qwen_json


class ToolPlanRuntimeService:
    _RESULT_BLOCK_TYPES = {"line", "bar", "pie", "kline", "flow", "table", "metric_strip", "structured_text", "text_list"}

    def __init__(
        self,
        *,
        tool_argument_planner: Optional[object] = None,
        runtime_execution_service: Optional[RuntimeExecutionService] = None,
        code_work_item_runner: Optional[CodeWorkItemRunner] = None,
        runtime_contract_service: Optional[ConversationRuntimeContractService] = None,
        tool_runtime_preflight_service: Optional[ToolRuntimePreflightService] = None,
        runtime_feedback_service: Optional[RuntimeFeedbackService] = None,
        enable_tool_preflight: bool = True,
    ) -> None:
        self.tool_argument_planner = tool_argument_planner
        self.runtime_execution_service = runtime_execution_service or RuntimeExecutionService()
        self.code_work_item_runner = code_work_item_runner or CodeWorkItemRunner()
        self.registry = get_prompt_registry()
        self.result_presentation_service = ResultPresentationService()
        self.runtime_contract_service = runtime_contract_service or ConversationRuntimeContractService()
        self.tool_runtime_preflight_service = tool_runtime_preflight_service or ToolRuntimePreflightService()
        self.runtime_feedback_service = runtime_feedback_service or RuntimeFeedbackService()
        self.enable_tool_preflight = bool(enable_tool_preflight)

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _is_missing_value(value: Any) -> bool:
        return value is None or value == ""

    def execute_for_assistant(
        self,
        *,
        execution_plan: dict | None,
        user_text: str,
        application_context: dict | None = None,
        thread_context: dict | None = None,
        thread_id: int | None = None,
        turn_id: int | None = None,
    ) -> dict[str, Any]:
        plan = self._normalize_runtime_plan(execution_plan if isinstance(execution_plan, dict) else {})
        app_ctx = application_context if isinstance(application_context, dict) else {}
        thread_ctx = thread_context if isinstance(thread_context, dict) else {}
        runtime_trace = self._begin_runtime_trace(
            execution_plan=plan,
            user_text=user_text,
            application_context=app_ctx,
            thread_id=thread_id,
            turn_id=turn_id,
        )
        runtime_steps = [
            dict(item)
            for item in (plan.get("work_items") or [])
            if isinstance(item, dict) and self._trim(item.get("type") or item.get("item_type")) and self._trim(item.get("name") or item.get("item_action"))
        ]
        if not runtime_steps:
            runtime_steps = self._select_tool_steps(plan)
        tool_names = [self._trim(step.get("name")) for step in runtime_steps if self._trim(step.get("name"))]
        step_runs: List[Dict[str, Any]] = []
        tool_runs: List[Dict[str, Any]] = []
        step_items: List[Dict[str, Any]] = []
        shared_outputs: Dict[str, Any] = {}
        completed_step_ids: set[str] = set()
        step_outputs: Dict[str, Dict[str, Any]] = {}
        self._append_runtime_event(
            runtime_trace=runtime_trace,
            event_type="tool_plan_started",
            actor_type="planner",
            actor_id="tool_plan_runtime",
            payload={
                "objective": self._trim(plan.get("objective")) or self._trim(user_text),
                "selected_tools": tool_names,
                "selected_path": (plan.get("selected_path") if isinstance(plan.get("selected_path"), dict) else {}),
            },
        )

        ordered_results = self._execute_runtime_steps_with_scheduler(
            runtime_steps=runtime_steps,
            user_text=user_text,
            thread_context=thread_ctx,
            runtime_trace=runtime_trace,
            shared_outputs=shared_outputs,
            completed_step_ids=completed_step_ids,
            step_outputs=step_outputs,
        )
        for step, run_record in ordered_results:
            step_type = self._trim(step.get("type")) or "tool"
            run_record["feedback"] = self.runtime_feedback_service.build_step_feedback(
                run_record,
                objective=self._trim(plan.get("objective")) or self._trim(user_text),
            )
            step_runs.append(run_record)
            step_items.append(self._build_runtime_item(run_record))
            if step_type in {"tool", "code"}:
                tool_runs.append(run_record)

        final_output, llm_usage = self._build_final_output(
            objective=self._trim(plan.get("objective")) or self._trim(user_text),
            tool_runs=tool_runs,
        )
        render_payload = self._build_render_payload(
            execution_plan=plan,
            final_output=final_output,
            tool_runs=tool_runs,
        )
        runtime_feedback = self.runtime_feedback_service.build_task_feedback(
            execution_plan=plan,
            tool_runs=tool_runs,
            final_output=final_output,
            step_feedback=[
                item.get("feedback")
                for item in step_runs
                if isinstance(item.get("feedback"), dict)
            ],
        )
        completed = len([item for item in tool_runs if self._trim(item.get("status")) == "completed"])
        failed = len([item for item in tool_runs if self._trim(item.get("status")) == "failed"])
        skipped = len([item for item in tool_runs if self._trim(item.get("status")) == "skipped"])
        self._append_runtime_event(
            runtime_trace=runtime_trace,
            event_type="tool_plan_completed",
            actor_type="system",
            actor_id="tool_plan_runtime",
            payload={
                "completed_tools": completed,
                "failed_tools": failed,
                "skipped_tools": skipped,
                "selected_tools": tool_names,
            },
        )
        try:
            self.runtime_execution_service.finish_task(
                thread_id=int(runtime_trace.get("thread_id")) if runtime_trace.get("thread_id") else None,
                task_id=int(runtime_trace.get("task_id")) if runtime_trace.get("task_id") else None,
                status="completed",
                result_summary=f"tool_plan completed: ok={completed}, failed={failed}, skipped={skipped}",
            )
        except Exception:
            pass
        runtime_contract = self.runtime_contract_service.build_execution_contract(
            execution_plan=plan,
            tool_runs=step_runs,
            final_output=final_output,
            render_payload=render_payload,
            runtime_trace=runtime_trace,
            runtime_feedback=runtime_feedback,
        )
        return {
            "mode": "tool_plan_completed",
            "message": self._trim(final_output.get("summary")) or f"已完成工具执行：成功 {completed} 个，失败 {failed} 个，跳过 {skipped} 个。",
            "items": step_items,
            "task_state": self._build_task_state(
                runtime_trace=runtime_trace,
                tool_runs=step_runs,
                execution_plan=plan,
            ),
            "task_result": {
                "final_output": final_output,
                "render_payload": render_payload,
            },
            "final_output": final_output,
            "render_payload": render_payload,
            "execution_plan": plan,
            "runtime_trace": runtime_trace,
            "runtime_feedback": runtime_feedback,
            "runtime_contract": runtime_contract,
            "runtime_modules": runtime_contract.get("modules") if isinstance(runtime_contract.get("modules"), list) else [],
            "runtime_node_results": runtime_contract.get("node_results") if isinstance(runtime_contract.get("node_results"), list) else [],
            "runtime_feedback_protocol": runtime_contract.get("feedback_protocol") if isinstance(runtime_contract.get("feedback_protocol"), dict) else {},
            "model_name": DEFAULT_CHAT_MODEL if llm_usage else "",
            "llm_usage": llm_usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def _execute_runtime_steps_with_scheduler(
        self,
        *,
        runtime_steps: List[Dict[str, Any]],
        user_text: str,
        thread_context: dict,
        runtime_trace: dict,
        shared_outputs: Dict[str, Any],
        completed_step_ids: set[str],
        step_outputs: Dict[str, Dict[str, Any]],
        terminal_step_ids: Optional[set[str]] = None,
    ) -> List[tuple[Dict[str, Any], Dict[str, Any]]]:
        pending: List[tuple[int, Dict[str, Any]]] = [
            (index, step)
            for index, step in enumerate(runtime_steps)
            if isinstance(step, dict)
        ]
        ordered_results: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
        terminal_step_ids = terminal_step_ids if terminal_step_ids is not None else set()

        while pending:
            ready_batch: List[tuple[int, Dict[str, Any]]] = []
            blocked_batch: List[tuple[int, Dict[str, Any]]] = []
            for index, step in pending:
                depends_on = [self._trim(item) for item in (step.get("depends_on") or []) if self._trim(item)]
                if self._dependencies_ready(
                    step=step,
                    depends_on=depends_on,
                    completed_step_ids=completed_step_ids,
                    terminal_step_ids=terminal_step_ids,
                ):
                    ready_batch.append((index, step))
                else:
                    blocked_batch.append((index, step))
            if not ready_batch:
                for _, step in blocked_batch:
                    ordered_results.append(
                        (
                            step,
                            {
                                "step_id": self._trim(step.get("step_id")),
                                "depends_on": [self._trim(item) for item in (step.get("depends_on") or []) if self._trim(item)],
                                "tool_name": self._trim(step.get("name")) or self._trim(step.get("item_action")) or self._trim(step.get("type")) or "step",
                                "status": "skipped",
                                "reason": "blocked_missing_dependencies",
                                "plan": {},
                                "result": {},
                                "retention": {},
                                "named_outputs": {},
                            },
                        )
                    )
                break

            parallel_batch = [
                (index, step)
                for index, step in ready_batch
                if self._trim(step.get("type") or step.get("item_type")) == "tool"
            ]
            sequential_batch = [
                (index, step)
                for index, step in ready_batch
                if self._trim(step.get("type") or step.get("item_type")) != "tool"
            ]
            batch_results: List[tuple[int, Dict[str, Any], Dict[str, Any]]] = []

            if len(parallel_batch) > 1:
                with ThreadPoolExecutor(max_workers=min(4, len(parallel_batch))) as executor:
                    future_map = {
                        executor.submit(
                            self._execute_runtime_step,
                            step=step,
                            step_type=self._trim(step.get("type")) or "tool",
                            execution_mode=self._trim(step.get("execution_mode")),
                            user_text=user_text,
                            thread_context=thread_context,
                            runtime_trace=runtime_trace,
                            shared_outputs=shared_outputs,
                            completed_step_ids=completed_step_ids,
                            terminal_step_ids=terminal_step_ids,
                            step_outputs=step_outputs,
                        ): (index, step)
                        for index, step in parallel_batch
                    }
                    for future in as_completed(future_map):
                        index, step = future_map[future]
                        batch_results.append((index, step, future.result()))
            else:
                for index, step in parallel_batch:
                    batch_results.append(
                        (
                            index,
                            step,
                            self._execute_runtime_step(
                                step=step,
                                step_type=self._trim(step.get("type")) or "tool",
                                execution_mode=self._trim(step.get("execution_mode")),
                                user_text=user_text,
                                thread_context=thread_context,
                                runtime_trace=runtime_trace,
                                shared_outputs=shared_outputs,
                                completed_step_ids=completed_step_ids,
                                terminal_step_ids=terminal_step_ids,
                                step_outputs=step_outputs,
                            ),
                        )
                    )

            for index, step in sequential_batch:
                batch_results.append(
                    (
                        index,
                        step,
                        self._execute_runtime_step(
                            step=step,
                            step_type=self._trim(step.get("type")) or "tool",
                            execution_mode=self._trim(step.get("execution_mode")),
                            user_text=user_text,
                            thread_context=thread_context,
                            runtime_trace=runtime_trace,
                            shared_outputs=shared_outputs,
                            completed_step_ids=completed_step_ids,
                            terminal_step_ids=terminal_step_ids,
                            step_outputs=step_outputs,
                        ),
                    )
                )

            for _, step, run_record in sorted(batch_results, key=lambda item: item[0]):
                ordered_results.append((step, run_record))
                step_id = self._trim(step.get("step_id"))
                if step_id:
                    terminal_step_ids.add(step_id)
                if self._trim(run_record.get("status")) == "completed":
                    if step_id:
                        completed_step_ids.add(step_id)
                        step_outputs[step_id] = {
                            "named_outputs": dict(run_record.get("named_outputs") or {}) if isinstance(run_record.get("named_outputs"), dict) else {},
                            "result": dict(run_record.get("result") or {}) if isinstance(run_record.get("result"), dict) else {},
                            "retention": dict(run_record.get("retention") or {}) if isinstance(run_record.get("retention"), dict) else {},
                        }
                    exported_outputs = self._merge_shared_outputs(
                        shared_outputs=shared_outputs,
                        step=step,
                        run_record=run_record,
                    )
                    if step_id and exported_outputs:
                        step_output_payload = step_outputs.get(step_id) if isinstance(step_outputs.get(step_id), dict) else {}
                        existing_named = step_output_payload.get("named_outputs") if isinstance(step_output_payload.get("named_outputs"), dict) else {}
                        step_output_payload["named_outputs"] = {
                            **existing_named,
                            **exported_outputs,
                        }
                        step_outputs[step_id] = step_output_payload
            pending = blocked_batch
        return ordered_results

    def _dependencies_ready(
        self,
        *,
        step: Dict[str, Any],
        depends_on: List[str],
        completed_step_ids: set[str],
        terminal_step_ids: set[str],
    ) -> bool:
        if all(dep in completed_step_ids for dep in depends_on):
            return True
        if self._allows_partial_dependency_execution(step=step):
            return all(dep in terminal_step_ids for dep in depends_on)
        return False

    def _allows_partial_dependency_execution(self, *, step: Dict[str, Any]) -> bool:
        step_type = self._trim(step.get("type") or step.get("item_type")).lower()
        step_name = self._trim(step.get("name") or step.get("item_action")).lower()
        return step_type in {"synthesis", "presentation"} or step_name in {"final_synthesis", "presentation_plan"}

    def _select_runtime_steps(self, plan: dict) -> List[Dict[str, Any]]:
        steps: List[Dict[str, Any]] = []
        for item in plan.get("work_items") or []:
            if not isinstance(item, dict):
                continue
            item_type = self._trim(item.get("type") or item.get("item_type"))
            item_name = self._trim(item.get("name") or item.get("item_action"))
            if item_type and item_name:
                steps.append(dict(item))
        if steps:
            return steps
        return self._select_tool_steps(plan)

    def _select_tool_steps(self, plan: dict) -> List[Dict[str, Any]]:
        steps: List[Dict[str, Any]] = []
        for item in plan.get("work_items") or []:
            if isinstance(item, dict) and self._trim(item.get("type") or item.get("item_type")) == "tool":
                tool_name = self._trim(item.get("name") or item.get("item_action"))
                if tool_name:
                    steps.append(dict(item))
        if steps:
            return steps
        names: List[str] = []
        for item in plan.get("candidate_tools") or []:
            if isinstance(item, dict):
                tool_name = self._trim(item.get("tool_name"))
                if tool_name and tool_name not in names:
                    names.append(tool_name)
        generated: List[Dict[str, Any]] = []
        for index, tool_name in enumerate(names[:4], start=1):
            generated.append(
                {
                    "step_id": f"step_{index}",
                    "depends_on": [],
                    "type": "tool",
                    "name": tool_name,
                    "status": "planned",
                    "input_binding": {},
                    "output_binding": {},
                }
            )
        return generated

    def _normalize_runtime_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        normalized = deepcopy(plan if isinstance(plan, dict) else {})
        work_items = normalized.get("work_items") if isinstance(normalized.get("work_items"), list) else []
        normalized_items: List[Dict[str, Any]] = []
        alias_map: Dict[str, str] = {}
        previous_step_id = ""
        for index, raw_item in enumerate(work_items, start=1):
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            step_id = self._trim(item.get("step_id")) or f"step_{index}"
            item["step_id"] = step_id
            raw_item_type = self._trim(item.get("type") or item.get("item_type"))
            execution_mode = self._trim(item.get("execution_mode"))
            item_type = "tool" if raw_item_type == "foreach" else raw_item_type
            item_name = self._trim(item.get("name") or item.get("item_action"))
            if item_type:
                item["type"] = item_type
                item["item_type"] = item_type
            if item_name:
                item["name"] = item_name
                item["item_action"] = self._trim(item.get("item_action")) or item_name
            item["execution_mode"] = execution_mode or ("foreach" if raw_item_type == "foreach" else "direct")
            item["foreach_binding"] = dict(item.get("foreach_binding") or {}) if isinstance(item.get("foreach_binding"), dict) else {}
            transform_spec = item.get("transform_spec") if isinstance(item.get("transform_spec"), dict) else {}
            if item_type == "transform" and not self._trim(transform_spec.get("dsl")) and self._trim(item.get("item_action")):
                item["transform_spec"] = {**transform_spec, "dsl": self._trim(item.get("item_action"))}
            alias_map[step_id] = step_id
            alias_map[f"step_{index}"] = step_id
            if previous_step_id and self._trim(item.get("type")) == "transform":
                alias_map[f"{previous_step_id}_transform"] = step_id
            normalized_items.append(item)
            previous_step_id = step_id
        for item in normalized_items:
            depends_on = item.get("depends_on") if isinstance(item.get("depends_on"), list) else []
            item["depends_on"] = [alias_map.get(self._trim(dep), self._trim(dep)) for dep in depends_on if self._trim(dep)]
            item["input_binding"] = self._normalize_binding_map(
                item.get("input_binding") if isinstance(item.get("input_binding"), dict) else {},
                alias_map=alias_map,
            )
            item["output_binding"] = self._normalize_binding_map(
                item.get("output_binding") if isinstance(item.get("output_binding"), dict) else {},
                alias_map=alias_map,
            )
        normalized["work_items"] = normalized_items
        return normalized

    def _normalize_binding_map(self, bindings: Dict[str, Any], *, alias_map: Dict[str, str]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        for key, value in bindings.items():
            normalized_key = self._trim(key)
            if not normalized_key:
                continue
            normalized[normalized_key] = self._normalize_binding_expr(value, alias_map=alias_map)
        return normalized

    def _normalize_binding_expr(self, value: Any, *, alias_map: Dict[str, str]) -> Any:
        if not isinstance(value, str):
            return value
        text = str(value or "").strip()
        if not text:
            return value
        if text.startswith("${") and text.endswith("}"):
            inner = self._normalize_reference_text(text[2:-1].strip(), alias_map=alias_map)
            return "${" + inner + "}"
        if text.startswith("$"):
            inner = self._normalize_reference_text(text[1:].strip(), alias_map=alias_map)
            return "$" + inner
        return value

    def _normalize_reference_text(self, text: str, *, alias_map: Dict[str, str]) -> str:
        normalized = str(text or "").strip()
        if not normalized:
            return normalized
        if normalized.startswith("step_") and ".output_binding." in normalized:
            step_ref, tail = normalized.split(".output_binding.", 1)
            normalized = f"{step_ref}.{tail}"
        if "." in normalized:
            head, tail = normalized.split(".", 1)
            return f"{alias_map.get(head, head)}.{tail}"
        return alias_map.get(normalized, normalized)

    def _execute_single_tool(
        self,
        *,
        step: Dict[str, Any],
        tool_name: str,
        user_text: str,
        thread_context: dict,
        runtime_trace: dict,
        shared_outputs: Dict[str, Any],
        completed_step_ids: set[str],
        step_outputs: Dict[str, Dict[str, Any]],
        resolved_inputs_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        step_id = self._trim(step.get("step_id"))
        depends_on = [self._trim(item) for item in (step.get("depends_on") or []) if self._trim(item)]
        unmet_dependencies = [item for item in depends_on if item not in completed_step_ids]
        if unmet_dependencies:
            self._append_runtime_event(
                runtime_trace=runtime_trace,
                event_type="tool_blocked",
                actor_type="planner",
                actor_id=tool_name,
                payload={
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "reason": "unmet_dependencies",
                    "depends_on": depends_on,
                    "unmet_dependencies": unmet_dependencies,
                },
            )
            return {
                "step_id": step_id,
                "depends_on": depends_on,
                "tool_name": tool_name,
                "status": "skipped",
                "reason": "unmet_dependencies",
                "plan": {},
                "result": {},
                "retention": {},
            }
        resolved_inputs = (
            dict(resolved_inputs_override)
            if isinstance(resolved_inputs_override, dict)
            else self._resolve_step_inputs(
                step=step,
                shared_outputs=shared_outputs,
                step_outputs=step_outputs,
            )
        )
        static_arguments = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
        raw_tool_args = {
            key: value
            for key, value in {**static_arguments, **resolved_inputs}.items()
            if not self._is_missing_value(value)
        }
        contract_normalized = normalize_tool_args_for_definition(tool_name, raw_tool_args)
        normalized_tool_args = contract_normalized.get("arguments") if isinstance(contract_normalized.get("arguments"), dict) else raw_tool_args
        dropped_fields = contract_normalized.get("dropped_fields") if isinstance(contract_normalized.get("dropped_fields"), list) else []
        missing_required = contract_normalized.get("missing_required") if isinstance(contract_normalized.get("missing_required"), list) else []
        used_defaults = contract_normalized.get("used_defaults") if isinstance(contract_normalized.get("used_defaults"), dict) else {}
        if missing_required:
            self._append_runtime_event(
                runtime_trace=runtime_trace,
                event_type="tool_skipped",
                actor_type="planner",
                actor_id=tool_name,
                payload={
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "reason": "missing_required_by_schema",
                    "status": "skipped",
                    "missing_arguments": missing_required,
                    "resolved_inputs": resolved_inputs,
                },
            )
            return {
                "step_id": step_id,
                "depends_on": depends_on,
                "tool_name": tool_name,
                "status": "skipped",
                "reason": "missing_required_by_schema",
                "plan": {"arguments": normalized_tool_args, "missing_arguments": missing_required},
                "result": {},
                "retention": {},
                "named_outputs": {},
            }
        effective_plan = {
            "arguments": normalized_tool_args,
            "dropped_arguments": dropped_fields,
            "defaults_applied_by_schema": used_defaults,
        }
        custom_tool_owner_ids = [
            self._trim(item)
            for item in (thread_context.get("_custom_tool_owner_ids") or [])
            if self._trim(item)
        ]
        if self.enable_tool_preflight:
            try:
                preflight = self.tool_runtime_preflight_service.validate_tool_call(
                    tool_name=tool_name,
                    arguments=normalized_tool_args,
                    custom_tool_owner_ids=custom_tool_owner_ids,
                )
            except TypeError:
                preflight = self.tool_runtime_preflight_service.validate_tool_call(
                    tool_name=tool_name,
                    arguments=normalized_tool_args,
                )
            if preflight.get("ok") is not True:
                self._append_runtime_event(
                    runtime_trace=runtime_trace,
                    event_type="tool_skipped",
                    actor_type="registry",
                    actor_id=tool_name,
                    payload={
                        "step_id": step_id,
                        "tool_name": tool_name,
                        "reason": self._trim(preflight.get("reason")) or "tool_preflight_blocked",
                        "status": "skipped",
                        "preflight": preflight,
                    },
                )
                return {
                    "step_id": step_id,
                    "depends_on": depends_on,
                    "tool_name": tool_name,
                    "status": "skipped",
                    "reason": self._trim(preflight.get("reason")) or "tool_preflight_blocked",
                    "plan": {**effective_plan, "preflight": preflight},
                    "result": {},
                    "retention": {},
                    "named_outputs": {},
                }
        try:
            tool_args = dict(normalized_tool_args)
            tool_args["_runtime"] = {
                "thread_id": runtime_trace.get("thread_id"),
                "task_id": runtime_trace.get("task_id"),
                "turn_id": runtime_trace.get("turn_id"),
                "thread_type": "chat",
                "task_type": self._trim(runtime_trace.get("plan_type")) or "tool_plan_run",
                "goal": self._trim(runtime_trace.get("goal")) or self._trim(runtime_trace.get("plan_type")) or "tool_plan_run",
                "assigned_agent": "tool_plan_runtime",
                "source_type": "assistant_planned_runtime" if self._trim(runtime_trace.get("plan_type")) == "planned_run" else "assistant_tool_plan_run",
                "custom_tool_owner_ids": custom_tool_owner_ids,
                # ToolPlanRuntimeService owns the invocation lifecycle for this
                # path.  The registry still resolves and invokes the tool, but
                # must not create a second invocation/event pair for the same
                # physical call.
                "_execution_tracking_owner": "tool_plan_runtime",
            }
            if dropped_fields:
                self._append_runtime_event(
                    runtime_trace=runtime_trace,
                    event_type="tool_argument_planned",
                    actor_type="planner",
                    actor_id="tool_argument_normalizer",
                    payload={
                        "tool_name": tool_name,
                        "step_id": step_id,
                        "status": "normalized",
                        "arguments": normalized_tool_args,
                        "missing_arguments": [],
                        "resolved_inputs": {"dropped_fields": dropped_fields, "used_defaults": used_defaults},
                    },
                )
            result = self.runtime_execution_service.execute_tool(
                tool_name=tool_name,
                args=tool_args,
                executor=lambda args: run_tool(
                    tool_name,
                    args,
                    runtime_ctx=tool_args.get("_runtime") if isinstance(tool_args.get("_runtime"), dict) else {},
                ),
            )
        except Exception as exc:
            self._append_runtime_event(
                runtime_trace=runtime_trace,
                event_type="tool_runtime_error",
                actor_type="tool",
                actor_id=tool_name,
                payload={
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "status": "failed",
                    "reason": "runtime_error",
                    "error": str(exc),
                },
            )
            return {
                "step_id": step_id,
                "depends_on": depends_on,
                "tool_name": tool_name,
                "status": "failed",
                "reason": "runtime_error",
                "error": str(exc),
                "plan": effective_plan,
                "result": {},
                "retention": {},
                "named_outputs": {},
            }
        try:
            retention = reduce_tool_result_for_runtime(tool_name, result if isinstance(result, dict) else {})
        except Exception as exc:
            self._append_runtime_event(
                runtime_trace=runtime_trace,
                event_type="tool_runtime_error",
                actor_type="runtime",
                actor_id="context_retention",
                payload={
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "status": "failed",
                    "reason": "result_retention_error",
                    "error": str(exc),
                },
            )
            return {
                "step_id": step_id,
                "depends_on": depends_on,
                "tool_name": tool_name,
                "status": "failed",
                "reason": "result_retention_error",
                "error": str(exc),
                "failure_kind": "result_retention_error",
                "plan": effective_plan,
                "result": result if isinstance(result, dict) else {},
                "retention": {},
                "named_outputs": {},
            }
        result_meta = (
            (result or {}).get("meta")
            if isinstance((result or {}).get("meta"), dict)
            else {}
        )
        return {
            "step_id": step_id,
            "depends_on": depends_on,
            "tool_name": tool_name,
            "status": "completed" if bool((result or {}).get("ok")) else "failed",
            "reason": "ok" if bool((result or {}).get("ok")) else "tool_failed",
            "error": self._trim((result or {}).get("error")),
            "failure_kind": self._trim(
                result_meta.get("failure_kind")
            ),
            "plan": effective_plan,
            "result": result if isinstance(result, dict) else {},
            "retention": retention,
            "named_outputs": {},
        }

    def _execute_runtime_step(
        self,
        *,
        step: Dict[str, Any],
        step_type: str,
        execution_mode: str,
        user_text: str,
        thread_context: dict,
        runtime_trace: dict,
        shared_outputs: Dict[str, Any],
        completed_step_ids: set[str],
        step_outputs: Dict[str, Dict[str, Any]],
        terminal_step_ids: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        if step_type == "tool" and execution_mode == "foreach":
            return self._execute_foreach_step(
                step=step,
                user_text=user_text,
                thread_context=thread_context,
                runtime_trace=runtime_trace,
                shared_outputs=shared_outputs,
                completed_step_ids=completed_step_ids,
                step_outputs=step_outputs,
            )
        if step_type == "tool":
            return self._execute_single_tool(
                step=step,
                tool_name=self._trim(step.get("name")),
                user_text=user_text,
                thread_context=thread_context,
                runtime_trace=runtime_trace,
                shared_outputs=shared_outputs,
                completed_step_ids=completed_step_ids,
                step_outputs=step_outputs,
            )
        if step_type == "code":
            return self._execute_code_step(
                step=step,
                runtime_trace=runtime_trace,
                shared_outputs=shared_outputs,
                completed_step_ids=completed_step_ids,
                step_outputs=step_outputs,
            )
        if step_type == "transform":
            return self._execute_transform_step(
                step=step,
                runtime_trace=runtime_trace,
                shared_outputs=shared_outputs,
                completed_step_ids=completed_step_ids,
                step_outputs=step_outputs,
            )
        if step_type == "foreach":
            return self._execute_foreach_step(
                step=step,
                user_text=user_text,
                thread_context=thread_context,
                runtime_trace=runtime_trace,
                shared_outputs=shared_outputs,
                completed_step_ids=completed_step_ids,
                step_outputs=step_outputs,
            )
        return self._complete_non_tool_step(
            step=step,
            runtime_trace=runtime_trace,
            completed_step_ids=completed_step_ids,
            terminal_step_ids=terminal_step_ids or set(),
        )

    def _execute_code_step(
        self,
        *,
        step: Dict[str, Any],
        runtime_trace: dict,
        shared_outputs: Dict[str, Any],
        completed_step_ids: set[str],
        step_outputs: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        step_id = self._trim(step.get("step_id"))
        depends_on = [self._trim(item) for item in (step.get("depends_on") or []) if self._trim(item)]
        step_name = self._trim(step.get("name")) or "analysis_python"
        unmet_dependencies = [item for item in depends_on if item not in completed_step_ids]
        if unmet_dependencies:
            return {
                "step_id": step_id,
                "depends_on": depends_on,
                "tool_name": step_name,
                "status": "skipped",
                "reason": "unmet_dependencies",
                "plan": {},
                "result": {},
                "retention": {},
                "named_outputs": {},
            }
        resolved_inputs = self._resolve_step_inputs(
            step=step,
            shared_outputs=shared_outputs,
            step_outputs=step_outputs,
        )
        missing_bindings = self._find_unresolved_bindings(step=step, resolved_inputs=resolved_inputs)
        if missing_bindings:
            self._append_runtime_event(
                runtime_trace=runtime_trace,
                event_type="code_runtime_error",
                actor_type="code_runtime",
                actor_id=step_name,
                payload={
                    "step_id": step_id,
                    "status": "failed",
                    "failure_kind": "missing_upstream_binding",
                    "missing_bindings": missing_bindings,
                },
            )
            return {
                "step_id": step_id,
                "depends_on": depends_on,
                "tool_name": step_name,
                "status": "failed",
                "reason": "missing_upstream_binding",
                "error": "missing code input bindings: " + ", ".join(missing_bindings),
                "plan": {"arguments": resolved_inputs, "missing_bindings": missing_bindings},
                "result": {
                    "tool": step_name,
                    "name": step_name,
                    "ok": False,
                    "data": {"structured_data": {}, "render_blocks": [], "artifacts": []},
                    "error": "missing code input bindings: " + ", ".join(missing_bindings),
                    "meta": {"failure_kind": "missing_upstream_binding"},
                },
                "retention": {},
                "named_outputs": {},
                "failure_kind": "missing_upstream_binding",
            }
        return self.code_work_item_runner.run(
            step=step,
            resolved_inputs=resolved_inputs,
            runtime_trace=runtime_trace,
            append_event=self._append_runtime_event,
        )

    def _execute_transform_step(
        self,
        *,
        step: Dict[str, Any],
        runtime_trace: dict,
        shared_outputs: Dict[str, Any],
        completed_step_ids: set[str],
        step_outputs: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        step_id = self._trim(step.get("step_id"))
        depends_on = [self._trim(item) for item in (step.get("depends_on") or []) if self._trim(item)]
        step_name = self._trim(step.get("name")) or "transform"
        unmet_dependencies = [item for item in depends_on if item not in completed_step_ids]
        if unmet_dependencies:
            return {
                "step_id": step_id,
                "depends_on": depends_on,
                "tool_name": step_name,
                "status": "skipped",
                "reason": "unmet_dependencies",
                "plan": {},
                "result": {},
                "retention": {},
                "named_outputs": {},
            }
        transform_result, named_outputs = self._run_transform_logic(
            step=step,
            shared_outputs=shared_outputs,
            step_outputs=step_outputs,
        )
        if transform_result is None and not named_outputs:
            return {
                "step_id": step_id,
                "depends_on": depends_on,
                "tool_name": step_name,
                "status": "skipped",
                "reason": "missing_upstream_binding",
                "plan": {"arguments": {}},
                "result": {},
                "retention": {},
                "named_outputs": {},
            }
        self._append_runtime_event(
            runtime_trace=runtime_trace,
            event_type="tool_result",
            actor_type="transform",
            actor_id=step_name,
            payload={
                "step_id": step_id,
                "tool_name": step_name,
                "display_name": self._runtime_step_display_name(step=step),
                "ok": True,
                "summary": ",".join(sorted(named_outputs.keys())[:4]),
            },
        )
        return {
            "step_id": step_id,
            "depends_on": depends_on,
            "tool_name": step_name,
            "status": "completed",
            "reason": "ok",
            "plan": {"arguments": {}},
            "result": {"tool": step_name, "ok": True, "data": transform_result, "error": ""},
            "retention": {"prompt_context": {"derived": named_outputs}},
            "named_outputs": named_outputs,
        }

    def _execute_foreach_step(
        self,
        *,
        step: Dict[str, Any],
        user_text: str,
        thread_context: dict,
        runtime_trace: dict,
        shared_outputs: Dict[str, Any],
        completed_step_ids: set[str],
        step_outputs: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        step_id = self._trim(step.get("step_id"))
        depends_on = [self._trim(item) for item in (step.get("depends_on") or []) if self._trim(item)]
        tool_name = self._trim(step.get("name"))
        unmet_dependencies = [item for item in depends_on if item not in completed_step_ids]
        if unmet_dependencies:
            return {
                "step_id": step_id,
                "depends_on": depends_on,
                "tool_name": tool_name,
                "status": "skipped",
                "reason": "unmet_dependencies",
                "plan": {},
                "result": {},
                "retention": {},
                "named_outputs": {},
            }
        loop_items = self._infer_foreach_items(step=step, shared_outputs=shared_outputs, step_outputs=step_outputs)
        child_runs: List[Dict[str, Any]] = []
        prompt_contexts: List[Dict[str, Any]] = []
        for index, loop_item in enumerate(loop_items, start=1):
            resolved_inputs = self._resolve_step_inputs(
                step=step,
                shared_outputs=shared_outputs,
                step_outputs=step_outputs,
                loop_item=loop_item,
                loop_index=index - 1,
            )
            child_run = self._execute_single_tool(
                step={
                    **step,
                    "step_id": f"{step_id}.{index}",
                    "depends_on": [],
                    "type": "tool",
                },
                tool_name=tool_name,
                user_text=user_text,
                thread_context=thread_context,
                runtime_trace=runtime_trace,
                shared_outputs=shared_outputs,
                completed_step_ids=set(),
                step_outputs=step_outputs,
                resolved_inputs_override=resolved_inputs,
            )
            child_runs.append(child_run)
            retention = child_run.get("retention") if isinstance(child_run.get("retention"), dict) else {}
            prompt_context = retention.get("prompt_context") if isinstance(retention.get("prompt_context"), dict) else {}
            if prompt_context:
                prompt_contexts.append({"item": loop_item, "prompt_context": prompt_context})
        named_outputs: Dict[str, Any] = {}
        output_binding = step.get("output_binding") if isinstance(step.get("output_binding"), dict) else {}
        child_results = [item.get("result") for item in child_runs if isinstance(item.get("result"), dict)]
        for target_name in output_binding.values():
            normalized_target = self._trim(target_name)
            if normalized_target:
                named_outputs[normalized_target] = child_results
        return {
            "step_id": step_id,
            "depends_on": depends_on,
            "tool_name": tool_name,
            "status": "completed" if child_runs else "skipped",
            "reason": "ok" if child_runs else "empty_iteration",
            "plan": {"arguments": {"foreach_count": len(loop_items)}},
            "result": {"tool": tool_name, "ok": bool(child_runs), "data": child_results, "error": ""},
            "retention": {"prompt_context": {"foreach_results": prompt_contexts}},
            "named_outputs": named_outputs,
        }

    def _complete_non_tool_step(
        self,
        *,
        step: Dict[str, Any],
        runtime_trace: dict,
        completed_step_ids: set[str],
        terminal_step_ids: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        step_id = self._trim(step.get("step_id"))
        depends_on = [self._trim(item) for item in (step.get("depends_on") or []) if self._trim(item)]
        step_name = self._trim(step.get("name")) or self._trim(step.get("type")) or "step"
        unmet_dependencies = [item for item in depends_on if item not in completed_step_ids]
        terminal_step_ids = terminal_step_ids or set()
        if unmet_dependencies and not (
            self._allows_partial_dependency_execution(step=step)
            and all(item in terminal_step_ids for item in depends_on)
        ):
            return {
                "step_id": step_id,
                "depends_on": depends_on,
                "tool_name": step_name,
                "status": "skipped",
                "reason": "unmet_dependencies",
                "plan": {},
                "result": {},
                "retention": {},
                "named_outputs": {},
            }
        self._append_runtime_event(
            runtime_trace=runtime_trace,
            event_type="tool_result",
            actor_type=self._trim(step.get("type")) or "planner",
            actor_id=step_name,
            payload={
                "step_id": step_id,
                "tool_name": step_name,
                "display_name": self._runtime_step_display_name(step=step),
                "ok": True,
                "partial_dependencies": unmet_dependencies,
            },
        )
        return {
            "step_id": step_id,
            "depends_on": depends_on,
            "tool_name": step_name,
            "status": "completed",
            "reason": "partial_dependencies" if unmet_dependencies else "ok",
            "plan": {},
            "result": {
                "tool": step_name,
                "ok": True,
                "data": {"partial_dependencies": unmet_dependencies} if unmet_dependencies else {},
                "error": "",
            },
            "retention": {},
            "named_outputs": {},
        }

    def _begin_runtime_trace(
        self,
        *,
        execution_plan: dict,
        user_text: str,
        application_context: dict,
        thread_id: int | None,
        turn_id: int | None,
    ) -> Dict[str, Any]:
        objective = self._trim(execution_plan.get("objective")) or self._trim(user_text)
        default_agent = application_context.get("default_agent") if isinstance(application_context.get("default_agent"), dict) else {}
        try:
            trace = self.runtime_execution_service.begin_artifact_run(
                artifact_type="tool_plan",
                artifact_name="assistant_tool_plan",
                input_payload={
                    "objective": objective,
                    "execution_plan": execution_plan,
                },
                runtime_ctx={
                    "thread_id": int(thread_id) if thread_id else None,
                    "turn_id": int(turn_id) if turn_id else None,
                    "thread_type": "chat",
                    "thread_title": f"tool_plan:{objective[:48]}",
                    "owner_type": "system",
                    "owner_id": "tool_plan_runtime",
                    "source_type": "assistant_planned_runtime" if self._trim(execution_plan.get("plan_type")) == "planned_run" else "assistant_tool_plan_run",
                    "context_summary": objective[:255],
                    "task_type": self._trim(execution_plan.get("plan_type")) or "tool_plan_run",
                    "goal": objective[:255],
                    "assigned_agent": self._trim(default_agent.get("agent_name")) or "tool_plan_runtime",
                },
            )
            trace["turn_id"] = int(turn_id) if turn_id else None
            trace["goal"] = objective
            trace["plan_type"] = self._trim(execution_plan.get("plan_type")) or "tool_plan_run"
            trace["local_events"] = []
            return trace
        except Exception:
            return {
                "thread_id": int(thread_id) if thread_id else None,
                "turn_id": int(turn_id) if turn_id else None,
                "task_id": None,
                "goal": objective,
                "plan_type": self._trim(execution_plan.get("plan_type")) or "tool_plan_run",
                "local_events": [],
            }

    def _append_runtime_event(
        self,
        *,
        runtime_trace: dict,
        event_type: str,
        actor_type: str,
        actor_id: str,
        payload: dict,
    ) -> None:
        local_events = runtime_trace.get("local_events")
        if isinstance(local_events, list):
            local_events.append(
                {
                    "event_type": self._trim(event_type),
                    "actor_type": self._trim(actor_type),
                    "actor_id": self._trim(actor_id),
                    "payload": dict(payload or {}),
                }
            )
        thread_id = runtime_trace.get("thread_id")
        if not thread_id:
            return
        try:
            self.runtime_execution_service.append_event(
                thread_id=int(thread_id),
                task_id=int(runtime_trace.get("task_id")) if runtime_trace.get("task_id") else None,
                turn_id=int(runtime_trace.get("turn_id")) if runtime_trace.get("turn_id") else None,
                event_type=event_type,
                actor_type=actor_type,
                actor_id=actor_id,
                payload=payload,
            )
        except Exception:
            return

    def _build_runtime_item(self, run_record: Dict[str, Any]) -> Dict[str, Any]:
        plan = run_record.get("plan") if isinstance(run_record.get("plan"), dict) else {}
        result = run_record.get("result") if isinstance(run_record.get("result"), dict) else {}
        return {
            "step_id": self._trim(run_record.get("step_id")),
            "depends_on": run_record.get("depends_on") if isinstance(run_record.get("depends_on"), list) else [],
            "name": self._trim(run_record.get("tool_name")),
            "display_name": self._trim(run_record.get("tool_name")),
            "status": self._trim(run_record.get("status")) or "unknown",
            "reason": self._trim(run_record.get("reason")),
            "tool_name": self._trim(run_record.get("tool_name")),
            "arguments": plan.get("arguments") if isinstance(plan.get("arguments"), dict) else {},
            "error": self._trim(run_record.get("error") or result.get("error")),
            "failure_kind": self._trim(run_record.get("failure_kind")),
            "feedback": run_record.get("feedback") if isinstance(run_record.get("feedback"), dict) else {},
        }

    def _build_task_state(
        self,
        *,
        runtime_trace: Dict[str, Any],
        tool_runs: List[Dict[str, Any]],
        execution_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        task_id = runtime_trace.get("task_id")
        plan_type = self._trim(execution_plan.get("plan_type")) or "tool_plan_run"
        plan_mode = self._trim(execution_plan.get("task_mode")) or ("planned" if plan_type == "planned_run" else "simple")
        completed = len([item for item in tool_runs if self._trim(item.get("status")) == "completed"])
        failed = len([item for item in tool_runs if self._trim(item.get("status")) == "failed"])
        skipped = len([item for item in tool_runs if self._trim(item.get("status")) == "skipped"])
        steps: List[Dict[str, Any]] = []
        local_events = runtime_trace.get("local_events") if isinstance(runtime_trace.get("local_events"), list) else []
        for index, event in enumerate(local_events, start=1):
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            event_type = self._trim(event.get("event_type")) or "progress"
            title = self._event_title(event_type=event_type, payload=payload)
            message = self._event_message(event_type=event_type, payload=payload)
            stage = self._event_stage(event_type=event_type)
            steps.append(
                {
                    "seq": index,
                    "stage": stage,
                    "step_type": event_type,
                    "title": title,
                    "message": message,
                    "status": self._event_status(event_type=event_type, payload=payload),
                }
            )
        return {
            "job": {
                "job_id": f"rt_{task_id}" if task_id else "tool_plan_runtime",
                "task_type": plan_type,
                "plan_mode": plan_mode,
                "status": "succeeded" if failed == 0 else "failed",
                "current_stage": "completed",
                "progress": 100.0,
                "result_summary": f"ok={completed}, failed={failed}, skipped={skipped}",
            },
            "steps": steps,
        }

    def _event_stage(self, *, event_type: str) -> str:
        if event_type in {"tool_plan_started", "tool_argument_planned", "tool_blocked", "tool_skipped"}:
            return "planning"
        if event_type in {"tool_call", "tool_result", "tool_runtime_error", "code_call", "code_result", "code_retry", "code_runtime_error"}:
            return "executing"
        if event_type == "tool_plan_completed":
            return "completed"
        return "progress"

    def _event_title(self, *, event_type: str, payload: Dict[str, Any]) -> str:
        display_name = self._trim(payload.get("display_name"))
        tool_name = self._trim(payload.get("tool_name"))
        if event_type == "tool_plan_started":
            return "生成执行计划"
        if event_type == "tool_plan_completed":
            return "执行完成"
        if event_type == "tool_argument_planned":
            return f"规划参数：{tool_name or 'tool'}"
        if event_type == "tool_blocked":
            return f"等待依赖：{tool_name or 'tool'}"
        if event_type == "tool_skipped":
            return f"跳过工具：{tool_name or 'tool'}"
        if event_type == "tool_call":
            return f"执行工具：{display_name or tool_name or 'tool'}"
        if event_type == "tool_result":
            return f"工具结果：{display_name or tool_name or 'tool'}"
        if event_type == "tool_runtime_error":
            return f"执行异常：{display_name or tool_name or 'tool'}"
        if event_type == "code_call":
            return "执行 Python 代码"
        if event_type == "code_result":
            return "代码执行完成"
        if event_type == "code_retry":
            return "重试代码执行"
        if event_type == "code_runtime_error":
            return "代码执行异常"
        return event_type

    def _event_message(self, *, event_type: str, payload: Dict[str, Any]) -> str:
        if event_type == "tool_plan_started":
            selected_tools = payload.get("selected_tools") if isinstance(payload.get("selected_tools"), list) else []
            return f"候选工具：{', '.join([self._trim(x) for x in selected_tools if self._trim(x)])}"
        if event_type == "tool_plan_completed":
            return (
                f"成功 {int(payload.get('completed_tools') or 0)} 个，"
                f"失败 {int(payload.get('failed_tools') or 0)} 个，"
                f"跳过 {int(payload.get('skipped_tools') or 0)} 个。"
            )
        if event_type == "tool_argument_planned":
            missing = payload.get("missing_arguments") if isinstance(payload.get("missing_arguments"), list) else []
            if missing:
                return f"缺少参数：{', '.join([self._trim(x) for x in missing if self._trim(x)])}"
            arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
            if arguments:
                return f"参数已就绪：{self._summary_arguments(arguments)}"
            return "参数规划完成。"
        if event_type in {"tool_blocked", "tool_skipped"}:
            reason = self._trim(payload.get("reason")) or "unknown"
            missing = payload.get("missing_arguments") if isinstance(payload.get("missing_arguments"), list) else []
            if missing:
                return f"{reason}；缺少参数：{', '.join([self._trim(x) for x in missing if self._trim(x)])}"
            return reason
        if event_type == "tool_result":
            status = "成功" if bool(payload.get("ok", True)) else "失败"
            return f"{status}"
        if event_type == "tool_runtime_error":
            return self._trim(payload.get("error")) or "runtime_error"
        if event_type == "code_call":
            return f"attempt={int(payload.get('attempt') or 1)}"
        if event_type == "code_result":
            diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
            duration_ms = int(diagnostics.get("duration_ms") or 0)
            return f"成功，耗时 {duration_ms} ms。"
        if event_type == "code_retry":
            return self._trim(payload.get("failure_kind")) or "retry"
        if event_type == "code_runtime_error":
            return self._trim(payload.get("failure_kind")) or "runtime_error"
        return ""

    def _event_status(self, *, event_type: str, payload: Dict[str, Any]) -> str:
        if event_type in {"tool_result", "code_result"}:
            return "completed" if bool(payload.get("ok", True)) else "failed"
        if event_type in {"tool_runtime_error", "code_runtime_error"}:
            return "failed"
        if event_type == "code_retry":
            return "running"
        if event_type == "tool_skipped":
            return "skipped"
        if event_type == "tool_blocked":
            return "blocked"
        if event_type == "tool_plan_completed":
            return "completed"
        return "completed"

    def _summary_arguments(self, arguments: Dict[str, Any]) -> str:
        pairs: List[str] = []
        for key, value in list(arguments.items())[:4]:
            key_text = self._trim(key)
            if not key_text:
                continue
            value_text = self._trim(value)
            if len(value_text) > 32:
                value_text = f"{value_text[:29]}..."
            pairs.append(f"{key_text}={value_text}")
        return ", ".join(pairs)

    def _runtime_step_display_name(self, *, step: Dict[str, Any]) -> str:
        step_type = self._trim(step.get("type") or step.get("item_type"))
        step_name = self._trim(step.get("name") or step.get("item_action"))
        if step_type == "transform":
            if "top(" in step_name or "first()" in step_name or "sort(" in step_name:
                return "整理筛选结果"
            if "project(" in step_name:
                return "整理中间结果"
            return "处理中间结果"
        if step_type == "foreach":
            return "批量执行"
        if step_type == "synthesis":
            return "汇总结果"
        return step_name or step_type or "step"

    def _resolve_step_inputs(
        self,
        *,
        step: Dict[str, Any],
        shared_outputs: Dict[str, Any],
        step_outputs: Dict[str, Dict[str, Any]],
        loop_item: Any = None,
        loop_index: int | None = None,
    ) -> Dict[str, Any]:
        bindings = step.get("input_binding") if isinstance(step.get("input_binding"), dict) else {}
        resolved: Dict[str, Any] = {}
        for key, expr in bindings.items():
            normalized_key = self._trim(key)
            if not normalized_key:
                continue
            value = self._resolve_generic_value(
                expr,
                shared_outputs=shared_outputs,
                step_outputs=step_outputs,
                loop_item=loop_item,
                loop_index=loop_index,
            )
            if not self._is_missing_value(value):
                resolved[normalized_key] = value
        return resolved

    def _find_unresolved_bindings(self, *, step: Dict[str, Any], resolved_inputs: Dict[str, Any]) -> List[str]:
        bindings = step.get("input_binding") if isinstance(step.get("input_binding"), dict) else {}
        missing: List[str] = []
        for key in bindings.keys():
            normalized_key = self._trim(key)
            if normalized_key and normalized_key not in resolved_inputs:
                missing.append(normalized_key)
        return missing

    def _merge_shared_outputs(self, *, shared_outputs: Dict[str, Any], step: Dict[str, Any], run_record: Dict[str, Any]) -> Dict[str, Any]:
        step_bindings = step.get("output_binding") if isinstance(step.get("output_binding"), dict) else {}
        exported: Dict[str, Any] = {}
        for key, expr in step_bindings.items():
            normalized_key = self._trim(key)
            if not normalized_key:
                continue
            value = self._resolve_output_binding_value(run_record=run_record, expr=expr)
            if value is None or value == "":
                continue
            if normalized_key in {"top1_code", "code"}:
                exported_value = self._trim(value).split(".", 1)[0]
            elif normalized_key in {"top1_name", "name", "query", "producer_tool_name"}:
                exported_value = self._trim(value)
            else:
                exported_value = value
            shared_outputs[normalized_key] = exported_value
            exported[normalized_key] = exported_value
        return exported

    def _resolve_output_binding_value(self, *, run_record: Dict[str, Any], expr: Any) -> Any:
        normalized_expr = self._trim(expr)
        if not normalized_expr:
            return None
        if not normalized_expr.startswith("$"):
            named_outputs = run_record.get("named_outputs") if isinstance(run_record.get("named_outputs"), dict) else {}
            if normalized_expr in named_outputs:
                return named_outputs.get(normalized_expr)
            return expr
        if normalized_expr == "$tool_name":
            return self._trim(run_record.get("tool_name"))
        path = normalized_expr[1:]
        if path.startswith("result."):
            return self._extract_path(run_record.get("result"), path[len("result."):])
        if path == "result":
            return run_record.get("result")
        if path.startswith("retention."):
            return self._extract_path(run_record.get("retention"), path[len("retention."):])
        if path == "retention":
            return run_record.get("retention")
        return self._extract_path(run_record, path)

    def _extract_path(self, payload: Any, path: str) -> Any:
        current = payload
        for token in [self._trim(part) for part in str(path or "").split(".") if self._trim(part)]:
            if isinstance(current, list):
                try:
                    index = int(token)
                except Exception:
                    projected = []
                    for item in current:
                        if isinstance(item, dict) and token in item:
                            projected.append(item.get(token))
                    if not projected:
                        return None
                    current = projected
                    continue
                if index < 0 or index >= len(current):
                    return None
                current = current[index]
                continue
            if isinstance(current, dict):
                if token not in current:
                    return None
                current = current[token]
                continue
            return None
        return current

    def _resolve_generic_value(
        self,
        expr: Any,
        *,
        shared_outputs: Dict[str, Any],
        step_outputs: Dict[str, Dict[str, Any]],
        loop_item: Any = None,
        loop_index: int | None = None,
    ) -> Any:
        if isinstance(expr, (list, dict, int, float, bool)):
            return expr
        normalized_expr = self._trim(expr)
        if not normalized_expr:
            return None
        if normalized_expr.startswith("${") and normalized_expr.endswith("}"):
            normalized_expr = normalized_expr[2:-1].strip()
        elif normalized_expr.startswith("$"):
            normalized_expr = normalized_expr[1:].strip()
        else:
            return expr
        if normalized_expr == "item":
            return loop_item
        if normalized_expr == "index":
            return loop_index
        if normalized_expr.startswith("item."):
            return self._extract_path(loop_item, normalized_expr[len("item."):])
        if loop_item is not None and "." in normalized_expr:
            head, tail = normalized_expr.split(".", 1)
            if isinstance(loop_item, dict):
                if head in loop_item:
                    value = loop_item.get(head)
                    if not self._is_missing_value(value):
                        return value
                value = self._extract_path(loop_item, tail)
                if not self._is_missing_value(value):
                    return value
                alias_map = {
                    "code": ["stock_code", "ts_code"],
                    "name": ["stock_name", "concept_name", "plate_name"],
                }
                for alias in alias_map.get(tail, []):
                    alias_value = self._extract_path(loop_item, alias)
                    if not self._is_missing_value(alias_value):
                        return alias_value
        if normalized_expr in shared_outputs:
            return shared_outputs.get(normalized_expr)
        if normalized_expr.startswith("step_"):
            step_ref, _, tail = normalized_expr.partition(".")
            if step_ref in step_outputs:
                step_payload = step_outputs.get(step_ref) or {}
                if not tail:
                    return step_payload
                named_outputs = step_payload.get("named_outputs") if isinstance(step_payload.get("named_outputs"), dict) else {}
                if tail in named_outputs:
                    return named_outputs.get(tail)
                if "." in tail:
                    head, rest = tail.split(".", 1)
                    if head in named_outputs:
                        return self._extract_path(named_outputs.get(head), rest)
                if tail.startswith("result."):
                    return self._extract_path(step_payload.get("result"), tail[len("result."):])
                if tail == "result":
                    return step_payload.get("result")
        if "." in normalized_expr:
            head, tail = normalized_expr.split(".", 1)
            if head in shared_outputs:
                value = self._extract_path(shared_outputs.get(head), tail)
                if loop_index is not None and isinstance(value, list) and 0 <= loop_index < len(value):
                    return value[loop_index]
                return value
        return shared_outputs.get(normalized_expr)

    def _infer_transform_source(
        self,
        *,
        step: Dict[str, Any],
        shared_outputs: Dict[str, Any],
        step_outputs: Dict[str, Dict[str, Any]],
    ) -> Any:
        depends_on = [self._trim(item) for item in (step.get("depends_on") or []) if self._trim(item)]
        for step_id in reversed(depends_on):
            payload = step_outputs.get(step_id) if isinstance(step_outputs.get(step_id), dict) else {}
            named_outputs = payload.get("named_outputs") if isinstance(payload.get("named_outputs"), dict) else {}
            for value in named_outputs.values():
                if not self._is_missing_value(value):
                    return value
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            if isinstance(result.get("data"), list):
                return result.get("data")
            if isinstance(result.get("data"), dict):
                return result.get("data")
        for value in shared_outputs.values():
            if isinstance(value, (list, dict)):
                return value
        return None

    def _coerce_record_list(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        return []

    def _sort_records(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def _key(item: Dict[str, Any]) -> str:
            for field in ["time", "published_at", "publish_time", "datetime", "date", "update_time"]:
                value = self._trim(item.get(field))
                if value:
                    return value
            return ""

        return sorted(list(records), key=_key, reverse=True)

    def _first_present(self, payload: Dict[str, Any], keys: List[str]) -> Any:
        for key in keys:
            if key in payload and not self._is_missing_value(payload.get(key)):
                return payload.get(key)
        return ""

    def _collect_concepts(
        self,
        payload: Any,
        *,
        shared_outputs: Dict[str, Any],
        step_outputs: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        values: List[str] = []

        def _extend_from(candidate: Any) -> None:
            if isinstance(candidate, list):
                for item in candidate:
                    if isinstance(item, str) and self._trim(item):
                        values.append(self._trim(item))
                    elif isinstance(item, dict):
                        name = self._trim(item.get("name") or item.get("concept_name") or item.get("plate_name"))
                        if name:
                            values.append(name)
            elif isinstance(candidate, dict):
                for key in ["concepts", "concept_list", "hot_concepts", "data"]:
                    if key in candidate:
                        _extend_from(candidate.get(key))

        _extend_from(payload)
        for value in shared_outputs.values():
            if not values:
                _extend_from(value)
        if not values:
            for payload in step_outputs.values():
                if not isinstance(payload, dict):
                    continue
                _extend_from(payload.get("named_outputs"))
                _extend_from(payload.get("result"))
                if values:
                    break
        deduped: List[str] = []
        for item in values:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def _build_trend_conclusion(self, payload: Any) -> str:
        records = self._coerce_record_list(payload)
        if len(records) < 2:
            return "指标序列样本不足，暂无法稳定判断趋势。"
        first = records[0]
        last = records[-1]
        first_value = self._numeric_value(first)
        last_value = self._numeric_value(last)
        if first_value is None or last_value is None:
            return "指标已返回，但缺少可直接比较的连续数值。"
        if last_value > first_value:
            return "最近一段时间整体偏上行。"
        if last_value < first_value:
            return "最近一段时间整体偏走弱。"
        return "最近一段时间整体较为平稳。"

    def _numeric_value(self, payload: Dict[str, Any]) -> Optional[float]:
        for key in ["value", "indicator_value", "close", "price"]:
            try:
                if key in payload and not self._is_missing_value(payload.get(key)):
                    return float(payload.get(key))
            except Exception:
                continue
        return None

    def _run_transform_logic(
        self,
        *,
        step: Dict[str, Any],
        shared_outputs: Dict[str, Any],
        step_outputs: Dict[str, Dict[str, Any]],
    ) -> tuple[Any, Dict[str, Any]]:
        step_name = self._trim(step.get("name")).lower()
        bindings = step.get("input_binding") if isinstance(step.get("input_binding"), dict) else {}
        source_data = self._resolve_generic_value(
            bindings.get("$input") if "$input" in bindings else (bindings.get("input") if "input" in bindings else bindings.get("source_data")),
            shared_outputs=shared_outputs,
            step_outputs=step_outputs,
            loop_item=None,
        )
        if self._is_missing_value(source_data):
            source_data = self._infer_transform_source(
                step=step,
                shared_outputs=shared_outputs,
                step_outputs=step_outputs,
            )
        transform_spec = step.get("transform_spec") if isinstance(step.get("transform_spec"), dict) else {}
        dsl = self._trim(transform_spec.get("dsl"))
        if dsl:
            result_data = self._execute_transform_dsl(dsl=dsl, source_data=source_data)
            named_outputs = self._bind_transform_outputs(
                output_binding=step.get("output_binding") if isinstance(step.get("output_binding"), dict) else {},
                result_data=result_data,
            )
            return result_data, named_outputs

        named_outputs: Dict[str, Any] = {}
        output_binding = step.get("output_binding") if isinstance(step.get("output_binding"), dict) else {}

        if "strongest_concept" in step_name:
            concepts = self._collect_concepts(
                source_data,
                shared_outputs=shared_outputs,
                step_outputs=step_outputs,
            )
            selected = concepts[0] if concepts else ""
            for source_key, target_name in output_binding.items():
                self._assign_named_output(named_outputs, source_key=source_key, target_name=target_name, value=selected)
            return selected, named_outputs

        if "sort" in step_name and isinstance(source_data, list):
            sorted_list = self._sort_records(source_data)
            for source_key, target_name in output_binding.items():
                self._assign_named_output(named_outputs, source_key=source_key, target_name=target_name, value=sorted_list)
            return sorted_list, named_outputs

        top_k_match = re.search(r"top[_ ]?(\d+)", step_name)
        if top_k_match:
            top_k = max(1, min(int(top_k_match.group(1)), 10))
            records = self._coerce_record_list(source_data)
            selected_records = records[:top_k]
            for source_key, target_name in output_binding.items():
                normalized_target = self._trim(target_name)
                lowered = normalized_target.lower()
                if not normalized_target:
                    continue
                if "code" in lowered and "list" in lowered:
                    output_value = [
                        {
                            "code": self._first_present(item, ["stock_code", "code", "ts_code"]),
                            "name": self._first_present(item, ["stock_name", "name"]),
                        }
                        for item in selected_records
                    ]
                elif "name" in lowered and "list" in lowered:
                    output_value = [
                        self._first_present(item, ["stock_name", "name"])
                        for item in selected_records
                    ]
                elif "company" in lowered or "target" in lowered:
                    output_value = [
                        {
                            "code": self._first_present(item, ["stock_code", "code", "ts_code"]),
                            "name": self._first_present(item, ["stock_name", "name"]),
                        }
                        for item in selected_records
                    ]
                else:
                    output_value = selected_records
                self._assign_named_output(named_outputs, source_key=source_key, target_name=target_name, value=output_value)
            return selected_records, named_outputs

        if "trend" in step_name:
            conclusion = self._build_trend_conclusion(source_data)
            for source_key, target_name in output_binding.items():
                self._assign_named_output(named_outputs, source_key=source_key, target_name=target_name, value=conclusion)
            return conclusion, named_outputs

        for source_key, target_name in output_binding.items():
            self._assign_named_output(named_outputs, source_key=source_key, target_name=target_name, value=source_data)
        return source_data, named_outputs

    def _bind_transform_outputs(self, *, output_binding: Dict[str, Any], result_data: Any) -> Dict[str, Any]:
        if not isinstance(output_binding, dict):
            return {}
        run_record = {
            "result": {"tool": "transform", "ok": True, "data": result_data, "error": ""},
            "retention": {},
            "named_outputs": {},
        }
        named_outputs: Dict[str, Any] = {}
        for key, expr in output_binding.items():
            normalized_key = self._trim(key)
            if not normalized_key:
                continue
            normalized_expr = self._trim(expr)
            value = None
            if normalized_expr.startswith("$"):
                if normalized_expr.startswith("$result.") and not normalized_expr.startswith("$result.data."):
                    value = self._extract_path(result_data, normalized_expr[len("$result."):])
                elif normalized_expr == "$result":
                    value = result_data
                else:
                    value = self._resolve_output_binding_value(run_record=run_record, expr=normalized_expr)
            elif isinstance(result_data, dict) and normalized_expr and normalized_expr in result_data:
                value = result_data.get(normalized_expr)
            elif normalized_expr == normalized_key or not normalized_expr:
                value = result_data if not isinstance(result_data, dict) else result_data.get(normalized_key, result_data)
            if self._is_missing_value(value):
                continue
            named_outputs[normalized_key] = value
        return named_outputs

    def _execute_transform_dsl(self, *, dsl: str, source_data: Any) -> Any:
        steps = [self._trim(part) for part in str(dsl or "").split("|") if self._trim(part)]
        current = source_data
        if not steps:
            return current
        if steps and steps[0] == "$input":
            steps = steps[1:]
        for op in steps:
            current = self._apply_transform_op(current=current, op=op)
        return current

    def _apply_transform_op(self, *, current: Any, op: str) -> Any:
        normalized = self._trim(op)
        if normalized.startswith("filter(") and normalized.endswith(")"):
            expr = normalized[len("filter("):-1].strip()
            return [item for item in self._coerce_record_list(current) if self._eval_filter_expr(expr, item)]
        if normalized.startswith("sort(") and normalized.endswith(")"):
            spec = normalized[len("sort("):-1].strip()
            return self._apply_sort_spec(self._coerce_record_list(current), spec)
        if normalized.startswith("top(") and normalized.endswith(")"):
            raw_n = normalized[len("top("):-1].strip()
            try:
                n = max(0, int(raw_n))
            except Exception:
                n = 0
            return list(current or [])[:n] if isinstance(current, list) else current
        if normalized == "first()":
            return current[0] if isinstance(current, list) and current else None
        if normalized.startswith("pluck(") and normalized.endswith(")"):
            path = normalized[len("pluck("):-1].strip()
            return self._apply_pluck(current, path)
        if normalized.startswith("project(") and normalized.endswith(")"):
            spec = normalized[len("project("):-1].strip()
            return self._apply_project(current, spec)
        return current

    def _eval_filter_expr(self, expr: str, item: Dict[str, Any]) -> bool:
        text = self._trim(expr)
        if not text:
            return True
        if "||" in text:
            return any(self._eval_filter_expr(part, item) for part in text.split("||"))
        if "&&" in text:
            return all(self._eval_filter_expr(part, item) for part in text.split("&&"))
        text = text.strip()
        if text.startswith("!"):
            return not self._eval_filter_expr(text[1:].strip(), item)
        for operator in ["!=", ">=", "<=", "==", ">", "<"]:
            if operator in text:
                left, right = text.split(operator, 1)
                left_value = self._resolve_filter_operand(left.strip(), item)
                right_value = self._resolve_filter_operand(right.strip(), item)
                return self._compare_filter_values(left_value, right_value, operator)
        value = self._resolve_filter_operand(text, item)
        return bool(value)

    def _resolve_filter_operand(self, token: str, item: Dict[str, Any]) -> Any:
        normalized = self._trim(token)
        if normalized == "null":
            return None
        if normalized in {"true", "false"}:
            return normalized == "true"
        if (normalized.startswith('"') and normalized.endswith('"')) or (normalized.startswith("'") and normalized.endswith("'")):
            return normalized[1:-1]
        try:
            if "." in normalized:
                return float(normalized)
            return int(normalized)
        except Exception:
            pass
        if normalized.startswith("@."):
            return self._extract_path(item, normalized[2:])
        return normalized

    def _compare_filter_values(self, left: Any, right: Any, operator: str) -> bool:
        if operator == "==":
            return left == right
        if operator == "!=":
            return left != right
        try:
            if operator == ">":
                return left > right
            if operator == "<":
                return left < right
            if operator == ">=":
                return left >= right
            if operator == "<=":
                return left <= right
        except Exception:
            return False
        return False

    def _apply_sort_spec(self, records: List[Dict[str, Any]], spec: str) -> List[Dict[str, Any]]:
        sorts: List[tuple[str, bool]] = []
        for part in [self._trim(x) for x in spec.split(",") if self._trim(x)]:
            pieces = part.split()
            path = pieces[0]
            descending = len(pieces) > 1 and pieces[1].lower() == "desc"
            if path.startswith("@."):
                path = path[2:]
            sorts.append((path, descending))
        result = list(records)
        for path, descending in reversed(sorts):
            result.sort(key=lambda item: self._sort_value(self._extract_path(item, path)), reverse=descending)
        return result

    def _sort_value(self, value: Any) -> Any:
        if value is None:
            return (1, "")
        return (0, value)

    def _apply_pluck(self, current: Any, path: str) -> Any:
        normalized_path = self._trim(path)
        if normalized_path.startswith("@."):
            normalized_path = normalized_path[2:]
        if isinstance(current, list):
            return [self._extract_path(item, normalized_path) for item in current if isinstance(item, dict)]
        if isinstance(current, dict):
            return self._extract_path(current, normalized_path)
        return None

    def _apply_project(self, current: Any, spec: str) -> Any:
        mappings: List[tuple[str, str]] = []
        for part in [self._trim(x) for x in spec.split(",") if self._trim(x)]:
            if "=" not in part:
                continue
            target, source = part.split("=", 1)
            target_name = self._trim(target)
            source_path = self._trim(source)
            mappings.append((target_name, source_path))
        if isinstance(current, list):
            return [
                {target: self._resolve_project_value(item, source) for target, source in mappings}
                for item in current
            ]
        if isinstance(current, dict):
            return {target: self._resolve_project_value(current, source) for target, source in mappings}
        return {target: self._resolve_project_value(current, source) for target, source in mappings}

    def _resolve_project_value(self, current: Any, source: str) -> Any:
        normalized = self._trim(source)
        if normalized in {"@", "$input", ""}:
            return current
        if normalized.startswith("@."):
            normalized = normalized[2:]
        elif normalized.startswith("$input."):
            normalized = normalized[len("$input."):]
        if isinstance(current, (dict, list)):
            return self._extract_path(current, normalized)
        return None

    def _assign_named_output(self, named_outputs: Dict[str, Any], *, source_key: Any, target_name: Any, value: Any) -> None:
        normalized_source = self._trim(source_key)
        normalized_target = self._trim(target_name)
        if normalized_source:
            named_outputs[normalized_source] = value
        if normalized_target:
            named_outputs[normalized_target] = value

    def _infer_foreach_items(
        self,
        *,
        step: Dict[str, Any],
        shared_outputs: Dict[str, Any],
        step_outputs: Dict[str, Dict[str, Any]],
    ) -> List[Any]:
        foreach_binding = step.get("foreach_binding") if isinstance(step.get("foreach_binding"), dict) else {}
        foreach_expr = foreach_binding.get("items")
        if foreach_expr is not None:
            value = self._resolve_generic_value(
                foreach_expr,
                shared_outputs=shared_outputs,
                step_outputs=step_outputs,
                loop_item=None,
            )
            if isinstance(value, list):
                if value and not isinstance(value[0], dict):
                    return [{"item": item} for item in value]
                return value
        bindings = step.get("input_binding") if isinstance(step.get("input_binding"), dict) else {}
        for expr in bindings.values():
            value = self._resolve_generic_value(
                expr,
                shared_outputs=shared_outputs,
                step_outputs=step_outputs,
                loop_item=None,
            )
            if isinstance(value, list):
                if value and not isinstance(value[0], dict):
                    field_name = "code" if "code" in self._trim(expr) else "name"
                    return [{field_name: item} for item in value]
                return value
        depends_on = [self._trim(item) for item in (step.get("depends_on") or []) if self._trim(item)]
        for step_id in reversed(depends_on):
            payload = step_outputs.get(step_id) if isinstance(step_outputs.get(step_id), dict) else {}
            named_outputs = payload.get("named_outputs") if isinstance(payload.get("named_outputs"), dict) else {}
            for value in named_outputs.values():
                if isinstance(value, list):
                    return value
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            if isinstance(result.get("data"), list):
                return result.get("data")
        return []

    def _build_final_output(
        self,
        *,
        objective: str,
        tool_runs: List[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], Optional[Dict[str, int]]]:
        summarized_tools = []
        for item in tool_runs:
            retention = item.get("retention") if isinstance(item.get("retention"), dict) else {}
            prompt_context = retention.get("prompt_context") if isinstance(retention.get("prompt_context"), dict) else {}
            summarized_tools.append(
                {
                    "tool_name": self._trim(item.get("tool_name")),
                    "status": self._trim(item.get("status")),
                    "reason": self._trim(item.get("reason")),
                    "error": self._trim(item.get("error")),
                    "arguments": ((item.get("plan") or {}) if isinstance(item.get("plan"), dict) else {}).get("arguments") or {},
                    "prompt_context": prompt_context,
                }
            )
        try:
            messages = self.registry.render_messages(
                "system.assistant.tool_plan_synthesis",
                {
                    "objective": objective,
                    "tool_runs": summarized_tools,
                },
            )
            payload, usage = chat_qwen_json(messages, enable_think=False)
            if isinstance(payload, dict):
                return self._normalize_final_output(payload, tool_runs), self._normalize_usage(usage)
        except Exception:
            pass
        return self._fallback_final_output(objective=objective, tool_runs=tool_runs), None

    def _normalize_final_output(self, payload: Dict[str, Any], tool_runs: List[Dict[str, Any]]) -> Dict[str, Any]:
        facts = []
        for item in payload.get("facts") or []:
            if isinstance(item, dict):
                detail = self._trim(item.get("detail") or item.get("text"))
                if detail:
                    facts.append({
                        "category": self._trim(item.get("category") or "observation"),
                        "detail": detail,
                    })
            elif self._trim(item):
                facts.append({"category": "observation", "detail": self._trim(item)})
        risks = []
        for item in payload.get("risks") or []:
            if isinstance(item, dict):
                description = self._trim(item.get("description") or item.get("detail") or item.get("text"))
                if description:
                    risks.append({
                        "type": self._trim(item.get("type") or "coverage"),
                        "description": description,
                    })
            elif self._trim(item):
                risks.append({"type": "coverage", "description": self._trim(item)})
        if not risks:
            failed = [row for row in tool_runs if self._trim(row.get("status")) != "completed"]
            if failed:
                risks.append({
                    "type": "coverage",
                    "description": f"有 {len(failed)} 个工具未成功执行，结果基于当前已获取证据生成。",
                })
        return {
            "summary": self._trim(payload.get("summary")) or "已完成临时工具计划执行。",
            "facts": facts,
            "risks": risks,
        }

    def _fallback_final_output(self, *, objective: str, tool_runs: List[Dict[str, Any]]) -> Dict[str, Any]:
        completed = [row for row in tool_runs if self._trim(row.get("status")) == "completed"]
        failed = [row for row in tool_runs if self._trim(row.get("status")) == "failed"]
        skipped = [row for row in tool_runs if self._trim(row.get("status")) == "skipped"]
        facts = [
            {
                "category": "execution",
                "detail": f"围绕“{objective}”共规划 {len(tool_runs)} 个工具，成功执行 {len(completed)} 个。",
            }
        ]
        for row in completed[:4]:
            prompt_context = ((row.get("retention") or {}) if isinstance(row.get("retention"), dict) else {}).get("prompt_context") or {}
            compressed = prompt_context.get("compressed") if isinstance(prompt_context, dict) else {}
            reasoning = prompt_context.get("reasoning") if isinstance(prompt_context, dict) else {}
            detail = ""
            if isinstance(compressed, dict) and compressed:
                detail = self._trim(str(next(iter(compressed.values()))))
            elif isinstance(reasoning, dict) and reasoning:
                detail = self._trim(str(next(iter(reasoning.values()))))
            facts.append({
                "category": self._trim(row.get("tool_name")) or "tool",
                "detail": detail or f"{self._trim(row.get('tool_name'))} 已返回结果。",
            })
        risks = []
        if failed:
            risks.append({
                "type": "tool_failure",
                "description": f"有 {len(failed)} 个工具执行失败，结果未覆盖全部候选证据。",
            })
        if skipped:
            risks.append({
                "type": "tool_skip",
                "description": f"有 {len(skipped)} 个工具因参数不足或未接入参数规划而被跳过。",
            })
        return {
            "summary": f"已基于 {len(completed)} 个已完成工具的结果生成任务总结。",
            "facts": facts,
            "risks": risks,
        }

    def _build_render_payload(
        self,
        *,
        execution_plan: dict,
        final_output: dict,
        tool_runs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        presentation_plan = execution_plan.get("presentation_plan") if isinstance(execution_plan.get("presentation_plan"), dict) else {}
        sections: List[Dict[str, Any]] = []
        sections.append(
            {
                "section_id": "summary",
                "title": "摘要",
                "blocks": [
                    {
                        "block_id": "summary",
                        "type": "structured_text",
                        "title": "结果摘要",
                        "data": {"summary": self._trim(final_output.get("summary"))},
                    }
                ],
            }
        )
        for run_record in tool_runs:
            if self._trim(run_record.get("status")) != "completed":
                continue
            rendered_sections = self._build_tool_render_sections(run_record)
            sections.extend(rendered_sections)
        reference_materials = self._build_reference_materials(tool_runs)
        sections = self._refine_uncertain_render_blocks_with_llm(sections)
        presentation_contract = self.result_presentation_service.build_contract(
            final_output=final_output,
            render_payload={
                "page_type": self._trim(presentation_plan.get("page_type")) or "tool_plan_result",
                "layout": self._trim(presentation_plan.get("layout")) or "report",
                "sections": sections,
            },
            objective=self._trim(execution_plan.get("objective")),
        )
        return {
            "page_type": self._trim(presentation_plan.get("page_type")) or "tool_plan_result",
            "layout": self._trim(presentation_plan.get("layout")) or "report",
            "sections": sections,
            "reference_materials": reference_materials,
            "presentation_contract": presentation_contract,
        }

    def _build_tool_render_sections(self, run_record: Dict[str, Any]) -> List[Dict[str, Any]]:
        direct_sections = self._build_sections_from_render_blocks(run_record)
        if direct_sections:
            return direct_sections

        retention = run_record.get("retention") if isinstance(run_record.get("retention"), dict) else {}
        render_artifacts = retention.get("render_artifacts") if isinstance(retention.get("render_artifacts"), dict) else {}
        render_preferences = retention.get("render_preferences") if isinstance(retention.get("render_preferences"), dict) else {}
        tool_name = self._trim(run_record.get("tool_name")) or "tool"
        sections: List[Dict[str, Any]] = []
        appended = False
        for path, value in render_artifacts.items():
            if isinstance(value, dict) and isinstance(value.get("sections"), list):
                sections.extend(deepcopy(value.get("sections") or []))
                appended = True
                continue
            block = self._build_block_from_artifact(
                path=path,
                value=value,
                render_preference=render_preferences.get(path) if isinstance(render_preferences.get(path), dict) else {},
            )
            if block:
                sections.append(
                    {
                        "section_id": f"tool_{tool_name}_{len(sections) + 1}",
                        "title": tool_name,
                        "blocks": [block],
                    }
                )
                appended = True
        if not appended:
            prompt_context = retention.get("prompt_context") if isinstance(retention.get("prompt_context"), dict) else {}
            summary_data = {
                "reasoning": prompt_context.get("reasoning") if isinstance(prompt_context.get("reasoning"), dict) else {},
                "compressed": prompt_context.get("compressed") if isinstance(prompt_context.get("compressed"), dict) else {},
            }
            sections.append(
                {
                    "section_id": f"tool_{tool_name}_summary",
                    "title": tool_name,
                    "blocks": [
                        {
                            "block_id": f"{tool_name}_summary",
                            "type": "structured_text",
                            "title": tool_name,
                            "data": summary_data,
                        }
                    ],
                }
            )
        return sections

    def _build_sections_from_render_blocks(self, run_record: Dict[str, Any]) -> List[Dict[str, Any]]:
        result = run_record.get("result") if isinstance(run_record.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        render_blocks = data.get("render_blocks") if isinstance(data.get("render_blocks"), list) else []
        if not render_blocks:
            return []
        blocks = []
        for index, block in enumerate(render_blocks, start=1):
            if not isinstance(block, dict):
                continue
            block_type = self._trim(block.get("type"))
            block_data = block.get("data") if isinstance(block.get("data"), dict) else {}
            if not block_type or not block_data:
                continue
            blocks.append(
                {
                    "block_id": self._trim(block.get("block_id")) or f"{self._trim(run_record.get('tool_name')) or 'tool'}_{index}",
                    "type": block_type,
                    "title": self._trim(block.get("title")) or self._humanize_path(f"block_{index}"),
                    "data": deepcopy(block_data),
                    "meta": deepcopy(block.get("meta") or {}),
                }
            )
        if not blocks:
            return []
        tool_name = self._trim(run_record.get("tool_name")) or "tool"
        return [
            {
                "section_id": f"tool_{tool_name}_render_blocks",
                "title": tool_name,
                "blocks": blocks,
            }
        ]

    def _build_block_from_artifact(
        self,
        *,
        path: str,
        value: Any,
        render_preference: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        title = self._humanize_path(path)
        preferred_type = self._trim((render_preference or {}).get("render_type")) or "auto"
        if preferred_type != "auto":
            forced = self._build_block_from_preference(
                path=path,
                title=title,
                value=value,
                render_type=preferred_type,
            )
            if forced:
                return forced
        if self._looks_like_kline_payload(value):
            if self._is_intraday_kline_payload(value):
                return {
                    "block_id": self._trim(path) or "intraday_line",
                    "type": "line",
                    "title": title,
                    "data": self._convert_intraday_kline_to_line_series(value),
                }
            return {
                "block_id": self._trim(path) or "kline",
                "type": "kline",
                "title": title,
                "data": value,
            }
        if isinstance(value, dict) and isinstance(value.get("series"), list):
            chart_type = self._trim(value.get("chart_type")) or "line"
            return {
                "block_id": self._trim(path) or "chart",
                "type": chart_type if chart_type in {"line", "kline", "bar", "pie", "flow"} else "line",
                "title": title,
                "data": value,
            }
        if isinstance(value, dict) and isinstance(value.get("series_map"), dict):
            labels = value.get("labels") if isinstance(value.get("labels"), list) else []
            series = [
                {"name": self._trim(name), "data": data if isinstance(data, list) else []}
                for name, data in value.get("series_map", {}).items()
                if self._trim(name)
            ]
            return {
                "block_id": self._trim(path) or "line",
                "type": "line",
                "title": title,
                "data": {"labels": labels, "series": series},
            }
        if isinstance(value, list) and value and isinstance(value[0], dict):
            headers = list(value[0].keys())
            return {
                "block_id": self._trim(path) or "table",
                "type": "table",
                "title": title,
                "data": {"columns": headers, "rows": value},
            }
        if isinstance(value, dict) and (isinstance(value.get("rows"), list) or isinstance(value.get("columns"), list) or isinstance(value.get("headers"), list)):
            return {
                "block_id": self._trim(path) or "table",
                "type": "table",
                "title": title,
                "data": value,
            }
        if isinstance(value, dict) and self._looks_like_metric_map(value):
            return {
                "block_id": self._trim(path) or "metrics",
                "type": "metric_strip",
                "title": title,
                "data": {
                    "items": [{"label": key, "value": self._stringify_scalar(child)} for key, child in value.items()]
                },
            }
        if isinstance(value, list):
            items = [self._normalize_text_list_item(item) for item in value[:12]]
            return {
                "block_id": self._trim(path) or "list",
                "type": "text_list",
                "title": title,
                "data": {"items": items},
                "meta": {
                    "_llm_refine_candidate": True,
                    "_llm_raw_value": deepcopy(value[:20]),
                    "_llm_current_type": "text_list",
                    "_llm_source_path": self._trim(path),
                },
            }
        if isinstance(value, dict):
            return {
                "block_id": self._trim(path) or "structured",
                "type": "structured_text",
                "title": title,
                "data": value,
                "meta": {
                    "_llm_refine_candidate": True,
                    "_llm_raw_value": deepcopy(value),
                    "_llm_current_type": "structured_text",
                    "_llm_source_path": self._trim(path),
                },
            }
        return None

    def _build_block_from_preference(self, *, path: str, title: str, value: Any, render_type: str) -> Optional[Dict[str, Any]]:
        normalized = self._trim(render_type).lower()
        if normalized == "flowchart":
            normalized = "flow"
        block_id = self._trim(path) or normalized or "block"
        if normalized == "kline":
            return {"block_id": block_id, "type": "kline", "title": title, "data": value if isinstance(value, dict) else {}}
        if normalized == "table":
            data = self._coerce_table_block_data(value)
        elif normalized == "metric_strip":
            data = self._coerce_metric_strip_data(value)
        elif normalized == "structured_text":
            data = value if isinstance(value, dict) else {"value": value}
        elif normalized == "text_list":
            data = self._coerce_text_list_data(value)
        if normalized == "line":
            data = self._coerce_line_chart_data(value)
        elif normalized == "bar":
            data = self._coerce_bar_chart_data(value)
        elif normalized == "pie":
            data = self._coerce_pie_chart_data(value)
        elif normalized == "flow":
            data = self._coerce_flowchart_data(value)
        elif normalized not in {"kline", "table", "metric_strip", "structured_text", "text_list"}:
            data = None
        if data is None:
            return None
        return {"block_id": block_id, "type": normalized, "title": title, "data": data}

    def _refine_uncertain_render_blocks_with_llm(self, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if str(os.getenv("RESULT_PRESENTATION_LLM_REFINE_ENABLED", "true")).strip().lower() not in {"1", "true", "yes", "on"}:
            return self._strip_internal_render_meta(sections)
        candidates: List[tuple[int, int, Dict[str, Any]]] = []
        for section_index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            blocks = section.get("blocks") if isinstance(section.get("blocks"), list) else []
            for block_index, block in enumerate(blocks):
                if not isinstance(block, dict):
                    continue
                meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
                if not meta.get("_llm_refine_candidate"):
                    continue
                if self._block_is_obviously_text(block):
                    continue
                candidates.append((section_index, block_index, block))
        if not candidates:
            return self._strip_internal_render_meta(sections)

        with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as executor:
            future_map = {
                executor.submit(self._classify_render_block_with_llm, block=block): (section_index, block_index)
                for section_index, block_index, block in candidates
            }
            for future in as_completed(future_map):
                section_index, block_index = future_map[future]
                try:
                    resolved_type = future.result()
                except Exception:
                    resolved_type = ""
                if not resolved_type:
                    continue
                block = ((sections[section_index].get("blocks") or [])[block_index]) if isinstance(sections[section_index], dict) else None
                if not isinstance(block, dict):
                    continue
                upgraded = self._upgrade_block_with_refined_type(block=block, refined_type=resolved_type)
                if upgraded:
                    sections[section_index]["blocks"][block_index] = upgraded
        return self._strip_internal_render_meta(sections)

    def _classify_render_block_with_llm(self, *, block: Dict[str, Any]) -> str:
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        raw_value = meta.get("_llm_raw_value")
        messages = self.registry.render_messages(
            "system.assistant.result_block_classifier",
            {
                "block_title": self._trim(block.get("title")),
                "current_type": self._trim(meta.get("_llm_current_type") or block.get("type")),
                "source_path": self._trim(meta.get("_llm_source_path")),
                "shape_summary": self._summarize_render_candidate_shape(raw_value),
                "preview_json": self._preview_json_text(raw_value),
                "allowed_types": [
                    "line",
                    "bar",
                    "pie",
                    "kline",
                    "flow",
                    "table",
                    "metric_strip",
                    "structured_text",
                    "text_list",
                ],
            },
        )
        payload, _usage = chat_qwen_flash_json(messages, enable_think=False)
        if isinstance(payload, dict):
            resolved_type = self._trim(payload.get("type")).lower()
            return resolved_type if resolved_type in self._RESULT_BLOCK_TYPES else ""
        return ""

    def _upgrade_block_with_refined_type(self, *, block: Dict[str, Any], refined_type: str) -> Optional[Dict[str, Any]]:
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        raw_value = meta.get("_llm_raw_value")
        path = self._trim(meta.get("_llm_source_path")) or self._trim(block.get("block_id"))
        rebuilt = self._build_block_from_preference(
            path=path,
            title=self._trim(block.get("title")),
            value=raw_value,
            render_type=refined_type,
        )
        if rebuilt:
            rebuilt_meta = dict(block.get("meta") or {})
            rebuilt_meta["classification_source"] = "llm_refine"
            rebuilt["meta"] = rebuilt_meta
            return rebuilt
        return None

    def _block_is_obviously_text(self, block: Dict[str, Any]) -> bool:
        block_type = self._trim(block.get("type"))
        data = block.get("data") if isinstance(block.get("data"), dict) else {}
        if block_type == "structured_text":
            keys = {self._trim(key) for key in data.keys() if self._trim(key)}
            if keys and keys.issubset({"summary", "text", "detail", "description", "reasoning", "compressed", "value"}):
                return True
        if block_type == "text_list":
            items = data.get("items") if isinstance(data.get("items"), list) else []
            if items and all(isinstance(item, dict) and not self._trim(item.get("title")) and self._trim(item.get("desc")) for item in items[:5]):
                return True
        return False

    def _coerce_table_block_data(self, value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return {"columns": list(value[0].keys()), "rows": value}
        if isinstance(value, dict) and (
            isinstance(value.get("rows"), list)
            or isinstance(value.get("columns"), list)
            or isinstance(value.get("headers"), list)
        ):
            return value
        return None

    def _coerce_metric_strip_data(self, value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict) and self._looks_like_metric_map(value):
            return {
                "items": [{"label": key, "value": self._stringify_scalar(child)} for key, child in value.items()]
            }
        return None

    def _coerce_text_list_data(self, value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, list):
            return {"items": [self._normalize_text_list_item(item) for item in value[:12]]}
        return None

    def _summarize_render_candidate_shape(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, list):
            first = value[0] if value else None
            return {
                "root_type": "list",
                "length": len(value),
                "item_type": type(first).__name__ if first is not None else "",
                "sample_keys": list(first.keys())[:8] if isinstance(first, dict) else [],
            }
        if isinstance(value, dict):
            keys = list(value.keys())
            return {
                "root_type": "dict",
                "key_count": len(keys),
                "sample_keys": keys[:12],
            }
        return {"root_type": type(value).__name__}

    def _preview_json_text(self, value: Any) -> str:
        import json

        preview = value
        if isinstance(value, list):
            preview = value[:5]
        elif isinstance(value, dict):
            preview = {key: value[key] for key in list(value.keys())[:8]}
        try:
            text = json.dumps(preview, ensure_ascii=False, indent=2)
        except Exception:
            text = self._trim(preview)
        return text[:1800]

    def _strip_internal_render_meta(self, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cleaned: List[Dict[str, Any]] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            cloned = deepcopy(section)
            blocks = cloned.get("blocks") if isinstance(cloned.get("blocks"), list) else []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
                for key in list(meta.keys()):
                    if str(key).startswith("_llm_"):
                        meta.pop(key, None)
                if not meta:
                    block.pop("meta", None)
                else:
                    block["meta"] = meta
            cleaned.append(cloned)
        return cleaned

    def _build_reference_materials(self, tool_runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        materials: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for run_record in tool_runs:
            retention = run_record.get("retention") if isinstance(run_record.get("retention"), dict) else {}
            reference_artifacts = retention.get("reference_artifacts") if isinstance(retention.get("reference_artifacts"), dict) else {}
            tool_name = self._trim(run_record.get("tool_name")) or "tool"
            for path, value in reference_artifacts.items():
                for item in self._reference_items_from_value(tool_name=tool_name, path=path, value=value):
                    key = "|".join([
                        self._trim(item.get("tool_name")),
                        self._trim(item.get("path")),
                        self._trim(item.get("title")),
                        self._trim(item.get("url")),
                    ])
                    if key in seen:
                        continue
                    seen.add(key)
                    materials.append(item)
        return materials

    def _reference_items_from_value(self, *, tool_name: str, path: str, value: Any) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if isinstance(value, list):
            for entry in value[:20]:
                items.extend(self._reference_items_from_value(tool_name=tool_name, path=path, value=entry))
            return items
        if isinstance(value, dict):
            title = self._trim(value.get("title") or value.get("name") or self._humanize_path(path))
            url = self._trim(value.get("url") or value.get("link"))
            summary = self._trim(value.get("summary") or value.get("snippet") or value.get("desc") or value.get("content"))
            meta = []
            for key in ("site", "source", "publish_time", "time"):
                if self._trim(value.get(key)):
                    meta.append(self._trim(value.get(key)))
            if title or url or summary:
                items.append(
                    {
                        "tool_name": tool_name,
                        "path": path,
                        "title": title or self._humanize_path(path),
                        "url": url,
                        "summary": summary[:240],
                        "meta": " | ".join(meta),
                    }
                )
                return items
        text = self._trim(value)
        if text:
            items.append(
                {
                    "tool_name": tool_name,
                    "path": path,
                    "title": self._humanize_path(path),
                    "url": "",
                    "summary": text[:240],
                    "meta": "",
                }
            )
        return items

    def _looks_like_kline_payload(self, value: Any) -> bool:
        return isinstance(value, dict) and isinstance(value.get("kline"), list)

    def _is_intraday_kline_payload(self, value: Any) -> bool:
        if not self._looks_like_kline_payload(value):
            return False
        kline = value.get("kline") or []
        if not kline or not isinstance(kline[0], list) or not kline[0]:
            return False
        first_label = self._trim(kline[0][0])
        return ":" in first_label and "-" not in first_label

    def _convert_intraday_kline_to_line_series(self, value: Dict[str, Any]) -> Dict[str, Any]:
        rows = value.get("kline") if isinstance(value.get("kline"), list) else []
        labels: List[str] = []
        price: List[float] = []
        volume: List[float] = []
        for row in rows[-120:]:
            if not isinstance(row, list) or len(row) < 6:
                continue
            labels.append(self._trim(row[0]))
            try:
                price.append(float(row[2]))
            except Exception:
                price.append(0.0)
            try:
                volume.append(float(row[5]))
            except Exception:
                volume.append(0.0)
        series = [{"name": "价格", "data": price}]
        if any(v for v in volume):
            series.append({"name": "成交量", "data": volume})
        indicators = value.get("indicators") if isinstance(value.get("indicators"), dict) else {}
        for name, data in indicators.items():
            if not isinstance(data, list):
                continue
            series.append(
                {
                    "name": self._trim(name),
                    "data": [
                        float(item[1]) if isinstance(item, list) and len(item) > 1 and isinstance(item[1], (int, float))
                        else 0.0
                        for item in data[-len(labels):]
                    ],
                }
            )
        return {"labels": labels, "series": series}

    def _convert_daily_kline_to_line_series(self, value: Dict[str, Any]) -> Dict[str, Any]:
        rows = value.get("kline") if isinstance(value.get("kline"), list) else []
        labels: List[str] = []
        close_values: List[float] = []
        volume_values: List[float] = []
        for row in rows[-120:]:
            if not isinstance(row, list) or len(row) < 6:
                continue
            labels.append(self._trim(row[0]))
            try:
                close_values.append(float(row[2]))
            except Exception:
                close_values.append(0.0)
            try:
                volume_values.append(float(row[5]))
            except Exception:
                volume_values.append(0.0)
        series = [{"name": "收盘价", "data": close_values}]
        if any(value for value in volume_values):
            series.append({"name": "成交量", "data": volume_values})
        return {"labels": labels, "series": series}

    def _coerce_line_chart_data(self, value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict):
            if isinstance(value.get("series"), list):
                return {
                    "labels": value.get("labels") if isinstance(value.get("labels"), list) else [],
                    "series": value.get("series") or [],
                }
            if isinstance(value.get("series_map"), dict):
                labels = value.get("labels") if isinstance(value.get("labels"), list) else []
                return {
                    "labels": labels,
                    "series": [
                        {"name": self._trim(name) or "series", "data": data if isinstance(data, list) else []}
                        for name, data in value.get("series_map", {}).items()
                    ],
                }
            if self._looks_like_kline_payload(value):
                if self._is_intraday_kline_payload(value):
                    return self._convert_intraday_kline_to_line_series(value)
                return self._convert_daily_kline_to_line_series(value)
            if self._looks_like_metric_map(value):
                labels = list(value.keys())
                return {"labels": labels, "series": [{"name": "数值", "data": [value[key] for key in labels]}]}
        if isinstance(value, list) and value and all(isinstance(item, (int, float)) for item in value):
            return {"labels": [str(index + 1) for index in range(len(value))], "series": [{"name": "数值", "data": value}]}
        return None

    def _coerce_bar_chart_data(self, value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict):
            if isinstance(value.get("categories"), list) and isinstance(value.get("series"), list):
                return {"categories": value.get("categories") or [], "series": value.get("series") or []}
            if self._looks_like_metric_map(value):
                categories = list(value.keys())
                return {"categories": categories, "series": [{"name": "数值", "data": [value[key] for key in categories]}]}
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            categories: List[str] = []
            data: List[float] = []
            for item in value[:20]:
                label = self._trim(item.get("label") or item.get("name") or item.get("title"))
                numeric = next((child for child in item.values() if isinstance(child, (int, float))), None)
                if not label or numeric is None:
                    continue
                categories.append(label)
                data.append(float(numeric))
            if categories:
                return {"categories": categories, "series": [{"name": "数值", "data": data}]}
        return None

    def _coerce_pie_chart_data(self, value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict):
            if isinstance(value.get("items"), list):
                items = [
                    {
                        "label": self._trim(item.get("label") or item.get("name") or item.get("title")),
                        "value": item.get("value"),
                    }
                    for item in value.get("items") or []
                    if isinstance(item, dict)
                    and self._trim(item.get("label") or item.get("name") or item.get("title"))
                    and isinstance(item.get("value"), (int, float))
                ]
                if items:
                    return {"items": items}
            if self._looks_like_metric_map(value):
                return {
                    "items": [
                        {"label": key, "value": child}
                        for key, child in value.items()
                        if isinstance(child, (int, float))
                    ]
                }
        return None

    def _coerce_flowchart_data(self, value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict):
            if isinstance(value.get("nodes"), list):
                return {"nodes": value.get("nodes") or [], "edges": value.get("edges") or []}
            if isinstance(value.get("steps"), list):
                nodes = []
                for index, item in enumerate(value.get("steps") or [], start=1):
                    label = self._trim(item.get("title") or item.get("label") or item.get("name")) if isinstance(item, dict) else self._trim(item)
                    if label:
                        nodes.append({"id": f"node_{index}", "label": label})
                return {"nodes": nodes, "edges": []} if nodes else None
        if isinstance(value, list):
            nodes = []
            for index, item in enumerate(value[:20], start=1):
                label = self._trim(item.get("title") or item.get("label") or item.get("name")) if isinstance(item, dict) else self._trim(item)
                if label:
                    nodes.append({"id": f"node_{index}", "label": label})
            return {"nodes": nodes, "edges": []} if nodes else None
        return None

    def _normalize_text_list_item(self, item: Any) -> Dict[str, str]:
        if isinstance(item, dict):
            return {
                "title": self._trim(item.get("title") or item.get("name") or item.get("label")),
                "desc": self._trim(item.get("desc") or item.get("detail") or item.get("snippet") or item.get("text")),
                "time": self._trim(item.get("time") or item.get("publish_time")),
            }
        return {"title": "", "desc": self._trim(item), "time": ""}

    def _looks_like_metric_map(self, value: Dict[str, Any]) -> bool:
        if not value:
            return False
        scalar_count = 0
        for child in value.values():
            if isinstance(child, (int, float, bool, str)) or child is None:
                scalar_count += 1
            else:
                return False
        return scalar_count > 0

    def _humanize_path(self, path: str) -> str:
        text = self._trim(path).replace(".", " / ").replace("_", " ")
        return text or "结果"

    def _stringify_scalar(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    def _normalize_usage(self, usage: Any) -> Dict[str, int]:
        source = usage if isinstance(usage, dict) else {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        }
        return {
            "prompt_tokens": int(source.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(source.get("completion_tokens", 0) or 0),
            "total_tokens": int(source.get("total_tokens", 0) or 0),
        }

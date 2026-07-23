from __future__ import annotations

from typing import Any, Dict, Optional

from src.services.conversation_preprocess_service import ConversationPreprocessService
from src.services.top_level_shortcut_service import TopLevelShortcutService


class AssistantDispatchPlanner:
    def __init__(
        self,
        *,
        preprocess_service: Optional[ConversationPreprocessService] = None,
        shortcut_service: Optional[TopLevelShortcutService] = None,
    ) -> None:
        self.preprocess_service = preprocess_service or ConversationPreprocessService()
        self.shortcut_service = shortcut_service or TopLevelShortcutService()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def plan_turn(
        self,
        *,
        text: str,
        attachments: list[dict] | None = None,
        thread_context: dict | None = None,
        application_context: dict | None = None,
        interaction_response: dict | None = None,
    ) -> Dict[str, Any]:
        shortcut = self.shortcut_service.resolve(
            text=text,
            interaction_response=interaction_response,
            application_context=application_context,
        )
        if shortcut is not None:
            return shortcut
        return self.plan_free_chat(
            text=text,
            attachments=attachments,
            thread_context=thread_context,
            application_context=application_context,
        )

    def plan_free_chat(
        self,
        *,
        text: str,
        attachments: list[dict] | None = None,
        thread_context: dict | None = None,
        application_context: dict | None = None,
    ) -> Dict[str, Any]:
        result = self.preprocess_service.preprocess(
            text=text,
            attachments=attachments,
            thread_context=thread_context,
            application_context=application_context,
            enable_llm=True,
        )
        dispatch_plan = result.get("dispatch_plan") if isinstance(result.get("dispatch_plan"), dict) else {}
        entry = self._trim(dispatch_plan.get("entry")) or "agent_route"
        target = dispatch_plan.get("target") if isinstance(dispatch_plan.get("target"), dict) else {}
        interaction = result.get("interaction") if isinstance(result.get("interaction"), dict) else {}
        intent = result.get("intent") if isinstance(result.get("intent"), dict) else {}
        work_context = result.get("work_context") if isinstance(result.get("work_context"), dict) else {}
        normalized_input = result.get("normalized_input") if isinstance(result.get("normalized_input"), dict) else {}
        normalized_request = result.get("normalized_request") if isinstance(result.get("normalized_request"), dict) else {}
        turn_mode = self._trim(dispatch_plan.get("turn_mode") or interaction.get("turn_mode"))
        if turn_mode not in {"normal_qa", "system_operation", "tool_development"}:
            raise ValueError(f"顶层意图协议错误：未知 turn_mode={turn_mode or '-'}")
        execution_plan = (
            dispatch_plan.get("execution_plan_preview")
            if isinstance(dispatch_plan.get("execution_plan_preview"), dict)
            else result.get("execution_plan_preview")
            if isinstance(result.get("execution_plan_preview"), dict)
            else result.get("execution_plan")
            if isinstance(result.get("execution_plan"), dict)
            else {}
        )
        task_state = self._build_planning_task_state(
            interaction=interaction,
            normalized_request=normalized_request,
            context_resolution=result.get("context_resolution") if isinstance(result.get("context_resolution"), dict) else {},
            execution_plan=execution_plan,
            entry=entry,
            execution_path=self._trim(result.get("execution_path")),
        )

        return {
            "entry": entry,
            "turn_mode": turn_mode,
            "domain": self._trim(result.get("domain")),
            "interaction_mode": self._trim(result.get("interaction_mode")),
            "execution_path": self._trim(result.get("execution_path")),
            "canonical_axes": {
                "domain": self._trim(result.get("domain")),
                "interaction_mode": self._trim(result.get("interaction_mode")),
                "execution_path": self._trim(result.get("execution_path")),
            },
            "legacy_runtime_axes": result.get("legacy_runtime_axes") if isinstance(result.get("legacy_runtime_axes"), dict) else {
                "task_domain": self._trim(result.get("task_domain")),
                "capability_family": self._trim(result.get("capability_family")),
            },
            # Backward-compatible aliases while callers migrate to canonical_axes / legacy_runtime_axes.
            "task_domain": self._trim(result.get("task_domain")),
            "capability_family": self._trim(result.get("capability_family")),
            "selected_agent": self._trim(dispatch_plan.get("selected_agent")),
            "semantic_turn": {
                "ori_question": self._trim(normalized_request.get("ori_question") or normalized_request.get("raw_user_text")),
                "resolved_question": self._trim(normalized_request.get("resolved_question") or normalized_request.get("round_task_desc")),
                "context_refs": normalized_request.get("context_refs") if isinstance(normalized_request.get("context_refs"), list) else [],
            },
            "planning_scope": self._trim(dispatch_plan.get("planning_scope")) or "top_level_dispatch",
            "execution_plan": (
                execution_plan
            ),
            "task_state": task_state,
            "thread_context_patch_preview": result.get("thread_context_patch_preview") if isinstance(result.get("thread_context_patch_preview"), dict) else {},
            "runtime_contract": result.get("runtime_contract") if isinstance(result.get("runtime_contract"), dict) else {},
            "conversation_mainline": result.get("conversation_mainline") if isinstance(result.get("conversation_mainline"), dict) else {},
            "runtime_modules": result.get("runtime_modules") if isinstance(result.get("runtime_modules"), list) else [],
            "runtime_node_results": result.get("runtime_node_results") if isinstance(result.get("runtime_node_results"), list) else [],
            "runtime_feedback_protocol": result.get("runtime_feedback_protocol") if isinstance(result.get("runtime_feedback_protocol"), dict) else {},
            "interaction": interaction,
            "intent": intent,
            "work_context": work_context,
            "normalized_input": normalized_input,
            "target": {
                "type": self._trim(target.get("type")),
                "name": self._trim(target.get("name")),
            },
            "browse_mode": self._trim(interaction.get("browse_mode") or intent.get("mode")),
            "asset_type": self._trim(interaction.get("asset_type") or intent.get("asset_type") or target.get("type")),
            "asset_name": self._trim(intent.get("asset_name") or target.get("name")),
            "llm_usage": result.get("llm_usage") if isinstance(result.get("llm_usage"), dict) else {},
            "source": "assistant_dispatch_planner",
            "preprocess_result": result,
        }

    def _build_planning_task_state(
        self,
        *,
        interaction: Dict[str, Any],
        normalized_request: Dict[str, Any],
        context_resolution: Dict[str, Any],
        execution_plan: Dict[str, Any],
        entry: str,
        execution_path: str,
    ) -> Dict[str, Any]:
        steps = []

        interaction_message = self._trim(interaction.get("analize"))
        if interaction_message:
            steps.append(
                {
                    "seq": len(steps) + 1,
                    "stage": "planning",
                    "step_type": "interaction_preprocessor",
                    "title": "理解问题",
                    "message": interaction_message,
                    "status": "completed",
                }
            )

        resolved_items = context_resolution.get("resolved_items") if isinstance(context_resolution.get("resolved_items"), list) else []
        resolution_summary = self._trim(context_resolution.get("resolution_summary"))
        if resolution_summary or resolved_items:
            steps.append(
                {
                    "seq": len(steps) + 1,
                    "stage": "planning",
                    "step_type": "context_resolution",
                    "title": "整理上下文",
                    "message": resolution_summary or f"已提取 {len(resolved_items)} 条相关前文信息。",
                    "status": "completed",
                }
            )

        round_task_desc = self._trim(normalized_request.get("round_task_desc"))
        if round_task_desc:
            steps.append(
                {
                    "seq": len(steps) + 1,
                    "stage": "planning",
                    "step_type": "conversation_task_finalization",
                    "title": "整理任务",
                    "message": round_task_desc,
                    "status": "completed",
                }
            )

        plan_message = self._build_plan_summary(execution_plan=execution_plan, entry=entry, execution_path=execution_path)
        if plan_message:
            steps.append(
                {
                    "seq": len(steps) + 1,
                    "stage": "planning",
                    "step_type": "planning_result",
                    "title": "生成执行方案",
                    "message": plan_message,
                    "status": "completed",
                }
            )

        return {
            "job": {
                "job_id": "planning_sync",
                "task_type": self._trim(execution_plan.get("plan_type")) or entry or "agent_route",
                "plan_mode": "planning",
                "status": "succeeded",
                "current_stage": "planning_completed",
                "progress": 100.0,
                "result_summary": plan_message or round_task_desc or self._trim(interaction.get("analize")),
            },
            "steps": steps,
        }

    def _build_plan_summary(self, *, execution_plan: Dict[str, Any], entry: str, execution_path: str) -> str:
        selected_tools = execution_plan.get("selected_tools") if isinstance(execution_plan.get("selected_tools"), list) else []
        work_items = execution_plan.get("work_items") if isinstance(execution_plan.get("work_items"), list) else []
        tool_names = [self._trim(item) for item in selected_tools if self._trim(item)]
        if tool_names:
            if work_items:
                return f"已选择 {len(tool_names)} 个工具：{', '.join(tool_names[:4])}；共规划 {len(work_items)} 个步骤。"
            return f"已选择工具：{', '.join(tool_names[:4])}。"
        if work_items:
            return f"已规划 {len(work_items)} 个执行步骤，执行路径为 {execution_path or entry or 'agent_route'}。"
        if execution_path or entry:
            return f"已确定处理路径：{execution_path or entry}。"
        return ""

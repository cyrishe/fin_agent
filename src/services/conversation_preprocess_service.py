from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from src.services.assistant_interaction_preprocessor import AssistantInteractionPreprocessor
from src.services.assistant_intent_service import AssistantIntentService
from src.services.agent_runtime_llm_planner_service import AgentRuntimeLLMPlannerService
from src.services.context_resolution_service import ContextResolutionService
from src.services.context_write_policy_service import ContextWritePolicyService
from src.services.conversation_runtime_contract_service import ConversationRuntimeContractService
from src.services.conversation_mainline_contract_service import ConversationMainlineContractService
from src.services.conversation_state_machine_service import ConversationStateMachineService
from src.services.conversation_task_finalizer_service import ConversationTaskFinalizerService
from src.services.execution_plan_service import AgentRuntimePlanner, ExecutionPlanService
from src.services.interaction_frame_service import InteractionFrameService
from src.services.system_command_service import SystemCommandService


class ConversationPreprocessService:
    def __init__(
        self,
        *,
        interaction_preprocessor: Optional[AssistantInteractionPreprocessor] = None,
        assistant_intent_service: Optional[AssistantIntentService] = None,
        agent_runtime_planner: Optional[AgentRuntimePlanner] = None,
        agent_runtime_llm_planner_service: Optional[AgentRuntimeLLMPlannerService] = None,
        execution_plan_service: Optional[ExecutionPlanService] = None,
        interaction_frame_service: Optional[InteractionFrameService] = None,
        conversation_state_machine_service: Optional[ConversationStateMachineService] = None,
        context_write_policy_service: Optional[ContextWritePolicyService] = None,
        runtime_contract_service: Optional[ConversationRuntimeContractService] = None,
        mainline_contract_service: Optional[ConversationMainlineContractService] = None,
        system_command_service: Optional[SystemCommandService] = None,
        context_resolution_service: Optional[ContextResolutionService] = None,
        conversation_task_finalizer_service: Optional[ConversationTaskFinalizerService] = None,
    ) -> None:
        self.interaction_preprocessor = interaction_preprocessor or AssistantInteractionPreprocessor()
        self.assistant_intent_service = assistant_intent_service or AssistantIntentService()
        self.agent_runtime_planner = agent_runtime_planner or execution_plan_service or AgentRuntimePlanner()
        self.interaction_frame_service = interaction_frame_service or InteractionFrameService()
        self.conversation_state_machine_service = conversation_state_machine_service or ConversationStateMachineService()
        self.context_write_policy_service = context_write_policy_service or ContextWritePolicyService()
        self.runtime_contract_service = runtime_contract_service or ConversationRuntimeContractService()
        self.mainline_contract_service = mainline_contract_service or ConversationMainlineContractService()
        self.context_resolution_service = context_resolution_service or ContextResolutionService()
        self.conversation_task_finalizer_service = conversation_task_finalizer_service or ConversationTaskFinalizerService()
        self.agent_runtime_llm_planner_service = agent_runtime_llm_planner_service or AgentRuntimeLLMPlannerService(
            fallback_planner=self.agent_runtime_planner,
            prompt_context_compiler=None,
        )
        self.system_command_service = system_command_service or SystemCommandService()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_usage(usage: Any) -> Dict[str, int]:
        source = usage if isinstance(usage, dict) else {}
        return {
            "prompt_tokens": int(source.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(source.get("completion_tokens", 0) or 0),
            "total_tokens": int(source.get("total_tokens", 0) or 0),
            "call_count": int(source.get("call_count", 0) or 0),
        }

    def _merge_llm_usage(self, *usages: Any) -> Dict[str, int]:
        merged = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}
        for usage in usages:
            normalized = self._normalize_usage(usage)
            if (
                normalized["prompt_tokens"] > 0
                or normalized["completion_tokens"] > 0
                or normalized["total_tokens"] > 0
            ) and normalized["call_count"] <= 0:
                normalized["call_count"] = 1
            merged["prompt_tokens"] += normalized["prompt_tokens"]
            merged["completion_tokens"] += normalized["completion_tokens"]
            merged["total_tokens"] += normalized["total_tokens"]
            merged["call_count"] += normalized["call_count"]
        return merged

    @staticmethod
    def _parse_slash_command(text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not raw.startswith("/"):
            return {"kind": "free_chat", "command": "", "args": [], "raw": raw}
        parts = raw.split()
        command = str(parts[0] or "").strip().lower()
        return {
            "kind": "slash",
            "command": command,
            "args": parts[1:],
            "raw": raw,
        }

    def preprocess(
        self,
        *,
        text: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        thread_context: Optional[Dict[str, Any]] = None,
        application_context: Optional[Dict[str, Any]] = None,
        enable_llm: bool = False,
    ) -> Dict[str, Any]:
        normalized_input = self._normalize_input(
            text=text,
            attachments=attachments,
            thread_context=thread_context,
            application_context=application_context,
        )
        work_context = self._build_work_context(
            thread_context=thread_context,
            application_context=application_context,
        )
        if bool(normalized_input.get("code_runtime_hint")):
            work_context["code_runtime_hint"] = True
            work_context["preferred_runtime"] = "code"
        normalized_command = normalized_input.get("normalized_command") if isinstance(normalized_input.get("normalized_command"), dict) else {}
        command_action = self._trim(normalized_command.get("action"))
        is_free_chat = not command_action
        active_workflow = work_context.get("active_workflow") if isinstance(work_context.get("active_workflow"), dict) else {}
        is_active_custom_tool_flow = is_free_chat and self._trim(active_workflow.get("type")) == "custom_tool_authoring"
        preprocess_enable_llm = enable_llm and not is_active_custom_tool_flow
        interaction = (
            self.interaction_preprocessor.classify(
                user_text=normalized_input["text"],
                thread_context=thread_context,
                application_context=application_context,
            )
            if preprocess_enable_llm and is_free_chat
            else {
                "analize": "",
                "domain_hint": "",
                "agent_hint": "",
                "needs_reference_resolution": False,
                "info_ready": False,
                "source": "disabled",
            }
        )
        intent = (
            self.assistant_intent_service.classify(
                user_text=normalized_input["text"],
                thread_context=thread_context,
            )
            if not is_free_chat
            else {"intent_type": "none", "source": "disabled_for_free_chat"}
        )
        preprocessing_signals = self._build_preprocessing_signals(
            normalized_input=normalized_input,
            thread_context=thread_context,
            work_context=work_context,
            interaction=interaction,
        )
        thread_state = self._build_thread_state(
            thread_context=thread_context,
            work_context=work_context,
        )
        context_window = self._build_context_window(
            thread_context=thread_context,
        )
        context_resolution = self.context_resolution_service.resolve(
            user_text=normalized_input["text"],
            context_window=context_window,
            thread_state=thread_state,
            preprocessing_signals=preprocessing_signals,
            interaction_result=interaction,
            enable_llm=preprocess_enable_llm,
        )
        normalized_request = self._build_normalized_request(
            normalized_input=normalized_input,
            interaction=interaction,
            preprocessing_signals=preprocessing_signals,
            context_resolution=context_resolution,
            enable_llm=preprocess_enable_llm,
        )
        if is_active_custom_tool_flow:
            task_domain_decision = self._decision(
                "system_operation",
                "thread_context",
                "continue active custom tool authoring flow",
            )
            task_domain = "system_operation"
            capability_family_decision = self._decision(
                "custom_tool_authoring",
                "thread_context",
                "active custom tool state is present",
            )
            capability_family = "custom_tool_authoring"
            selected_agent = "custom_tool_builder"
            execution_plan_preview = {}
        else:
            task_domain_decision = self._decide_task_domain(
                normalized_input=normalized_input,
                normalized_request=normalized_request,
            )
            task_domain = self._trim(task_domain_decision.get("value")) or "business_dialog"
            capability_family_decision = self._decide_capability_family(
                normalized_input=normalized_input,
                normalized_request=normalized_request,
                task_domain=task_domain,
            )
            capability_family = self._trim(capability_family_decision.get("value")) or "business_analysis"
            selected_agent = self._select_agent_name(
                task_domain=task_domain,
                work_context=work_context,
                interaction=interaction,
            )
            execution_plan_preview = self._build_execution_plan_preview(
                normalized_request=normalized_request,
                work_context=work_context,
                application_context=application_context,
                task_domain=task_domain,
                capability_family=capability_family,
                selected_agent=selected_agent,
                interaction_frame=None,
                conversation_state=None,
                enable_llm=enable_llm,
            )
        dispatch_plan = self._build_dispatch_plan(
            normalized_input=normalized_input,
            normalized_request=normalized_request,
            task_domain=task_domain,
            capability_family=capability_family,
            selected_agent=selected_agent,
            execution_plan_preview=execution_plan_preview,
        )
        turn_frame = self._build_turn_frame(
            normalized_input=normalized_input,
            normalized_request=normalized_request,
            dispatch_plan=dispatch_plan,
            thread_context=thread_context,
        )
        interaction_compat = self._build_interaction_compat(normalized_request=normalized_request)
        interaction_frame = self.interaction_frame_service.build_frame(
            normalized_input=normalized_input,
            work_context=work_context,
            task_domain=task_domain,
            capability_family=capability_family,
            interaction=interaction_compat,
            dispatch_plan=dispatch_plan,
            execution_plan_preview=execution_plan_preview,
            thread_context=thread_context,
        )
        conversation_state = self.conversation_state_machine_service.derive_state(
            interaction_frame=interaction_frame,
            dispatch_plan=dispatch_plan,
            work_context=work_context,
        )
        observation_preview = self._build_observation_preview(
            conversation_state=conversation_state,
            dispatch_plan=dispatch_plan,
        )
        context_write_policy = self.context_write_policy_service.build_policy(
            normalized_input=normalized_input,
            interaction_frame=interaction_frame,
            conversation_state=conversation_state,
            dispatch_plan=dispatch_plan,
            execution_plan_preview=execution_plan_preview,
            work_context=work_context,
        )
        continuity_axes = self._build_continuity_axes(
            task_domain=task_domain,
            interaction_frame=interaction_frame,
            dispatch_plan=dispatch_plan,
            execution_plan_preview=execution_plan_preview,
        )
        thread_context_patch_preview = self._build_thread_context_patch_preview(
            context_write_policy=context_write_policy,
            continuity_axes=continuity_axes,
        )
        runtime_contract = self.runtime_contract_service.build_preprocess_contract(
            normalized_input=normalized_input,
            interaction=interaction,
            context_resolution=context_resolution,
            normalized_request=normalized_request,
            task_domain_decision=task_domain_decision,
            capability_family_decision=capability_family_decision,
            selected_agent=selected_agent,
            dispatch_plan=dispatch_plan,
            execution_plan_preview=execution_plan_preview,
            interaction_frame=interaction_frame,
            conversation_state=conversation_state,
            context_write_policy=context_write_policy,
            thread_context_patch_preview=thread_context_patch_preview,
        )
        conversation_mainline = self.mainline_contract_service.build_contract(
            normalized_input=normalized_input,
            dispatch_plan=dispatch_plan,
            execution_plan=execution_plan_preview,
            work_context=work_context,
            conversation_state=conversation_state,
        )
        domain = self._trim(continuity_axes.get("domain")) or self._normalize_domain(task_domain)
        interaction_mode = self._trim(interaction_frame.get("interaction_mode")) or "execute_business_task"
        execution_path = self._trim(continuity_axes.get("execution_path")) or self._normalize_execution_path(dispatch_plan, execution_plan_preview)
        llm_usage = self._merge_llm_usage(
            interaction.get("llm_usage") if isinstance(interaction, dict) else None,
            context_resolution.get("llm_usage") if isinstance(context_resolution, dict) else None,
            normalized_request.get("llm_usage") if isinstance(normalized_request, dict) else None,
            execution_plan_preview.get("llm_usage") if isinstance(execution_plan_preview, dict) else None,
        )
        return {
            "normalized_input": normalized_input,
            "domain": domain,
            "interaction_mode": interaction_mode,
            "execution_path": execution_path,
            "task_domain": task_domain,
            "input_modalities": normalized_input["input_modalities"],
            "work_context": work_context,
            "thread_state": thread_state,
            "context_window": context_window,
            "preprocessing_signals": preprocessing_signals,
            "context_resolution": context_resolution,
            "normalized_request": normalized_request,
            "turn_frame": turn_frame,
            "interaction_frame": interaction_frame,
            "conversation_state": conversation_state,
            "observation_preview": observation_preview,
            "context_write_policy": context_write_policy,
            "continuity_axes": continuity_axes,
            "thread_context_patch_preview": thread_context_patch_preview,
            "runtime_contract": runtime_contract,
            "conversation_mainline": conversation_mainline,
            "runtime_modules": runtime_contract.get("modules") if isinstance(runtime_contract.get("modules"), list) else [],
            "runtime_node_results": runtime_contract.get("node_results") if isinstance(runtime_contract.get("node_results"), list) else [],
            "runtime_feedback_protocol": runtime_contract.get("feedback_protocol") if isinstance(runtime_contract.get("feedback_protocol"), dict) else {},
            "interaction": interaction,
            "interaction_compat": interaction_compat,
            "intent": intent,
            "capability_family": capability_family,
            "decision_sources": {
                "interaction": self._trim(interaction.get("source")) or "unknown",
                "intent": "rule",
                "domain": {
                    "value": domain,
                    "source": "derived.continuity_axes",
                    "reason": self._trim(task_domain_decision.get("reason")) or "derived_from_task_domain",
                },
                "interaction_mode": {
                    "value": interaction_mode,
                    "source": "derived.interaction_frame",
                    "reason": "derived_from_interaction_frame",
                },
                "execution_path": {
                    "value": execution_path,
                    "source": "derived.continuity_axes",
                    "reason": "derived_from_dispatch_or_selected_path",
                },
                "task_domain": task_domain_decision,
                "capability_family": capability_family_decision,
            },
            "legacy_runtime_axes": {
                "task_domain": task_domain,
                "capability_family": capability_family,
            },
            "dispatch_plan": dispatch_plan,
            "execution_plan_preview": execution_plan_preview,
            # Backward-compatible alias while downstream callers migrate to execution_plan_preview.
            "execution_plan": execution_plan_preview,
            "llm_usage": llm_usage,
        }

    def _normalize_input(
        self,
        *,
        text: str,
        attachments: Optional[List[Dict[str, Any]]],
        thread_context: Optional[Dict[str, Any]],
        application_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        raw_text = self._trim(text)
        normalized_text = raw_text
        code_runtime_hint = False
        if normalized_text.lower().startswith("/code"):
            parts = normalized_text.split(maxsplit=1)
            if parts and parts[0].lower() == "/code":
                code_runtime_hint = True
                normalized_text = self._trim(parts[1] if len(parts) > 1 else "")
        normalized_attachments = self._normalize_attachments(attachments)
        parsed_command = self._parse_slash_command(normalized_text)
        normalized_command = (
            self.system_command_service.normalize(
                command=str(parsed_command.get("command") or ""),
                args=parsed_command.get("args") if isinstance(parsed_command.get("args"), list) else [],
            )
            if parsed_command.get("kind") == "slash"
            else {"action": "", "kind": "free_chat", "args": [], "target_type": "", "target_name": ""}
        )
        return {
            "text": normalized_text,
            "raw_text": raw_text,
            "code_runtime_hint": code_runtime_hint,
            "attachments": normalized_attachments,
            "has_attachments": bool(normalized_attachments),
            "has_images": any(item.get("kind") == "image" for item in normalized_attachments),
            "input_modalities": self._detect_input_modalities(normalized_text, normalized_attachments),
            "slash_command": parsed_command,
            "normalized_command": normalized_command,
            "thread_context_present": isinstance(thread_context, dict),
            "application_name": self._trim((application_context or {}).get("application_name")),
        }

    def _normalize_attachments(self, attachments: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in attachments or []:
            if not isinstance(item, dict):
                continue
            attachment_id = self._trim(item.get("attachment_id"))
            kind = self._trim(item.get("kind")) or "unknown"
            mime_type = self._trim(item.get("mime_type"))
            if not attachment_id and not mime_type and kind == "unknown":
                continue
            rows.append({
                "attachment_id": attachment_id,
                "kind": kind,
                "mime_type": mime_type,
                "file_name": self._trim(item.get("file_name")),
                "file_size": item.get("file_size") if isinstance(item.get("file_size"), int) else 0,
            })
        return rows

    def _detect_input_modalities(self, text: str, attachments: List[Dict[str, Any]]) -> List[str]:
        has_text = bool(self._trim(text))
        has_images = any(item.get("kind") == "image" for item in attachments)
        has_files = any(item.get("kind") in {"table", "document"} for item in attachments)
        if has_text and has_files:
            return ["text_with_file"]
        if has_files:
            return ["file_only"]
        if has_text and has_images:
            return ["text_with_image"]
        if has_images:
            return ["image_only"]
        return ["text_only"]

    def _build_work_context(
        self,
        *,
        thread_context: Optional[Dict[str, Any]],
        application_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        ctx = thread_context if isinstance(thread_context, dict) else {}
        app_ctx = application_context if isinstance(application_context, dict) else {}
        assistant_agent = app_ctx.get("assistant_agent") if isinstance(app_ctx.get("assistant_agent"), dict) else {}
        execution_agent = app_ctx.get("execution_agent") if isinstance(app_ctx.get("execution_agent"), dict) else {}
        custom_tool_state = ctx.get("custom_tool_state") if isinstance(ctx.get("custom_tool_state"), dict) else {}
        image_ids = [
            self._trim(item) for item in (ctx.get("last_image_attachment_ids") or [])
            if self._trim(item)
        ]
        recent_attachments: List[Dict[str, Any]] = []
        if image_ids:
            recent_attachments.append(
                {
                    "attachment_ids": image_ids,
                    "kind": "image",
                    "summary": self._trim(ctx.get("last_image_summary")),
                }
            )
        work_context = {
            "application_name": self._trim(app_ctx.get("application_name")),
            "assistant_agent": self._trim(assistant_agent.get("agent_name")),
            "execution_agent": self._trim(execution_agent.get("agent_name")),
            "thread_active_skill_name": self._trim(ctx.get("active_skill_name")),
            "thread_active_skill_canonical_name": self._trim(ctx.get("active_skill_canonical_name")),
            "recent_attachments": recent_attachments,
            "recent_result_subject": self._trim(ctx.get("recent_result_subject")),
        }
        if custom_tool_state:
            work_context["active_workflow"] = {
                "type": "custom_tool_authoring",
                "status": self._trim(custom_tool_state.get("status")),
                "tool_name": self._trim(custom_tool_state.get("tool_name")),
            }
        owner_ids = [
            self._trim(item)
            for item in (ctx.get("_custom_tool_owner_ids") or [])
            if self._trim(item)
        ]
        if owner_ids:
            work_context["_custom_tool_owner_ids"] = owner_ids
        return work_context

    def _build_preprocessing_signals(
        self,
        *,
        normalized_input: Dict[str, Any],
        thread_context: Optional[Dict[str, Any]],
        work_context: Optional[Dict[str, Any]],
        interaction: Dict[str, Any],
    ) -> Dict[str, Any]:
        text = self._trim(normalized_input.get("text"))
        return {
            "needs_reference_resolution": self._should_resolve_reference(
                text=text,
                interaction=interaction,
            ),
            "code_runtime_hint": bool(normalized_input.get("code_runtime_hint")),
            "info_ready": bool(interaction.get("info_ready")),
            "time_refs": self._extract_time_refs(text),
            "resolved_references": self._extract_resolved_references(
                text=text,
                thread_context=thread_context,
            ),
            "correction_signals": self._extract_correction_signals(text),
            "attachment_signals": self._extract_attachment_signals(normalized_input),
            "recent_result_subject": self._trim((work_context or {}).get("recent_result_subject")),
        }

    def _build_thread_state(
        self,
        *,
        thread_context: Optional[Dict[str, Any]],
        work_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        ctx = thread_context if isinstance(thread_context, dict) else {}
        work = work_context if isinstance(work_context, dict) else {}
        reference_memory = ctx.get("reference_memory") if isinstance(ctx.get("reference_memory"), dict) else {}
        return {
            "session_application_name": self._trim(work.get("application_name")),
            "session_assistant_agent": self._trim(work.get("assistant_agent")),
            "session_execution_agent": self._trim(work.get("execution_agent")),
            "thread_active_skill_name": self._trim(work.get("thread_active_skill_name")),
            "thread_active_skill_canonical_name": self._trim(work.get("thread_active_skill_canonical_name")),
            "recent_attachments": self._work_context_recent_attachments(work),
            "recent_result_subject": self._trim(work.get("recent_result_subject")),
            "reference_memory": reference_memory,
            "thread_summary": self._trim(ctx.get("thread_summary")),
        }

    def _build_context_window(
        self,
        *,
        thread_context: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        ctx = thread_context if isinstance(thread_context, dict) else {}
        raw_window = ctx.get("context_window")
        if isinstance(raw_window, list):
            normalized: List[Dict[str, Any]] = []
            for item in raw_window:
                if not isinstance(item, dict):
                    continue
                attachments: List[Dict[str, str]] = []
                for att in (item.get("attachments") or []):
                    if not isinstance(att, dict):
                        continue
                    attachment_id = self._trim(att.get("attachment_id"))
                    if not attachment_id:
                        continue
                    attachments.append(
                        {
                            "attachment_id": attachment_id,
                            "attachment_summary": self._trim(att.get("attachment_summary")),
                        }
                    )
                normalized.append(
                    {
                        "round": int(item.get("round") or 0),
                        "role": self._trim(item.get("role")) or "unknown",
                        "text": self._trim(item.get("text")),
                        "attachments": attachments,
                    }
                )
            if normalized:
                return normalized
        summary = self._trim(ctx.get("thread_summary"))
        if summary:
            return [
                {
                    "round": 0,
                    "role": "assistant",
                    "text": summary,
                    "attachments": [],
                }
            ]
        recent_subject = self._trim(ctx.get("recent_result_subject"))
        if recent_subject:
            return [
                {
                    "round": 0,
                    "role": "assistant",
                    "text": recent_subject,
                    "attachments": [],
                }
            ]
        return []

    def _build_normalized_request(
        self,
        *,
        normalized_input: Dict[str, Any],
        interaction: Dict[str, Any],
        preprocessing_signals: Dict[str, Any],
        context_resolution: Dict[str, Any],
        enable_llm: bool,
    ) -> Dict[str, Any]:
        text = self._trim(normalized_input.get("text"))
        context_relation = self._derive_context_relation(
            text=text,
            interaction=interaction,
            preprocessing_signals=preprocessing_signals,
        )
        focus = self._build_fallback_focus(
            preprocessing_signals=preprocessing_signals,
            context_resolution=context_resolution,
        )
        target_asset = self._build_fallback_target_asset(
            text=text,
            domain_hint=self._trim(interaction.get("domain_hint")),
        )
        domain = "system" if self._trim(interaction.get("domain_hint")) == "system" else "business"
        command = normalized_input.get("normalized_command") if isinstance(normalized_input.get("normalized_command"), dict) else {}
        resolved_items = context_resolution.get("resolved_items") if isinstance(context_resolution.get("resolved_items"), list) else []
        has_context_resolution = bool(resolved_items) or bool(self._trim(context_resolution.get("resolution_summary")))
        interaction_requires_reference = bool(interaction.get("needs_reference_resolution"))
        should_finalize_with_llm = (
            enable_llm
            and not self._trim(command.get("action"))
            and (
                interaction_requires_reference
                or has_context_resolution
                or (context_relation == "corrective")
            )
        )
        finalized: Dict[str, Any] = {}
        if should_finalize_with_llm:
            finalized = self.conversation_task_finalizer_service.finalize(
                raw_user_text=text,
                interaction_result=interaction,
                preprocessing_signals=preprocessing_signals,
                context_resolution=context_resolution,
                context_relation=context_relation,
                focus=focus,
                target_asset=target_asset,
                domain=domain,
            )
        round_task_desc = self._trim(finalized.get("round_task_desc")) or text
        return {
            "raw_user_text": text,
            "analize": self._trim(finalized.get("analize")),
            "round_task_desc": round_task_desc,
            "task_splitd": [],
            "domain": domain,
            "context_relation": context_relation,
            "focus": focus,
            "target_asset": target_asset,
            "needs_reference_resolution": bool(preprocessing_signals.get("needs_reference_resolution")),
            "info_ready": bool(preprocessing_signals.get("info_ready")),
            "source": self._trim(finalized.get("source")) or "raw_input",
            "llm_usage": finalized.get("llm_usage") if isinstance(finalized.get("llm_usage"), dict) else {},
        }

    def _work_context_active_skill(self, work_context: Dict[str, Any]) -> str:
        return self._trim(
            work_context.get("thread_active_skill_canonical_name")
            or work_context.get("thread_active_skill_name")
            or work_context.get("active_skill_canonical_name")
            or work_context.get("active_skill_name")
        )

    def _work_context_recent_attachments(self, work_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = work_context.get("recent_attachments")
        if isinstance(rows, list):
            normalized: List[Dict[str, Any]] = []
            for item in rows:
                if not isinstance(item, dict):
                    continue
                attachment_ids = [self._trim(x) for x in (item.get("attachment_ids") or []) if self._trim(x)]
                if not attachment_ids:
                    continue
                normalized.append(
                    {
                        "attachment_ids": attachment_ids,
                        "kind": self._trim(item.get("kind")) or "attachment",
                        "summary": self._trim(item.get("summary")),
                    }
                )
            if normalized:
                return normalized
        legacy_ids = [self._trim(x) for x in (work_context.get("last_image_attachment_ids") or []) if self._trim(x)]
        if legacy_ids:
            return [{"attachment_ids": legacy_ids, "kind": "image", "summary": self._trim(work_context.get("last_image_summary"))}]
        return []

    def _decision(self, value: str, source: str, reason: str) -> Dict[str, str]:
        return {"value": value, "source": source, "reason": reason}

    def _should_resolve_reference(
        self,
        *,
        text: str,
        interaction: Dict[str, Any],
    ) -> bool:
        if bool(interaction.get("needs_reference_resolution")):
            return True
        normalized = self._trim(text)
        return self._contains_any(
            normalized,
            [
                "第二个",
                "第2个",
                "他们",
                "它们",
                "前面那个",
                "刚才那个",
                "上一轮",
                "上一步",
                "前面图里",
                "这张图",
                "那张图",
                "你前面",
                "刚才说的",
                "第二点计划",
            ],
        )

    def _extract_time_refs(self, text: str) -> List[Dict[str, str]]:
        normalized = self._trim(text)
        today = dt.date.today()
        rows: List[Dict[str, str]] = []
        mapping = {
            "今天": today.strftime("%Y-%m-%d"),
            "明天": (today + dt.timedelta(days=1)).strftime("%Y-%m-%d"),
            "昨天": (today - dt.timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        for raw, normalized_value in mapping.items():
            if raw in normalized:
                rows.append({"raw": raw, "normalized": normalized_value})
        return rows

    def _extract_resolved_references(
        self,
        *,
        text: str,
        thread_context: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        ctx = thread_context if isinstance(thread_context, dict) else {}
        reference_memory = ctx.get("reference_memory") if isinstance(ctx.get("reference_memory"), dict) else {}
        objects = reference_memory.get("objects") if isinstance(reference_memory.get("objects"), list) else []
        normalized_objects: List[Dict[str, str]] = []
        for item in objects:
            if not isinstance(item, dict):
                continue
            item_type = self._trim(item.get("object_type") or item.get("type"))
            item_id = self._trim(item.get("object_id") or item.get("id"))
            item_label = self._trim(item.get("display_name")) or item_id
            if not item_type or not item_label:
                continue
            normalized_objects.append({"type": item_type, "label": item_label})
        if not normalized_objects:
            return []
        if self._contains_any(text, ["第二个", "第2个"]) and len(normalized_objects) >= 2:
            item = normalized_objects[1]
            return [{"raw": "第二个", "type": item["type"], "label": item["label"]}]
        if self._contains_any(text, ["最后一个", "最后那个"]) and normalized_objects:
            item = normalized_objects[-1]
            return [{"raw": "最后一个", "type": item["type"], "label": item["label"]}]
        if self._contains_any(text, ["他们", "它们"]) and len(normalized_objects) >= 2:
            return [{"raw": "他们", "type": "group", "label": "、".join(item["label"] for item in normalized_objects[:4])}]
        for item in normalized_objects:
            if item["label"] and item["label"] in text:
                return [{"raw": item["label"], "type": item["type"], "label": item["label"]}]
        return []

    def _extract_attachment_signals(self, normalized_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = normalized_input.get("attachments") if isinstance(normalized_input.get("attachments"), list) else []
        normalized: List[Dict[str, Any]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            attachment_id = self._trim(item.get("attachment_id"))
            if not attachment_id:
                continue
            normalized.append(
                {
                    "attachment_ids": [attachment_id],
                    "kind": self._trim(item.get("kind")) or "attachment",
                    "summary": self._trim(item.get("summary"))
                    or self._trim(item.get("file_name"))
                    or self._trim(item.get("mime_type")),
                }
            )
        return normalized

    def _extract_correction_signals(self, text: str) -> List[Dict[str, str]]:
        normalized = self._trim(text)
        rows: List[Dict[str, str]] = []
        if self._contains_any(normalized, ["你前面", "你刚才", "理解错", "不是这个", "不对", "核实", "纠正"]):
            rows.append(
                {
                    "type": "user_correction",
                    "raw": normalized,
                    "summary": "用户在纠正、质疑或要求核实上一轮内容",
                }
            )
        return rows

    def _derive_context_relation(
        self,
        *,
        text: str,
        interaction: Dict[str, Any],
        preprocessing_signals: Dict[str, Any],
    ) -> str:
        normalized = self._trim(text)
        if self._contains_any(normalized, ["不对", "错了", "理解错", "核实", "重新核实", "纠正", "不是"]):
            return "corrective"
        if bool(preprocessing_signals.get("needs_reference_resolution")):
            if self._contains_any(normalized, ["好的", "继续", "接着", "那就继续", "行", "可以", "好"]):
                return "followup"
            return "referential"
        if self._contains_any(normalized, ["好的", "继续", "接着", "进一步", "展开", "再看看"]):
            return "followup"
        if self._trim(interaction.get("domain_hint")) == "system":
            return "new"
        return "new"

    def _build_fallback_focus(
        self,
        *,
        preprocessing_signals: Dict[str, Any],
        context_resolution: Dict[str, Any],
    ) -> Dict[str, str]:
        references = preprocessing_signals.get("resolved_references") if isinstance(preprocessing_signals.get("resolved_references"), list) else []
        if references:
            first = references[0] if isinstance(references[0], dict) else {}
            return {
                "type": self._trim(first.get("type")) or "reference",
                "label": self._trim(first.get("label")),
            }
        resolved_items = context_resolution.get("resolved_items") if isinstance(context_resolution.get("resolved_items"), list) else []
        if resolved_items:
            first = resolved_items[0] if isinstance(resolved_items[0], dict) else {}
            source_type = self._trim(first.get("source_type"))
            focus_type = "attachment" if source_type == "attachment" else "reference"
            return {"type": focus_type, "label": self._trim(first.get("summary"))}
        return {"type": "", "label": ""}

    def _build_fallback_target_asset(
        self,
        *,
        text: str,
        domain_hint: str,
    ) -> Dict[str, str]:
        if domain_hint == "system" and self._contains_any(text, ["技能", "skill"]):
            return {"type": "skill", "label": ""}
        if domain_hint == "system" and self._contains_any(text, ["工具", "tool"]):
            return {"type": "tool", "label": ""}
        return {"type": "", "label": ""}

    def _decide_task_domain(
        self,
        *,
        normalized_input: Dict[str, Any],
        normalized_request: Dict[str, Any],
    ) -> Dict[str, str]:
        command_action = self._trim(((normalized_input.get("normalized_command") or {}).get("action")))
        if command_action in {
            "catalog_skills",
            "catalog_tools",
            "catalog_agents",
            "catalog_applications",
            "catalog_tasks",
            "catalog_services",
            "open_skill",
            "open_tool",
            "open_agent",
            "open_application",
        }:
            return self._decision("system_operation", "rule.command", "slash_catalog_or_asset_command")
        if command_action in {"new_skill", "draft_skill", "refine_skill", "new_agent", "new_application"}:
            return self._decision("design_refinement", "rule.command", "slash_design_command")
        if command_action in {"run_skill"}:
            return self._decision("business_dialog", "rule.command", "slash_run_command_as_business")
        if self._trim(normalized_request.get("domain")) == "system":
            return self._decision("system_operation", "normalized_request", "domain_hint_system")
        return self._decision("business_dialog", "default", "fallback_business_dialog")

    def _decide_capability_family(
        self,
        *,
        normalized_input: Dict[str, Any],
        normalized_request: Dict[str, Any],
        task_domain: str,
    ) -> Dict[str, str]:
        command_action = self._trim(((normalized_input.get("normalized_command") or {}).get("action")))
        has_images = bool(normalized_input.get("has_images"))
        if task_domain == "system_operation":
            if command_action in {"open_application", "open_skill", "open_tool", "open_agent"}:
                return self._decision("asset_open", "rule.command", "system_asset_open")
            return self._decision("catalog", "default.system", "system_catalog")

        if task_domain == "business_dialog":
            focus = normalized_request.get("focus") if isinstance(normalized_request.get("focus"), dict) else {}
            focus_type = self._trim(focus.get("type"))
            if focus_type == "image":
                if has_images:
                    return self._decision("visual_analysis", "normalized_request", "image_focus_with_current_image")
                return self._decision("visual_followup", "normalized_request", "image_focus_followup")
            if has_images:
                return self._decision("visual_analysis", "rule.modality", "image_input")
            return self._decision("business_analysis", "default.business", "business_dialog_default")
        if has_images:
            return self._decision("visual_analysis", "rule.modality", "image_input")
        return self._decision("business_analysis", "default.business", "fallback_business_analysis")

    def _build_dispatch_plan(
        self,
        *,
        normalized_input: Dict[str, Any],
        normalized_request: Dict[str, Any],
        task_domain: str,
        capability_family: str,
        selected_agent: str,
        execution_plan_preview: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized_command = normalized_input.get("normalized_command") if isinstance(normalized_input.get("normalized_command"), dict) else {}
        command_action = self._trim(normalized_command.get("action"))
        dispatch_plan = {
            "version": "v1",
            "domain": task_domain,
            "selected_agent": selected_agent,
            "planning_scope": "top_level_dispatch",
            "context_bindings": {"normalized_request": True},
            "target": {"type": "", "name": ""},
            "entry": "agent_route",
        }

        if capability_family == "custom_tool_authoring":
            dispatch_plan["entry"] = "custom_tool_flow"
            dispatch_plan["target"] = {
                "type": "custom_tool",
                "name": self._trim(
                    ((normalized_request.get("focus") or {}) if isinstance(normalized_request.get("focus"), dict) else {}).get("label")
                ),
            }
            return dispatch_plan

        if task_domain == "system_operation":
            if capability_family == "asset_open":
                target_asset = normalized_request.get("target_asset") if isinstance(normalized_request.get("target_asset"), dict) else {}
                asset_type = self._trim(normalized_command.get("target_type") or target_asset.get("type"))
                asset_name = self._trim(normalized_command.get("target_name") or target_asset.get("label"))
                dispatch_plan["entry"] = "asset_open"
                dispatch_plan["target"] = {"type": asset_type or "unknown", "name": asset_name}
                return dispatch_plan
            dispatch_plan["entry"] = "catalog_browse"
            dispatch_plan["target"] = {"type": self._trim(command_action) or "catalog", "name": ""}
            return dispatch_plan

        if capability_family in {"visual_analysis", "visual_followup"}:
            focus = normalized_request.get("focus") if isinstance(normalized_request.get("focus"), dict) else {}
            dispatch_plan["entry"] = "vision_intake"
            dispatch_plan["target"] = {
                "type": "attachment" if normalized_input.get("has_images") else "recent_attachment",
                "name": self._trim(focus.get("label")),
            }
            return dispatch_plan

        if task_domain == "business_dialog":
            selected_path = (
                execution_plan_preview.get("selected_path")
                if isinstance(execution_plan_preview.get("selected_path"), dict)
                else {}
            )
            path_type = self._trim(selected_path.get("type"))
            path_target = selected_path.get("target") if isinstance(selected_path.get("target"), dict) else {}
            if path_type == "skill_run":
                dispatch_plan["entry"] = "skill_run"
                dispatch_plan["target"] = {
                    "type": self._trim(path_target.get("type")) or "skill",
                    "name": self._trim(path_target.get("name")),
                }
                dispatch_plan["planning_scope"] = "agent_runtime"
                dispatch_plan["execution_plan_preview"] = execution_plan_preview
                dispatch_plan["execution_plan"] = execution_plan_preview
                return dispatch_plan
            if path_type in {"tool_plan_run", "planned_run"}:
                dispatch_plan["entry"] = path_type
                dispatch_plan["target"] = {
                    "type": "planned_graph" if path_type == "planned_run" else "tool_group",
                    "name": "planned_workflow" if path_type == "planned_run" else "direct_tools",
                }
                dispatch_plan["planning_scope"] = "agent_runtime"
                dispatch_plan["execution_plan_preview"] = execution_plan_preview
                dispatch_plan["execution_plan"] = execution_plan_preview
                return dispatch_plan
            if capability_family == "skill_run":
                dispatch_plan["entry"] = "skill_run"
                dispatch_plan["target"] = {
                    "type": "skill",
                    "name": self._trim(((normalized_request.get("target_asset") or {}) if isinstance(normalized_request.get("target_asset"), dict) else {}).get("label")),
                }
                dispatch_plan["planning_scope"] = "agent_runtime"
                if execution_plan_preview:
                    dispatch_plan["execution_plan_preview"] = execution_plan_preview
                    dispatch_plan["execution_plan"] = execution_plan_preview
                return dispatch_plan

        dispatch_plan["entry"] = "agent_route"
        dispatch_plan["target"] = {"type": "agent", "name": selected_agent}
        if execution_plan_preview:
            dispatch_plan["execution_plan_preview"] = execution_plan_preview
            dispatch_plan["execution_plan"] = execution_plan_preview
            dispatch_plan["planning_scope"] = "agent_runtime"
        return dispatch_plan

    def _build_execution_plan_preview(
        self,
        *,
        normalized_request: Dict[str, Any],
        work_context: Dict[str, Any],
        application_context: Optional[Dict[str, Any]],
        task_domain: str,
        capability_family: str,
        selected_agent: str,
        interaction_frame: Optional[Dict[str, Any]],
        conversation_state: Optional[Dict[str, Any]],
        enable_llm: bool,
    ) -> Dict[str, Any]:
        if task_domain not in {"business_dialog"}:
            return {}
        if capability_family not in {"business_analysis", "visual_analysis", "visual_followup", "agent_route"}:
            return {}
        if self._trim(selected_agent) and self._trim(selected_agent) != self._trim(work_context.get("execution_agent")):
            return self.agent_runtime_planner.build_agent_route_plan(
                target_agent=self._trim(selected_agent),
                reason="selected_non_execution_agent_for_business_request",
            )
        if enable_llm:
            planner_result = self.agent_runtime_llm_planner_service.build_plan(
                user_objective=self._trim(normalized_request.get("round_task_desc") or normalized_request.get("raw_user_text")),
                recall_query=self._trim(normalized_request.get("raw_user_text")),
                tool_queries=[self._trim(item) for item in (normalized_request.get("task_splitd") or []) if self._trim(item)],
                work_context=work_context,
                application_context=application_context if isinstance(application_context, dict) else {},
                interaction_frame=interaction_frame if isinstance(interaction_frame, dict) else {},
                conversation_state=conversation_state if isinstance(conversation_state, dict) else {},
                enable_llm=True,
                allow_fallback=False,
            )
            if isinstance(planner_result.get("execution_plan"), dict) and planner_result.get("execution_plan"):
                return self._attach_planner_preview_metadata(
                    execution_plan=planner_result.get("execution_plan"),
                    planner_result=planner_result,
                )
        return self.agent_runtime_planner.build_business_dialog_plan(
            text=self._trim(normalized_request.get("round_task_desc") or normalized_request.get("raw_user_text")),
            recall_query=self._trim(normalized_request.get("raw_user_text")),
            tool_queries=[self._trim(item) for item in (normalized_request.get("task_splitd") or []) if self._trim(item)],
            work_context=work_context,
            application_context=application_context if isinstance(application_context, dict) else {},
            enable_llm=enable_llm,
        )

    def _attach_planner_preview_metadata(
        self,
        *,
        execution_plan: Dict[str, Any],
        planner_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized_plan = dict(execution_plan) if isinstance(execution_plan, dict) else {}
        metadata_fields = (
            "structured_task",
            "task_mode_result",
            "thinking_mode_result",
            "deep_plan_preview",
        )
        for field_name in metadata_fields:
            field_value = planner_result.get(field_name)
            if isinstance(field_value, dict) and field_value:
                normalized_plan[field_name] = field_value
        planner_source = self._trim(planner_result.get("source"))
        if planner_source:
            normalized_plan["planner_result_source"] = planner_source
        return normalized_plan

    def _build_turn_frame(
        self,
        *,
        normalized_input: Dict[str, Any],
        normalized_request: Dict[str, Any],
        dispatch_plan: Dict[str, Any],
        thread_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        focus_hint = normalized_request.get("focus") if isinstance(normalized_request.get("focus"), dict) else {}
        target_hint = normalized_request.get("target_asset") if isinstance(normalized_request.get("target_asset"), dict) else {}
        target = dispatch_plan.get("target") if isinstance(dispatch_plan.get("target"), dict) else {}
        existing_frame = (
            (thread_context or {}).get("interaction_frame")
            if isinstance((thread_context or {}).get("interaction_frame"), dict)
            else {}
        )
        focus_type = self._trim(focus_hint.get("type")) or self._trim(target.get("type"))
        focus_id = self._trim(focus_hint.get("label")) or self._trim(target.get("name"))
        if not focus_type:
            focus_type, focus_id = self._resolve_turn_frame_focus(
                work_context={},
                existing_frame=existing_frame,
            )
        target_asset_type = self._trim(target_hint.get("type"))
        target_asset_id = self._trim(target_hint.get("label"))
        if not target_asset_type:
            target_asset_type = self._trim(target.get("type"))
            target_asset_id = target_asset_id or self._trim(target.get("name"))

        return {
            "current_goal": self._trim(normalized_request.get("round_task_desc")) or self._trim(normalized_input.get("text")),
            "focus_object": {
                "type": focus_type or "unknown",
                "id": focus_id,
            },
            "target_asset": {
                "type": target_asset_type,
                "id": target_asset_id,
            },
            "resolved_references": [],
            "unresolved_references": ["context_reference"] if bool(normalized_request.get("needs_reference_resolution")) else [],
            "accepted_constraints": [],
            "pending_questions": [],
        }

    def _resolve_turn_frame_focus(
        self,
        *,
        work_context: Dict[str, Any],
        existing_frame: Dict[str, Any],
    ) -> tuple[str, str]:
        active_focus_type = self._trim(existing_frame.get("active_focus_type"))
        active_focus_id = self._trim(existing_frame.get("active_focus_id"))
        if active_focus_type:
            return active_focus_type, active_focus_id
        active_skill = self._work_context_active_skill(work_context)
        if active_skill:
            return "skill", active_skill
        recent_subject = self._trim(work_context.get("recent_result_subject"))
        if recent_subject:
            return "task", recent_subject
        recent_attachments = self._work_context_recent_attachments(work_context)
        if recent_attachments:
            first = recent_attachments[0]
            attachment_ids = first.get("attachment_ids") if isinstance(first.get("attachment_ids"), list) else []
            if attachment_ids:
                return self._trim(first.get("kind")) or "attachment", self._trim(attachment_ids[0])
            return self._trim(first.get("kind")) or "attachment", "recent_attachment"
        return "unknown", ""

    def _build_continuity_axes(
        self,
        *,
        task_domain: str,
        interaction_frame: Dict[str, Any],
        dispatch_plan: Dict[str, Any],
        execution_plan_preview: Dict[str, Any],
    ) -> Dict[str, str]:
        interaction_mode = self._trim(interaction_frame.get("interaction_mode"))
        selected_path = execution_plan_preview.get("selected_path") if isinstance(execution_plan_preview.get("selected_path"), dict) else {}
        execution_path = self._trim(selected_path.get("type")) or self._trim(dispatch_plan.get("entry")) or "agent_route"
        return {
            "domain": "system" if task_domain in {"system_operation", "design_refinement"} else "business",
            "interaction_mode": interaction_mode,
            "execution_path": execution_path,
        }

    def _build_observation_preview(
        self,
        *,
        conversation_state: Dict[str, Any],
        dispatch_plan: Dict[str, Any],
    ) -> Dict[str, str]:
        state = self._trim(conversation_state.get("state")) or "idle"
        entry = self._trim(dispatch_plan.get("entry")) or "agent_route"
        if state == "awaiting_user_clarification":
            return {
                "state": state,
                "next_action": "wait_user",
                "reason": "clarification_required",
            }
        if state == "suspended":
            return {
                "state": state,
                "next_action": "suspend",
                "reason": "task_suspended",
            }
        if state == "resuming":
            return {
                "state": state,
                "next_action": "continue",
                "reason": "resume_previous_task",
            }
        if entry in {"catalog_browse", "asset_open", "skill_refine", "skill_run", "tool_plan_run", "planned_run", "vision_intake", "agent_route"}:
            return {
                "state": state or "active",
                "next_action": "continue",
                "reason": f"dispatch_to_{entry}",
            }
        return {
            "state": state or "idle",
            "next_action": "idle",
            "reason": "no_dispatch_entry",
        }

    def _build_thread_context_patch_preview(
        self,
        *,
        context_write_policy: Dict[str, Any],
        continuity_axes: Dict[str, str],
    ) -> Dict[str, Any]:
        interaction_frame_update = (
            context_write_policy.get("interaction_frame_update")
            if isinstance(context_write_policy.get("interaction_frame_update"), dict)
            else {}
        )
        task_state_update = (
            context_write_policy.get("task_state_update")
            if isinstance(context_write_policy.get("task_state_update"), dict)
            else {}
        )
        active_focus_update = (
            context_write_policy.get("active_focus_update")
            if isinstance(context_write_policy.get("active_focus_update"), dict)
            else {}
        )
        reference_memory_update = (
            context_write_policy.get("reference_memory_update")
            if isinstance(context_write_policy.get("reference_memory_update"), dict)
            else {}
        )
        return {
            "interaction_frame": interaction_frame_update,
            "conversation_state": {
                "state": self._trim(task_state_update.get("state")),
                "resume_hint": self._trim(task_state_update.get("resume_hint")),
                "suspended_task_stack": task_state_update.get("suspended_task_stack") if isinstance(task_state_update.get("suspended_task_stack"), list) else [],
            },
            "continuity_axes": {
                "domain": self._trim(continuity_axes.get("domain")),
                "interaction_mode": self._trim(continuity_axes.get("interaction_mode")),
                "execution_path": self._trim(continuity_axes.get("execution_path")),
            },
            "active_focus_type": self._trim(active_focus_update.get("type")),
            "active_focus_id": self._trim(active_focus_update.get("id")),
            "reference_scope": reference_memory_update.get("reference_scope") if isinstance(reference_memory_update.get("reference_scope"), list) else [],
            "reference_memory": {
                "recent_result_subject": self._trim(reference_memory_update.get("recent_result_subject")),
                "recent_attachment_ids": reference_memory_update.get("recent_attachment_ids") if isinstance(reference_memory_update.get("recent_attachment_ids"), list) else [],
                "objects": reference_memory_update.get("objects") if isinstance(reference_memory_update.get("objects"), list) else [],
            },
        }

    def _normalize_domain(self, task_domain: str) -> str:
        return "system" if task_domain in {"system_operation", "design_refinement"} else "business"

    def _normalize_execution_path(self, dispatch_plan: Dict[str, Any], execution_plan_preview: Dict[str, Any]) -> str:
        selected_path = execution_plan_preview.get("selected_path") if isinstance(execution_plan_preview.get("selected_path"), dict) else {}
        return self._trim(selected_path.get("type")) or self._trim(dispatch_plan.get("entry")) or "agent_route"

    def _build_interaction_compat(self, *, normalized_request: Dict[str, Any]) -> Dict[str, Any]:
        focus = normalized_request.get("focus") if isinstance(normalized_request.get("focus"), dict) else {}
        target = normalized_request.get("target_asset") if isinstance(normalized_request.get("target_asset"), dict) else {}
        return {
            "needs_reference_resolution": bool(normalized_request.get("needs_reference_resolution")),
            "interaction_mode_hint": "",
            "resume_from_context": False,
            "turn_frame_hints": {
                "current_goal": self._trim(normalized_request.get("round_task_desc")),
                "focus_object": {
                    "type": self._trim(focus.get("type")),
                    "id": self._trim(focus.get("label")),
                },
                "target_asset": {
                    "type": self._trim(target.get("type")),
                    "id": self._trim(target.get("label")),
                },
            },
        }

    def _select_agent_name(
        self,
        *,
        task_domain: str,
        work_context: Dict[str, Any],
        interaction: Dict[str, Any],
    ) -> str:
        if task_domain == "system_operation":
            return "system_agent"
        if task_domain == "design_refinement":
            return "system_agent"
        if task_domain == "business_dialog":
            agent_hint = self._trim(interaction.get("agent_hint"))
            if agent_hint in {"default_assistant", "investment_analyst", "system_agent"}:
                return agent_hint
            return self._trim(work_context.get("execution_agent")) or self._trim(work_context.get("assistant_agent"))
        return self._trim(work_context.get("assistant_agent"))

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> bool:
        return any(keyword in text for keyword in keywords)

    def _looks_like_visual_followup(self, text: str) -> bool:
        normalized = self._trim(text).lower()
        if not normalized:
            return False
        return self._contains_any(
            normalized,
            [
                "图片",
                "截图",
                "刚刚那张",
                "刚才那张",
                "还能看到",
                "k线",
                "k线图",
                "均线",
                "量能",
                "图上",
                "图里",
            ],
        )

    def _looks_like_design_refine_request(self, text: str) -> bool:
        normalized = self._trim(text).lower()
        if not normalized:
            return False
        if not self._contains_any(
            normalized,
            [
                "优化",
                "修改",
                "调整",
                "重写",
                "改一下",
                "改",
                "融入",
                "纳入",
                "加进去",
                "加入",
            ],
        ):
            return False
        return self._contains_any(
            normalized,
            [
                "skill",
                "技能",
                "提示词",
                "当前这个",
                "刚才那个",
                "图表分析能力",
                "这种图表",
                "这种k线",
                "处理方式",
            ],
        )

    def _looks_like_active_skill_run_request(self, text: str) -> bool:
        normalized = self._trim(text).lower()
        if not normalized:
            return False
        if not self._contains_any(
            normalized,
            [
                "运行",
                "执行",
                "跑一下",
                "run",
                "用",
                "基于",
                "当前这个",
                "刚才那个",
            ],
        ):
            return False
        return self._contains_any(
            normalized,
            [
                "skill",
                "技能",
                "当前这个",
                "刚才那个",
                "当前",
                "这个",
            ],
        )

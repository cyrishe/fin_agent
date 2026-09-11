from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.services.assistant_interaction_preprocessor import AssistantInteractionPreprocessor
from src.services.agent_runtime_llm_planner_service import AgentRuntimeLLMPlannerService
from src.services.context_resolution_service import ContextResolutionService
from src.services.conversation_runtime_contract_service import ConversationRuntimeContractService
from src.services.conversation_mainline_contract_service import ConversationMainlineContractService
from src.services.execution_plan_service import AgentRuntimePlanner, ExecutionPlanService
from src.services.system_command_service import SystemCommandService


class ConversationPreprocessError(RuntimeError):
    pass


class ConversationPreprocessService:
    def __init__(
        self,
        *,
        interaction_preprocessor: Optional[AssistantInteractionPreprocessor] = None,
        agent_runtime_planner: Optional[AgentRuntimePlanner] = None,
        agent_runtime_llm_planner_service: Optional[AgentRuntimeLLMPlannerService] = None,
        execution_plan_service: Optional[ExecutionPlanService] = None,
        runtime_contract_service: Optional[ConversationRuntimeContractService] = None,
        mainline_contract_service: Optional[ConversationMainlineContractService] = None,
        system_command_service: Optional[SystemCommandService] = None,
        context_resolution_service: Optional[ContextResolutionService] = None,
        agent_owned_runtime_names: Optional[set[str]] = None,
    ) -> None:
        self.interaction_preprocessor = interaction_preprocessor or AssistantInteractionPreprocessor()
        self.agent_runtime_planner = agent_runtime_planner or execution_plan_service or AgentRuntimePlanner()
        self.runtime_contract_service = runtime_contract_service or ConversationRuntimeContractService()
        self.mainline_contract_service = mainline_contract_service or ConversationMainlineContractService()
        self.context_resolution_service = context_resolution_service or ContextResolutionService()
        self.agent_runtime_llm_planner_service = agent_runtime_llm_planner_service or AgentRuntimeLLMPlannerService(
            fallback_planner=self.agent_runtime_planner,
            prompt_context_compiler=None,
        )
        self.system_command_service = system_command_service or SystemCommandService()
        self.agent_owned_runtime_names = {
            self._trim(item)
            for item in (agent_owned_runtime_names or set())
            if self._trim(item)
        }

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
        is_slash_command = self._trim((normalized_input.get("slash_command") or {}).get("kind")) == "slash"
        thread_state = self._build_thread_state(
            thread_context=thread_context,
            work_context=work_context,
        )
        context_window = self._build_context_window(
            thread_context=thread_context,
        )
        pre_context_signals = self._build_preprocessing_signals(
            normalized_input=normalized_input,
            thread_context=thread_context,
            work_context=work_context,
            interaction={},
        )
        context_resolution = self.context_resolution_service.resolve(
            user_text=normalized_input["text"],
            context_window=context_window,
            thread_state=thread_state,
            preprocessing_signals=pre_context_signals,
            interaction_result={},
            enable_llm=enable_llm and not is_slash_command,
        )
        interaction = (
            self._classify_slash_command(
                normalized_command=normalized_command,
                application_context=application_context,
            )
            if is_slash_command
            else
            self.interaction_preprocessor.classify(
                user_text=normalized_input["text"],
                thread_context=thread_context,
                application_context=application_context,
                context_resolution=context_resolution,
            )
            if enable_llm
            else {
                "agent_name": "",
                "turn_mode": "",
                "analize": "",
                "domain_hint": "",
                "agent_hint": "",
                "needs_reference_resolution": False,
                "info_ready": False,
                "source": "disabled",
            }
        )
        intent = {
            "intent_type": "explicit_command" if is_slash_command else "llm_top_intent",
            "source": "shortcut:command" if is_slash_command else "interaction_preprocess",
        }
        preprocessing_signals = self._build_preprocessing_signals(
            normalized_input=normalized_input,
            thread_context=thread_context,
            work_context=work_context,
            interaction=interaction,
        )
        normalized_request = self._build_normalized_request(
            normalized_input=normalized_input,
            interaction=interaction,
            preprocessing_signals=preprocessing_signals,
            context_resolution=context_resolution,
        )
        turn_mode = self._resolve_turn_mode(
            interaction=interaction,
            normalized_command=normalized_command,
        )
        if turn_mode == "tool_development":
            task_domain_decision = self._decision(
                "business_dialog",
                "top_intent",
                "turn_mode_tool_development",
            )
            task_domain = "business_dialog"
            capability_family_decision = self._decision(
                "custom_tool_authoring",
                "top_intent",
                "turn_mode_tool_development",
            )
            capability_family = "custom_tool_authoring"
            selected_agent = self._select_agent_name(
                task_domain=task_domain,
                work_context=work_context,
                interaction=interaction,
                application_context=application_context,
            )
            execution_plan_preview = {}
        else:
            task_domain_decision = (
                self._decision("system_operation", "top_intent", "turn_mode_system_operation")
                if turn_mode == "system_operation"
                else self._decide_task_domain(
                    normalized_input=normalized_input,
                    normalized_request=normalized_request,
                )
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
                application_context=application_context,
            )
            execution_plan_preview = self._build_execution_plan_preview(
                normalized_request=normalized_request,
                work_context=work_context,
                application_context=application_context,
                task_domain=task_domain,
                capability_family=capability_family,
                selected_agent=selected_agent,
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
        dispatch_plan["turn_mode"] = turn_mode
        # Conversation routing is already decided by the SOFT context and intent
        # stages above. Do not derive another state machine from the result or
        # persist synthetic focus/resume state that could constrain a later turn.
        thread_context_patch_preview: Dict[str, Any] = {}
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
            thread_context_patch_preview=thread_context_patch_preview,
        )
        conversation_mainline = self.mainline_contract_service.build_contract(
            normalized_input=normalized_input,
            dispatch_plan=dispatch_plan,
            execution_plan=execution_plan_preview,
            work_context=work_context,
        )
        domain = self._normalize_domain(task_domain)
        interaction_mode = turn_mode
        execution_path = self._normalize_execution_path(dispatch_plan, execution_plan_preview)
        llm_usage = self._merge_llm_usage(
            interaction.get("llm_usage") if isinstance(interaction, dict) else None,
            context_resolution.get("llm_usage") if isinstance(context_resolution, dict) else None,
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
            "thread_context_patch_preview": thread_context_patch_preview,
            "runtime_contract": runtime_contract,
            "conversation_mainline": conversation_mainline,
            "runtime_modules": runtime_contract.get("modules") if isinstance(runtime_contract.get("modules"), list) else [],
            "runtime_node_results": runtime_contract.get("node_results") if isinstance(runtime_contract.get("node_results"), list) else [],
            "runtime_feedback_protocol": runtime_contract.get("feedback_protocol") if isinstance(runtime_contract.get("feedback_protocol"), dict) else {},
            "interaction": interaction,
            "intent": intent,
            "capability_family": capability_family,
            "decision_sources": {
                "interaction": self._trim(interaction.get("source")) or "unknown",
                "intent": "rule",
                "domain": {
                    "value": domain,
                    "source": "top_intent",
                    "reason": self._trim(task_domain_decision.get("reason")) or "derived_from_task_domain",
                },
                "interaction_mode": {
                    "value": interaction_mode,
                    "source": "top_intent",
                    "reason": "same_as_turn_mode",
                },
                "execution_path": {
                    "value": execution_path,
                    "source": "dispatch_plan",
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
        parsed_command = self._parse_slash_command(raw_text if code_runtime_hint else normalized_text)
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
        default_agent = app_ctx.get("default_agent") if isinstance(app_ctx.get("default_agent"), dict) else {}
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
            "default_agent": self._trim(default_agent.get("agent_name")),
            "thread_active_skill_name": self._trim(ctx.get("active_skill_name")),
            "thread_active_skill_canonical_name": self._trim(ctx.get("active_skill_canonical_name")),
            "recent_attachments": recent_attachments,
            "recent_result_subject": self._trim(ctx.get("recent_result_subject")),
        }
        if custom_tool_state:
            tool_name = self._trim(custom_tool_state.get("tool_name"))
            display_name = self._trim(
                ((custom_tool_state.get("design_contract") or {}) if isinstance(custom_tool_state.get("design_contract"), dict) else {}).get("display_name")
            )
            context_name = tool_name or display_name or "current"
            work_context["active_workflow"] = {
                "type": "custom_tool_authoring",
                "tool_name": tool_name,
                "context_ref": f"custom_tool:{context_name}",
                "summary": f"最近的工具开发任务：{display_name or tool_name or '当前工具'}",
                "owner_agent": self._trim(custom_tool_state.get("owner_agent") or default_agent.get("agent_name")),
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
            "needs_reference_resolution": bool(interaction.get("needs_reference_resolution")),
            "code_runtime_hint": bool(normalized_input.get("code_runtime_hint")),
            "info_ready": bool(interaction.get("info_ready")),
            "time_refs": [],
            "resolved_references": [],
            "correction_signals": [],
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
        active_workflow = work.get("active_workflow") if isinstance(work.get("active_workflow"), dict) else {}
        context_objects: List[Dict[str, str]] = []
        if active_workflow:
            context_ref = self._trim(active_workflow.get("context_ref"))
            if context_ref:
                context_objects.append(
                    {
                        "context_ref": context_ref,
                        "summary": self._trim(active_workflow.get("summary")),
                    }
                )
        return {
            "session_application_name": self._trim(work.get("application_name")),
            "session_default_agent": self._trim(work.get("default_agent")),
            "thread_active_skill_name": self._trim(work.get("thread_active_skill_name")),
            "thread_active_skill_canonical_name": self._trim(work.get("thread_active_skill_canonical_name")),
            "recent_attachments": self._work_context_recent_attachments(work),
            "recent_result_subject": self._trim(work.get("recent_result_subject")),
            "reference_memory": reference_memory,
            "thread_summary": self._trim(ctx.get("thread_summary")),
            "active_workflow": active_workflow,
            "context_objects": context_objects,
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
    ) -> Dict[str, Any]:
        text = self._trim(normalized_input.get("text"))
        context_refs = context_resolution.get("context_refs") if isinstance(context_resolution.get("context_refs"), list) else []
        context_relation = "referential" if context_refs else "new"
        focus = self._build_focus_from_structured_context(
            preprocessing_signals=preprocessing_signals,
            context_resolution=context_resolution,
        )
        target_asset = {"type": "", "label": ""}
        domain = "system" if self._trim(interaction.get("turn_mode")) == "system_operation" or self._trim(interaction.get("domain_hint")) == "system" else "business"
        ori_question = self._trim(context_resolution.get("ori_question")) or text
        resolved_question = self._trim(context_resolution.get("resolved_question"))
        if not resolved_question:
            raise ConversationPreprocessError("上下文语义协议错误：resolved_question 不能为空")
        return {
            "ori_question": ori_question,
            "resolved_question": resolved_question,
            "context_refs": list(context_refs),
            # Compatibility fields while the execution runtime migrates to the v1 turn protocol.
            "raw_user_text": ori_question,
            "analize": "",
            "round_task_desc": resolved_question,
            "task_splitd": [],
            "domain": domain,
            "context_relation": context_relation,
            "focus": focus,
            "target_asset": target_asset,
            "needs_reference_resolution": bool(context_refs),
            "info_ready": True,
            "source": self._trim(context_resolution.get("source")) or "context_resolution",
            "llm_usage": context_resolution.get("llm_usage") if isinstance(context_resolution.get("llm_usage"), dict) else {},
        }

    def _resolve_turn_mode(
        self,
        *,
        interaction: Dict[str, Any],
        normalized_command: Dict[str, Any],
    ) -> str:
        action = self._trim(normalized_command.get("action"))
        args = normalized_command.get("args") if isinstance(normalized_command.get("args"), list) else []
        sub_action = self._trim(args[0]).lower() if args else ""
        if action:
            if action == "custom_tool" and sub_action in {"create", "edit"}:
                return "tool_development"
            return "system_operation"
        turn_mode = self._trim(interaction.get("turn_mode"))
        if turn_mode in {"normal_qa", "system_operation", "tool_development"}:
            return turn_mode
        raise ValueError("顶层意图协议错误：自然语言请求缺少有效 turn_mode")

    def _classify_slash_command(
        self,
        *,
        normalized_command: Dict[str, Any],
        application_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        action = self._trim(normalized_command.get("action"))
        args = normalized_command.get("args") if isinstance(normalized_command.get("args"), list) else []
        sub_action = self._trim(args[0]).lower() if args else ""
        turn_mode = (
            "tool_development"
            if action == "custom_tool" and sub_action in {"create", "edit"}
            else "system_operation"
        )
        app = application_context if isinstance(application_context, dict) else {}
        rows = app.get("available_agents") if isinstance(app.get("available_agents"), list) else []
        available_names = [
            self._trim(item.get("agent_name") or item.get("name"))
            for item in rows
            if isinstance(item, dict) and self._trim(item.get("agent_name") or item.get("name"))
        ]
        if action == "custom_tool":
            default_agent = app.get("default_agent") if isinstance(app.get("default_agent"), dict) else {}
            preferred = self._trim(default_agent.get("agent_name"))
        else:
            general_agent = next(
                (
                    item for item in rows
                    if isinstance(item, dict) and self._trim(item.get("role")) == "general_assistant"
                ),
                {},
            )
            preferred = self._trim(general_agent.get("agent_name"))
        agent_name = preferred if preferred in available_names else (available_names[0] if available_names else preferred)
        return {
            "agent_name": agent_name,
            "turn_mode": turn_mode,
            "agent_hint": agent_name,
            "domain_hint": "system" if turn_mode == "system_operation" else "business",
            "needs_reference_resolution": False,
            "info_ready": True,
            "source": "rule:slash_command",
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

    def _build_focus_from_structured_context(
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
        return self._decision("business_dialog", "default", "business_dialog_default")

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
        return self._decision("business_analysis", "default.business", "business_analysis_default")

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
        enable_llm: bool,
    ) -> Dict[str, Any]:
        if task_domain not in {"business_dialog"}:
            return {}
        if capability_family not in {"business_analysis", "visual_analysis", "visual_followup", "agent_route"}:
            return {}
        if self._trim(selected_agent) in self.agent_owned_runtime_names:
            return self.agent_runtime_planner.build_agent_route_plan(
                target_agent=self._trim(selected_agent),
                reason="selected_agent_owns_normal_qa_runtime",
            )
        if self._trim(selected_agent) and self._trim(selected_agent) != self._trim(work_context.get("default_agent")):
            return self.agent_runtime_planner.build_agent_route_plan(
                target_agent=self._trim(selected_agent),
                reason="selected_non_default_agent_for_business_request",
            )
        if enable_llm:
            planner_result = self.agent_runtime_llm_planner_service.build_plan(
                user_objective=self._trim(normalized_request.get("round_task_desc") or normalized_request.get("raw_user_text")),
                recall_query=self._trim(normalized_request.get("raw_user_text")),
                tool_queries=[self._trim(item) for item in (normalized_request.get("task_splitd") or []) if self._trim(item)],
                work_context=work_context,
                application_context=application_context if isinstance(application_context, dict) else {},
                enable_llm=True,
                allow_fallback=False,
            )
            if isinstance(planner_result.get("execution_plan"), dict) and planner_result.get("execution_plan"):
                return self._attach_planner_preview_metadata(
                    execution_plan=planner_result.get("execution_plan"),
                    planner_result=planner_result,
                )
            error = self._trim(planner_result.get("error"))
            raise ConversationPreprocessError(
                f"Agent 执行规划失败：{error or '模型没有返回可执行计划'}"
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

    def _normalize_domain(self, task_domain: str) -> str:
        return "system" if task_domain in {"system_operation", "design_refinement"} else "business"

    def _normalize_execution_path(self, dispatch_plan: Dict[str, Any], execution_plan_preview: Dict[str, Any]) -> str:
        selected_path = execution_plan_preview.get("selected_path") if isinstance(execution_plan_preview.get("selected_path"), dict) else {}
        return self._trim(selected_path.get("type")) or self._trim(dispatch_plan.get("entry")) or "agent_route"

    def _select_agent_name(
        self,
        *,
        task_domain: str,
        work_context: Dict[str, Any],
        interaction: Dict[str, Any],
        application_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        app = application_context if isinstance(application_context, dict) else {}
        rows = app.get("available_agents") if isinstance(app.get("available_agents"), list) else []
        allowed_ordered = [
            self._trim(item.get("agent_name") or item.get("name"))
            for item in rows
            if isinstance(item, dict) and self._trim(item.get("agent_name") or item.get("name"))
        ]
        allowed_names = set(allowed_ordered)
        agent_name = self._trim(interaction.get("agent_name") or interaction.get("agent_hint"))
        if agent_name and (not allowed_names or agent_name in allowed_names):
            return agent_name
        configured_agent = self._trim(work_context.get("default_agent"))
        if configured_agent and (not allowed_names or configured_agent in allowed_names):
            return configured_agent
        return allowed_ordered[0] if allowed_ordered else self._trim(work_context.get("default_agent"))

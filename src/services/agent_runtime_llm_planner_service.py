from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.services.capability_search_service import CapabilitySearchService
from src.services.planner_question_contract_service import PlannerQuestionContractService
from src.services.quant_research_authoring_affordance_service import QuantResearchAuthoringAffordanceService
from src.services.security_subject_classifier_service import SecuritySubjectClassifierService
from src.services.tool_candidate_rerank_service import ToolCandidateRerankService
from src.services.prompt_context_compiler_service import PromptContextCompilerService
from src.services.execution_plan_service import AgentRuntimePlanner
from src.services.tool_argument_compiler_service import ToolArgumentCompilerService
from src.utils.ai_service import chat_qwen, extract_first_json
from src.services.deep_thinking_planner_service import DeepThinkingPlannerService
from src.services.custom_tool_service import CustomToolStoreService
from src.tools.registry import canonicalize_tool_name, is_tool_definition_disabled


class AgentRuntimeLLMPlannerService:
    def __init__(
        self,
        *,
        capability_search_service: Optional[CapabilitySearchService] = None,
        fallback_planner: Optional[AgentRuntimePlanner] = None,
        prompt_context_compiler: Optional[PromptContextCompilerService] = None,
        thinking_mode_selector: Optional[object] = None,
        deep_thinking_planner: Optional[object] = None,
        task_mode_router: Optional[object] = None,
        tool_candidate_rerank_service: Optional[ToolCandidateRerankService] = None,
        tool_argument_compiler_service: Optional[ToolArgumentCompilerService] = None,
        planner_question_contract_service: Optional[PlannerQuestionContractService] = None,
        quant_research_authoring_affordance_service: Optional[QuantResearchAuthoringAffordanceService] = None,
        security_subject_classifier_service: Optional[SecuritySubjectClassifierService] = None,
        custom_tool_store_service: Optional[CustomToolStoreService] = None,
    ) -> None:
        self.registry = get_prompt_registry()
        self.capability_search_service = capability_search_service or CapabilitySearchService()
        self.fallback_planner = fallback_planner or AgentRuntimePlanner(
            capability_search_service=self.capability_search_service
        )
        self.prompt_context_compiler = prompt_context_compiler or PromptContextCompilerService()
        self.thinking_mode_selector = thinking_mode_selector
        self.deep_thinking_planner = deep_thinking_planner or DeepThinkingPlannerService()
        self.task_mode_router = task_mode_router
        self.tool_candidate_rerank_service = tool_candidate_rerank_service or ToolCandidateRerankService()
        self.tool_argument_compiler_service = tool_argument_compiler_service or ToolArgumentCompilerService()
        self.planner_question_contract_service = planner_question_contract_service or PlannerQuestionContractService()
        self.quant_research_authoring_affordance_service = (
            quant_research_authoring_affordance_service or QuantResearchAuthoringAffordanceService()
        )
        self.security_subject_classifier_service = security_subject_classifier_service or SecuritySubjectClassifierService()
        self.custom_tool_store_service = custom_tool_store_service or CustomToolStoreService()
        self.tool_definitions_dir = Path("src/tools/definitions")
        self.tool_schemas_dir = Path("src/tools/schemas")

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def _classify_security_subject(
        self,
        *,
        objective: str,
        application_context: Dict[str, Any],
        enable_llm: bool,
    ) -> Dict[str, Any]:
        try:
            has_subject_tags = self.capability_search_service.has_tool_subject_tags(
                application_context=application_context
            )
        except Exception:
            has_subject_tags = False
        if not has_subject_tags:
            return {
                "subject": "general",
                "subjects": ["general"],
                "reason": "tool_subject_tags_not_configured",
                "source": "skipped",
                "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        return self.security_subject_classifier_service.classify(
            task_desc=objective,
            enable_llm=enable_llm,
        )

    def _call_fallback_planner(
        self,
        *,
        text: str,
        recall_query: str,
        tool_queries: List[str],
        work_context: Dict[str, Any],
        application_context: Dict[str, Any],
        enable_llm: bool,
    ) -> Dict[str, Any]:
        try:
            return self.fallback_planner.build_business_dialog_plan(
                text=text,
                recall_query=recall_query,
                tool_queries=tool_queries,
                work_context=work_context,
                application_context=application_context,
                enable_llm=enable_llm,
            )
        except TypeError:
            try:
                return self.fallback_planner.build_business_dialog_plan(
                    text=text,
                    recall_query=recall_query,
                    tool_queries=tool_queries,
                    work_context=work_context,
                    application_context=application_context,
                )
            except TypeError:
                return self.fallback_planner.build_business_dialog_plan(
                    text=text,
                    tool_queries=tool_queries,
                    work_context=work_context,
                    application_context=application_context,
                )

    def build_plan(
        self,
        *,
        user_objective: str,
        recall_query: str = "",
        tool_queries: Optional[List[str]] = None,
        work_context: Optional[Dict[str, Any]] = None,
        application_context: Optional[Dict[str, Any]] = None,
        enable_llm: bool = True,
        allow_fallback: bool = True,
    ) -> Dict[str, Any]:
        objective = self._trim(user_objective)
        retrieval_query = self._trim(recall_query) or objective
        ctx = work_context if isinstance(work_context, dict) else {}
        app_ctx = application_context if isinstance(application_context, dict) else {}
        agent_context = self._build_agent_context(app_ctx, ctx)
        subject_result = self._classify_security_subject(
            objective=objective,
            application_context=app_ctx,
            enable_llm=enable_llm,
        )

        capability_result = self.capability_search_service.find_for_agent_runtime(
            query=retrieval_query,
            tool_queries=[self._trim(item) for item in (tool_queries or []) if self._trim(item)],
            work_context=ctx,
            application_context=app_ctx,
            tool_subject_tags=subject_result.get("subjects") if isinstance(subject_result.get("subjects"), list) else [],
            tool_top_k=8,
        )
        capability_result = self._filter_disabled_capability_tools(capability_result)
        planner_skills = capability_result.get("planner_skills") if isinstance(capability_result.get("planner_skills"), list) else []
        planner_tools = capability_result.get("planner_tools") if isinstance(capability_result.get("planner_tools"), list) else []
        raw_planner_skills = planner_skills
        raw_planner_tools = planner_tools
        planner_question_contract = self.planner_question_contract_service.build_contract(
            objective=objective,
            work_context=ctx,
            capability_result=capability_result,
        )
        analysis_affordances = self._build_analysis_affordances(
            capability_result,
            objective=objective,
            planner_question_contract=planner_question_contract,
        )
        structured_task: Dict[str, Any] = {}
        task_mode_result: Dict[str, Any] = {}
        thinking_mode_result = self._select_thinking_mode(
            objective=objective,
            work_context=ctx,
            application_context=app_ctx,
            planner_skills=raw_planner_skills,
            planner_tools=raw_planner_tools,
            enable_llm=enable_llm,
        )
        tool_rerank_enable_llm = bool(
            enable_llm or thinking_mode_result.get("thinking_mode") == "fast_thinking"
        )
        capability_result = self._apply_tool_rerank(
            capability_result=capability_result,
            objective=objective,
            enable_llm=tool_rerank_enable_llm,
        )
        capability_result = self._filter_disabled_capability_tools(capability_result)
        planner_skills = capability_result.get("planner_skills") if isinstance(capability_result.get("planner_skills"), list) else []
        planner_tools = capability_result.get("planner_tools") if isinstance(capability_result.get("planner_tools"), list) else []
        deep_plan_preview = self._build_deep_plan_preview(
            objective=objective,
            capability_result=capability_result,
            work_context=ctx,
            thinking_mode_result=thinking_mode_result,
            enable_llm_assessment=enable_llm and thinking_mode_result.get("thinking_mode") == "deep_thinking",
        )
        prompt_context_sections = self.prompt_context_compiler.compile_sections(
            profile="planner",
            agent_context=agent_context,
            work_context=ctx,
            candidate_skills=planner_skills,
            candidate_tools=planner_tools,
        )
        if not objective:
            fallback_plan = self._call_fallback_planner(
                text=objective,
                recall_query=retrieval_query,
                tool_queries=[self._trim(item) for item in (tool_queries or []) if self._trim(item)],
                work_context=ctx,
                application_context=app_ctx,
                enable_llm=enable_llm,
            )
            clarification_questions = ["你希望我处理什么任务？请说明目标、对象或结果形式。"]
            return {
                "ok": True,
                "source": "rule_clarification",
                "planner_prompt_key": "system.agent_runtime.planner",
                "capability_result": capability_result,
                "structured_task": structured_task,
                "task_mode_result": task_mode_result,
                "subject_result": subject_result,
                "thinking_mode_result": thinking_mode_result,
                "deep_plan_preview": deep_plan_preview,
                "prompt_context_sections": prompt_context_sections,
                "execution_plan": self._build_clarification_plan(
                    fallback_plan=fallback_plan,
                    agent_context=agent_context,
                    objective=objective,
                    clarification_questions=clarification_questions,
                    analysis_affordances=analysis_affordances,
                    planner_question_contract=planner_question_contract,
                ),
                "planner_question_contract": planner_question_contract,
                "clarification_needed": True,
                "clarification_questions": clarification_questions,
                "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        if thinking_mode_result.get("thinking_mode") == "fast_thinking":
            fallback_plan = self._call_fallback_planner(
                text=objective,
                recall_query=retrieval_query,
                tool_queries=[self._trim(item) for item in (tool_queries or []) if self._trim(item)],
                work_context=ctx,
                application_context=app_ctx,
                enable_llm=True,
            )
            normalized_fast_plan = self._normalize_execution_plan(
                payload={},
                fallback_plan=fallback_plan,
                agent_context=agent_context,
                analysis_affordances=analysis_affordances,
                planner_question_contract=planner_question_contract,
            )
            normalized_fast_plan["thinking_mode"] = "fast_thinking"
            return {
                "ok": True,
                "source": "fast_thinking_planner",
                "planner_prompt_key": "",
                "capability_result": capability_result,
                "structured_task": structured_task,
                "task_mode_result": task_mode_result,
                "subject_result": subject_result,
                "thinking_mode_result": thinking_mode_result,
                "deep_plan_preview": {},
                "prompt_context_sections": prompt_context_sections,
                "execution_plan": normalized_fast_plan,
                "planner_question_contract": planner_question_contract,
                "clarification_needed": bool(normalized_fast_plan.get("clarification_needed")),
                "clarification_questions": normalized_fast_plan.get("clarification_questions") if isinstance(normalized_fast_plan.get("clarification_questions"), list) else [],
                "llm_usage": self._merge_usage(
                    capability_result.get("tool_rerank", {}).get("llm_usage") if isinstance(capability_result.get("tool_rerank"), dict) else None,
                    thinking_mode_result.get("llm_usage") if isinstance(thinking_mode_result.get("llm_usage"), dict) else None,
                ),
            }

        if not enable_llm:
            fallback_plan = self._call_fallback_planner(
                text=objective,
                recall_query=retrieval_query,
                tool_queries=[self._trim(item) for item in (tool_queries or []) if self._trim(item)],
                work_context=ctx,
                application_context=app_ctx,
                enable_llm=enable_llm,
            )
            normalized_fallback_plan = self._normalize_execution_plan(
                payload={},
                fallback_plan=fallback_plan,
                agent_context=agent_context,
                analysis_affordances=analysis_affordances,
                planner_question_contract=planner_question_contract,
            )
            return {
                "ok": True,
                "source": "fallback_rule_planner",
                "planner_prompt_key": "system.agent_runtime.planner",
                "capability_result": capability_result,
                "structured_task": structured_task,
                "task_mode_result": task_mode_result,
                "subject_result": subject_result,
                "thinking_mode_result": thinking_mode_result,
                "deep_plan_preview": deep_plan_preview,
                "prompt_context_sections": prompt_context_sections,
                "execution_plan": normalized_fallback_plan,
                "planner_question_contract": planner_question_contract,
                "clarification_needed": bool(normalized_fallback_plan.get("clarification_needed")),
                "clarification_questions": normalized_fallback_plan.get("clarification_questions") if isinstance(normalized_fallback_plan.get("clarification_questions"), list) else [],
                "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        try:
            planner_round_1 = self._run_high_level_planner(
                objective=objective,
                agent_context=agent_context,
                prompt_context_sections=prompt_context_sections,
                work_context=ctx,
                planner_skills=planner_skills,
                planner_tools=planner_tools,
            )
            high_level_payload = planner_round_1.get("payload")
            high_level_plan_text = self._trim(planner_round_1.get("text"))
            compiler_input_plan_text = self._extract_stage2_plan_text(high_level_plan_text)
            if isinstance(high_level_payload, dict) or high_level_plan_text:
                try:
                    compiler_round = self._run_plan_compiler(
                        high_level_plan=high_level_payload,
                        high_level_plan_text=compiler_input_plan_text,
                        planner_tools=planner_tools,
                    )
                except Exception:
                    compiler_round = {"payload": None, "usage": None}
                compiled_payload = compiler_round.get("payload")
                normalized_source_payload = compiled_payload if isinstance(compiled_payload, dict) else high_level_payload
                fallback_plan: Dict[str, Any] = {}
                if self._needs_fallback_binding_backfill(normalized_source_payload):
                    fallback_plan = self._call_fallback_planner(
                        text=objective,
                        recall_query=retrieval_query,
                        tool_queries=[self._trim(item) for item in (tool_queries or []) if self._trim(item)],
                        work_context=ctx,
                        application_context=app_ctx,
                        enable_llm=enable_llm,
                    )
                normalized = self._normalize_execution_plan(
                    payload=self._backfill_tool_step_arguments(
                        payload=normalized_source_payload,
                        objective=objective,
                        planner_tools=planner_tools,
                        enable_llm=enable_llm,
                    ),
                    fallback_plan=fallback_plan,
                    agent_context=agent_context,
                    analysis_affordances=analysis_affordances,
                    planner_question_contract=planner_question_contract,
                )
                return {
                    "ok": True,
                    "source": "deep_thinking_planner",
                    "planner_prompt_key": "system.agent_runtime.planner",
                    "planner_compile_prompt_key": "system.agent_runtime.plan_compiler",
                    "capability_result": capability_result,
                    "structured_task": structured_task,
                    "task_mode_result": task_mode_result,
                    "subject_result": subject_result,
                    "thinking_mode_result": thinking_mode_result,
                    "deep_plan_preview": deep_plan_preview,
                    "prompt_context_sections": prompt_context_sections,
                    "execution_plan": normalized,
                    "planner_question_contract": planner_question_contract,
                    "raw_plan": normalized_source_payload,
                    "raw_high_level_plan": high_level_payload,
                    "raw_high_level_plan_text": high_level_plan_text,
                    "compiler_input_plan_text": compiler_input_plan_text,
                    "raw_compiled_plan": compiled_payload if isinstance(compiled_payload, dict) else {},
                    "compiler_validation_errors": compiler_round.get("validation_errors") if isinstance(compiler_round.get("validation_errors"), list) else [],
                    "planner_explanation": planner_round_1.get("explanation") or "",
                    "clarification_needed": bool(normalized.get("clarification_needed")),
                    "clarification_questions": normalized.get("clarification_questions") if isinstance(normalized.get("clarification_questions"), list) else [],
                "llm_usage": self._merge_usage(
                    capability_result.get("tool_rerank", {}).get("llm_usage") if isinstance(capability_result.get("tool_rerank"), dict) else None,
                    thinking_mode_result.get("llm_usage") if isinstance(thinking_mode_result.get("llm_usage"), dict) else None,
                    planner_round_1.get("usage"),
                    compiler_round.get("usage"),
                ),
            }
        except Exception as exc:
            if not allow_fallback:
                return {
                    "ok": False,
                    "source": "planner_error_no_fallback",
                    "planner_prompt_key": "system.agent_runtime.planner",
                    "capability_result": capability_result,
                    "structured_task": structured_task,
                    "task_mode_result": task_mode_result,
                    "subject_result": subject_result,
                    "thinking_mode_result": thinking_mode_result,
                    "deep_plan_preview": deep_plan_preview,
                    "prompt_context_sections": prompt_context_sections,
                    "execution_plan": {},
                    "planner_question_contract": planner_question_contract,
                    "error": str(exc),
                    "clarification_needed": False,
                    "clarification_questions": [],
                    "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            fallback_plan = self._call_fallback_planner(
                text=objective,
                recall_query=retrieval_query,
                tool_queries=[self._trim(item) for item in (tool_queries or []) if self._trim(item)],
                work_context=ctx,
                application_context=app_ctx,
                enable_llm=enable_llm,
            )
            normalized_fallback_plan = self._normalize_execution_plan(
                payload={},
                fallback_plan=fallback_plan,
                agent_context=agent_context,
                analysis_affordances=analysis_affordances,
                planner_question_contract=planner_question_contract,
            )
            return {
                "ok": False,
                "source": "planner_error_fallback",
                "planner_prompt_key": "system.agent_runtime.planner",
                "capability_result": capability_result,
                "structured_task": structured_task,
                "task_mode_result": task_mode_result,
                "subject_result": subject_result,
                "thinking_mode_result": thinking_mode_result,
                "deep_plan_preview": deep_plan_preview,
                "prompt_context_sections": prompt_context_sections,
                "execution_plan": normalized_fallback_plan,
                "planner_question_contract": planner_question_contract,
                "error": str(exc),
                "clarification_needed": bool(normalized_fallback_plan.get("clarification_needed")),
                "clarification_questions": normalized_fallback_plan.get("clarification_questions") if isinstance(normalized_fallback_plan.get("clarification_questions"), list) else [],
                "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        if not allow_fallback:
            return {
                "ok": False,
                "source": "planner_empty_no_fallback",
                "planner_prompt_key": "system.agent_runtime.planner",
                "capability_result": capability_result,
                "structured_task": structured_task,
                "task_mode_result": task_mode_result,
                "subject_result": subject_result,
                "thinking_mode_result": thinking_mode_result,
                "deep_plan_preview": deep_plan_preview,
                "prompt_context_sections": prompt_context_sections,
                "execution_plan": {},
                "planner_question_contract": planner_question_contract,
                "clarification_needed": False,
                "clarification_questions": [],
                "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        fallback_plan = self._call_fallback_planner(
            text=objective,
            recall_query=retrieval_query,
            tool_queries=[self._trim(item) for item in (tool_queries or []) if self._trim(item)],
            work_context=ctx,
            application_context=app_ctx,
            enable_llm=enable_llm,
        )
        normalized_fallback_plan = self._normalize_execution_plan(
            payload={},
            fallback_plan=fallback_plan,
            agent_context=agent_context,
            analysis_affordances=analysis_affordances,
            planner_question_contract=planner_question_contract,
        )
        return {
            "ok": True,
            "source": "planner_empty_fallback",
            "planner_prompt_key": "system.agent_runtime.planner",
            "capability_result": capability_result,
            "structured_task": structured_task,
            "task_mode_result": task_mode_result,
            "subject_result": subject_result,
            "thinking_mode_result": thinking_mode_result,
            "deep_plan_preview": deep_plan_preview,
            "prompt_context_sections": prompt_context_sections,
            "execution_plan": normalized_fallback_plan,
            "planner_question_contract": planner_question_contract,
            "clarification_needed": bool(normalized_fallback_plan.get("clarification_needed")),
            "clarification_questions": normalized_fallback_plan.get("clarification_questions")
            if isinstance(normalized_fallback_plan.get("clarification_questions"), list)
            else [],
            "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def _apply_tool_rerank(
        self,
        *,
        capability_result: Dict[str, Any],
        objective: str,
        enable_llm: bool,
    ) -> Dict[str, Any]:
        planner_tools = capability_result.get("planner_tools") if isinstance(capability_result.get("planner_tools"), list) else []
        if not planner_tools:
            return capability_result
        rerank = self.tool_candidate_rerank_service.rerank(
            task_desc=objective,
            candidate_tools=planner_tools[:8],
            enable_llm=enable_llm,
        )
        selected_names = [self._trim(name) for name in (rerank.get("selected_tools") or []) if self._trim(name)]
        if selected_names:
            selected_set = set(selected_names)
            filtered_planner_tools = [item for item in planner_tools if self._trim(item.get("tool_name")) in selected_set]
            filtered_tools = [
                item for item in (capability_result.get("tools") or [])
                if isinstance(item, dict) and self._trim(item.get("tool_name")) in selected_set
            ]
        else:
            filtered_planner_tools = planner_tools
            filtered_tools = capability_result.get("tools") if isinstance(capability_result.get("tools"), list) else []
        return {
            **capability_result,
            "tools": filtered_tools,
            "planner_tools": filtered_planner_tools,
            "tool_rerank": rerank,
        }

    def _is_disabled_tool_name(self, tool_name: str) -> bool:
        normalized = canonicalize_tool_name(self._trim(tool_name))
        return bool(normalized and is_tool_definition_disabled(normalized))

    def _filter_disabled_capability_tools(self, capability_result: Dict[str, Any]) -> Dict[str, Any]:
        filtered = dict(capability_result or {})
        for key in ("tools", "planner_tools"):
            rows = filtered.get(key) if isinstance(filtered.get(key), list) else []
            filtered[key] = [
                item
                for item in rows
                if isinstance(item, dict) and not self._is_disabled_tool_name(self._trim(item.get("tool_name")))
            ]
        rerank = filtered.get("tool_rerank") if isinstance(filtered.get("tool_rerank"), dict) else {}
        if rerank:
            filtered["tool_rerank"] = {
                **rerank,
                "selected_tools": [
                    self._trim(name)
                    for name in (rerank.get("selected_tools") or [])
                    if self._trim(name) and not self._is_disabled_tool_name(self._trim(name))
                ],
            }
        return filtered

    def _run_high_level_planner(
        self,
        *,
        objective: str,
        agent_context: Dict[str, Any],
        prompt_context_sections: List[Dict[str, Any]],
        work_context: Dict[str, Any],
        planner_skills: List[Dict[str, Any]],
        planner_tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        messages = self.registry.render_messages(
            "system.agent_runtime.planner",
            {
                "user_objective": objective,
                "candidate_tools_summary": self._format_planner_tool_summaries(planner_tools),
                "candidate_skills_summary": self._format_planner_skill_summaries(planner_skills),
                "code_runtime_hint": bool(work_context.get("code_runtime_hint")),
                "runtime_preference": self._trim(work_context.get("preferred_runtime")),
            },
        )
        planner_text, usage = chat_qwen(messages, enable_think=False)
        return {
            "text": planner_text,
            "payload": extract_first_json(planner_text, log_errors=False),
            "explanation": self._extract_planner_explanation(planner_text),
            "usage": usage,
        }

    def _run_plan_compiler(
        self,
        *,
        high_level_plan: Optional[Dict[str, Any]],
        high_level_plan_text: str,
        planner_tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        candidate_tool_contracts = self._collect_plan_tool_contracts(
            high_level_plan=high_level_plan if isinstance(high_level_plan, dict) else {},
            planner_tools=planner_tools,
        )
        messages = self.registry.render_messages(
            "system.agent_runtime.plan_compiler",
            {
                "high_level_plan_text": high_level_plan_text,
                "candidate_tool_contract_sections": self._format_compiler_tool_contract_sections(
                    high_level_plan=high_level_plan if isinstance(high_level_plan, dict) else {},
                    high_level_plan_text=high_level_plan_text,
                    candidate_tool_contracts=candidate_tool_contracts,
                ),
            },
        )
        compiler_text, usage = chat_qwen(messages, enable_think=False)
        payload = extract_first_json(compiler_text, log_errors=False)
        payload, repair_usage, validation_errors = self._repair_plan_contracts(
            payload=payload if isinstance(payload, dict) else {},
            high_level_plan_text=high_level_plan_text,
            high_level_plan=high_level_plan if isinstance(high_level_plan, dict) else {},
            candidate_tool_contracts=candidate_tool_contracts,
        )
        return {
            "payload": payload,
            "usage": self._merge_usage(usage, repair_usage),
            "validation_errors": validation_errors,
        }

    def _repair_plan_contracts(
        self,
        *,
        payload: Dict[str, Any],
        high_level_plan_text: str,
        high_level_plan: Dict[str, Any],
        candidate_tool_contracts: List[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], Any, List[Dict[str, Any]]]:
        if not isinstance(payload, dict):
            return {}, None, [{"scope": "plan", "message": "compiled payload is not a dict"}]
        repaired = self._apply_rule_repairs(payload)
        validation_errors = self._validate_plan_contracts(repaired)
        if not validation_errors:
            return repaired, None, []
        errors_by_step: Dict[str, List[Dict[str, Any]]] = {}
        for item in validation_errors:
            step_id = self._trim(item.get("step_id"))
            if not step_id:
                continue
            errors_by_step.setdefault(step_id, []).append(item)
        total_usage = None
        if not errors_by_step:
            return repaired, total_usage, validation_errors
        work_items = repaired.get("work_items") if isinstance(repaired.get("work_items"), list) else []
        for index, item in enumerate(work_items):
            if not isinstance(item, dict):
                continue
            step_id = self._trim(item.get("step_id"))
            step_errors = errors_by_step.get(step_id) or []
            if not step_errors:
                continue
            if self._step_errors_are_rule_fixable(step_errors):
                continue
            try:
                repaired_step, usage = self._run_plan_compiler_step_repair(
                    high_level_plan_text=high_level_plan_text,
                    current_step=item,
                    step_errors=step_errors,
                    upstream_steps=work_items[:index],
                    candidate_tool_contract_sections=self._format_step_tool_contract_section(
                        step=item,
                        high_level_plan=high_level_plan,
                        candidate_tool_contracts=candidate_tool_contracts,
                    ),
                )
            except Exception:
                repaired_step, usage = None, None
            total_usage = self._merge_usage(total_usage, usage)
            if isinstance(repaired_step, dict):
                work_items[index] = repaired_step
                repaired["work_items"] = work_items
                repaired = self._apply_rule_repairs(repaired)
        final_errors = self._validate_plan_contracts(repaired)
        return repaired, total_usage, final_errors

    def _run_plan_compiler_step_repair(
        self,
        *,
        high_level_plan_text: str,
        current_step: Dict[str, Any],
        step_errors: List[Dict[str, Any]],
        upstream_steps: List[Dict[str, Any]],
        candidate_tool_contract_sections: str,
    ) -> tuple[Optional[Dict[str, Any]], Any]:
        messages = self.registry.render_messages(
            "system.agent_runtime.plan_compiler_step_repair",
            {
                "high_level_plan_text": high_level_plan_text,
                "candidate_tool_contract_sections": candidate_tool_contract_sections,
                "current_step_json": json.dumps(current_step, ensure_ascii=False, indent=2),
                "upstream_steps_json": json.dumps(upstream_steps, ensure_ascii=False, indent=2),
                "validation_errors_json": json.dumps(step_errors, ensure_ascii=False, indent=2),
            },
        )
        repair_text, usage = chat_qwen(messages, enable_think=False)
        return extract_first_json(repair_text, log_errors=False), usage

    def _apply_rule_repairs(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        repaired = json.loads(json.dumps(payload, ensure_ascii=False))
        work_items = repaired.get("work_items") if isinstance(repaired.get("work_items"), list) else []
        for item in work_items:
            if not isinstance(item, dict):
                continue
            item_type = self._trim(item.get("item_type") or item.get("type"))
            input_binding = item.get("input_binding") if isinstance(item.get("input_binding"), dict) else {}
            if item_type == "transform":
                dsl = self._trim(((item.get("transform_spec") or {}) if isinstance(item.get("transform_spec"), dict) else {}).get("dsl") or item.get("item_action") or item.get("name"))
                if "$input" in dsl and "$input" not in input_binding and len(input_binding) == 1:
                    only_key, only_value = next(iter(input_binding.items()))
                    if self._trim(only_key) not in {"$input", ""}:
                        item["input_binding"] = {"$input": only_value}
            raw_item_type = self._trim(item.get("type") or item.get("item_type"))
            execution_mode = self._trim(item.get("execution_mode"))
            if raw_item_type == "foreach" and not execution_mode:
                item["type"] = "tool"
                item["item_type"] = "tool"
                item["execution_mode"] = "foreach"
        repaired["work_items"] = work_items
        return repaired

    def _step_errors_are_rule_fixable(self, step_errors: List[Dict[str, Any]]) -> bool:
        for item in step_errors:
            message = self._trim(item.get("message"))
            if not message:
                continue
            if "transform step should not use tool-style input key" in message:
                continue
            if "transform DSL uses `$input` but input_binding does not provide `$input`" in message:
                continue
            return False
        return bool(step_errors)

    def _build_agent_context(self, application_context: Dict[str, Any], work_context: Dict[str, Any]) -> Dict[str, Any]:
        default_agent = application_context.get("default_agent") if isinstance(application_context.get("default_agent"), dict) else {}
        return {
            "application_name": self._trim(application_context.get("application_name")),
            "default_agent": self._trim(default_agent.get("agent_name")) or self._trim(work_context.get("default_agent")),
            "active_skill_name": self._trim(
                work_context.get("thread_active_skill_canonical_name")
                or work_context.get("thread_active_skill_name")
                or work_context.get("active_skill_canonical_name")
                or work_context.get("active_skill_name")
            ),
        }

    def _build_deep_plan_preview(
        self,
        *,
        objective: str,
        capability_result: Dict[str, Any],
        work_context: Dict[str, Any],
        thinking_mode_result: Dict[str, Any],
        enable_llm_assessment: bool,
    ) -> Dict[str, Any]:
        if thinking_mode_result.get("thinking_mode") != "deep_thinking":
            return {}
        planner = self.deep_thinking_planner
        if not planner or not hasattr(planner, "build_initial_plan"):
            return {}
        try:
            preview = planner.build_initial_plan(
                user_objective=objective,
                capability_result=capability_result,
                work_context=work_context,
                enable_llm_assessment=enable_llm_assessment,
            )
        except Exception:
            return {}
        return preview if isinstance(preview, dict) else {}

    def _select_thinking_mode(
        self,
        *,
        objective: str,
        work_context: Dict[str, Any],
        application_context: Dict[str, Any],
        planner_skills: List[Dict[str, Any]],
        planner_tools: List[Dict[str, Any]],
        enable_llm: bool,
    ) -> Dict[str, Any]:
        raw_mode = (
            work_context.get("planner_thinking_mode")
            or work_context.get("preferred_thinking_mode")
            or work_context.get("thinking_mode")
            or application_context.get("planner_thinking_mode")
        )
        forced = self._normalize_thinking_mode(raw_mode)
        if forced:
            return {
                "selector_type": "planner_thinking_mode_router",
                "thinking_mode": forced,
                "mode_source": "forced",
                "reason": "explicit_thinking_mode",
                "should_use_deep_plan": forced == "deep_thinking",
                "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        if enable_llm and self._trim(raw_mode).lower() in {"auto", "auto_llm", "automatic"}:
            llm_result = self._classify_thinking_mode_with_llm(objective=objective, work_context=work_context)
            if llm_result:
                return llm_result
        rule_mode, reason = self._classify_thinking_mode_by_rule(objective)
        return {
            "selector_type": "planner_thinking_mode_router",
            "thinking_mode": rule_mode,
            "mode_source": "rule_fallback",
            "reason": reason,
            "should_use_deep_plan": rule_mode == "deep_thinking",
            "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def _classify_thinking_mode_with_llm(
        self,
        *,
        objective: str,
        work_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not objective:
            return {}
        try:
            messages = self.registry.render_messages(
                "system.agent_runtime.thinking_mode_router",
                {
                    "user_objective": objective,
                    "work_context": work_context,
                },
            )
            text, usage = chat_qwen(messages, enable_think=False)
            payload = extract_first_json(text, log_errors=False)
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        mode = self._normalize_thinking_mode(payload.get("thinking_mode") or payload.get("mode"))
        if not mode:
            return {}
        return {
            "selector_type": "planner_thinking_mode_router",
            "thinking_mode": mode,
            "mode_source": "auto_llm",
            "reason": self._trim(payload.get("reason")) or "llm_classified_task_complexity",
            "confidence": payload.get("confidence") if isinstance(payload.get("confidence"), (int, float)) else 0,
            "should_use_deep_plan": mode == "deep_thinking",
            "llm_usage": usage if isinstance(usage, dict) else {},
        }

    def _classify_thinking_mode_by_rule(self, objective: str) -> tuple[str, str]:
        text = self._trim(objective)
        deep_keywords = [
            "为什么",
            "为何",
            "原因",
            "分析",
            "对比",
            "比较",
            "怎么看",
            "影响",
            "风险",
            "机会",
            "建议",
            "策略",
            "归因",
            "综合",
            "研判",
            "前景",
            "逻辑",
            "是否值得",
            "能不能买",
        ]
        fast_keywords = [
            "查",
            "查询",
            "多少",
            "最新",
            "当前",
            "现在",
            "市盈率",
            "股价",
            "行情",
            "资金流",
            "成交额",
            "排名",
            "列表",
            "有哪些",
            "哪一些",
            "k线",
            "K线",
        ]
        if any(keyword in text for keyword in deep_keywords):
            return "deep_thinking", "analysis_or_judgment_request"
        if ("先" in text and any(keyword in text for keyword in ["再", "然后", "接着"])) or "分步骤" in text:
            return "deep_thinking", "multi_step_planning_request"
        if any(keyword in text for keyword in fast_keywords):
            return "fast_thinking", "direct_lookup_or_metric_request"
        return "deep_thinking", "default_to_deep_for_ambiguous_business_request"

    def _normalize_thinking_mode(self, value: Any) -> str:
        normalized = self._trim(value).lower()
        if normalized in {"fast", "fast_thinking", "simple", "simple_thinking", "quick", "quick_thinking"}:
            return "fast_thinking"
        if normalized in {"deep", "deep_thinking", "deep_plan", "planned", "planner"}:
            return "deep_thinking"
        return ""

    def _build_analysis_affordances(
        self,
        capability_result: Dict[str, Any],
        *,
        objective: str = "",
        planner_question_contract: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        quant_candidates = [
            self._to_quant_affordance(item)
            for item in (capability_result.get("planner_quant_capabilities") or [])
            if isinstance(item, dict)
        ][:5]
        authoring_affordance = self.quant_research_authoring_affordance_service.build_affordance(
            objective=objective,
            planner_question_contract=planner_question_contract if isinstance(planner_question_contract, dict) else {},
        )
        return {
            "quant_research_capabilities": quant_candidates,
            "quant_authoring_affordance": authoring_affordance,
            "upgrade_options": [
                {
                    "mode": "published_quant_research",
                    "capability_id": item["capability_id"],
                    "display_name": item["display_name"],
                    "capability_type": item["capability_type"],
                    "requires_confirmation": True,
                    "execution": "not_planned",
                }
                for item in quant_candidates
            ],
        }

    def _to_quant_affordance(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "capability_id": self._trim(item.get("capability_id")),
            "version": self._trim(item.get("version")),
            "display_name": self._trim(item.get("display_name")) or self._trim(item.get("capability_id")),
            "capability_type": self._trim(item.get("capability_type")),
            "purpose": self._trim(item.get("purpose")),
            "best_for": [self._trim(x) for x in item.get("best_for", []) if self._trim(x)],
            "params_schema": dict(item.get("params_schema") or {}),
            "spec_refs": [
                dict(ref)
                for ref in (item.get("spec_refs") or [])
                if isinstance(ref, dict)
            ],
            "execution_policy": dict(item.get("execution_policy") or {}),
        }

    def _needs_fallback_binding_backfill(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        work_items = payload.get("work_items")
        if not isinstance(work_items, list):
            return False
        for item in work_items:
            if not isinstance(item, dict):
                continue
            item_type = self._trim(item.get("type") or item.get("item_type"))
            if item_type != "tool":
                continue
            has_depends = isinstance(item.get("depends_on"), list) and bool(item.get("depends_on"))
            has_input_binding = isinstance(item.get("input_binding"), dict) and bool(item.get("input_binding"))
            has_output_binding = isinstance(item.get("output_binding"), dict) and bool(item.get("output_binding"))
            if not (has_depends or has_input_binding or has_output_binding):
                return True
        return False

    def _normalize_execution_plan(
        self,
        *,
        payload: Dict[str, Any],
        fallback_plan: Dict[str, Any],
        agent_context: Dict[str, Any],
        analysis_affordances: Optional[Dict[str, Any]] = None,
        task_mode_result: Optional[Dict[str, Any]] = None,
        planner_question_contract: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        requested_plan_mode = self._trim(payload.get("plan_mode"))
        task_mode = self._resolve_task_mode(
            requested_plan_mode=requested_plan_mode,
            requested_plan_type=self._trim(payload.get("plan_type")),
            fallback_task_mode="",
        )
        clarification_needed = bool(payload.get("clarification_needed"))
        clarification_questions = [
            self._trim(item)
            for item in (payload.get("clarification_questions") or [])
            if self._trim(item)
        ][:3]
        selected_skill = self._trim(payload.get("selected_skill"))
        requested_plan_type = self._trim(payload.get("plan_type")) or self._trim((fallback_plan.get("selected_path") or {}).get("type")) or "tool_plan_run"
        plan_type = self._resolve_plan_type(
            requested_plan_type=requested_plan_type,
            task_mode=task_mode,
        )

        work_items = payload.get("work_items") if isinstance(payload.get("work_items"), list) else []
        normalized_items: List[Dict[str, Any]] = []
        for item in work_items:
            if not isinstance(item, dict):
                continue
            raw_item_type = self._trim(item.get("type") or item.get("item_type"))
            execution_mode = self._trim(item.get("execution_mode"))
            item_type = "tool" if raw_item_type == "foreach" else raw_item_type
            if raw_item_type == "foreach" and not execution_mode:
                execution_mode = "foreach"
            item_name = self._trim(item.get("name") or item.get("item_action"))
            if item_type and item_name:
                if item_type == "tool" and self._is_disabled_tool_name(item_name):
                    continue
                transform_spec = dict(item.get("transform_spec") or {}) if isinstance(item.get("transform_spec"), dict) else {}
                if item_type == "transform" and not self._trim(transform_spec.get("dsl")) and self._trim(item.get("item_action")):
                    transform_spec["dsl"] = self._trim(item.get("item_action"))
                depends_on = [
                    self._trim(dep)
                    for dep in (item.get("depends_on") or [])
                    if self._trim(dep)
                ]
                normalized_items.append(
                    {
                        "step_id": self._trim(item.get("step_id")) or f"step_{len(normalized_items) + 1}",
                        "intent": self._trim(item.get("intent")),
                        "depends_on": depends_on,
                        "type": item_type,
                        "name": item_name,
                        "item_type": item_type,
                        "item_action": self._trim(item.get("item_action")) or item_name,
                        "execution_mode": execution_mode or "direct",
                        "foreach_binding": dict(item.get("foreach_binding") or {}) if isinstance(item.get("foreach_binding"), dict) else {},
                        "status": self._trim(item.get("status")) or "planned",
                        "transform_spec": transform_spec,
                        "runtime_profile": item.get("runtime_profile") if isinstance(item.get("runtime_profile"), (dict, str)) else {},
                        "code_task_spec": dict(item.get("code_task_spec") or {}) if isinstance(item.get("code_task_spec"), dict) else {},
                        "code": self._trim(item.get("code")),
                        "arguments": dict(item.get("arguments") or {}) if isinstance(item.get("arguments"), dict) else {},
                        "input_binding": dict(item.get("input_binding") or {}) if isinstance(item.get("input_binding"), dict) else {},
                        "output_binding": dict(item.get("output_binding") or {}) if isinstance(item.get("output_binding"), dict) else {},
                    }
                )

        if not normalized_items and fallback_plan:
            normalized_items = fallback_plan.get("work_items") if isinstance(fallback_plan.get("work_items"), list) else []
            normalized_items = [
                item for item in normalized_items
                if not (
                    isinstance(item, dict)
                    and self._trim(item.get("type") or item.get("item_type")) == "tool"
                    and self._is_disabled_tool_name(self._trim(item.get("item_action") or item.get("name")))
                )
            ]
        elif normalized_items and fallback_plan and plan_type in {"tool_plan_run", "planned_run"}:
            fallback_items = fallback_plan.get("work_items") if isinstance(fallback_plan.get("work_items"), list) else []
            fallback_tool_items = [
                item for item in fallback_items
                if isinstance(item, dict) and self._trim(item.get("type") or item.get("item_type")) == "tool"
            ]
            normalized_tool_items = [
                item for item in normalized_items
                if isinstance(item, dict) and self._trim(item.get("type") or item.get("item_type")) == "tool"
            ]
            if fallback_tool_items and len(fallback_tool_items) == len(normalized_tool_items):
                for index, tool_item in enumerate(normalized_tool_items):
                    if tool_item.get("depends_on") or tool_item.get("input_binding") or tool_item.get("output_binding"):
                        continue
                    fallback_tool = fallback_tool_items[index]
                    tool_item["step_id"] = self._trim(fallback_tool.get("step_id")) or self._trim(tool_item.get("step_id"))
                    tool_item["depends_on"] = [
                        self._trim(dep)
                        for dep in (fallback_tool.get("depends_on") or [])
                        if self._trim(dep)
                    ]
                    tool_item["input_binding"] = (
                        dict(fallback_tool.get("input_binding") or {})
                        if isinstance(fallback_tool.get("input_binding"), dict)
                        else {}
                    )
                    tool_item["output_binding"] = (
                        dict(fallback_tool.get("output_binding") or {})
                        if isinstance(fallback_tool.get("output_binding"), dict)
                        else {}
                    )

        selected_tools: List[str] = []
        seen_tool_names: set[str] = set()
        for item in normalized_items:
            if not isinstance(item, dict):
                continue
            if self._trim(item.get("item_type") or item.get("type")) != "tool":
                continue
            tool_name = self._trim(item.get("item_action") or item.get("name"))
            if not tool_name or tool_name in seen_tool_names:
                continue
            seen_tool_names.add(tool_name)
            selected_tools.append(tool_name)
        selected_tools = selected_tools[:5]

        selected_path = {
            "type": plan_type,
            "target": {
                "type": (
                    "skill"
                    if plan_type == "skill_run"
                    else "planned_graph"
                    if plan_type == "planned_run"
                    else "tool_group"
                    if plan_type == "tool_plan_run"
                    else "hybrid"
                ),
                "name": (
                    selected_skill
                    if plan_type == "skill_run"
                    else "planned_workflow"
                    if plan_type == "planned_run"
                    else "direct_tools"
                    if plan_type == "tool_plan_run"
                    else selected_skill or "hybrid_plan"
                ),
            },
            "reason": self._trim(payload.get("reason")) or self._trim((fallback_plan.get("selected_path") or {}).get("reason")),
        }

        presentation_plan = {}
        fallback_presentation = fallback_plan.get("presentation_plan") if isinstance(fallback_plan.get("presentation_plan"), dict) else {}
        normalized_presentation = {
            "layout": self._trim(presentation_plan.get("layout")) or self._trim(fallback_presentation.get("layout")) or "report",
            "page_type": self._trim(presentation_plan.get("page_type")) or self._trim(fallback_presentation.get("page_type")) or "analysis_result",
            "preferred_block_types": [
                self._trim(item)
                for item in (presentation_plan.get("preferred_block_types") or fallback_presentation.get("preferred_block_types") or [])
                if self._trim(item)
            ],
            "sections": presentation_plan.get("sections") if isinstance(presentation_plan.get("sections"), list) else fallback_presentation.get("sections") or [],
            "analysis_affordances": analysis_affordances if isinstance(analysis_affordances, dict) else {},
        }

        return {
            "planner_type": "agent_runtime_llm_planner",
            "planner_scope": "agent_local",
            "planner_agent": self._trim(agent_context.get("default_agent")) or "default_agent",
            "objective": self._trim(payload.get("objective")) or self._trim(fallback_plan.get("objective")),
            "domain": "business",
            "legacy_domain": "business_dialog",
            "candidate_skills": fallback_plan.get("candidate_skills") if isinstance(fallback_plan.get("candidate_skills"), list) else [],
            "candidate_tools": [
                item for item in (fallback_plan.get("candidate_tools") if isinstance(fallback_plan.get("candidate_tools"), list) else [])
                if not (
                    isinstance(item, dict)
                    and self._is_disabled_tool_name(self._trim(item.get("tool_name") or item.get("name")))
                )
            ],
            "candidate_quant_capabilities": (
                analysis_affordances.get("quant_research_capabilities")
                if isinstance(analysis_affordances, dict) and isinstance(analysis_affordances.get("quant_research_capabilities"), list)
                else []
            ),
            "planner_question_contract": planner_question_contract if isinstance(planner_question_contract, dict) else {},
            "plan_type": plan_type,
            "execution_path": plan_type,
            "task_mode": task_mode or ("planned" if plan_type == "planned_run" else ""),
            "selected_skill": selected_skill,
            "selected_tools": selected_tools,
            "selected_path": selected_path,
            "work_items": normalized_items,
            "planned_dag": self._build_planned_dag(work_items=normalized_items) if plan_type == "planned_run" else {},
            "evidence_requirements": [],
            "analysis_affordances": analysis_affordances if isinstance(analysis_affordances, dict) else {},
            "question_contract": planner_question_contract if isinstance(planner_question_contract, dict) else {},
            "clarification_needed": clarification_needed,
            "clarification_questions": clarification_questions,
            "presentation_plan": normalized_presentation,
            "reason": self._trim((fallback_plan.get("selected_path") or {}).get("reason")),
            "revision_count": 0,
            "status": "planned",
        }

    def _backfill_tool_step_arguments(
        self,
        *,
        payload: Dict[str, Any],
        objective: str,
        planner_tools: List[Dict[str, Any]],
        enable_llm: bool,
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        work_items = payload.get("work_items") if isinstance(payload.get("work_items"), list) else []
        if not work_items:
            return payload
        planner_tool_map = {
            self._trim(item.get("tool_name")): item
            for item in planner_tools
            if isinstance(item, dict) and self._trim(item.get("tool_name"))
        }
        cloned = json.loads(json.dumps(payload, ensure_ascii=False))
        for item in (cloned.get("work_items") or []):
            if not isinstance(item, dict):
                continue
            item_type = self._trim(item.get("item_type") or item.get("type"))
            if item_type != "tool":
                continue
            tool_name = self._trim(item.get("item_action") or item.get("name"))
            if not tool_name:
                continue
            current_arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            current_input_binding = item.get("input_binding") if isinstance(item.get("input_binding"), dict) else {}
            compile_result = self.tool_argument_compiler_service.compile_arguments(
                tool_name=tool_name,
                user_objective=objective,
                step_intent=self._trim(item.get("intent")),
                current_arguments=current_arguments,
                current_input_binding=current_input_binding,
                planner_tool=planner_tool_map.get(tool_name) if isinstance(planner_tool_map.get(tool_name), dict) else {},
                enable_llm=enable_llm,
            )
            compiled_arguments = compile_result.get("arguments") if isinstance(compile_result.get("arguments"), dict) else {}
            if compiled_arguments:
                item["arguments"] = {**compiled_arguments, **current_arguments}
        return cloned

    def _build_clarification_plan(
        self,
        *,
        fallback_plan: Dict[str, Any],
        agent_context: Dict[str, Any],
        objective: str,
        clarification_questions: List[str],
        analysis_affordances: Optional[Dict[str, Any]] = None,
        planner_question_contract: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            **self._normalize_execution_plan(
                payload={
                    "objective": objective,
                    "clarification_needed": True,
                    "clarification_questions": clarification_questions,
                    "reason": "missing_required_objective",
                },
                fallback_plan=fallback_plan,
                agent_context=agent_context,
                analysis_affordances=analysis_affordances,
                planner_question_contract=planner_question_contract,
            ),
            "work_items": [],
        }

    def _resolve_plan_type(self, *, requested_plan_type: str, task_mode: str) -> str:
        normalized_requested = self._trim(requested_plan_type) or "tool_plan_run"
        normalized_task_mode = self._trim(task_mode)
        if normalized_task_mode == "planned" and normalized_requested == "tool_plan_run":
            return "planned_run"
        return normalized_requested

    def _resolve_task_mode(
        self,
        *,
        requested_plan_mode: str,
        requested_plan_type: str,
        fallback_task_mode: str,
    ) -> str:
        normalized_mode = self._trim(requested_plan_mode)
        if normalized_mode in {"simple", "planned", "deep"}:
            return normalized_mode
        normalized_type = self._trim(requested_plan_type)
        if normalized_type == "planned_run":
            return "planned"
        if normalized_type in {"tool_plan_run", "skill_run", "hybrid"}:
            return self._trim(fallback_task_mode) or "simple"
        return self._trim(fallback_task_mode)

    def _build_planned_dag(self, *, work_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, str]] = []
        for item in work_items:
            if not isinstance(item, dict):
                continue
            step_id = self._trim(item.get("step_id"))
            raw_item_type = self._trim(item.get("type") or item.get("item_type"))
            item_type = "tool" if raw_item_type == "foreach" else raw_item_type
            item_name = self._trim(item.get("name") or item.get("item_action"))
            if not step_id or not item_type or not item_name:
                continue
            nodes.append(
                {
                    "step_id": step_id,
                    "intent": self._trim(item.get("intent")),
                    "type": item_type,
                    "name": item_name,
                    "item_type": item_type,
                    "item_action": self._trim(item.get("item_action")) or item_name,
                    "execution_mode": self._trim(item.get("execution_mode")) or ("foreach" if raw_item_type == "foreach" else "direct"),
                    "foreach_binding": dict(item.get("foreach_binding") or {}) if isinstance(item.get("foreach_binding"), dict) else {},
                    "status": self._trim(item.get("status")) or "planned",
                    "depends_on": [
                        self._trim(dep)
                        for dep in (item.get("depends_on") or [])
                        if self._trim(dep)
                    ],
                    "transform_spec": dict(item.get("transform_spec") or {}) if isinstance(item.get("transform_spec"), dict) else {},
                    "runtime_profile": item.get("runtime_profile") if isinstance(item.get("runtime_profile"), (dict, str)) else {},
                    "code_task_spec": dict(item.get("code_task_spec") or {}) if isinstance(item.get("code_task_spec"), dict) else {},
                    "input_binding": dict(item.get("input_binding") or {}) if isinstance(item.get("input_binding"), dict) else {},
                    "output_binding": dict(item.get("output_binding") or {}) if isinstance(item.get("output_binding"), dict) else {},
                }
            )
            for dependency in item.get("depends_on") or []:
                dep = self._trim(dependency)
                if dep:
                    edges.append({"from_step_id": dep, "to_step_id": step_id})
        return {
            "dag_type": "planned_execution_graph",
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
        }

    def _normalize_usage(self, usage: Any) -> Dict[str, int]:
        if isinstance(usage, dict):
            return {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            }
        return {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }

    def _merge_usage(self, *usages: Any) -> Dict[str, int]:
        merged = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for usage in usages:
            normalized = self._normalize_usage(usage)
            merged["prompt_tokens"] += normalized["prompt_tokens"]
            merged["completion_tokens"] += normalized["completion_tokens"]
            merged["total_tokens"] += normalized["total_tokens"]
        return merged

    def _extract_stage2_plan_text(self, high_level_plan_text: str) -> str:
        text = self._trim(high_level_plan_text)
        if not text:
            return text
        patterns = [
            r"执行步骤：",
            r"执行步骤:",
            r"Excute-Plan:",
            r"Execute-Plan:",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                extracted = text[match.start():].strip()
                if extracted:
                    return extracted
        return text

    def _validate_plan_contracts(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return [{"scope": "plan", "message": "compiled payload is not a dict"}]
        normalized = self._normalize_execution_plan(
            payload=payload,
            fallback_plan={},
            agent_context={},
        )
        work_items = normalized.get("work_items") if isinstance(normalized.get("work_items"), list) else []
        exported_types: Dict[str, Dict[str, str]] = {}
        errors: List[Dict[str, Any]] = []

        for item in work_items:
            if not isinstance(item, dict):
                continue
            step_id = self._trim(item.get("step_id"))
            item_type = self._trim(item.get("item_type") or item.get("type"))
            item_action = self._trim(item.get("item_action") or item.get("name"))
            execution_mode = self._trim(item.get("execution_mode")) or "direct"
            input_binding = item.get("input_binding") if isinstance(item.get("input_binding"), dict) else {}
            output_binding = item.get("output_binding") if isinstance(item.get("output_binding"), dict) else {}
            foreach_binding = item.get("foreach_binding") if isinstance(item.get("foreach_binding"), dict) else {}

            if item_type == "tool":
                schema_names = {
                    self._trim(row.get("name"))
                    for row in self._load_tool_input_schema_fields(item_action)
                    if isinstance(row, dict) and self._trim(row.get("name"))
                }
                for key, value in input_binding.items():
                    normalized_key = self._trim(key)
                    if normalized_key and normalized_key not in schema_names:
                        errors.append(
                            {
                                "step_id": step_id,
                                "scope": "input_binding",
                                "message": f"tool input key `{normalized_key}` is not in input schema of `{item_action}`",
                            }
                        )
                    if not self._is_supported_binding_value(value):
                        errors.append(
                            {
                                "step_id": step_id,
                                "scope": "input_binding",
                                "message": f"binding value `{value}` is not in supported binding syntax",
                            }
                        )
                if execution_mode == "foreach" and not self._trim(foreach_binding.get("items")):
                    errors.append(
                        {
                            "step_id": step_id,
                            "scope": "foreach_binding",
                            "message": "execution_mode=foreach but foreach_binding.items is missing",
                        }
                    )
                foreach_items_value = foreach_binding.get("items")
                if foreach_items_value and not self._is_supported_foreach_items_value(foreach_items_value):
                    errors.append(
                        {
                            "step_id": step_id,
                            "scope": "foreach_binding",
                            "message": f"foreach_binding.items `{foreach_items_value}` is not in supported binding syntax",
                        }
                    )
                for exported_name, expr in output_binding.items():
                    exported_type = self._infer_tool_output_expr_type(item_action, expr)
                    if exported_type in {"missing", "invalid_expr"}:
                        errors.append(
                            {
                                "step_id": step_id,
                                "scope": "output_binding",
                                "message": f"output expr `{expr}` is not compatible with output schema of `{item_action}`",
                            }
                        )
                    elif self._trim(exported_name):
                        exported_types.setdefault(step_id, {})[self._trim(exported_name)] = exported_type
            elif item_type == "transform":
                dsl = self._trim(((item.get("transform_spec") or {}) if isinstance(item.get("transform_spec"), dict) else {}).get("dsl"))
                input_ref_type = "unknown"
                if "$input" in dsl and "$input" not in input_binding:
                    errors.append(
                        {
                            "step_id": step_id,
                            "scope": "input_binding",
                            "message": "transform DSL uses `$input` but input_binding does not provide `$input`",
                        }
                    )
                for key in input_binding.keys():
                    normalized_key = self._trim(key)
                    if normalized_key and normalized_key != "$input":
                        errors.append(
                            {
                                "step_id": step_id,
                                "scope": "input_binding",
                                "message": f"transform step should not use tool-style input key `{normalized_key}`",
                            }
                        )
                for value in input_binding.values():
                    if not self._is_supported_binding_value(value):
                        errors.append(
                            {
                                "step_id": step_id,
                                "scope": "input_binding",
                                "message": f"binding value `{value}` is not in supported binding syntax",
                            }
                        )
                input_ref = self._trim(input_binding.get("$input"))
                if input_ref.startswith("${") and input_ref.endswith("}"):
                    ref = input_ref[2:-1].strip()
                    if ref.startswith("step_"):
                        step_ref, _, tail = ref.partition(".")
                        export_name, _ = self._split_export_reference_tail(tail)
                        ref_type = exported_types.get(step_ref, {}).get(export_name, "")
                        input_ref_type = ref_type or "unknown"
                        if self._transform_dsl_expects_array(dsl) and ref_type in {"object", "scalar", "unknown"}:
                            errors.append(
                                {
                                    "step_id": step_id,
                                    "scope": "input_binding",
                                    "message": f"transform DSL expects list-like `$input`, but `{input_ref}` resolves to `{ref_type or 'unknown'}`",
                                }
                            )
                result_type = self._infer_transform_result_type(dsl=dsl, input_type=input_ref_type)
                for exported_name, expr in output_binding.items():
                    normalized_expr = self._trim(expr)
                    if normalized_expr == "$result":
                        continue
                    if normalized_expr.startswith("$result."):
                        if result_type in {"scalar", "array<scalar>", "unknown"}:
                            errors.append(
                                {
                                    "step_id": step_id,
                                    "scope": "output_binding",
                                    "message": f"transform output expr `{normalized_expr}` is not compatible with transform result type `{result_type}`",
                                }
                            )
                exported_types.setdefault(step_id, {}).update(self._infer_transform_output_types(item, input_type=input_ref_type))
            elif item_type == "code":
                for value in input_binding.values():
                    if not self._is_supported_binding_value(value):
                        errors.append(
                            {
                                "step_id": step_id,
                                "scope": "input_binding",
                                "message": f"binding value `{value}` is not in supported binding syntax",
                            }
                        )
                for exported_name, expr in output_binding.items():
                    normalized_expr = self._trim(expr)
                    if normalized_expr and not (normalized_expr == "$result" or normalized_expr.startswith("$result.")):
                        errors.append(
                            {
                                "step_id": step_id,
                                "scope": "output_binding",
                                "message": f"code output expr `{expr}` must reference `$result`",
                            }
                        )
                    elif self._trim(exported_name):
                        exported_types.setdefault(step_id, {})[self._trim(exported_name)] = "unknown"

            item_shape = self._infer_foreach_item_shape(step=item, exported_types=exported_types)
            refs = self._collect_binding_refs(item)
            for ref in refs:
                if ref.startswith("step_"):
                    step_ref, _, tail = ref.partition(".")
                    if not tail:
                        continue
                    if tail.startswith("result."):
                        continue
                    export_name, tail_suffix = self._split_export_reference_tail(tail)
                    ref_type = exported_types.get(step_ref, {}).get(export_name)
                    if not ref_type:
                        errors.append(
                            {
                                "step_id": step_id,
                                "scope": "binding_ref",
                                "message": f"reference `{ref}` does not match exported fields of `{step_ref}`",
                            }
                        )
                    elif tail_suffix.startswith("["):
                        if ref_type not in {"array<object>", "array<scalar>"}:
                            errors.append(
                                {
                                    "step_id": step_id,
                                    "scope": "binding_ref",
                                    "message": f"reference `{ref}` uses index access on non-array exported value",
                                }
                            )
                        elif "." in tail_suffix and ref_type == "array<scalar>":
                            errors.append(
                                {
                                    "step_id": step_id,
                                    "scope": "binding_ref",
                                    "message": f"reference `{ref}` traverses into scalar array element",
                                }
                            )
                    elif "." in tail_suffix and ref_type in {"scalar", "array<scalar>"}:
                        errors.append(
                            {
                                "step_id": step_id,
                                "scope": "binding_ref",
                                "message": f"reference `{ref}` traverses into non-object exported value",
                            }
                        )
                elif ref.startswith("item."):
                    if item_shape != "object":
                        errors.append(
                            {
                                "step_id": step_id,
                                "scope": "binding_ref",
                                "message": f"reference `{ref}` expects foreach item object, but current foreach items are not object-shaped",
                            }
                        )
        return errors

    def _collect_binding_refs(self, item: Dict[str, Any]) -> List[str]:
        refs: List[str] = []
        for binding_name in ("input_binding", "foreach_binding"):
            bindings = item.get(binding_name) if isinstance(item.get(binding_name), dict) else {}
            for value in bindings.values():
                if not isinstance(value, str):
                    continue
                text = self._trim(value)
                if text.startswith("${") and text.endswith("}"):
                    refs.append(text[2:-1].strip())
        return refs

    def _split_export_reference_tail(self, tail: str) -> tuple[str, str]:
        normalized = self._trim(tail)
        if not normalized:
            return "", ""
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(.*)$", normalized)
        if not match:
            return normalized, ""
        return match.group(1), match.group(2) or ""

    def _infer_foreach_item_shape(self, *, step: Dict[str, Any], exported_types: Dict[str, Dict[str, str]]) -> str:
        execution_mode = self._trim(step.get("execution_mode")) or "direct"
        if execution_mode != "foreach":
            return ""
        foreach_binding = step.get("foreach_binding") if isinstance(step.get("foreach_binding"), dict) else {}
        expr = self._trim(foreach_binding.get("items"))
        if expr.startswith("${") and expr.endswith("}"):
            ref = expr[2:-1].strip()
            if ref.startswith("step_"):
                step_ref, _, tail = ref.partition(".")
                export_name, tail_suffix = self._split_export_reference_tail(tail)
                ref_type = exported_types.get(step_ref, {}).get(export_name, "")
                if tail_suffix.startswith("[") and ref_type == "array<object>":
                    return "object"
                if tail_suffix.startswith("[") and ref_type == "array<scalar>":
                    return "scalar"
                if ref_type == "array<object>":
                    return "object"
                if ref_type == "array<scalar>":
                    return "scalar"
        return "object"

    def _infer_tool_output_expr_type(self, tool_name: str, expr: Any) -> str:
        normalized_expr = self._trim(expr)
        if not normalized_expr.startswith("$result"):
            return "invalid_expr"
        definition = self._load_tool_definition(tool_name)
        schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        output_schema = self._resolve_output_schema(
            schemas.get("output") if isinstance(schemas.get("output"), dict) else {}
        )
        if not output_schema:
            return "unknown"
        if normalized_expr == "$result":
            return "object"
        path = normalized_expr[len("$result."):].strip() if normalized_expr.startswith("$result.") else ""
        if not path:
            return "object"
        schema = self._resolve_schema_path(output_schema, path)
        if not schema:
            return "missing"
        return self._compact_schema_type(schema)

    def _transform_dsl_expects_array(self, dsl: str) -> bool:
        normalized = self._trim(dsl)
        return any(token in normalized for token in ("| top(", "| filter(", "| sort(", "| first()", "| pluck("))

    def _is_supported_binding_value(self, value: Any) -> bool:
        if not isinstance(value, str):
            return True
        normalized = self._trim(value)
        if not normalized:
            return True
        if not normalized.startswith("$"):
            return True
        if normalized.startswith("${") and normalized.endswith("}"):
            return True
        if re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", normalized):
            return True
        return False

    def _is_supported_foreach_items_value(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        normalized = self._trim(value)
        if not normalized:
            return False
        if normalized.startswith("${") and normalized.endswith("}"):
            return True
        if re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", normalized):
            return True
        return False

    def _infer_transform_result_type(self, *, dsl: str, input_type: str) -> str:
        steps = [self._trim(part) for part in str(dsl or "").split("|") if self._trim(part)]
        current_type = input_type or "unknown"
        if steps and steps[0] == "$input":
            steps = steps[1:]
        for op in steps:
            normalized = self._trim(op)
            if normalized.startswith("filter(") and normalized.endswith(")"):
                continue
            if normalized.startswith("sort(") and normalized.endswith(")"):
                continue
            if normalized.startswith("top(") and normalized.endswith(")"):
                continue
            if normalized == "first()":
                if current_type == "array<object>":
                    current_type = "object"
                elif current_type == "array<scalar>":
                    current_type = "scalar"
                else:
                    current_type = "unknown"
                continue
            if normalized.startswith("pluck(") and normalized.endswith(")"):
                if current_type == "array<object>":
                    current_type = "array<scalar>"
                elif current_type == "object":
                    current_type = "scalar"
                else:
                    current_type = "unknown"
                continue
            if normalized.startswith("project(") and normalized.endswith(")"):
                if current_type == "array<object>":
                    current_type = "array<object>"
                else:
                    current_type = "object"
        return current_type or "unknown"

    def _infer_transform_output_types(self, item: Dict[str, Any], *, input_type: str = "unknown") -> Dict[str, str]:
        output_binding = item.get("output_binding") if isinstance(item.get("output_binding"), dict) else {}
        dsl = self._trim(((item.get("transform_spec") or {}) if isinstance(item.get("transform_spec"), dict) else {}).get("dsl"))
        result_type = self._infer_transform_result_type(dsl=dsl, input_type=input_type)
        inferred: Dict[str, str] = {}
        for exported_name, expr in output_binding.items():
            normalized_name = self._trim(exported_name)
            normalized_expr = self._trim(expr)
            if not normalized_name:
                continue
            if normalized_expr == "$result":
                inferred[normalized_name] = result_type
            elif normalized_expr.startswith("$result."):
                if result_type == "array<object>":
                    inferred[normalized_name] = "array<scalar>"
                else:
                    inferred[normalized_name] = "scalar"
            else:
                inferred[normalized_name] = "unknown"
        return inferred

    def _resolve_schema_path(self, schema: Dict[str, Any], path: str) -> Dict[str, Any]:
        current = schema if isinstance(schema, dict) else {}
        for raw_part in path.split("."):
            part = self._trim(raw_part)
            if not part:
                continue
            if part.isdigit():
                current = self._array_item_schema(current)
                continue
            if part.endswith("[]"):
                base = part[:-2]
                properties = current.get("properties") if isinstance(current.get("properties"), dict) else {}
                current = properties.get(base) if isinstance(properties.get(base), dict) else {}
                current = self._array_item_schema(current)
                continue
            if self._trim(current.get("type")) == "array":
                current = self._array_item_schema(current)
            properties = current.get("properties") if isinstance(current.get("properties"), dict) else {}
            current = properties.get(part) if isinstance(properties.get(part), dict) else {}
            if not current:
                return {}
        return current if isinstance(current, dict) else {}

    def _compact_schema_type(self, schema: Dict[str, Any]) -> str:
        schema_type = self._schema_type(schema)
        if schema_type == "array<object>":
            return "array<object>"
        if schema_type.startswith("array<"):
            return "array<scalar>"
        if schema_type == "object":
            return "object"
        if schema_type and schema_type != "unknown":
            return "scalar"
        return "unknown"

    def _collect_plan_tool_contracts(
        self,
        *,
        high_level_plan: Dict[str, Any],
        planner_tools: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        requested_names: List[str] = []
        for item in high_level_plan.get("work_items") or []:
            if not isinstance(item, dict):
                continue
            for candidate in item.get("tool_candidates") or []:
                name = self._trim(candidate)
                if name and name not in requested_names:
                    requested_names.append(name)
        if not requested_names:
            requested_names = [
                self._trim(item.get("tool_name"))
                for item in planner_tools
                if isinstance(item, dict) and self._trim(item.get("tool_name"))
            ][:8]
        contracts: List[Dict[str, Any]] = []
        for tool in planner_tools:
            if not isinstance(tool, dict):
                continue
            tool_name = self._trim(tool.get("tool_name"))
            if not tool_name or tool_name not in requested_names:
                continue
            contracts.append(
                {
                    "tool_name": tool_name,
                    "display_name": self._trim(tool.get("display_name")) or tool_name,
                    "purpose": self._trim(tool.get("purpose")),
                    "best_for": [
                        self._trim(item)
                        for item in (tool.get("best_for") or [])
                        if self._trim(item)
                    ],
                    "invocation_example": self._load_tool_invocation_example(tool_name),
                    "input_schema": self._load_tool_input_schema_fields(tool_name),
                    "output_schema": self._load_tool_output_schema_fields(tool_name),
                    "input_notes": [
                        self._trim(item)
                        for item in (tool.get("input_notes") or [])
                        if self._trim(item)
                    ],
                }
            )
        return contracts

    def _format_planner_tool_summaries(self, planner_tools: List[Dict[str, Any]]) -> str:
        rows: List[str] = []
        for index, tool in enumerate(planner_tools[:12], start=1):
            if not isinstance(tool, dict):
                continue
            tool_name = self._trim(tool.get("tool_name"))
            if not tool_name:
                continue
            purpose = self._trim(tool.get("purpose"))
            best_for = [self._trim(item) for item in (tool.get("best_for") or []) if self._trim(item)]
            rows.append(f"### 工具 {index}")
            rows.append(f"- tool_name: {tool_name}")
            if purpose:
                rows.append(f"- purpose: {purpose}")
            if best_for:
                rows.append(f"- best_for: {', '.join(best_for)}")
            rows.append("")
        return "\n".join(rows).strip()

    def _format_planner_skill_summaries(self, planner_skills: List[Dict[str, Any]]) -> str:
        rows: List[str] = []
        for index, skill in enumerate(planner_skills[:8], start=1):
            if not isinstance(skill, dict):
                continue
            skill_name = self._trim(skill.get("skill_name") or skill.get("name"))
            if not skill_name:
                continue
            purpose = self._trim(skill.get("purpose") or skill.get("description"))
            best_for = [self._trim(item) for item in (skill.get("best_for") or []) if self._trim(item)]
            rows.append(f"### 技能 {index}")
            rows.append(f"- skill_name: {skill_name}")
            if purpose:
                rows.append(f"- purpose: {purpose}")
            if best_for:
                rows.append(f"- best_for: {', '.join(best_for)}")
            rows.append("")
        return "\n".join(rows).strip()

    def _format_compiler_tool_contract_sections(
        self,
        *,
        high_level_plan: Dict[str, Any],
        high_level_plan_text: str,
        candidate_tool_contracts: List[Dict[str, Any]],
    ) -> str:
        contract_map = {
            self._trim(item.get("tool_name")): item
            for item in candidate_tool_contracts
            if isinstance(item, dict) and self._trim(item.get("tool_name"))
        }
        sections: List[str] = []
        work_items = high_level_plan.get("work_items") if isinstance(high_level_plan.get("work_items"), list) else []
        step_specs = self._extract_step_specs_for_compiler(
            high_level_plan=high_level_plan,
            high_level_plan_text=high_level_plan_text,
        )
        for index, item in enumerate(step_specs, start=1):
            step_id = self._trim(item.get("step_id")) or f"step_{index}"
            intent = self._trim(item.get("intent"))
            candidates = [
                self._trim(name)
                for name in (item.get("tool_candidates") or [])
                if self._trim(name) and self._trim(name) in contract_map
            ]
            if not candidates:
                continue
            sections.append(f"## 候选工具 - {step_id}")
            if intent:
                sections.append(f"- step_intent: {intent}")
            for name in candidates:
                sections.extend(self._format_tool_contract_markdown(contract_map[name]))
            sections.append("")
        if sections:
            return "\n".join(sections).strip()
        fallback_rows: List[str] = []
        for item in candidate_tool_contracts:
            if not isinstance(item, dict):
                continue
            fallback_rows.extend(self._format_tool_contract_markdown(item))
            fallback_rows.append("")
        return "\n".join(fallback_rows).strip()

    def _format_step_tool_contract_section(
        self,
        *,
        step: Dict[str, Any],
        high_level_plan: Dict[str, Any],
        high_level_plan_text: str,
        candidate_tool_contracts: List[Dict[str, Any]],
    ) -> str:
        contract_map = {
            self._trim(item.get("tool_name")): item
            for item in candidate_tool_contracts
            if isinstance(item, dict) and self._trim(item.get("tool_name"))
        }
        candidates = [
            self._trim(name)
            for name in (step.get("tool_candidates") or [])
            if self._trim(name) and self._trim(name) in contract_map
        ]
        step_action = self._trim(step.get("item_action") or step.get("name"))
        step_type = self._trim(step.get("item_type") or step.get("type"))
        if step_type == "tool" and step_action and step_action in contract_map and step_action not in candidates:
            candidates.append(step_action)
        if not candidates:
            return self._format_compiler_tool_contract_sections(
                high_level_plan=high_level_plan,
                high_level_plan_text=high_level_plan_text,
                candidate_tool_contracts=candidate_tool_contracts,
            )
        rows = [f"## 候选工具 - {self._trim(step.get('step_id')) or 'step'}"]
        intent = self._trim(step.get("intent"))
        if intent:
            rows.append(f"- step_intent: {intent}")
        for name in candidates:
            rows.extend(self._format_tool_contract_markdown(contract_map[name]))
        return "\n".join(rows)

    def _extract_step_specs_for_compiler(
        self,
        *,
        high_level_plan: Dict[str, Any],
        high_level_plan_text: str,
    ) -> List[Dict[str, Any]]:
        work_items = high_level_plan.get("work_items") if isinstance(high_level_plan.get("work_items"), list) else []
        specs: List[Dict[str, Any]] = []
        for index, item in enumerate(work_items, start=1):
            if not isinstance(item, dict):
                continue
            candidates = [
                self._trim(name)
                for name in (item.get("tool_candidates") or [])
                if self._trim(name)
            ]
            if not candidates:
                continue
            specs.append(
                {
                    "step_id": self._trim(item.get("step_id")) or f"step_{index}",
                    "intent": self._trim(item.get("intent")),
                    "tool_candidates": candidates,
                }
            )
        if specs:
            return specs

        text = str(high_level_plan_text or "")
        pattern = re.compile(r"\*\s*Step_(\d+)\s*:\s*(.*?)(?=\n\s*\*\s*Step_\d+\s*:|\Z)", re.S)
        parsed_specs: List[Dict[str, Any]] = []
        for match in pattern.finditer(text):
            step_no = self._trim(match.group(1))
            body = self._trim(match.group(2))
            if not step_no or not body:
                continue
            intent_match = re.search(r"目标[:：]\s*(.+)", body)
            intent = self._trim(intent_match.group(1)) if intent_match else ""
            hand_match = re.search(r"手段[:：]\s*(.+)", body)
            hand_text = self._trim(hand_match.group(1)) if hand_match else body
            bracket_candidates: List[str] = []
            for group in re.findall(r"\[([^\]]+)\]", hand_text):
                for name in group.split(","):
                    normalized = self._trim(name)
                    if normalized:
                        bracket_candidates.append(normalized)
            parsed_specs.append(
                {
                    "step_id": f"step_{step_no}",
                    "intent": intent,
                    "tool_candidates": bracket_candidates,
                }
            )
        return parsed_specs

    def _format_tool_contract_markdown(self, contract: Dict[str, Any]) -> List[str]:
        tool_name = self._trim(contract.get("tool_name"))
        if not tool_name:
            return []
        display_name = self._trim(contract.get("display_name"))
        purpose = self._trim(contract.get("purpose"))
        best_for = [self._trim(item) for item in (contract.get("best_for") or []) if self._trim(item)]
        input_schema = contract.get("input_schema") if isinstance(contract.get("input_schema"), list) else []
        output_schema = contract.get("output_schema") if isinstance(contract.get("output_schema"), list) else []
        input_notes = [self._trim(item) for item in (contract.get("input_notes") or []) if self._trim(item)]

        rows = [f"### 工具：{tool_name}"]
        if display_name:
            rows.append(f"- display_name: {display_name}")
        if purpose:
            rows.append(f"- purpose: {purpose}")
        if best_for:
            rows.append(f"- best_for: {', '.join(best_for)}")
        invocation_example = self._trim(contract.get("invocation_example"))
        if invocation_example:
            rows.append(f"- invocation_example: `{invocation_example}`")

        rows.append("- input_sample:")
        rows.append("```json")
        rows.append(json.dumps(self._build_schema_sample_payload(input_schema), ensure_ascii=False, indent=2))
        rows.append("```")
        if input_schema:
            rows.append("- input_fields:")
            for field in input_schema:
                if not isinstance(field, dict):
                    continue
                field_name = self._trim(field.get("name"))
                if not field_name:
                    continue
                field_type = self._trim(field.get("type")) or "unknown"
                field_desc = self._trim(field.get("desc"))
                constraints = self._format_field_constraints(field)
                detail = "；".join([item for item in [field_desc, constraints] if item])
                required = "required" if bool(field.get("required")) else "optional"
                rows.append(f"  - `{field_name}` | `{field_type}` | `{required}` | {detail}")
        if input_notes:
            rows.append(f"- input_notes: {'；'.join(input_notes)}")

        rows.append("- output_sample:")
        rows.append("```json")
        rows.append(json.dumps(self._build_output_sample_payload(output_schema), ensure_ascii=False, indent=2))
        rows.append("```")
        if output_schema:
            rows.append("- output_fields:")
            for field in output_schema:
                if not isinstance(field, dict):
                    continue
                field_name = self._trim(field.get("name"))
                if not field_name:
                    continue
                field_type = self._trim(field.get("type")) or "unknown"
                field_desc = self._trim(field.get("desc"))
                ref = f"$result.{field_name}" if field_name != "data" else "$result.data"
                rows.append(f"  - `{field_name}` | `{field_type}` | 引用 `{ref}` | {field_desc}")
        return rows

    def _build_schema_sample_payload(self, input_schema: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for field in input_schema:
            if not isinstance(field, dict):
                continue
            field_name = self._trim(field.get("name"))
            if not field_name:
                continue
            payload[field_name] = self._sample_value_for_type(self._trim(field.get("type")))
            enum_values = [self._trim(item) for item in (field.get("enum") or []) if self._trim(item)]
            if enum_values:
                payload[field_name] = enum_values[0]
        return payload

    def _build_output_sample_payload(self, output_schema: List[Dict[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {"data": {}}
        for field in output_schema[:8]:
            if not isinstance(field, dict):
                continue
            field_name = self._trim(field.get("name"))
            field_type = self._trim(field.get("type"))
            if not field_name.startswith("data"):
                continue
            self._assign_sample_path(result, field_name, self._sample_value_for_type(field_type))
        return result

    def _assign_sample_path(self, payload: Dict[str, Any], dotted_path: str, value: Any) -> None:
        parts = [self._trim(part) for part in dotted_path.split(".") if self._trim(part)]
        current: Any = payload
        index = 0
        while index < len(parts):
            part = parts[index]
            is_last = index == len(parts) - 1
            if part.endswith("[]"):
                key = part[:-2]
                existing = current.get(key)
                if not isinstance(existing, list) or not existing:
                    current[key] = [{}]
                if is_last:
                    if not current[key]:
                        current[key] = [value]
                    elif isinstance(current[key][0], dict):
                        current[key][0] = value
                    else:
                        current[key][0] = value
                    return
                current = current[key][0]
            else:
                if is_last:
                    current[part] = value
                    return
                if not isinstance(current.get(part), dict):
                    current[part] = {}
                current = current[part]
            index += 1

    def _sample_value_for_type(self, field_type: str) -> Any:
        normalized = self._trim(field_type)
        if normalized in {"string", "const"}:
            return "sample_text"
        if normalized in {"integer", "number"}:
            return 1
        if normalized == "boolean":
            return True
        if normalized == "object":
            return {}
        if normalized == "array<object>":
            return [{}]
        if normalized.startswith("array<"):
            inner = normalized[6:-1]
            return [self._sample_value_for_type(inner)]
        return ""

    def _load_tool_input_schema_fields(self, tool_name: str) -> List[Dict[str, Any]]:
        definition = self._load_tool_definition(tool_name)
        schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        input_schema = schemas.get("input") if isinstance(schemas.get("input"), dict) else {}
        properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
        required = {
            self._trim(item)
            for item in (input_schema.get("required") or [])
            if self._trim(item)
        }
        rows: List[Dict[str, Any]] = []
        for field_name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                continue
            name = self._trim(field_name)
            if not name:
                continue
            rows.append(
                {
                    "name": name,
                    "type": self._schema_type(field_schema),
                    "required": name in required,
                    "desc": self._trim(field_schema.get("description") or field_schema.get("title")),
                    "enum": self._schema_enum(field_schema),
                    "default": field_schema.get("default"),
                    "minimum": field_schema.get("minimum"),
                    "maximum": field_schema.get("maximum"),
                }
            )
        return rows

    def _load_tool_output_schema_fields(self, tool_name: str) -> List[Dict[str, Any]]:
        definition = self._load_tool_definition(tool_name)
        schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        output_schema = self._resolve_output_schema(
            schemas.get("output") if isinstance(schemas.get("output"), dict) else {}
        )
        properties = output_schema.get("properties") if isinstance(output_schema.get("properties"), dict) else {}
        data_schema = properties.get("data") if isinstance(properties.get("data"), dict) else {}
        rows = self._extract_schema_fields(data_schema, prefix="data")
        return rows[:20]

    def _load_tool_definition(self, tool_name: str) -> Dict[str, Any]:
        path = self.tool_definitions_dir / f"{tool_name}.tool.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        try:
            bundle = self.custom_tool_store_service.load(tool_name)
        except Exception:
            return {}
        manifest = bundle.get("manifest") if isinstance(bundle.get("manifest"), dict) else {}
        output_schema = bundle.get("output_schema") if isinstance(bundle.get("output_schema"), dict) else {}
        return {
            "identity": {
                "display_name": self._trim(manifest.get("display_name")) or tool_name,
                "description": self._trim(manifest.get("description")),
            },
            "availability": {
                "lifecycle": self._trim(manifest.get("status")) or "draft",
                "retrieval_mode": "retrievable",
                "visibility": "visible",
            },
            "capabilities": [self._trim(item) for item in (manifest.get("capabilities") or []) if self._trim(item)],
            "schemas": {
                "input": bundle.get("input_schema") if isinstance(bundle.get("input_schema"), dict) else {},
                "output": {
                    "type": "object",
                    "properties": {"data": output_schema},
                },
            },
        }

    def _load_tool_invocation_example(self, tool_name: str) -> str:
        definition = self._load_tool_definition(tool_name)
        return self._trim(definition.get("invocation_example"))

    def _resolve_output_schema(self, schema_ref_or_schema: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(schema_ref_or_schema, dict):
            return {}
        schema_ref = self._trim(schema_ref_or_schema.get("$ref"))
        if schema_ref:
            path = Path(schema_ref)
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    return {}
        return schema_ref_or_schema

    def _extract_schema_fields(self, schema: Dict[str, Any], *, prefix: str) -> List[Dict[str, Any]]:
        if not isinstance(schema, dict):
            return []
        schema_type = self._schema_type(schema)
        if schema_type == "object":
            properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            required = {
                self._trim(item)
                for item in (schema.get("required") or [])
                if self._trim(item)
            }
            rows: List[Dict[str, Any]] = []
            for field_name, field_schema in properties.items():
                if not isinstance(field_schema, dict):
                    continue
                full_name = f"{prefix}.{self._trim(field_name)}" if prefix else self._trim(field_name)
                field_type = self._schema_type(field_schema)
                rows.append(
                    {
                        "name": full_name,
                        "type": field_type,
                        "required": self._trim(field_name) in required,
                        "desc": self._trim(field_schema.get("description") or field_schema.get("title")),
                    }
                )
                if field_type == "object":
                    rows.extend(self._extract_schema_fields(field_schema, prefix=full_name))
                elif field_type == "array<object>":
                    rows.extend(self._extract_schema_fields(self._array_item_schema(field_schema), prefix=full_name + "[]"))
            return rows
        if schema_type == "array<object>":
            return self._extract_schema_fields(self._array_item_schema(schema), prefix=prefix + "[]")
        return [
            {
                "name": prefix,
                "type": schema_type,
                "required": False,
                "desc": self._trim(schema.get("description") or schema.get("title")),
            }
        ]

    def _array_item_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        if self._trim(schema.get("type")) == "array":
            item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
            return item_schema
        return schema

    def _schema_type(self, schema: Dict[str, Any]) -> str:
        raw_type = schema.get("type")
        if isinstance(raw_type, list):
            normalized = [self._trim(item) for item in raw_type if self._trim(item)]
            raw = "|".join(normalized)
        else:
            raw = self._trim(raw_type)
        if raw == "array":
            item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
            item_type = self._trim(item_schema.get("type"))
            if item_type == "object":
                return "array<object>"
            if item_type:
                return f"array<{item_type}>"
        if raw:
            return raw
        if "const" in schema:
            return "const"
        return "unknown"

    @staticmethod
    def _schema_enum(schema: Dict[str, Any]) -> List[str]:
        values = schema.get("enum")
        if not isinstance(values, list):
            return []
        return [str(item) for item in values if item is not None and str(item).strip()]

    def _format_field_constraints(self, field: Dict[str, Any]) -> str:
        parts: List[str] = []
        enum_values = [self._trim(item) for item in (field.get("enum") or []) if self._trim(item)]
        if enum_values:
            parts.append("allowed: " + ", ".join(enum_values[:8]))
        if field.get("default") not in {None, ""}:
            parts.append(f"default: {field.get('default')}")
        minimum = field.get("minimum")
        maximum = field.get("maximum")
        if minimum is not None or maximum is not None:
            bounds: List[str] = []
            if minimum is not None:
                bounds.append(f">={minimum}")
            if maximum is not None:
                bounds.append(f"<={maximum}")
            parts.append("range: " + " ".join(bounds))
        return "；".join(parts)

    def _extract_planner_explanation(self, planner_text: Any) -> str:
        text = str(planner_text or "").strip()
        if not text:
            return ""
        marker = text.find("{")
        if marker < 0:
            return text
        return text[:marker].strip()

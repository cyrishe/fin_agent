from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.services.capability_search_service import CapabilitySearchService
from src.services.display_contract_compiler import DisplayContractCompiler
from src.services.planner_question_contract_service import PlannerQuestionContractService
from src.services.quant_research_authoring_affordance_service import QuantResearchAuthoringAffordanceService
from src.services.tool_argument_compiler_service import ToolArgumentCompilerService
from src.services.tool_candidate_rerank_service import ToolCandidateRerankService
from src.skill_runtime.tool_argument_planner import ToolArgumentPlanner
from src.tools.registry import canonicalize_tool_name


class AgentRuntimePlanner:
    def __init__(
        self,
        *,
        capability_search_service: Optional[CapabilitySearchService] = None,
        display_contract_compiler: Optional[DisplayContractCompiler] = None,
        tool_argument_planner: Optional[ToolArgumentPlanner] = None,
        tool_argument_compiler_service: Optional[ToolArgumentCompilerService] = None,
        tool_candidate_rerank_service: Optional[ToolCandidateRerankService] = None,
        planner_question_contract_service: Optional[PlannerQuestionContractService] = None,
        quant_research_authoring_affordance_service: Optional[QuantResearchAuthoringAffordanceService] = None,
    ) -> None:
        self.capability_search_service = capability_search_service or CapabilitySearchService()
        self.display_contract_compiler = display_contract_compiler or DisplayContractCompiler()
        self.tool_argument_planner = tool_argument_planner or ToolArgumentPlanner()
        self.tool_argument_compiler_service = tool_argument_compiler_service or ToolArgumentCompilerService()
        self.tool_candidate_rerank_service = tool_candidate_rerank_service or ToolCandidateRerankService()
        self.planner_question_contract_service = planner_question_contract_service or PlannerQuestionContractService()
        self.quant_research_authoring_affordance_service = (
            quant_research_authoring_affordance_service or QuantResearchAuthoringAffordanceService()
        )

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> bool:
        normalized = str(text or "").strip().lower()
        return any(str(keyword or "").strip().lower() in normalized for keyword in keywords)

    def build_business_dialog_plan(
        self,
        *,
        text: str,
        recall_query: str = "",
        tool_queries: Optional[List[str]] = None,
        work_context: Optional[Dict[str, Any]] = None,
        application_context: Optional[Dict[str, Any]] = None,
        enable_llm: bool = True,
    ) -> Dict[str, Any]:
        objective = self._trim(text)
        retrieval_query = self._trim(recall_query) or objective
        ctx = work_context if isinstance(work_context, dict) else {}
        app_ctx = application_context if isinstance(application_context, dict) else {}
        planner_agent = self._trim(
            (app_ctx.get("default_agent") or {}).get("agent_name")
            if isinstance(app_ctx.get("default_agent"), dict)
            else ctx.get("default_agent")
        ) or "default_agent"
        capability_result = self.capability_search_service.find_for_agent_runtime(
            query=retrieval_query,
            tool_queries=[self._trim(item) for item in (tool_queries or []) if self._trim(item)],
            work_context=ctx,
            application_context=app_ctx,
            tool_top_k=8,
        )
        capability_result = self._apply_tool_rerank(
            capability_result=capability_result,
            objective=objective,
            enable_llm=enable_llm,
        )
        candidate_skills = capability_result.get("skills") if isinstance(capability_result.get("skills"), list) else []
        candidate_tools = capability_result.get("tools") if isinstance(capability_result.get("tools"), list) else []
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
        selected_path = self._select_path(
            text=objective,
            work_context=ctx,
            candidate_skills=candidate_skills,
            candidate_tools=candidate_tools,
        )
        selected_tool_names = [
            str(item.get("tool_name") or "").strip()
            for item in candidate_tools[:4]
            if str(item.get("tool_name") or "").strip()
        ]
        return {
            "planner_type": "agent_runtime_planner",
            "planner_scope": "agent_local",
            "planner_agent": planner_agent,
            "objective": objective,
            "domain": "business_dialog",
            "candidate_skills": candidate_skills[:5],
            "candidate_tools": candidate_tools[:6],
            "candidate_quant_capabilities": analysis_affordances["quant_research_capabilities"],
            "planner_question_contract": planner_question_contract,
            "question_contract": planner_question_contract,
            "selected_path": selected_path,
            "work_items": self._build_work_items(
                selected_path,
                selected_tool_names,
                user_text=objective,
                planner_tools=capability_result.get("planner_tools") if isinstance(capability_result.get("planner_tools"), list) else [],
                enable_llm=enable_llm,
            ),
            "evidence_state": {
                "selected_tools": selected_tool_names,
                "active_skill_name": self._trim(
                    ctx.get("thread_active_skill_canonical_name")
                    or ctx.get("thread_active_skill_name")
                    or ctx.get("active_skill_canonical_name")
                    or ctx.get("active_skill_name")
                ),
                "analysis_affordances": analysis_affordances,
                "planner_question_contract": planner_question_contract,
                "question_contract": planner_question_contract,
            },
            "presentation_plan": self._build_presentation_plan(
                text=objective,
                selected_tools=selected_tool_names,
                selected_path=selected_path,
                analysis_affordances=analysis_affordances,
            ),
            "revision_count": 0,
            "status": "planned",
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

    def build_agent_route_plan(
        self,
        *,
        target_agent: str,
        reason: str,
    ) -> Dict[str, Any]:
        normalized_target = self._trim(target_agent) or "default_assistant"
        return {
            "planner_type": "agent_runtime_planner",
            "planner_scope": "agent_local",
            "planner_agent": normalized_target,
            "objective": "",
            "domain": "business_dialog",
            "candidate_skills": [],
            "candidate_tools": [],
            "candidate_quant_capabilities": [],
            "selected_path": {
                "type": "agent_route",
                "target": {"type": "agent", "name": normalized_target},
                "reason": reason,
            },
            "work_items": [
                {
                    "step_id": "step_1",
                    "depends_on": [],
                    "type": "agent_route",
                    "name": normalized_target,
                    "status": "planned",
                }
            ],
            "evidence_state": {},
            "presentation_plan": {
                "layout": "chat",
                "page_type": "chat_result",
                "preferred_block_types": ["structured_text"],
                "sections": [],
                "display_contract": {},
            },
            "revision_count": 0,
            "status": "planned",
        }

    def _select_path(
        self,
        *,
        text: str,
        work_context: Dict[str, Any],
        candidate_skills: List[Dict[str, Any]],
        candidate_tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        active_skill = self._trim(
            work_context.get("thread_active_skill_canonical_name")
            or work_context.get("thread_active_skill_name")
            or work_context.get("active_skill_canonical_name")
            or work_context.get("active_skill_name")
        )
        if active_skill and self._looks_like_active_skill_run_request(text):
            return {
                "type": "skill_run",
                "target": {"type": "skill", "name": active_skill},
                "reason": "reuse_active_skill",
            }
        top_skill = candidate_skills[0] if candidate_skills else {}
        top_tool = candidate_tools[0] if candidate_tools else {}
        if top_skill and self._looks_like_deliverable_request(text):
            return {
                "type": "skill_run",
                "target": {"type": "skill", "name": self._trim(top_skill.get("skill_name"))},
                "reason": "best_matching_skill",
            }
        if top_tool:
            return {
                "type": "tool_plan_run",
                "target": {"type": "tool_group", "name": "direct_tools"},
                "reason": "best_matching_tools",
            }
        return {
            "type": "agent_route",
            "target": {"type": "agent", "name": ""},
            "reason": "insufficient_direct_match",
        }

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

    def _build_presentation_plan(
        self,
        *,
        text: str,
        selected_tools: List[str],
        selected_path: Dict[str, Any],
        analysis_affordances: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        display_contract = self.display_contract_compiler.compile(
            selected_tools=selected_tools,
            requirement_text=text,
            skill_name=self._trim(((selected_path.get("target") or {}).get("name"))),
        )
        normalized = self._trim(text)
        preferred_blocks: List[str] = []
        if self._contains_any(normalized, ["表格", "排名", "榜单"]):
            preferred_blocks.append("table")
        if self._contains_any(normalized, ["曲线", "趋势", "均线", "走势图"]):
            preferred_blocks.append("line")
        if self._contains_any(normalized, ["报告", "总结", "分析"]):
            preferred_blocks.append("structured_text")
        return {
            "layout": "report" if self._contains_any(normalized, ["报告", "总结", "分析"]) else "chat",
            "page_type": self._trim(display_contract.get("page_type")) or "analysis_result",
            "preferred_block_types": preferred_blocks + [
                item for item in (display_contract.get("preferred_block_types") or [])
                if item not in preferred_blocks
            ],
            "sections": display_contract.get("sections") if isinstance(display_contract.get("sections"), list) else [],
            "display_contract": display_contract,
            "analysis_affordances": analysis_affordances if isinstance(analysis_affordances, dict) else {},
        }

    def _build_work_items(
        self,
        selected_path: Dict[str, Any],
        selected_tools: List[str],
        *,
        user_text: str = "",
        planner_tools: Optional[List[Dict[str, Any]]] = None,
        enable_llm: bool = True,
    ) -> List[Dict[str, Any]]:
        path_type = self._trim(selected_path.get("type"))
        target = selected_path.get("target") if isinstance(selected_path.get("target"), dict) else {}
        if path_type == "skill_run":
            return [
                {"step_id": "step_1", "depends_on": [], "type": "skill", "name": self._trim(target.get("name")), "status": "planned"},
                {"step_id": "step_2", "depends_on": ["step_1"], "type": "synthesis", "name": "final_synthesis", "status": "planned"},
                {"step_id": "step_3", "depends_on": ["step_2"], "type": "presentation", "name": "presentation_plan", "status": "planned"},
            ]
        if path_type == "tool_plan_run":
            items = self._build_tool_work_items(
                selected_tools,
                user_text=user_text,
                planner_tools=planner_tools or [],
                enable_llm=enable_llm,
            )
            items.extend(
                [
                    {
                        "step_id": f"step_{len(items) + 1}",
                        "depends_on": [str(item.get("step_id")) for item in items if self._trim(item.get("step_id"))],
                        "type": "synthesis",
                        "name": "final_synthesis",
                        "status": "planned",
                    },
                    {
                        "step_id": f"step_{len(items) + 2}",
                        "depends_on": [f"step_{len(items) + 1}"],
                        "type": "presentation",
                        "name": "presentation_plan",
                        "status": "planned",
                    },
                ]
            )
            return items
        return [{"step_id": "step_1", "depends_on": [], "type": "agent_route", "name": self._trim(target.get("name")) or "default_agent", "status": "planned"}]

    def _build_tool_work_items(
        self,
        selected_tools: List[str],
        *,
        user_text: str = "",
        planner_tools: Optional[List[Dict[str, Any]]] = None,
        enable_llm: bool = True,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        planner_tool_map = {
            canonicalize_tool_name(str(item.get("tool_name") or "").strip()): item
            for item in (planner_tools or [])
            if isinstance(item, dict) and self._trim(item.get("tool_name"))
        }
        producer_step_id = ""
        dependent_tools = {
            "financial_news_search",
            "stock_quote",
            "stock_realtime_quote",
            "stock_history_kline",
            "stock_intraday_kline",
            "stock_funds",
            "stock_realtime_funds_flow",
            "stock_history_funds_flow",
            "stock_industry_funds_flow",
            "equity_research_search",
        }
        producer_tools = {
            "个股动量排名",
            "实时个股动量排名",
            "get_hot_industries_and_leaders",
            "get_hot_sectors_and_leaders",
            "get_hot_concepts_and_leaders",
            "实时行情排名查询",
        }
        argument_plans = {
            canonicalize_tool_name(str(tool_name or "").strip()): self.tool_argument_planner.build_plan(
                tool_name=str(tool_name or "").strip(),
                user_text=user_text,
                context={},
            )
            for tool_name in selected_tools
            if self._trim(tool_name)
        }
        for index, tool_name in enumerate(selected_tools, start=1):
            step_id = f"step_{index}"
            normalized_tool = canonicalize_tool_name(self._trim(tool_name))
            depends_on: List[str] = []
            input_binding: Dict[str, str] = {}
            arguments: Dict[str, Any] = {}
            output_binding: Dict[str, str] = self._default_output_binding(normalized_tool)
            argument_plan = argument_plans.get(normalized_tool) if isinstance(argument_plans.get(normalized_tool), dict) else {}
            if argument_plan.get("status") == "ready":
                arguments = (
                    argument_plan.get("arguments")
                    if isinstance(argument_plan.get("arguments"), dict)
                    else {}
                )
            if producer_step_id and normalized_tool in dependent_tools:
                depends_on = [producer_step_id]
                input_binding = {
                    "code": "$top1_code",
                    "name": "$top1_name",
                    "query": "$top1_name",
                }
            if enable_llm:
                compiled_plan = self.tool_argument_compiler_service.compile_arguments(
                    tool_name=normalized_tool,
                    user_objective=user_text,
                    current_arguments=arguments,
                    current_input_binding=input_binding,
                    planner_tool=planner_tool_map.get(normalized_tool) if isinstance(planner_tool_map.get(normalized_tool), dict) else {},
                    enable_llm=True,
                )
                compiled_arguments = compiled_plan.get("arguments") if isinstance(compiled_plan.get("arguments"), dict) else {}
                if compiled_arguments:
                    arguments = {**compiled_arguments, **arguments}
            if normalized_tool in producer_tools:
                producer_step_id = step_id
            item = {
                "step_id": step_id,
                "depends_on": depends_on,
                "type": "tool",
                "name": normalized_tool,
                "status": "planned",
                "input_binding": input_binding,
                "output_binding": output_binding,
            }
            if arguments:
                item["arguments"] = arguments
            items.append(item)
        return items

    def _default_output_binding(self, tool_name: str) -> Dict[str, str]:
        normalized_tool = self._trim(tool_name)
        if normalized_tool in {"个股动量排名", "实时个股动量排名"}:
            return {
                "top1_code": "$result.data.0.items.0.stock_code",
                "top1_name": "$result.data.0.items.0.stock_name",
                "top1_symbol": "$result.data.0.items.0",
                "producer_tool_name": normalized_tool,
            }
        if normalized_tool in {"get_hot_industries_and_leaders", "get_hot_sectors_and_leaders", "get_hot_concepts_and_leaders"}:
            return {
                "top1_code": "$result.data.items.0.leaders.0.code",
                "top1_name": "$result.data.items.0.leaders.0.name",
                "top1_symbol": "$result.data.items.0.leaders.0",
                "producer_tool_name": normalized_tool,
            }
        if normalized_tool == "实时行情排名查询":
            return {
                "top1_code": "$result.data.0.stock_code",
                "top1_name": "$result.data.0.stock_name",
                "top1_symbol": "$result.data.0",
                "producer_tool_name": normalized_tool,
            }
        return {}

    def _looks_like_run_request(self, text: str) -> bool:
        normalized = self._trim(text)
        return self._contains_any(normalized, ["生成", "报告", "总结", "分析", "跑一下", "执行一下", "给我一份", "返回"])

    def _looks_like_deliverable_request(self, text: str) -> bool:
        normalized = self._trim(text)
        return self._contains_any(normalized, ["生成", "报告", "总结", "整理", "给我", "来一份", "返回", "输出"])

    def _looks_like_active_skill_run_request(self, text: str) -> bool:
        normalized = self._trim(text)
        if not self._contains_any(normalized, ["运行", "执行", "跑一下", "run", "用", "基于", "当前这个", "刚才那个"]):
            return False
        return self._contains_any(normalized, ["skill", "技能", "当前这个", "刚才那个", "当前", "这个"])


# Backward-compatible alias while callers migrate to the clearer runtime-planner name.
ExecutionPlanService = AgentRuntimePlanner

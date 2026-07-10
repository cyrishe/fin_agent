from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.services.deep_thinking_assessment_service import DeepThinkingAssessmentService
from src.services.deep_thinking_execution_preview_service import DeepThinkingExecutionPreviewService
from src.services.deep_thinking_refiner_service import DeepThinkingRefinerService
from src.utils.ai_service import chat_qwen_json


class DeepThinkingPlannerService:
    DEFAULT_BUDGET = {
        "max_rounds": 4,
        "max_tasks": 12,
    }

    def __init__(
        self,
        *,
        assessment_service: Optional[DeepThinkingAssessmentService] = None,
        refiner_service: Optional[DeepThinkingRefinerService] = None,
        execution_preview_service: Optional[DeepThinkingExecutionPreviewService] = None,
    ) -> None:
        self.registry = get_prompt_registry()
        self.assessment_service = assessment_service or DeepThinkingAssessmentService()
        self.refiner_service = refiner_service or DeepThinkingRefinerService()
        self.execution_preview_service = execution_preview_service or DeepThinkingExecutionPreviewService()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> bool:
        normalized = str(text or "").strip().lower()
        return any(str(keyword or "").strip().lower() in normalized for keyword in keywords)

    def build_initial_plan(
        self,
        *,
        user_objective: str,
        capability_result: Optional[Dict[str, Any]] = None,
        work_context: Optional[Dict[str, Any]] = None,
        enable_llm_assessment: bool = False,
    ) -> Dict[str, Any]:
        objective = self._trim(user_objective)
        capability = capability_result if isinstance(capability_result, dict) else {}
        ctx = work_context if isinstance(work_context, dict) else {}
        candidate_tools = capability.get("planner_tools") if isinstance(capability.get("planner_tools"), list) else []
        candidate_skills = capability.get("planner_skills") if isinstance(capability.get("planner_skills"), list) else []
        llm_plan = self._build_with_llm(
            objective=objective,
            candidate_tools=candidate_tools,
            candidate_skills=candidate_skills,
            work_context=ctx,
            enable_llm=enable_llm_assessment,
        )
        question_frame = llm_plan.get("question_frame") if isinstance(llm_plan.get("question_frame"), dict) else self._build_question_frame(objective)
        lanes = llm_plan.get("investigation_lanes") if isinstance(llm_plan.get("investigation_lanes"), list) and llm_plan.get("investigation_lanes") else self._build_lanes(
            objective=objective,
            candidate_tools=candidate_tools,
            candidate_skills=candidate_skills,
        )
        active_skill_name = self._trim(
            ctx.get("thread_active_skill_canonical_name")
            or ctx.get("thread_active_skill_name")
            or ctx.get("active_skill_canonical_name")
            or ctx.get("active_skill_name")
        )
        if active_skill_name:
            question_frame["active_skill_name"] = active_skill_name

        plan = {
            "plan_version": "deep_plan_v2_preview",
            "thinking_mode": "deep_thinking",
            "planner_type": "deep_thinking_planner",
            "objective": objective,
            "question_frame": question_frame,
            "investigation_lanes": lanes,
            "evidence_state": {
                "findings": [],
                "open_questions": [],
                "insufficient_lanes": [self._trim(item.get("lane_id")) for item in lanes if self._trim(item.get("lane_id"))],
            },
            "budget": {
                **self.DEFAULT_BUDGET,
                "used_rounds": 0,
                "used_tasks": 0,
            },
            "stop_conditions": [
                "evidence_sufficient",
                "max_rounds_reached",
                "max_tasks_reached",
                "low_marginal_gain",
            ],
            "selected_skills_hint": [
                self._trim(item.get("skill_name"))
                for item in candidate_skills[:3]
                if self._trim(item.get("skill_name"))
            ],
            "selected_tools_hint": [
                self._trim(item.get("tool_name"))
                for item in candidate_tools[:5]
                if self._trim(item.get("tool_name"))
            ],
            "planner_source": self._trim(llm_plan.get("planner_source")) or ("llm_deep_planner" if enable_llm_assessment else "fallback_deep_planner"),
        }
        plan["assessment"] = self.assessment_service.assess(
            user_objective=objective,
            deep_plan=plan,
            evidence_state=plan.get("evidence_state"),
            completed_items=[],
            enable_llm=enable_llm_assessment,
        )
        plan["refine_patch"] = self.refiner_service.build_refine_patch(
            user_objective=objective,
            deep_plan=plan,
            assessment=plan.get("assessment"),
            evidence_state=plan.get("evidence_state"),
            enable_llm=enable_llm_assessment,
        )
        plan["round_1_execution_preview"] = self.execution_preview_service.build_round_preview(
            user_objective=objective,
            deep_plan=plan,
            refine_patch=plan.get("refine_patch"),
            enable_llm=enable_llm_assessment,
        )
        return plan

    def _build_with_llm(
        self,
        *,
        objective: str,
        candidate_tools: List[Dict[str, Any]],
        candidate_skills: List[Dict[str, Any]],
        work_context: Dict[str, Any],
        enable_llm: bool,
    ) -> Dict[str, Any]:
        if not enable_llm or not objective:
            return {}
        try:
            messages = self.registry.render_messages(
                "system.agent_runtime.deep_planner",
                {
                    "user_objective": objective,
                    "candidate_tools": candidate_tools,
                    "candidate_skills": candidate_skills,
                    "work_context": work_context,
                },
            )
            payload, _usage = chat_qwen_json(messages, enable_think=False)
            if not isinstance(payload, dict):
                return {}
            return self._normalize_llm_plan(payload, candidate_tools=candidate_tools)
        except Exception:
            return {}

    def _normalize_llm_plan(
        self,
        payload: Dict[str, Any],
        *,
        candidate_tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        question_frame = payload.get("question_frame") if isinstance(payload.get("question_frame"), dict) else {}
        lanes = payload.get("investigation_lanes") if isinstance(payload.get("investigation_lanes"), list) else []
        allowed_tool_names = {
            self._trim(item.get("tool_name"))
            for item in candidate_tools
            if self._trim(item.get("tool_name"))
        }
        normalized_lanes: List[Dict[str, Any]] = []
        for index, lane in enumerate(lanes[:5], start=1):
            if not isinstance(lane, dict):
                continue
            lane_id = self._trim(lane.get("lane_id")) or f"lane_{index}"
            name = self._trim(lane.get("name"))
            why_relevant = self._trim(lane.get("why_relevant"))
            if not name or not why_relevant:
                continue
            tasks = lane.get("tasks") if isinstance(lane.get("tasks"), list) else []
            normalized_tasks: List[Dict[str, Any]] = []
            for task_index, task in enumerate(tasks[:2], start=1):
                if not isinstance(task, dict):
                    continue
                goal = self._trim(task.get("goal"))
                if not goal:
                    continue
                preferred_tools = [
                    tool_name
                    for tool_name in [self._trim(item) for item in (task.get("preferred_tools") or [])]
                    if tool_name and tool_name in allowed_tool_names
                ][:4]
                normalized_tasks.append(
                    {
                        "task_id": self._trim(task.get("task_id")) or f"{lane_id}_task_{task_index}",
                        "goal": goal,
                        "preferred_tools": preferred_tools,
                        "status": self._trim(task.get("status")) or "planned",
                    }
                )
            if not normalized_tasks:
                normalized_tasks.append(
                    {
                        "task_id": f"{lane_id}_task_1",
                        "goal": why_relevant,
                        "preferred_tools": [],
                        "status": "planned",
                    }
                )
            normalized_lanes.append(
                {
                    "lane_id": lane_id,
                    "name": name,
                    "why_relevant": why_relevant,
                    "priority": int(lane.get("priority", 80) or 80),
                    "status": self._trim(lane.get("status")) or "planned",
                    "tasks": normalized_tasks,
                }
            )
        return {
            "planner_source": "llm_deep_planner",
            "question_frame": {
                "target": self._trim(question_frame.get("target")) or self._trim(payload.get("objective")),
                "time_scope": self._trim(question_frame.get("time_scope")) or "implicit_current_or_query_defined",
                "output_goal": self._trim(question_frame.get("output_goal")) or "给出结论、证据链和不确定性说明",
                "analysis_mode": self._trim(question_frame.get("analysis_mode")) or "open_research",
            },
            "investigation_lanes": normalized_lanes,
        }

    def _build_question_frame(self, objective: str) -> Dict[str, Any]:
        analysis_mode = "open_research"
        if self._contains_any(objective, ["为什么", "为何", "原因", "归因", "驱动", "催化"]):
            analysis_mode = "causal_analysis"
        elif self._contains_any(objective, ["比较", "对比", "重叠", "交集", "区别"]):
            analysis_mode = "comparison"
        elif self._contains_any(objective, ["筛选", "找出", "选出"]) and self._contains_any(objective, ["分析", "解释", "归因", "判断"]):
            analysis_mode = "screen_then_explain"
        return {
            "target": objective,
            "time_scope": "implicit_current_or_query_defined",
            "output_goal": "给出结论、证据链和不确定性说明",
            "analysis_mode": analysis_mode,
        }

    def _build_lanes(
        self,
        *,
        objective: str,
        candidate_tools: List[Dict[str, Any]],
        candidate_skills: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        lanes: List[Dict[str, Any]] = []

        def add_lane(lane_id: str, name: str, why_relevant: str, preferred_tools: List[str], priority: int) -> None:
            if any(self._trim(item.get("lane_id")) == lane_id for item in lanes):
                return
            lanes.append(
                {
                    "lane_id": lane_id,
                    "name": name,
                    "why_relevant": why_relevant,
                    "priority": priority,
                    "status": "planned",
                    "tasks": [
                        {
                            "task_id": f"{lane_id}_task_1",
                            "goal": why_relevant,
                            "preferred_tools": preferred_tools,
                            "status": "planned",
                        }
                    ],
                }
            )

        if self._contains_any(objective, ["大盘", "市场", "下跌", "上涨", "盘面", "板块", "龙头"]):
            add_lane(
                "lane_market_structure",
                "盘面结构线",
                "识别指数、板块、权重股与市场广度是否构成主要拖累或共振。",
                self._pick_tools(candidate_tools, ["market_realtime_breadth", "market_history_amount", "market_minute_amount_series", "个股动量排名", "get_hot_sectors_and_leaders"]),
                95,
            )
            add_lane(
                "lane_news_catalyst",
                "新闻催化线",
                "检查近期或当日市场新闻、事件催化与盘面表现是否方向一致。",
                self._pick_tools(candidate_tools, ["financial_news_search"]),
                90,
            )
            add_lane(
                "lane_funds",
                "资金行为线",
                "检查主力资金、板块资金和风险偏好是否出现集中撤退或切换。",
                self._pick_tools(candidate_tools, ["stock_realtime_funds_flow", "stock_history_funds_flow", "stock_industry_funds_flow", "大盘情绪指标"]),
                88,
            )

        if self._contains_any(objective, ["宏观", "监管", "政策", "财报", "业绩", "估值"]):
            add_lane(
                "lane_macro_policy",
                "宏观政策线",
                "检查宏观政策、监管或海外事件是否构成上层驱动。",
                self._pick_tools(candidate_tools, ["financial_news_search"]),
                86,
            )
            add_lane(
                "lane_fundamental",
                "基本面时间窗线",
                "检查财报季、业绩预告、估值切换是否解释当前异动。",
                self._pick_tools(candidate_tools, ["equity_research_search", "financial_news_search"]),
                84,
            )

        if self._contains_any(objective, ["比较", "对比", "重叠", "交集", "核心标的"]):
            add_lane(
                "lane_cross_theme_compare",
                "交叉比较线",
                "比较不同主题、行业或板块的代表标的，识别交集与核心资产。",
                self._pick_tools(candidate_tools, ["get_hot_industries_and_leaders", "get_hot_sectors_and_leaders", "get_hot_concepts_and_leaders"]),
                92,
            )

        if self._contains_any(objective, ["公司", "个股", "工业富联", "画像", "属于哪些", "概念"]):
            add_lane(
                "lane_company_profile",
                "公司画像线",
                "先建立公司所属概念和相关事件的初始画像，再判断是否需要深挖。",
                self._pick_tools(candidate_tools, ["get_company_taxonomy_profile", "financial_news_search", "stock_realtime_quote", "stock_history_kline"]),
                90,
            )

        if not lanes:
            add_lane(
                "lane_general_research",
                "通用调查线",
                "先收集最相关的一手或二手信息，建立初步证据框架。",
                self._pick_tools(candidate_tools, [self._trim(candidate_tools[0].get("tool_name"))] if candidate_tools else []),
                80,
            )

        lanes.sort(key=lambda item: -int(item.get("priority", 0) or 0))
        return lanes[:5]

    def _pick_tools(self, candidate_tools: List[Dict[str, Any]], preferred_names: List[str]) -> List[str]:
        rows: List[str] = []
        normalized_candidates = [
            self._trim(item.get("tool_name"))
            for item in candidate_tools
            if self._trim(item.get("tool_name"))
        ]
        for name in preferred_names:
            normalized = self._trim(name)
            if normalized and normalized in normalized_candidates and normalized not in rows:
                rows.append(normalized)
        if rows:
            return rows[:4]
        return normalized_candidates[:3]

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional


class PlannerQuestionContractService:
    """Builds a small, read-only contract for planner task lanes.

    The contract is an affordance layer, not a router. A user request may expose
    multiple lanes, and downstream planners may still choose a simple answer,
    a DAG, a skill, or code execution based on available capabilities.
    """

    VERSION = "planner_question_contract.v1"
    LANES = ("direct_query", "deep_analysis", "skill_lifecycle", "code_execution")

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> bool:
        normalized = str(text or "").strip().lower()
        return any(str(keyword or "").strip().lower() in normalized for keyword in keywords)

    def build_contract(
        self,
        *,
        objective: str,
        work_context: Optional[Mapping[str, Any]] = None,
        capability_result: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        text = self._trim(objective)
        ctx = work_context if isinstance(work_context, Mapping) else {}
        capability = capability_result if isinstance(capability_result, Mapping) else {}

        lane_items = self._build_lanes(text=text, work_context=ctx, capability_result=capability)
        primary_lane = next((item["lane"] for item in lane_items if item.get("priority") == "primary"), "direct_query")
        permissions = self._execution_permissions(lane_items)
        return {
            "version": self.VERSION,
            "primary_lane": primary_lane,
            "lanes": lane_items,
            "minimum_viable_answer": self._minimum_viable_answer(text=text, primary_lane=primary_lane),
            "execution_affordance": self._execution_affordance(lane_items),
            "upgrade_options": self._upgrade_options(lane_items),
            "hard_constraints": {
                "writes_require_confirmation": True,
                "publishing_requires_verification": True,
                "published_quant_capabilities_read_only_in_planner": True,
                "no_direct_quant_execution_without_confirmation": True,
            },
            "execution_permissions": permissions,
            "clarification_policy": self._clarification_policy(lane_items),
            "source": "derived_planner_question_contract",
        }

    def _build_lanes(
        self,
        *,
        text: str,
        work_context: Mapping[str, Any],
        capability_result: Mapping[str, Any],
    ) -> List[Dict[str, Any]]:
        signals = self._signals(text=text, work_context=work_context, capability_result=capability_result)
        lanes = [
            self._lane(
                lane="direct_query",
                priority="primary" if not any(signals[key] for key in ("skill_lifecycle", "deep_analysis", "code_execution")) else "candidate",
                reason_code="default_direct_lane" if text else "missing_objective",
                allowed_plan_types=["tool_plan_run", "planned_run"],
                requires_confirmation=False,
            )
        ]
        if signals["deep_analysis"]:
            lanes.append(
                self._lane(
                    lane="deep_analysis",
                    priority="primary" if not signals["skill_lifecycle"] else "candidate",
                    reason_code=signals["deep_analysis_reason"],
                    allowed_plan_types=["planned_run", "hybrid", "skill_run"],
                    requires_confirmation=False,
                )
            )
        if signals["skill_lifecycle"]:
            lanes.append(
                self._lane(
                    lane="skill_lifecycle",
                    priority="primary",
                    reason_code=signals["skill_lifecycle_reason"],
                    allowed_plan_types=["hybrid", "skill_run", "planned_run"],
                    requires_confirmation=True,
                )
            )
        if signals["code_execution"]:
            lanes.append(
                self._lane(
                    lane="code_execution",
                    priority="candidate",
                    reason_code=signals["code_execution_reason"],
                    allowed_plan_types=["planned_run", "hybrid"],
                    requires_confirmation=False,
                )
            )
        return lanes

    def _signals(
        self,
        *,
        text: str,
        work_context: Mapping[str, Any],
        capability_result: Mapping[str, Any],
    ) -> Dict[str, Any]:
        preferred_thinking = self._trim(work_context.get("preferred_thinking_mode"))
        preferred_task_mode = self._trim(work_context.get("preferred_task_mode"))
        has_quant_candidates = bool(capability_result.get("planner_quant_capabilities"))
        skill_lifecycle = bool(work_context.get("skill_authoring_hint")) or self._contains_any(
            text,
            ["skill", "保存为", "发布为", "创建", "新建", "固化", "复用", "验证并保存"],
        )
        code_execution = bool(work_context.get("code_runtime_hint")) or self._contains_any(
            text,
            ["python", "代码", "计算", "聚合", "回测", "sandbox", "机器学习"],
        )
        deep_analysis = (
            preferred_thinking == "deep"
            or preferred_task_mode == "deep"
            or has_quant_candidates
            or self._contains_any(text, ["分析", "比较", "成长性", "评分", "原因", "观点", "综合"])
        )
        return {
            "deep_analysis": deep_analysis,
            "deep_analysis_reason": (
                "explicit_deep_context"
                if preferred_thinking == "deep" or preferred_task_mode == "deep"
                else "published_capability_available"
                if has_quant_candidates
                else "analysis_language_detected"
            ),
            "skill_lifecycle": skill_lifecycle,
            "skill_lifecycle_reason": (
                "explicit_skill_authoring_context"
                if work_context.get("skill_authoring_hint")
                else "artifact_lifecycle_language_detected"
            ),
            "code_execution": code_execution,
            "code_execution_reason": (
                "explicit_code_runtime_context"
                if work_context.get("code_runtime_hint")
                else "code_or_computation_language_detected"
            ),
        }

    def _lane(
        self,
        *,
        lane: str,
        priority: str,
        reason_code: str,
        allowed_plan_types: List[str],
        requires_confirmation: bool,
    ) -> Dict[str, Any]:
        return {
            "lane": lane,
            "priority": priority,
            "reason_code": reason_code,
            "allowed_plan_types": allowed_plan_types,
            "requires_confirmation": requires_confirmation,
        }

    def _execution_permissions(self, lanes: List[Dict[str, Any]]) -> Dict[str, bool]:
        lane_names = {self._trim(item.get("lane")) for item in lanes if isinstance(item, Mapping)}
        return {
            "may_call_tools": True,
            "may_plan_dag": True,
            "may_use_code": "code_execution" in lane_names,
            "may_create_draft": "skill_lifecycle" in lane_names,
            "may_publish": False,
            "may_execute_published_quant": False,
        }

    def _minimum_viable_answer(self, *, text: str, primary_lane: str) -> Dict[str, Any]:
        if not self._trim(text):
            return {
                "can_answer_now": False,
                "answer_mode": "clarify",
                "requires_clarification_before_answer": True,
            }
        if primary_lane == "skill_lifecycle":
            answer_mode = "draft_plan_with_confirmation"
        elif primary_lane == "deep_analysis":
            answer_mode = "analysis_outline_with_evidence"
        elif primary_lane == "code_execution":
            answer_mode = "calculation_plan_with_evidence"
        else:
            answer_mode = "brief_with_evidence"
        return {
            "can_answer_now": True,
            "answer_mode": answer_mode,
            "requires_clarification_before_answer": False,
        }

    def _execution_affordance(self, lanes: List[Dict[str, Any]]) -> Dict[str, Any]:
        lane_names = {self._trim(item.get("lane")) for item in lanes if isinstance(item, Mapping)}
        allowed_work_item_types = ["tool", "transform"]
        if "skill_lifecycle" in lane_names:
            allowed_work_item_types.append("skill")
        if "code_execution" in lane_names:
            allowed_work_item_types.append("code")
        side_effect_limit = "draft_only" if "skill_lifecycle" in lane_names else "read_only"
        requires_confirmation = any(bool(item.get("requires_confirmation")) for item in lanes if isinstance(item, Mapping))
        return {
            "allowed_work_item_types": allowed_work_item_types,
            "side_effect_limit": side_effect_limit,
            "requires_confirmation": requires_confirmation,
        }

    def _upgrade_options(self, lanes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for item in lanes:
            if not isinstance(item, Mapping):
                continue
            lane = self._trim(item.get("lane"))
            if not lane:
                continue
            result.append(
                {
                    "mode": lane,
                    "requires_confirmation": bool(item.get("requires_confirmation")),
                    "allowed_plan_types": [
                        self._trim(plan_type)
                        for plan_type in (item.get("allowed_plan_types") or [])
                        if self._trim(plan_type)
                    ],
                    "reason_code": self._trim(item.get("reason_code")),
                }
            )
        return result

    def _clarification_policy(self, lanes: List[Dict[str, Any]]) -> Dict[str, Any]:
        lane_names = {self._trim(item.get("lane")) for item in lanes if isinstance(item, Mapping)}
        ask_when = ["missing_entity", "missing_date_or_window"]
        if "deep_analysis" in lane_names:
            ask_when.append("ambiguous_business_concept")
        if "skill_lifecycle" in lane_names:
            ask_when.extend(["missing_factor_definition", "missing_validation_scope"])
        return {
            "max_questions": 3,
            "ask_when": ask_when,
        }

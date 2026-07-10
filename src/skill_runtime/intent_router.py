import re
from typing import Any, Dict, List

from src.skill_runtime.clarification import ClarificationBuilder
from src.skill_runtime.entity_resolver import EntityResolver
from src.skill_runtime.time_resolver import TimeResolver


class IntentRouter:
    """
    Minimal entrance-layer router.

    This layer decides:
    - whether a request should go to a single skill
    - whether it likely needs multiple skills
    - whether slots are missing and user clarification is preferable
    """

    STOCK_CODE_RE = re.compile(r"\b\d{6}\b")
    ROUTER_VERSION = "v1"

    def __init__(
        self,
        *,
        entity_resolver: EntityResolver | None = None,
        time_resolver: TimeResolver | None = None,
        clarification_builder: ClarificationBuilder | None = None,
    ) -> None:
        self.entity_resolver = entity_resolver or EntityResolver()
        self.time_resolver = time_resolver or TimeResolver()
        self.clarification_builder = clarification_builder or ClarificationBuilder()

    def route(
        self,
        *,
        user_text: str,
        context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        text = str(user_text or "").strip()
        context = context or {}
        lowered = text.lower()
        resolved_entity = self.entity_resolver.resolve(user_text=text, context=context)
        resolved_time = self.time_resolver.resolve(user_text=text, context=context)

        explicit_task_type = str(context.get("task_type") or "").strip()
        if explicit_task_type in {"stock_deep_dive", "hotspot_trace"}:
            return self._single_skill_route(
                skill_name=explicit_task_type,
                confidence=0.98,
                reason="context.task_type explicitly specifies the skill",
                normalized_input=self._build_normalized_input(
                    text,
                    context,
                    resolved_entity=resolved_entity,
                    resolved_time=resolved_time,
                ),
            )

        route = self._route_by_semantics(
            text=text,
            lowered=lowered,
            context=context,
            resolved_entity=resolved_entity,
            resolved_time=resolved_time,
        )
        return self._apply_skill_constraints(route=route, context=context)

    def build_route_snapshot(
        self,
        *,
        route: Dict[str, Any],
        user_text: str,
        context: Dict[str, Any] | None = None,
        source: str = "natural_language_preview",
    ) -> Dict[str, Any]:
        context = context or {}
        composite_plan = self.build_composite_execution_plan(route=route)
        route_object = route if isinstance(route, dict) else {}
        missing_slots = route_object.get("missing_slots") or []
        clarification_state = None
        if str(route_object.get("route_type") or "") == "clarify" and missing_slots:
            clarification_state = self.clarification_builder.build(
                missing_slots=missing_slots,
                context_summary={
                    "task_type": str(context.get("task_type") or "").strip(),
                    "code": str(context.get("code") or "").strip(),
                    "name": str(context.get("name") or context.get("company") or "").strip(),
                },
            )
        return {
            "snapshot_type": "route_snapshot",
            "source": str(source or "natural_language_preview").strip() or "natural_language_preview",
            "router_version": self.ROUTER_VERSION,
            "user_text": str(user_text or "").strip(),
            "context_summary": {
                "task_type": str(context.get("task_type") or "").strip(),
                "code": str(context.get("code") or "").strip(),
                "name": str(context.get("name") or context.get("company") or "").strip(),
            },
            "route": route,
            "composite_execution_plan": composite_plan,
            "clarification_state": clarification_state,
        }

    def build_explicit_skill_route_snapshot(
        self,
        *,
        skill_name: str,
        input_payload: Dict[str, Any],
        source: str = "explicit_skill",
    ) -> Dict[str, Any]:
        payload = input_payload if isinstance(input_payload, dict) else {}
        user_text = str(payload.get("question") or payload.get("user_text") or "").strip()
        route = self._single_skill_route(
            skill_name=str(skill_name or "").strip(),
            confidence=1.0,
            reason="task submission explicitly targets a specific skill",
            normalized_input=self._build_normalized_input(user_text, payload),
        )
        return self.build_route_snapshot(
            route=route,
            user_text=user_text,
            context=payload,
            source=source,
        )

    def build_composite_execution_plan(
        self,
        *,
        route: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        if str(route.get("route_type") or "") != "multi_skill":
            return None
        hint = route.get("composite_plan_hint") or {}
        plan_key = str(hint.get("plan_key") or "").strip()
        if plan_key != "hotspot_then_deep_dive":
            return None
        normalized_input = route.get("normalized_input") or {}
        return {
            "plan_type": "multi_skill",
            "summary": "先做热点归因与时间线追踪，再对代表标的做深度分析。",
            "steps": [
                {
                    "step_id": "trace_hotspot",
                    "skill_name": "hotspot_trace",
                    "goal": "识别热点驱动、时间线、催化和风险，产出代表标的候选。",
                    "depends_on": [],
                    "input_hint": {
                        "task_type": "hotspot_trace",
                        "question": normalized_input.get("question") or normalized_input.get("user_text") or "",
                    },
                },
                {
                    "step_id": "deep_dive_representative",
                    "skill_name": "stock_deep_dive",
                    "goal": "对热点中的代表标的做更完整的行情、资金、研报、新闻和风险深挖。",
                    "depends_on": ["trace_hotspot"],
                    "input_hint": {
                        "task_type": "stock_deep_dive",
                    },
                },
            ],
            "final_merge_strategy": "以热点追踪结果为主视图，深度分析结果作为代表标的附录或跳转入口。",
        }

    @staticmethod
    def _apply_skill_constraints(
        *,
        route: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        allowed_skills = [str(x).strip() for x in (context or {}).get("allowed_skills", []) if str(x).strip()]
        if not allowed_skills:
            return route

        allowed_set = set(allowed_skills)
        constrained = dict(route or {})
        candidate_skills = [str(x).strip() for x in constrained.get("candidate_skills", []) if str(x).strip()]
        filtered_candidates = [name for name in candidate_skills if name in allowed_set]
        selected_skill = str(constrained.get("selected_skill") or "").strip()
        route_type = str(constrained.get("route_type") or "").strip()

        constrained["candidate_skills"] = filtered_candidates

        if route_type == "single_skill":
            if selected_skill and selected_skill in allowed_set:
                return constrained
            return {
                "route_type": "clarify",
                "confidence": min(float(constrained.get("confidence") or 0.0), 0.5),
                "reason": "the detected skill is not allowed under current agent policy",
                "candidate_skills": filtered_candidates,
                "selected_skill": None,
                "missing_slots": ["task_focus"],
                "normalized_input": constrained.get("normalized_input") or {},
                "composite_plan_hint": None,
            }

        if route_type == "multi_skill":
            if len(filtered_candidates) >= 2:
                return constrained
            if len(filtered_candidates) == 1:
                return {
                    "route_type": "single_skill",
                    "confidence": min(float(constrained.get("confidence") or 0.0), 0.7),
                    "reason": "agent policy narrows the route to a single allowed skill",
                    "candidate_skills": filtered_candidates,
                    "selected_skill": filtered_candidates[0],
                    "missing_slots": [],
                    "normalized_input": constrained.get("normalized_input") or {},
                    "composite_plan_hint": None,
                }
            return {
                "route_type": "clarify",
                "confidence": min(float(constrained.get("confidence") or 0.0), 0.4),
                "reason": "none of the detected skills are allowed under current agent policy",
                "candidate_skills": [],
                "selected_skill": None,
                "missing_slots": ["task_focus"],
                "normalized_input": constrained.get("normalized_input") or {},
                "composite_plan_hint": None,
            }

        if route_type == "clarify":
            constrained["candidate_skills"] = filtered_candidates or allowed_skills
            return constrained

        return constrained

    def _route_by_semantics(
        self,
        *,
        text: str,
        lowered: str,
        context: Dict[str, Any],
        resolved_entity: Dict[str, Any],
        resolved_time: Dict[str, Any],
    ) -> Dict[str, Any]:
        has_stock_code = bool(resolved_entity.get("code"))
        stock_name = str(resolved_entity.get("name") or "").strip()
        has_stock_subject = has_stock_code or bool(stock_name)

        wants_hotspot = any(word in text for word in ["热点", "概念", "板块", "异动", "催化", "持续性", "时间线"])
        wants_deep_dive = any(word in text for word in ["深挖", "全面分析", "投顾", "风险角度", "资金面", "研报", "新闻和风险"])
        wants_representative = any(word in text for word in ["龙头", "代表股", "代表标的", "核心个股"])

        if wants_hotspot and (wants_representative or "先找热点再深挖" in text or "顺便深挖" in text):
            return {
                "route_type": "multi_skill",
                "confidence": 0.86,
                "reason": "the request mixes hotspot tracing with representative-stock deep dive",
                "candidate_skills": ["hotspot_trace", "stock_deep_dive"],
                "selected_skill": None,
                "missing_slots": [],
                "normalized_input": self._build_normalized_input(
                    text,
                    context,
                    resolved_entity=resolved_entity,
                    resolved_time=resolved_time,
                ),
                "composite_plan_hint": {
                    "plan_key": "hotspot_then_deep_dive",
                },
            }

        if wants_hotspot and not has_stock_subject:
            return self._single_skill_route(
                skill_name="hotspot_trace",
                confidence=0.9,
                reason="the request focuses on hotspot attribution, catalyst, timeline or concept/sector continuity",
                normalized_input=self._build_normalized_input(
                    text,
                    context,
                    resolved_entity=resolved_entity,
                    resolved_time=resolved_time,
                ),
            )

        if has_stock_subject or wants_deep_dive:
            missing_slots: List[str] = []
            normalized_input = self._build_normalized_input(
                text,
                context,
                resolved_entity=resolved_entity,
                resolved_time=resolved_time,
            )
            if not normalized_input.get("code") and not normalized_input.get("name"):
                missing_slots.append("code_or_name")
            route_type = "clarify" if missing_slots else "single_skill"
            return {
                "route_type": route_type,
                "confidence": 0.82 if not missing_slots else 0.62,
                "reason": "the request looks like a single-stock deep dive or stock-focused analysis",
                "candidate_skills": ["stock_deep_dive"],
                "selected_skill": "stock_deep_dive" if not missing_slots else None,
                "missing_slots": missing_slots,
                "normalized_input": normalized_input,
                "composite_plan_hint": None,
            }

        return {
            "route_type": "clarify",
            "confidence": 0.35,
            "reason": "insufficient intent signal to safely choose a skill",
            "candidate_skills": ["hotspot_trace", "stock_deep_dive"],
            "selected_skill": None,
            "missing_slots": ["task_focus"],
            "normalized_input": self._build_normalized_input(
                text,
                context,
                resolved_entity=resolved_entity,
                resolved_time=resolved_time,
            ),
            "composite_plan_hint": None,
        }

    @staticmethod
    def _single_skill_route(
        *,
        skill_name: str,
        confidence: float,
        reason: str,
        normalized_input: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "route_type": "single_skill",
            "confidence": confidence,
            "reason": reason,
            "candidate_skills": [skill_name],
            "selected_skill": skill_name,
            "missing_slots": [],
            "normalized_input": normalized_input,
            "composite_plan_hint": None,
        }

    @classmethod
    def _build_normalized_input(
        cls,
        text: str,
        context: Dict[str, Any],
        *,
        resolved_entity: Dict[str, Any] | None = None,
        resolved_time: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        resolved_entity = resolved_entity or {}
        resolved_time = resolved_time or {}
        normalized = {
            "user_text": text,
            "question": str(context.get("question") or text or "").strip(),
            "code": str(resolved_entity.get("code") or context.get("code") or "").strip(),
            "name": str(resolved_entity.get("name") or context.get("name") or context.get("company") or "").strip(),
            "concept": str(resolved_entity.get("concept") or context.get("concept") or "").strip(),
            "task_type": str(context.get("task_type") or "").strip(),
            "time_range": resolved_time,
        }
        if not normalized["code"]:
            match = cls.STOCK_CODE_RE.search(text)
            if match:
                normalized["code"] = match.group(0)
        return normalized

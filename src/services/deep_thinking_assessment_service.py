from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_json


class DeepThinkingAssessmentService:
    def __init__(self) -> None:
        self.registry = get_prompt_registry()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def assess(
        self,
        *,
        deep_plan: Optional[Dict[str, Any]] = None,
        evidence_state: Optional[Dict[str, Any]] = None,
        completed_items: Optional[List[Dict[str, Any]]] = None,
        user_objective: str = "",
        enable_llm: bool = True,
    ) -> Dict[str, Any]:
        plan = deep_plan if isinstance(deep_plan, dict) else {}
        evidence = evidence_state if isinstance(evidence_state, dict) else {}
        completed = completed_items if isinstance(completed_items, list) else []
        if enable_llm:
            llm_result = self._assess_with_llm(
                user_objective=user_objective,
                deep_plan=plan,
                evidence_state=evidence,
                completed_items=completed,
            )
            if llm_result:
                return llm_result
        return self._assess_fallback(plan=plan, evidence=evidence, completed=completed)

    def _assess_with_llm(
        self,
        *,
        user_objective: str,
        deep_plan: Dict[str, Any],
        evidence_state: Dict[str, Any],
        completed_items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        try:
            messages = self.registry.render_messages(
                "system.agent_runtime.deep_assessor",
                {
                    "user_objective": self._trim(user_objective),
                    "deep_plan": deep_plan,
                    "evidence_state": evidence_state,
                    "completed_items": completed_items,
                },
            )
            payload, _usage = chat_qwen_json(messages, enable_think=False)
            if not isinstance(payload, dict):
                return {}
            return self._normalize_llm_assessment(payload)
        except Exception:
            return {}

    def _normalize_llm_assessment(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        recommended_action = self._trim(payload.get("recommended_action")) or "continue"
        return {
            "assessment_type": "deep_thinking_assessment",
            "assessment_source": "llm_deep_assessor",
            "is_sufficient": bool(payload.get("is_sufficient", False)),
            "confidence": round(float(payload.get("confidence", 0.0) or 0.0), 2),
            "recommended_action": recommended_action,
            "gaps": [
                self._trim(item)
                for item in (payload.get("gaps") or [])
                if self._trim(item)
            ][:6],
            "covered_lanes": [
                self._trim(item)
                for item in (payload.get("covered_lanes") or [])
                if self._trim(item)
            ][:8],
            "insufficient_lanes": [
                self._trim(item)
                for item in (payload.get("insufficient_lanes") or [])
                if self._trim(item)
            ][:8],
            "stop_reason": self._trim(payload.get("stop_reason")),
            "reason": self._trim(payload.get("reason")),
        }

    def _assess_fallback(
        self,
        *,
        plan: Dict[str, Any],
        evidence: Dict[str, Any],
        completed: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        lanes = plan.get("investigation_lanes") if isinstance(plan.get("investigation_lanes"), list) else []
        budget = plan.get("budget") if isinstance(plan.get("budget"), dict) else {}
        findings = evidence.get("findings") if isinstance(evidence.get("findings"), list) else []
        open_questions = evidence.get("open_questions") if isinstance(evidence.get("open_questions"), list) else []

        lane_ids = [
            self._trim(item.get("lane_id"))
            for item in lanes
            if isinstance(item, dict) and self._trim(item.get("lane_id"))
        ]
        covered_lane_ids = self._extract_covered_lanes(completed=completed, evidence=evidence)
        insufficient_lanes = [lane_id for lane_id in lane_ids if lane_id not in covered_lane_ids]

        max_rounds = int(budget.get("max_rounds", 0) or 0)
        used_rounds = int(budget.get("used_rounds", 0) or 0)
        max_tasks = int(budget.get("max_tasks", 0) or 0)
        used_tasks = int(budget.get("used_tasks", 0) or 0)

        has_findings = bool(findings)
        has_open_questions = bool(open_questions)
        no_remaining_lanes = not insufficient_lanes
        budget_exhausted = (max_rounds > 0 and used_rounds >= max_rounds) or (max_tasks > 0 and used_tasks >= max_tasks)
        sufficient = has_findings and (no_remaining_lanes or not has_open_questions)

        recommended_action = "continue"
        reason = "需要继续补充调查证据。"
        if sufficient:
            recommended_action = "finalize_now"
            reason = "已有发现且主要调查线已覆盖，可以进入综合结论。"
        elif budget_exhausted:
            recommended_action = "finalize_now"
            reason = "已达到预算上限，应基于现有证据收敛输出并注明不确定性。"
        elif has_findings and insufficient_lanes:
            recommended_action = "refine_lanes"
            reason = "已有初步发现，但仍有关键调查线未覆盖。"

        confidence = self._estimate_confidence(
            total_lanes=len(lane_ids),
            covered_lanes=len(covered_lane_ids),
            findings_count=len(findings),
            open_questions_count=len(open_questions),
        )

        return {
            "assessment_type": "deep_thinking_assessment",
            "assessment_source": "fallback_heuristic",
            "is_sufficient": sufficient or budget_exhausted,
            "confidence": confidence,
            "recommended_action": recommended_action,
            "gaps": self._build_gaps(insufficient_lanes=insufficient_lanes, open_questions=open_questions),
            "covered_lanes": covered_lane_ids,
            "insufficient_lanes": insufficient_lanes,
            "stop_reason": "budget_exhausted" if budget_exhausted and not sufficient else "evidence_sufficient" if sufficient else "",
            "reason": reason,
        }

    def _extract_covered_lanes(self, *, completed: List[Dict[str, Any]], evidence: Dict[str, Any]) -> List[str]:
        rows: List[str] = []
        evidence_lanes = evidence.get("covered_lanes") if isinstance(evidence.get("covered_lanes"), list) else []
        for item in evidence_lanes:
            lane_id = self._trim(item)
            if lane_id and lane_id not in rows:
                rows.append(lane_id)

        for item in completed:
            if not isinstance(item, dict):
                continue
            lane_id = self._trim(item.get("lane_id"))
            status = self._trim(item.get("status"))
            if lane_id and status in {"done", "completed", "succeeded"} and lane_id not in rows:
                rows.append(lane_id)
        return rows

    def _estimate_confidence(
        self,
        *,
        total_lanes: int,
        covered_lanes: int,
        findings_count: int,
        open_questions_count: int,
    ) -> float:
        if total_lanes <= 0:
            return 0.0
        coverage_score = min(1.0, covered_lanes / float(total_lanes))
        findings_bonus = min(0.3, findings_count * 0.1)
        open_question_penalty = min(0.3, open_questions_count * 0.08)
        return round(max(0.0, min(1.0, coverage_score * 0.7 + findings_bonus - open_question_penalty)), 2)

    def _build_gaps(self, *, insufficient_lanes: List[str], open_questions: List[str]) -> List[str]:
        rows: List[str] = []
        for lane_id in insufficient_lanes[:4]:
            rows.append(f"未完成调查线: {lane_id}")
        for question in open_questions[:4]:
            normalized = self._trim(question)
            if normalized:
                rows.append(normalized)
        return rows

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_json


class DeepThinkingRefinerService:
    def __init__(self) -> None:
        self.registry = get_prompt_registry()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def build_refine_patch(
        self,
        *,
        user_objective: str,
        deep_plan: Optional[Dict[str, Any]] = None,
        assessment: Optional[Dict[str, Any]] = None,
        evidence_state: Optional[Dict[str, Any]] = None,
        enable_llm: bool = True,
    ) -> Dict[str, Any]:
        plan = deep_plan if isinstance(deep_plan, dict) else {}
        assess = assessment if isinstance(assessment, dict) else {}
        evidence = evidence_state if isinstance(evidence_state, dict) else {}
        if enable_llm:
            llm_patch = self._build_with_llm(
                user_objective=user_objective,
                deep_plan=plan,
                assessment=assess,
                evidence_state=evidence,
            )
            if llm_patch:
                return llm_patch
        return self._build_fallback(plan=plan, assessment=assess, evidence=evidence)

    def _build_with_llm(
        self,
        *,
        user_objective: str,
        deep_plan: Dict[str, Any],
        assessment: Dict[str, Any],
        evidence_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            messages = self.registry.render_messages(
                "system.agent_runtime.deep_refiner",
                {
                    "user_objective": self._trim(user_objective),
                    "deep_plan": deep_plan,
                    "assessment": assessment,
                    "evidence_state": evidence_state,
                },
            )
            payload, _usage = chat_qwen_json(messages, enable_think=False)
            if not isinstance(payload, dict):
                return {}
            return self._normalize_patch(payload)
        except Exception:
            return {}

    def _normalize_patch(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "patch_type": "deep_refine_patch",
            "patch_source": "llm_deep_refiner",
            "prioritized_lanes": [
                self._trim(item)
                for item in (payload.get("prioritized_lanes") or [])
                if self._trim(item)
            ][:4],
            "deprioritized_lanes": [
                self._trim(item)
                for item in (payload.get("deprioritized_lanes") or [])
                if self._trim(item)
            ][:4],
            "next_tasks": [
                self._trim(item)
                for item in (payload.get("next_tasks") or [])
                if self._trim(item)
            ][:6],
            "notes": [
                self._trim(item)
                for item in (payload.get("notes") or [])
                if self._trim(item)
            ][:6],
            "reason": self._trim(payload.get("reason")),
        }

    def _build_fallback(
        self,
        *,
        plan: Dict[str, Any],
        assessment: Dict[str, Any],
        evidence: Dict[str, Any],
    ) -> Dict[str, Any]:
        lanes = plan.get("investigation_lanes") if isinstance(plan.get("investigation_lanes"), list) else []
        insufficient_lanes = assessment.get("insufficient_lanes") if isinstance(assessment.get("insufficient_lanes"), list) else []
        prioritized_lanes = [self._trim(item) for item in insufficient_lanes if self._trim(item)][:2]
        lane_map = {
            self._trim(item.get("lane_id")): item
            for item in lanes
            if isinstance(item, dict) and self._trim(item.get("lane_id"))
        }
        next_tasks: List[str] = []
        for lane_id in prioritized_lanes:
            lane = lane_map.get(lane_id) or {}
            task_rows = lane.get("tasks") if isinstance(lane.get("tasks"), list) else []
            if task_rows and isinstance(task_rows[0], dict):
                goal = self._trim(task_rows[0].get("goal"))
                if goal:
                    next_tasks.append(goal)
        if not next_tasks:
            next_tasks.append("先补齐最关键的未覆盖调查线。")
        open_questions = evidence.get("open_questions") if isinstance(evidence.get("open_questions"), list) else []
        notes = [self._trim(item) for item in open_questions if self._trim(item)][:2]
        if not notes:
            notes = ["优先补关键缺口，不要平均分配调查资源。"]
        return {
            "patch_type": "deep_refine_patch",
            "patch_source": "fallback_heuristic",
            "prioritized_lanes": prioritized_lanes,
            "deprioritized_lanes": [],
            "next_tasks": next_tasks,
            "notes": notes,
            "reason": "根据 assessment 中的关键缺口，优先推进未覆盖调查线。",
        }

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_json


class DeepThinkingExecutionPreviewService:
    def __init__(self) -> None:
        self.registry = get_prompt_registry()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def build_round_preview(
        self,
        *,
        user_objective: str,
        deep_plan: Optional[Dict[str, Any]] = None,
        refine_patch: Optional[Dict[str, Any]] = None,
        enable_llm: bool = True,
    ) -> Dict[str, Any]:
        plan = deep_plan if isinstance(deep_plan, dict) else {}
        patch = refine_patch if isinstance(refine_patch, dict) else {}
        if enable_llm:
            llm_preview = self._build_with_llm(
                user_objective=user_objective,
                deep_plan=plan,
                refine_patch=patch,
            )
            if llm_preview:
                return llm_preview
        return self._build_fallback(plan=plan, refine_patch=patch)

    def _build_with_llm(
        self,
        *,
        user_objective: str,
        deep_plan: Dict[str, Any],
        refine_patch: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            messages = self.registry.render_messages(
                "system.agent_runtime.deep_execution_preview",
                {
                    "user_objective": self._trim(user_objective),
                    "deep_plan": deep_plan,
                    "refine_patch": refine_patch,
                },
            )
            payload, _usage = chat_qwen_json(messages, enable_think=False)
            if not isinstance(payload, dict):
                return {}
            return self._normalize_preview(payload)
        except Exception:
            return {}

    def _normalize_preview(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        work_items = payload.get("work_items") if isinstance(payload.get("work_items"), list) else []
        normalized_items: List[Dict[str, Any]] = []
        for item in work_items[:4]:
            if not isinstance(item, dict):
                continue
            normalized_items.append(
                {
                    "step_id": self._trim(item.get("step_id")) or f"round1_step_{len(normalized_items) + 1}",
                    "lane_id": self._trim(item.get("lane_id")),
                    "goal": self._trim(item.get("goal")),
                    "preferred_tools": [
                        self._trim(tool)
                        for tool in (item.get("preferred_tools") or [])
                        if self._trim(tool)
                    ][:4],
                    "status": self._trim(item.get("status")) or "planned",
                }
            )
        return {
            "preview_type": "round_1_execution_preview",
            "preview_source": "llm_deep_execution_preview",
            "work_items": normalized_items,
            "notes": [
                self._trim(item)
                for item in (payload.get("notes") or [])
                if self._trim(item)
            ][:6],
            "reason": self._trim(payload.get("reason")),
        }

    def _build_fallback(self, *, plan: Dict[str, Any], refine_patch: Dict[str, Any]) -> Dict[str, Any]:
        lanes = plan.get("investigation_lanes") if isinstance(plan.get("investigation_lanes"), list) else []
        prioritized_lanes = refine_patch.get("prioritized_lanes") if isinstance(refine_patch.get("prioritized_lanes"), list) else []
        lane_map = {
            self._trim(item.get("lane_id")): item
            for item in lanes
            if isinstance(item, dict) and self._trim(item.get("lane_id"))
        }
        work_items: List[Dict[str, Any]] = []
        for index, lane_id in enumerate(prioritized_lanes[:2], start=1):
            lane = lane_map.get(self._trim(lane_id)) or {}
            tasks = lane.get("tasks") if isinstance(lane.get("tasks"), list) else []
            first_task = tasks[0] if tasks and isinstance(tasks[0], dict) else {}
            work_items.append(
                {
                    "step_id": f"round1_step_{index}",
                    "lane_id": self._trim(lane_id),
                    "goal": self._trim(first_task.get("goal")) or self._trim(lane.get("why_relevant")),
                    "preferred_tools": [
                        self._trim(tool)
                        for tool in (first_task.get("preferred_tools") or [])
                        if self._trim(tool)
                    ][:4],
                    "status": "planned",
                }
            )
        return {
            "preview_type": "round_1_execution_preview",
            "preview_source": "fallback_heuristic",
            "work_items": work_items,
            "notes": [
                "只预览下一轮最关键的 1 到 2 个工作项，不在此阶段展开完整执行链。"
            ],
            "reason": "根据 refine_patch 中的优先调查线，生成第一轮候选执行草图。",
        }

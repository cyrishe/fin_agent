from __future__ import annotations

from typing import Any, Dict, List


class ContextWritePolicyService:
    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def build_policy(
        self,
        *,
        normalized_input: Dict[str, Any],
        interaction_frame: Dict[str, Any],
        conversation_state: Dict[str, Any],
        dispatch_plan: Dict[str, Any],
        execution_plan_preview: Dict[str, Any],
        work_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        active_focus_type = self._trim(interaction_frame.get("active_focus_type"))
        active_focus_id = self._trim(interaction_frame.get("active_focus_id"))
        state = self._trim(conversation_state.get("state")) or "idle"
        goal = self._trim(interaction_frame.get("current_user_goal"))
        return {
            "policy_version": "v1",
            "thread_summary_update": {
                "writer": "synthesis",
                "summary_hint": goal or self._trim(normalized_input.get("text")),
                "update_mode": "append_or_refresh",
            },
            "active_focus_update": {
                "writer": "preprocess",
                "type": active_focus_type,
                "id": active_focus_id,
                "changed": bool(active_focus_id),
            },
            "task_state_update": {
                "writer": "execution_runtime",
                "state": self._map_task_state(
                    conversation_state_state=state,
                    dispatch_entry=self._trim(dispatch_plan.get("entry")),
                    execution_status=self._trim(execution_plan_preview.get("status")),
                ),
                "resume_hint": self._trim(interaction_frame.get("resume_hint")),
                "suspended_task_stack": self._normalize_stack(interaction_frame.get("suspended_task_stack")),
            },
            "reference_memory_update": {
                "writer": "synthesis",
                "reference_scope": interaction_frame.get("reference_scope") if isinstance(interaction_frame.get("reference_scope"), list) else [],
                "recent_result_subject": self._trim(work_context.get("recent_result_subject")) or active_focus_id,
                "recent_attachment_ids": self._recent_attachment_ids(work_context),
                "objects": self._build_reference_objects(
                    interaction_frame=interaction_frame,
                    work_context=work_context,
                ),
            },
            "interaction_frame_update": {
                "writer": "preprocess",
                "interaction_mode": self._trim(interaction_frame.get("interaction_mode")),
                "active_focus_type": active_focus_type,
                "active_focus_id": active_focus_id,
                "current_user_goal": goal,
                "accepted_constraints": self._dedupe(interaction_frame.get("accepted_constraints")),
                "pending_questions": self._dedupe(interaction_frame.get("pending_questions")),
                "suspended_task_id": self._trim(interaction_frame.get("suspended_task_id")),
                "suspended_task_stack": self._normalize_stack(interaction_frame.get("suspended_task_stack")),
                "resume_hint": self._trim(interaction_frame.get("resume_hint")),
                "reference_scope": self._dedupe(interaction_frame.get("reference_scope")),
            },
        }

    def _active_skill(self, work_context: Dict[str, Any]) -> str:
        return self._trim(
            work_context.get("thread_active_skill_canonical_name")
            or work_context.get("thread_active_skill_name")
            or work_context.get("active_skill_canonical_name")
            or work_context.get("active_skill_name")
        )

    def _recent_attachment_ids(self, work_context: Dict[str, Any]) -> List[str]:
        rows = work_context.get("recent_attachments")
        if isinstance(rows, list):
            for item in rows:
                if not isinstance(item, dict):
                    continue
                attachment_ids = [self._trim(x) for x in (item.get("attachment_ids") or []) if self._trim(x)]
                if attachment_ids:
                    return attachment_ids
        return [self._trim(x) for x in (work_context.get("last_image_attachment_ids") or []) if self._trim(x)]

    def _map_task_state(
        self,
        *,
        conversation_state_state: str,
        dispatch_entry: str,
        execution_status: str,
    ) -> str:
        if conversation_state_state == "awaiting_user_clarification":
            return "waiting_user"
        if conversation_state_state == "suspended":
            return "suspended"
        if execution_status == "completed":
            return "completed"
        if dispatch_entry in {"skill_run", "tool_plan_run", "planned_run", "agent_route", "vision_intake"}:
            return "active"
        return "idle"

    def _build_reference_objects(
        self,
        *,
        interaction_frame: Dict[str, Any],
        work_context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        order_index = 0
        active_focus_type = self._trim(interaction_frame.get("active_focus_type"))
        active_focus_id = self._trim(interaction_frame.get("active_focus_id"))
        if active_focus_type and active_focus_id:
            rows.append(
                self._build_reference_object(
                    object_type=active_focus_type,
                    object_id=active_focus_id,
                    display_name=active_focus_id,
                    source="active_focus",
                    order_index=order_index,
                    salience_score=1.0,
                    is_active_focus_candidate=True,
                )
            )
            order_index += 1
        active_skill = self._active_skill(work_context)
        if active_skill and not any(item.get("object_type") == "skill" and item.get("object_id") == active_skill for item in rows):
            rows.append(
                self._build_reference_object(
                    object_type="skill",
                    object_id=active_skill,
                    display_name=active_skill,
                    source="active_skill",
                    order_index=order_index,
                    salience_score=0.95,
                    is_active_focus_candidate=True,
                )
            )
            order_index += 1
        recent_subject = self._trim(work_context.get("recent_result_subject"))
        if recent_subject and not any(item.get("object_id") == recent_subject for item in rows):
            rows.append(
                self._build_reference_object(
                    object_type="task",
                    object_id=recent_subject,
                    display_name=recent_subject,
                    source="recent_result",
                    order_index=order_index,
                    salience_score=0.8,
                    is_active_focus_candidate=False,
                )
            )
            order_index += 1
        image_ids = self._recent_attachment_ids(work_context)
        if image_ids:
            image_id = self._trim(image_ids[0])
            if image_id and not any(item.get("object_type") == "image" and item.get("object_id") == image_id for item in rows):
                rows.append(
                    self._build_reference_object(
                        object_type="image",
                        object_id=image_id,
                        display_name=image_id,
                        source="recent_image",
                        order_index=order_index,
                        salience_score=0.7,
                        is_active_focus_candidate=False,
                    )
                )
        return rows

    def _build_reference_object(
        self,
        *,
        object_type: str,
        object_id: str,
        display_name: str,
        source: str,
        order_index: int,
        salience_score: float,
        is_active_focus_candidate: bool,
    ) -> Dict[str, Any]:
        return {
            "object_type": self._trim(object_type),
            "object_id": self._trim(object_id),
            "display_name": self._trim(display_name) or self._trim(object_id),
            "source": self._trim(source),
            "source_turn_id": "",
            "order_index": int(order_index),
            "salience_score": float(salience_score),
            "is_active_focus_candidate": bool(is_active_focus_candidate),
        }

    def _dedupe(self, values: Any) -> List[str]:
        rows: List[str] = []
        for value in values or []:
            item = self._trim(value)
            if item and item not in rows:
                rows.append(item)
        return rows

    def _normalize_stack(self, values: Any) -> List[str]:
        rows: List[str] = []
        for value in values or []:
            item = self._trim(value)
            if item:
                rows.append(item)
        return rows

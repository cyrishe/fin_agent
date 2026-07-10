from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.services.reference_resolution_service import ReferenceResolutionService


class InteractionFrameService:
    def __init__(self, *, reference_resolution_service: Optional[ReferenceResolutionService] = None) -> None:
        self.reference_resolution_service = reference_resolution_service or ReferenceResolutionService()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def _active_skill(self, work_context: Dict[str, Any]) -> str:
        return self._trim(
            work_context.get("thread_active_skill_canonical_name")
            or work_context.get("thread_active_skill_name")
            or work_context.get("active_skill_canonical_name")
            or work_context.get("active_skill_name")
        )

    def _recent_attachments(self, work_context: Dict[str, Any]) -> List[Dict[str, Any]]:
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

    def build_frame(
        self,
        *,
        normalized_input: Dict[str, Any],
        work_context: Dict[str, Any],
        task_domain: str,
        capability_family: str,
        interaction: Optional[Dict[str, Any]] = None,
        dispatch_plan: Optional[Dict[str, Any]] = None,
        execution_plan_preview: Optional[Dict[str, Any]] = None,
        thread_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        existing = (
            (thread_context or {}).get("interaction_frame")
            if isinstance((thread_context or {}).get("interaction_frame"), dict)
            else {}
        )
        reference_memory = (
            (thread_context or {}).get("reference_memory")
            if isinstance((thread_context or {}).get("reference_memory"), dict)
            else {}
        )
        dispatch = dispatch_plan if isinstance(dispatch_plan, dict) else {}
        signals = interaction if isinstance(interaction, dict) else {}
        execution_plan = execution_plan_preview if isinstance(execution_plan_preview, dict) else {}
        has_reference_memory = bool(reference_memory.get("objects")) if isinstance(reference_memory.get("objects"), list) else False
        target = dispatch.get("target") if isinstance(dispatch.get("target"), dict) else {}
        turn_hints = signals.get("turn_frame_hints") if isinstance(signals.get("turn_frame_hints"), dict) else {}
        focus_hint = turn_hints.get("focus_object") if isinstance(turn_hints.get("focus_object"), dict) else {}
        active_focus_type, active_focus_id = self._resolve_focus(
            normalized_input=normalized_input,
            work_context=work_context,
            dispatch_plan=dispatch,
            target=target,
            reference_memory=reference_memory,
            focus_hint=focus_hint,
        )
        current_user_goal = self._resolve_goal(
            text=self._trim(normalized_input.get("text")),
            hinted_goal=self._trim(turn_hints.get("current_goal")),
            existing=existing,
        )
        resume_from_context = bool(signals.get("resume_from_context"))
        return {
            "interaction_mode": self._resolve_interaction_mode(
                task_domain=task_domain,
                capability_family=capability_family,
                interaction=signals,
                dispatch_entry=self._trim(dispatch.get("entry")),
                existing=existing,
            ),
            "active_focus_type": active_focus_type,
            "active_focus_id": active_focus_id,
            "current_user_goal": current_user_goal,
            "accepted_constraints": self._collect_constraints(
                normalized_input=normalized_input,
                existing=existing,
            ),
            "pending_questions": self._collect_pending_questions(
                existing=existing,
                dispatch_plan=dispatch,
                execution_plan_preview=execution_plan,
            ),
            "clarification_needed": bool(execution_plan.get("clarification_needed")),
            "suspended_task_id": self._trim(existing.get("suspended_task_id")) if resume_from_context else "",
            "suspended_task_stack": self._normalize_stack(existing.get("suspended_task_stack")) if resume_from_context else [],
            "resume_hint": self._resolve_resume_hint(existing=existing, work_context=work_context) if resume_from_context else "",
            "reference_scope": self._resolve_reference_scope(
                active_focus_type=active_focus_type,
                work_context=work_context,
                has_reference_memory=has_reference_memory,
            ),
        }

    def _resolve_interaction_mode(
        self,
        *,
        task_domain: str,
        capability_family: str,
        interaction: Dict[str, Any],
        dispatch_entry: str,
        existing: Dict[str, Any],
    ) -> str:
        hinted_mode = self._trim(interaction.get("interaction_mode_hint"))
        if hinted_mode == "continue_visual_analysis":
            return "execute_business_task"
        if hinted_mode:
            return hinted_mode
        if bool(interaction.get("resume_from_context")):
            return "resume_previous_task"
        if task_domain in {"design_refinement"}:
            return "refine_design_asset"
        if task_domain in {"system_operation"}:
            if dispatch_entry == "catalog_browse":
                return "browse_catalog"
            if dispatch_entry == "asset_open":
                return "open_asset"
            return "system_manage"
        if task_domain in {"business_dialog"}:
            return "execute_business_task"
        return self._trim(existing.get("interaction_mode")) or "execute_business_task"

    def _resolve_focus(
        self,
        *,
        normalized_input: Dict[str, Any],
        work_context: Dict[str, Any],
        dispatch_plan: Dict[str, Any],
        target: Dict[str, Any],
        reference_memory: Dict[str, Any],
        focus_hint: Dict[str, Any],
    ) -> tuple[str, str]:
        text = self._trim(normalized_input.get("text")).lower()
        dispatch_entry = self._trim(dispatch_plan.get("entry"))
        target_type = self._trim(target.get("type"))
        target_name = self._trim(target.get("name"))
        hinted_type = self._trim(focus_hint.get("type"))
        hinted_id = self._trim(focus_hint.get("id"))
        if hinted_type and hinted_type != "unknown":
            return hinted_type, hinted_id
        if dispatch_entry in {"asset_open", "skill_run", "vision_intake"} and target_type and target_name:
            return target_type, target_name
        referenced = self.reference_resolution_service.resolve_focus(text=text, reference_memory=reference_memory)
        if referenced:
            return referenced
        active_skill = self._active_skill(work_context)
        if active_skill:
            return "skill", active_skill
        recent_attachments = self._recent_attachments(work_context)
        if recent_attachments:
            first = recent_attachments[0]
            attachment_ids = first.get("attachment_ids") if isinstance(first.get("attachment_ids"), list) else []
            if attachment_ids:
                return self._trim(first.get("kind")) or "attachment", self._trim(attachment_ids[0])
            return self._trim(first.get("kind")) or "attachment", "recent_attachment"
        return "unknown", ""

    def _resolve_goal(
        self,
        *,
        text: str,
        hinted_goal: str,
        existing: Dict[str, Any],
    ) -> str:
        if hinted_goal:
            return hinted_goal
        if text:
            return text
        prior = self._trim(existing.get("current_user_goal"))
        if prior:
            return prior
        return ""

    def _collect_constraints(
        self,
        *,
        normalized_input: Dict[str, Any],
        existing: Dict[str, Any],
    ) -> List[str]:
        constraints: List[str] = []
        for modality in normalized_input.get("input_modalities", []) or []:
            value = self._trim(modality)
            if value:
                constraints.append(f"modality:{value}")
        for row in existing.get("accepted_constraints", []) or []:
            value = self._trim(row)
            if value and value not in constraints:
                constraints.append(value)
        return constraints

    def _collect_pending_questions(
        self,
        *,
        existing: Dict[str, Any],
        dispatch_plan: Dict[str, Any],
        execution_plan_preview: Dict[str, Any],
    ) -> List[str]:
        pending = [self._trim(x) for x in (existing.get("pending_questions") or []) if self._trim(x)]
        clarification_questions = [
            self._trim(x)
            for x in (execution_plan_preview.get("clarification_questions") or [])
            if self._trim(x)
        ]
        if clarification_questions:
            return clarification_questions
        if self._trim(dispatch_plan.get("entry")) == "agent_route" and not pending:
            return pending
        return pending

    def _resolve_resume_hint(self, *, existing: Dict[str, Any], work_context: Dict[str, Any]) -> str:
        hint = self._trim(existing.get("resume_hint"))
        if hint:
            return hint
        stack = self._normalize_stack(existing.get("suspended_task_stack"))
        if stack:
            latest = self._trim(stack[-1])
            if latest:
                return f"resume:{latest}"
        recent = self._trim(work_context.get("recent_result_subject"))
        if recent:
            return f"resume:{recent}"
        active_skill = self._active_skill(work_context)
        if active_skill:
            return f"resume:{active_skill}"
        return ""

    def _resolve_reference_scope(
        self,
        *,
        active_focus_type: str,
        work_context: Dict[str, Any],
        has_reference_memory: bool,
    ) -> List[str]:
        scope: List[str] = []
        if active_focus_type and active_focus_type != "unknown":
            scope.append("active_focus")
        if self._trim(work_context.get("recent_result_subject")):
            scope.append("recent_results")
        if self._recent_attachments(work_context):
            scope.append("recent_attachments")
        if has_reference_memory:
            scope.append("reference_memory")
        scope.append("thread_summary")
        return scope

    def _normalize_stack(self, value: Any) -> List[str]:
        rows: List[str] = []
        for item in value or []:
            normalized = self._trim(item)
            if normalized:
                rows.append(normalized)
        return rows

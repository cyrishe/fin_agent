from __future__ import annotations

from typing import Any, Dict


class ConversationStateMachineService:
    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def derive_state(
        self,
        *,
        interaction_frame: Dict[str, Any],
        dispatch_plan: Dict[str, Any],
        work_context: Dict[str, Any],
    ) -> Dict[str, str]:
        mode = self._trim(interaction_frame.get("interaction_mode"))
        entry = self._trim(dispatch_plan.get("entry"))
        pending = interaction_frame.get("pending_questions") if isinstance(interaction_frame.get("pending_questions"), list) else []
        suspended_stack = interaction_frame.get("suspended_task_stack") if isinstance(interaction_frame.get("suspended_task_stack"), list) else []
        if bool(interaction_frame.get("clarification_needed")):
            return self._decision("awaiting_user_clarification", "clarification_needed")
        if pending:
            return self._decision("awaiting_user_clarification", "pending_questions_present")
        if mode == "resume_previous_task":
            return self._decision("resuming", "resume_mode")
        if self._trim(interaction_frame.get("suspended_task_id")) or suspended_stack:
            return self._decision("suspended", "suspended_task_present")
        if entry in {"skill_run", "tool_plan_run", "planned_run", "agent_route", "vision_intake", "catalog_browse", "asset_open", "skill_refine"}:
            return self._decision("active", "dispatch_entry_active")
        if mode in {"execute_business_task", "browse_catalog", "open_asset", "system_manage", "refine_design_asset"}:
            return self._decision("active", "interaction_mode_active")
        return self._decision("idle", "fallback_idle")

    def _decision(self, state: str, reason: str) -> Dict[str, str]:
        return {"state": state, "reason": reason}

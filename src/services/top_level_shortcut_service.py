from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


class TopLevelShortcutError(ValueError):
    pass


class TopLevelShortcutService:
    """Resolve trusted UI actions and explicit commands inside the top-level router."""

    INTERACTION_ACTIONS = {
        ("custom_tool.design_review", "custom_tool.confirm_design", "accept"): {
            "handler": "custom_tool.action",
        },
        ("custom_tool.coding_review", "custom_tool.activate_draft", "accept"): {
            "handler": "custom_tool.action",
        },
        ("custom_tool.coding_failure", "custom_tool.retry_coding", "accept"): {
            "handler": "custom_tool.action",
        },
    }

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def resolve(
        self,
        *,
        text: str,
        interaction_response: Optional[Mapping[str, Any]] = None,
        application_context: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        response = interaction_response if isinstance(interaction_response, Mapping) else {}
        if response and not self._trim(text):
            return self._interaction_plan(response=response, application_context=application_context)
        return self._command_plan(text=text, application_context=application_context)

    def _interaction_plan(
        self,
        *,
        response: Mapping[str, Any],
        application_context: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        interaction_id = self._trim(response.get("interaction_id"))
        action_id = self._trim(response.get("action_id"))
        action = self._trim(response.get("action"))
        rule = self.INTERACTION_ACTIONS.get((interaction_id, action_id, action))
        if rule is None:
            if not any(key[0] == interaction_id for key in self.INTERACTION_ACTIONS):
                raise TopLevelShortcutError(f"unknown custom tool interaction: {interaction_id or '-'}")
            if not any(key[0] == interaction_id and key[1] == action_id for key in self.INTERACTION_ACTIONS):
                raise TopLevelShortcutError(f"action {action_id or '-'} does not belong to interaction {interaction_id}")
            raise TopLevelShortcutError(f"unsupported custom tool interaction action: {action or '-'}")
        return self._custom_tool_plan(
            application_context=application_context,
            source="shortcut:interaction",
            shortcut={
                "kind": "interaction",
                "interaction_id": interaction_id,
                "action_id": action_id,
                "action": action,
                **rule,
            },
        )

    def _command_plan(
        self,
        *,
        text: str,
        application_context: Optional[Mapping[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        parts = self._trim(text).split()
        if len(parts) < 2 or parts[0].lower() != "/custom_tool":
            return None
        sub_action = parts[1].lower()
        if sub_action not in {"create", "edit"}:
            return None
        return self._custom_tool_plan(
            application_context=application_context,
            source="shortcut:command",
            shortcut={
                "kind": "command",
                "command": "/custom_tool",
                "sub_action": sub_action,
                "handler": "custom_tool.command",
                "stage": "design",
            },
        )

    def _custom_tool_plan(
        self,
        *,
        application_context: Optional[Mapping[str, Any]],
        source: str,
        shortcut: Dict[str, Any],
    ) -> Dict[str, Any]:
        app = application_context if isinstance(application_context, Mapping) else {}
        default_agent = app.get("default_agent") if isinstance(app.get("default_agent"), Mapping) else {}
        selected_agent = self._trim(default_agent.get("agent_name") or default_agent.get("name")) or "investment_analyst"
        return {
            "entry": "custom_tool_flow",
            "turn_mode": "tool_development",
            "domain": "business",
            "selected_agent": selected_agent,
            "planning_scope": "top_level_shortcut",
            "target": {"type": "custom_tool", "name": ""},
            "shortcut": shortcut,
            "thread_context_patch_preview": {},
            "source": source,
        }

from __future__ import annotations

from typing import Any, Dict, List


class SystemCommandService:
    CATALOG_ALIASES = {
        "/skills": "catalog_skills",
        "/tools": "catalog_tools",
        "/agents": "catalog_agents",
        "/applications": "catalog_applications",
        "/apps": "catalog_applications",
        "/tasks": "catalog_tasks",
        "/services": "catalog_services",
    }

    SINGULAR_OPEN_OR_LIST = {
        "/skill": ("catalog_skills", "open_skill", "skill"),
        "/tool": ("catalog_tools", "open_tool", "tool"),
        "/agent": ("catalog_agents", "open_agent", "agent"),
        "/application": ("catalog_applications", "open_application", "application"),
    }

    DIRECT_ACTIONS = {
        "/custom_tool": "custom_tool",
        "/custom-tool": "custom_tool",
        "/new-skill": "new_skill",
        "/new_skill": "new_skill",
        "/draft-skill": "draft_skill",
        "/draft_skill": "draft_skill",
        "/refine-skill": "refine_skill",
        "/refine_skill": "refine_skill",
        "/run-skill": "run_skill",
        "/run_skill": "run_skill",
        "/new-agent": "new_agent",
        "/new_agent": "new_agent",
        "/new-application": "new_application",
        "/new_application": "new_application",
    }

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def normalize(self, *, command: str, args: List[str] | None = None) -> Dict[str, Any]:
        normalized_command = self._trim(command).lower()
        normalized_args = [self._trim(item) for item in (args or []) if self._trim(item)]

        if normalized_command in self.CATALOG_ALIASES:
            action = self.CATALOG_ALIASES[normalized_command]
            return self._result(
                command=normalized_command,
                canonical_command=normalized_command,
                action=action,
                args=normalized_args,
                kind="catalog",
            )

        if normalized_command in self.SINGULAR_OPEN_OR_LIST:
            list_action, open_action, target_type = self.SINGULAR_OPEN_OR_LIST[normalized_command]
            if normalized_args:
                return self._result(
                    command=normalized_command,
                    canonical_command=normalized_command,
                    action=open_action,
                    args=normalized_args,
                    kind="open",
                    target_type=target_type,
                    target_name=normalized_args[0],
                )
            return self._result(
                command=normalized_command,
                canonical_command=normalized_command,
                action=list_action,
                args=normalized_args,
                kind="catalog",
            )

        if normalized_command in self.DIRECT_ACTIONS:
            action = self.DIRECT_ACTIONS[normalized_command]
            return self._result(
                command=normalized_command,
                canonical_command=normalized_command,
                action=action,
                args=normalized_args,
                kind="direct",
            )

        return self._result(
            command=normalized_command,
            canonical_command=normalized_command,
            action="unknown_command",
            args=normalized_args,
            kind="unknown",
        )

    def _result(
        self,
        *,
        command: str,
        canonical_command: str,
        action: str,
        args: List[str],
        kind: str,
        target_type: str = "",
        target_name: str = "",
    ) -> Dict[str, Any]:
        return {
            "command": command,
            "canonical_command": canonical_command,
            "action": action,
            "kind": kind,
            "args": args,
            "target_type": target_type,
            "target_name": target_name,
        }

from typing import Any, Dict, Optional

from src.services.application_studio_service import ApplicationStudioService
from src.services.agent_runtime_service import AgentRuntimeService


class ApplicationRuntimeError(ValueError):
    pass


class ApplicationRuntimeService:
    def __init__(
        self,
        *,
        application_studio_service: Optional[ApplicationStudioService] = None,
        agent_runtime_service: Optional[AgentRuntimeService] = None,
    ) -> None:
        self.application_studio_service = application_studio_service or ApplicationStudioService()
        self.agent_runtime_service = agent_runtime_service or AgentRuntimeService()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def get_application_context(self, application_name: str) -> Dict[str, Any]:
        normalized = self._trim(application_name)
        if not normalized:
            raise ApplicationRuntimeError("application_name 不能为空")
        bundle = self.application_studio_service.load_application_bundle(normalized)
        config = (bundle.get("files") or {}).get("application_config") or {}
        default_agent = self._resolve_default_agent(config)
        workspace = self._build_workspace(config, normalized)
        return {
            "application_name": normalized,
            "display_name": self._trim(config.get("display_name")) or normalized,
            "application_config": config,
            "available_agents": self._resolve_available_agents(config, default_agent),
            "default_agent": default_agent,
            "workspace": workspace,
            "workspace_links": self._build_workspace_links(config, workspace),
            "result_workspace_routes": self._build_result_workspace_routes(config),
            "quick_commands": self._build_quick_commands(config),
            "quick_actions": self._build_quick_actions(config),
            "chat_placeholder": self._trim(config.get("chat_placeholder")),
            "assistant_intro": self._trim(config.get("assistant_intro")),
        }

    def build_route_context(self, application_name: str) -> Dict[str, Any]:
        app_ctx = self.get_application_context(application_name)
        default_agent = app_ctx.get("default_agent") if isinstance(app_ctx.get("default_agent"), dict) else {}
        if not default_agent:
            return {}
        return {
            "application_name": app_ctx.get("application_name"),
            "agent_name": self._trim(default_agent.get("agent_name")),
            "allowed_skills": [self._trim(x) for x in default_agent.get("skills", []) if self._trim(x)],
            "allowed_tools": [self._trim(x) for x in default_agent.get("tools", []) if self._trim(x)],
        }

    def _resolve_agent_summary(self, agent_name: str) -> Optional[Dict[str, Any]]:
        normalized = self._trim(agent_name)
        if not normalized:
            return None
        agent_ctx = self.agent_runtime_service.get_agent_context(normalized)
        config = agent_ctx.get("config") if isinstance(agent_ctx.get("config"), dict) else {}
        return {
            "agent_name": normalized,
            "display_name": self._trim(config.get("display_name")) or normalized,
            "role": self._trim(config.get("role")),
            "persona": self._trim(config.get("persona")),
            "skills": [self._trim(x) for x in config.get("skills", []) if self._trim(x)],
            "tools": [self._trim(x) for x in config.get("tools", []) if self._trim(x)],
            "handoff_agents": [self._trim(x) for x in config.get("handoff_agents", []) if self._trim(x)],
            "config": config,
            "runtime_profile": agent_ctx.get("runtime_profile") or {},
        }

    def _resolve_default_agent(self, application_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        agent_name = self._trim(application_config.get("default_agent"))
        return self._resolve_agent_summary(agent_name) if agent_name else None

    def _resolve_available_agents(
        self,
        application_config: Dict[str, Any],
        default_agent: Optional[Dict[str, Any]],
    ) -> list[Dict[str, Any]]:
        names = [self._trim(x) for x in application_config.get("available_agents", []) if self._trim(x)]
        if not names:
            names = []
            for item in (default_agent,):
                agent_name = self._trim((item or {}).get("agent_name")) if isinstance(item, dict) else ""
                if agent_name and agent_name not in names:
                    names.append(agent_name)
        resolved: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            summary = self._resolve_agent_summary(name)
            if summary:
                resolved.append(summary)
        return resolved

    def _build_workspace(self, application_config: Dict[str, Any], application_name: str) -> Dict[str, Any]:
        workspace = application_config.get("workspace") if isinstance(application_config.get("workspace"), dict) else {}
        workspace_type = self._trim(workspace.get("type")) or ("router" if application_name == "investment_workbench" else "workspace")
        title = self._trim(workspace.get("title")) or ("Task Router" if workspace_type == "router" else "Workspace")
        url = self._trim(workspace.get("url")) or "/router/studio"
        return {
            "type": workspace_type,
            "title": title,
            "url": url,
        }

    def _build_workspace_links(self, application_config: Dict[str, Any], workspace: Dict[str, Any]) -> list[Dict[str, str]]:
        links = application_config.get("workspace_links") if isinstance(application_config.get("workspace_links"), list) else []
        normalized: list[Dict[str, str]] = []
        for item in links:
            if not isinstance(item, dict):
                continue
            label = self._trim(item.get("label"))
            url = self._trim(item.get("url"))
            if label and url:
                normalized.append({"label": label, "url": url})
        if normalized:
            return normalized
        return [
            {"label": "Skills", "url": "/skills/studio"},
            {"label": "Tools", "url": "/tools"},
            {"label": "Tasks", "url": self._trim(workspace.get("url")) or "/router/studio"},
            {"label": "Services", "url": "/services"},
        ]

    def _build_quick_commands(self, application_config: Dict[str, Any]) -> list[str]:
        commands = application_config.get("quick_commands") if isinstance(application_config.get("quick_commands"), list) else []
        normalized = [self._trim(x) for x in commands if self._trim(x)]
        if normalized:
            return normalized
        return ["/applications", "/agents", "/skills", "/tools", "/tasks", "/services"]

    def _build_quick_actions(self, application_config: Dict[str, Any]) -> list[Dict[str, str]]:
        actions = application_config.get("quick_actions") if isinstance(application_config.get("quick_actions"), list) else []
        normalized: list[Dict[str, str]] = []
        for item in actions:
            if not isinstance(item, dict):
                continue
            title = self._trim(item.get("title"))
            description = self._trim(item.get("description"))
            prompt = self._trim(item.get("prompt"))
            if title and prompt:
                normalized.append(
                    {
                        "title": title,
                        "description": description,
                        "prompt": prompt,
                    }
                )
        if normalized:
            return normalized
        return []

    def _build_result_workspace_routes(self, application_config: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
        routes = (
            application_config.get("result_workspace_routes")
            if isinstance(application_config.get("result_workspace_routes"), dict)
            else {}
        )
        normalized: Dict[str, Dict[str, str]] = {}
        for mode, item in routes.items():
            mode_name = self._trim(mode)
            if not mode_name or not isinstance(item, dict):
                continue
            url = self._trim(item.get("url"))
            if not url:
                continue
            normalized[mode_name] = {
                "type": self._trim(item.get("type")) or "workspace",
                "title": self._trim(item.get("title")) or "Workspace",
                "url": url,
            }
        return normalized

    def _build_default_workspace(self, application_name: str) -> Dict[str, Any]:
        return {
            "type": "workspace",
            "title": "Workspace",
            "url": "/router/studio",
        }

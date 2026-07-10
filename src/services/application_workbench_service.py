from collections import defaultdict
from typing import Any, Callable, Dict, Optional

from src.services.application_runtime_service import ApplicationRuntimeService


class ApplicationWorkbenchService:
    def __init__(
        self,
        *,
        application_runtime_service: Optional[ApplicationRuntimeService] = None,
    ) -> None:
        self.application_runtime_service = application_runtime_service or ApplicationRuntimeService()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def get_ui_context(
        self,
        application_name: str,
        *,
        url_transform: Optional[Callable[[str], str]] = None,
    ) -> Dict[str, Any]:
        transform = url_transform or (lambda value: value)
        app_ctx = self.application_runtime_service.get_application_context(application_name)
        workspace = app_ctx.get("workspace") if isinstance(app_ctx.get("workspace"), dict) else {}
        workspace_links = app_ctx.get("workspace_links") if isinstance(app_ctx.get("workspace_links"), list) else []
        normalized_links = []
        for item in workspace_links:
            if not isinstance(item, dict):
                continue
            label = self._trim(item.get("label"))
            url = self._trim(item.get("url"))
            if label and url:
                normalized_links.append({"label": label, "url": transform(url)})
        return {
            **app_ctx,
            "workspace": {
                **workspace,
                "url": transform(self._trim(workspace.get("url")) or "/router/studio"),
            },
            "workspace_links": normalized_links,
        }

    def apply_workspace_orchestration(
        self,
        result: Dict[str, Any],
        *,
        application_context: Optional[Dict[str, Any]] = None,
        url_transform: Optional[Callable[[str], str]] = None,
    ) -> Dict[str, Any]:
        payload = dict(result or {}) if isinstance(result, dict) else {}
        routes = application_context.get("result_workspace_routes") if isinstance(application_context, dict) else {}
        mode = self._trim(payload.get("mode"))
        route = routes.get(mode) if isinstance(routes, dict) and mode else None
        if not isinstance(route, dict):
            return payload
        formatting_context = self._extract_workspace_context(payload)
        url = self._format_workspace_value(route.get("url") or "", formatting_context)
        if not url:
            return payload
        transform = url_transform or (lambda value: value)
        workspace = payload.get("workspace") if isinstance(payload.get("workspace"), dict) else {}
        payload["workspace"] = {
            "type": self._trim(route.get("type")) or self._trim(workspace.get("type")) or "workspace",
            "title": self._format_workspace_value(
                route.get("title") or workspace.get("title") or "Workspace",
                formatting_context,
            ),
            "url": transform(url),
        }
        self._attach_workspace_to_items(payload)
        return payload

    def _extract_workspace_context(self, payload: Dict[str, Any]) -> Dict[str, str]:
        bundle = payload.get("bundle") if isinstance(payload.get("bundle"), dict) else {}
        return {
            "application_name": self._trim(payload.get("application_name") or bundle.get("application_name")),
            "agent_name": self._trim(payload.get("agent_name") or bundle.get("agent_name")),
            "skill_name": self._trim(payload.get("skill_name") or bundle.get("skill_name")),
            "tool_name": self._trim(payload.get("tool_name") or bundle.get("tool_name")),
        }

    def _format_workspace_value(self, template: str, context: Dict[str, str]) -> str:
        text = self._trim(template)
        if not text:
            return ""
        safe_mapping = {str(key): str(value) for key, value in (context or {}).items() if value not in (None, "")}
        try:
            return text.format_map(defaultdict(str, safe_mapping))
        except Exception:
            return text

    def _attach_workspace_to_items(self, payload: Dict[str, Any]) -> None:
        items = payload.get("items")
        workspace = payload.get("workspace") if isinstance(payload.get("workspace"), dict) else {}
        workspace_url = self._trim(workspace.get("url"))
        workspace_title = self._trim(workspace.get("title")) or "工作区"
        if not isinstance(items, list) or not workspace_url:
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            item.setdefault("workspace_url", workspace_url)
            item.setdefault("workspace_title", workspace_title)

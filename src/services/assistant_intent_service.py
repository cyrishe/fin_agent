from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.agent_studio_service import AgentStudioService
    from src.services.application_studio_service import ApplicationStudioService
    from src.services.skill_studio_service import SkillStudioService
    from src.services.tool_studio_service import ToolStudioService


class AssistantIntentService:
    def __init__(
        self,
        *,
        skill_studio_service: Optional["SkillStudioService"] = None,
        tool_studio_service: Optional["ToolStudioService"] = None,
        application_studio_service: Optional["ApplicationStudioService"] = None,
        agent_studio_service: Optional["AgentStudioService"] = None,
        skills_root: str = "src/skills",
        tool_definitions_dir: str = "src/tools/definitions",
        applications_root: str = "src/applications",
        agents_root: str = "src/agents",
    ) -> None:
        self.skill_studio_service = skill_studio_service
        self.tool_studio_service = tool_studio_service
        self.application_studio_service = application_studio_service
        self.agent_studio_service = agent_studio_service
        self.skills_root = Path(skills_root)
        self.tool_definitions_dir = Path(tool_definitions_dir)
        self.applications_root = Path(applications_root)
        self.agents_root = Path(agents_root)

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> bool:
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _normalize_text(text: str) -> str:
        return str(text or "").strip().lower()

    def _is_retrievable(self, item: Dict[str, Any]) -> bool:
        availability = item.get("availability") if isinstance(item.get("availability"), dict) else {}
        lifecycle = self._trim(availability.get("lifecycle") or "active").lower() or "active"
        retrieval_mode = self._trim(availability.get("retrieval_mode") or "retrievable").lower() or "retrievable"
        return lifecycle == "active" and retrieval_mode == "retrievable"

    def _looks_like_catalog_browse(self, text: str, nouns: List[str]) -> bool:
        browse_verbs = ["列出", "看看", "查看", "有哪些", "全部", "所有", "浏览"]
        return self._contains_any(text, nouns) and self._contains_any(text, browse_verbs)

    def _looks_like_active_skill_run(self, text: str) -> bool:
        run_verbs = ["运行", "执行", "跑一下", "run", "用", "基于"]
        target_hints = ["刚才那个", "当前这个", "这个skill", "这个技能", "当前skill", "当前技能"]
        return self._contains_any(text, run_verbs) and self._contains_any(text, target_hints)

    def _looks_like_active_skill_open(self, text: str) -> bool:
        open_verbs = ["查看", "打开", "编辑", "看看"]
        target_hints = ["刚才那个", "当前这个", "这个skill", "这个技能", "当前skill", "当前技能"]
        return self._contains_any(text, open_verbs) and self._contains_any(text, target_hints)

    def _looks_like_asset_open_request(self, text: str, asset_type: str) -> bool:
        open_verbs = ["打开", "查看", "编辑", "看看", "进入", "切换到"]
        type_hints = {
            "skill": ["skill", "技能"],
            "tool": ["tool", "工具"],
            "application": ["application", "app", "应用"],
            "agent": ["agent", "agents", "智能体"],
        }
        hints = type_hints.get(asset_type, [])
        return self._contains_any(text, open_verbs) and self._contains_any(text, hints)

    def classify(
        self,
        *,
        user_text: str,
        thread_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text = self._trim(user_text)
        lowered = self._normalize_text(text)
        ctx = thread_context if isinstance(thread_context, dict) else {}
        active_skill_name = self._trim(ctx.get("active_skill_name"))
        active_skill_canonical_name = self._trim(ctx.get("active_skill_canonical_name"))

        browse_mappings = [
            ("skills_catalog", ["技能", "skills", "skill"], "list"),
            ("tools_catalog", ["工具", "tools", "tool"], "list"),
            ("applications_catalog", ["应用", "applications", "apps", "application"], "list"),
            ("agents_catalog", ["agent", "agents", "智能体"], "list"),
        ]
        for mode, nouns, _verb in browse_mappings:
            if self._looks_like_catalog_browse(lowered, nouns):
                return {"intent_type": "catalog_browse", "mode": mode}

        if active_skill_name and self._looks_like_active_skill_run(lowered):
            return {
                "intent_type": "skill_run",
                "skill_name": active_skill_canonical_name or active_skill_name,
                "requirement_text": text,
            }

        if active_skill_name and self._looks_like_active_skill_open(lowered):
            return {
                "intent_type": "asset_open",
                "asset_type": "skill",
                "asset_name": active_skill_canonical_name or active_skill_name,
            }

        exact_skill = self._match_skill_name(text)
        if exact_skill and self._looks_like_asset_open_request(lowered, "skill"):
            return {
                "intent_type": "asset_open",
                "asset_type": "skill",
                "asset_name": exact_skill,
            }

        exact_tool = self._match_tool_name(text)
        if exact_tool and self._looks_like_asset_open_request(lowered, "tool"):
            return {
                "intent_type": "asset_open",
                "asset_type": "tool",
                "asset_name": exact_tool,
            }

        exact_application = self._match_application_name(text)
        if exact_application and self._looks_like_asset_open_request(lowered, "application"):
            return {
                "intent_type": "asset_open",
                "asset_type": "application",
                "asset_name": exact_application,
            }

        exact_agent = self._match_agent_name(text)
        if exact_agent and self._looks_like_asset_open_request(lowered, "agent"):
            return {
                "intent_type": "asset_open",
                "asset_type": "agent",
                "asset_name": exact_agent,
            }

        return {"intent_type": "none"}

    def _match_skill_name(self, text: str) -> str:
        normalized = self._trim(text)
        if not normalized:
            return ""
        for skill_name in self._list_skill_names():
            if skill_name and skill_name in normalized:
                return skill_name
        return ""

    def _match_tool_name(self, text: str) -> str:
        normalized = self._trim(text)
        if not normalized:
            return ""
        for tool_name in self._list_tool_names():
            if tool_name and tool_name in normalized:
                return tool_name
        return ""

    def _match_application_name(self, text: str) -> str:
        normalized = self._trim(text)
        if not normalized:
            return ""
        for application_name in self._list_application_names():
            if application_name and application_name in normalized:
                return application_name
        return ""

    def _match_agent_name(self, text: str) -> str:
        normalized = self._trim(text)
        if not normalized:
            return ""
        for agent_name in self._list_agent_names():
            if agent_name and agent_name in normalized:
                return agent_name
        return ""

    def _list_skill_names(self) -> List[str]:
        if self.skill_studio_service is not None:
            return [
                self._trim(item.get("skill_name"))
                for item in self.skill_studio_service.list_skills()
                if self._trim(item.get("skill_name")) and self._is_retrievable(item)
            ]
        rows: List[str] = []
        if not self.skills_root.exists():
            return rows
        for path in sorted(self.skills_root.iterdir()):
            if path.is_dir() and not path.name.startswith("."):
                rows.append(path.name)
        return rows

    def _list_tool_names(self) -> List[str]:
        if self.tool_studio_service is not None:
            return [
                self._trim(item.get("tool_name") or item.get("name"))
                for item in self.tool_studio_service.list_tools()
                if self._trim(item.get("tool_name") or item.get("name")) and self._is_retrievable(item)
            ]
        rows: List[str] = []
        if not self.tool_definitions_dir.exists():
            return rows
        for path in sorted(self.tool_definitions_dir.glob("*.tool.json")):
            rows.append(path.name.replace(".tool.json", ""))
        return rows

    def _list_application_names(self) -> List[str]:
        if self.application_studio_service is not None:
            return [
                self._trim(item.get("application_name") or item.get("name"))
                for item in self.application_studio_service.list_applications()
                if self._trim(item.get("application_name") or item.get("name"))
            ]
        rows: List[str] = []
        if not self.applications_root.exists():
            return rows
        for path in sorted(self.applications_root.iterdir()):
            if path.is_dir() and not path.name.startswith("."):
                rows.append(path.name)
        return rows

    def _list_agent_names(self) -> List[str]:
        if self.agent_studio_service is not None:
            return [
                self._trim(item.get("agent_name") or item.get("name"))
                for item in self.agent_studio_service.list_agents()
                if self._trim(item.get("agent_name") or item.get("name"))
            ]
        rows: List[str] = []
        if not self.agents_root.exists():
            return rows
        for path in sorted(self.agents_root.iterdir()):
            if path.is_dir() and not path.name.startswith("."):
                rows.append(path.name)
        return rows

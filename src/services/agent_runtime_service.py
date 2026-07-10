from typing import Any, Dict, List, Optional

from src.services.agent_studio_service import AgentStudioService


class AgentRuntimeError(ValueError):
    pass


class AgentRuntimeService:
    def __init__(self, *, agent_studio_service: Optional[AgentStudioService] = None) -> None:
        self.agent_studio_service = agent_studio_service or AgentStudioService()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def get_agent_context(self, agent_name: str) -> Dict[str, Any]:
        normalized = self._trim(agent_name)
        if not normalized:
            raise AgentRuntimeError("agent_name 不能为空")
        bundle = self.agent_studio_service.load_agent_bundle(normalized)
        config = (bundle.get("files") or {}).get("agent_config") or {}
        soul_md = str((bundle.get("files") or {}).get("soul_md_text") or "")
        compiled = self.build_runtime_profile(agent_name=normalized, config=config, soul_md=soul_md)
        return {
            "agent_name": normalized,
            "display_name": self._trim(config.get("display_name")) or normalized,
            "role": self._trim(config.get("role")),
            "config": config,
            "soul_md": soul_md,
            "runtime_profile": compiled,
        }

    def build_runtime_profile(
        self,
        *,
        agent_name: str,
        config: Dict[str, Any],
        soul_md: str,
    ) -> Dict[str, Any]:
        role = self._trim(config.get("role")) or agent_name
        responsibilities = [self._trim(x) for x in config.get("responsibilities", []) if self._trim(x)]
        skills = [self._trim(x) for x in config.get("skills", []) if self._trim(x)]
        tools = [self._trim(x) for x in config.get("tools", []) if self._trim(x)]
        handoff_agents = [self._trim(x) for x in config.get("handoff_agents", []) if self._trim(x)]
        context_policy = config.get("context_policy") if isinstance(config.get("context_policy"), dict) else {}
        skill_policy = config.get("skill_policy") if isinstance(config.get("skill_policy"), dict) else {}
        tool_policy = config.get("tool_policy") if isinstance(config.get("tool_policy"), dict) else {}
        memory_policy = config.get("memory_policy") if isinstance(config.get("memory_policy"), dict) else {}
        response_style_policy = (
            config.get("response_style_policy")
            if isinstance(config.get("response_style_policy"), dict)
            else {}
        )

        sections = [
            {
                "section": "identity",
                "title": "Identity",
                "content": self._trim(config.get("display_name")) or agent_name,
            },
            {
                "section": "role",
                "title": "Role",
                "content": role,
            },
            {
                "section": "soul",
                "title": "Soul",
                "content": str(soul_md or "").strip(),
            },
            {
                "section": "responsibilities",
                "title": "Responsibilities",
                "content": responsibilities,
            },
            {
                "section": "context_policy",
                "title": "Context Policy",
                "content": context_policy,
            },
            {
                "section": "skill_policy",
                "title": "Skill Policy",
                "content": {
                    **skill_policy,
                    "allowed_skills": skills,
                },
            },
            {
                "section": "tool_policy",
                "title": "Tool Policy",
                "content": {
                    **tool_policy,
                    "allowed_tools": tools,
                },
            },
            {
                "section": "handoff_policy",
                "title": "Handoff Policy",
                "content": {
                    "allowed_agents": handoff_agents,
                },
            },
            {
                "section": "memory_policy",
                "title": "Memory Policy",
                "content": memory_policy,
            },
            {
                "section": "response_style_policy",
                "title": "Response Style Policy",
                "content": response_style_policy,
            },
        ]
        return {
            "agent_name": agent_name,
            "display_name": self._trim(config.get("display_name")) or agent_name,
            "role": role,
            "skills": skills,
            "tools": tools,
            "handoff_agents": handoff_agents,
            "sections": sections,
            "system_prompt_text": self._build_system_prompt_text(sections),
        }

    def _build_system_prompt_text(self, sections: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for section in sections:
            title = self._trim(section.get("title"))
            content = section.get("content")
            if not title or content in (None, "", [], {}):
                continue
            lines.append(f"## {title}")
            if isinstance(content, list):
                for item in content:
                    item_text = self._trim(item)
                    if item_text:
                        lines.append(f"- {item_text}")
            elif isinstance(content, dict):
                for key, value in content.items():
                    if value in (None, "", [], {}):
                        continue
                    lines.append(f"- {key}: {value}")
            else:
                lines.append(str(content).strip())
            lines.append("")
        return "\n".join(lines).strip()

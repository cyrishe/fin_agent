import json
from pathlib import Path
from typing import Any, Dict, List

from src.services.runtime_artifact_service import RuntimeArtifactService


class AgentStudioError(ValueError):
    pass


class AgentStudioService:
    def __init__(self, agents_root: str = "src/agents") -> None:
        self.agents_root = Path(agents_root)
        self.runtime_artifacts = RuntimeArtifactService(agents_root=agents_root)

    def list_agents(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not self.agents_root.exists():
            return rows
        for agent_dir in sorted(self.agents_root.iterdir()):
            if not agent_dir.is_dir():
                continue
            try:
                bundle = self.load_agent_bundle(agent_dir.name)
            except Exception:
                continue
            files = bundle.get("files") or {}
            config = files.get("agent_config") or {}
            rows.append(
                {
                    "agent_name": agent_dir.name,
                    "display_name": str(config.get("display_name") or agent_dir.name).strip(),
                    "role": str(config.get("role") or "").strip(),
                    "status": str(config.get("status") or "").strip(),
                    "version": str(config.get("version") or "").strip(),
                    "enabled": bool(config.get("enabled", True)),
                    "skills": [str(x).strip() for x in config.get("skills", []) if str(x).strip()],
                    "tools": [str(x).strip() for x in config.get("tools", []) if str(x).strip()],
                    "handoff_agents": [str(x).strip() for x in config.get("handoff_agents", []) if str(x).strip()],
                }
            )
        return rows

    def load_agent_bundle(self, agent_name: str) -> Dict[str, Any]:
        normalized = str(agent_name or "").strip()
        if not normalized:
            raise AgentStudioError("agent_name 不能为空")
        agent_dir = self.agents_root / normalized
        soul_path = agent_dir / "SOUL.md"
        config_path = agent_dir / "agent.json"
        schema_path = agent_dir / "schema.json"
        if not soul_path.exists():
            raise FileNotFoundError(f"agent soul 不存在: {soul_path}")

        config_obj = self._load_json_if_exists(config_path)
        schema_obj = self._load_json_if_exists(schema_path)
        return {
            "agent_name": normalized,
            "files": {
                "soul_md_text": soul_path.read_text(encoding="utf-8"),
                "agent_config_text": json.dumps(config_obj, ensure_ascii=False, indent=2),
                "schema_text": json.dumps(schema_obj, ensure_ascii=False, indent=2),
                "agent_config": config_obj,
                "schema": schema_obj,
            },
            "meta": {
                "agent_dir": str(agent_dir),
            },
        }

    def build_agent_template_bundle(self, agent_name: str) -> Dict[str, Any]:
        normalized = str(agent_name or "").strip()
        if not normalized:
            raise AgentStudioError("agent_name 不能为空")
        title = normalized.replace("_", " ").strip() or "new agent"
        soul_text = "\n".join(
            [
                f"# {title.title()}",
                "",
                "## 角色",
                "",
                "请描述这个 agent 的角色、职责边界和风格。",
                "",
                "## 行为约束",
                "",
                "- 优先在受控 skill / tool 范围内工作",
                "- 输出尽量结构化、可审计",
                "",
                "## 协作方式",
                "",
                "- 需要时调用 skill",
                "- 需要时调用 tool",
                "",
            ]
        )
        config_obj = {
            "name": normalized,
            "display_name": title.title(),
            "version": "v1",
            "status": "draft",
            "enabled": True,
            "owner": "agents",
            "domain": "generic",
            "role": normalized,
            "capabilities": [],
            "tags": [],
            "keywords": [],
            "responsibilities": [],
            "context_policy": {},
            "skill_policy": {"mode": "strict"},
            "tool_policy": {"mode": "strict"},
            "skills": [],
            "tools": [],
            "handoff_agents": [],
        }
        schema_obj = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{normalized} Run Input",
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
        return {
            "agent_name": normalized,
            "files": {
                "soul_md_text": soul_text,
                "agent_config_text": json.dumps(config_obj, ensure_ascii=False, indent=2),
                "schema_text": json.dumps(schema_obj, ensure_ascii=False, indent=2),
                "agent_config": config_obj,
                "schema": schema_obj,
            },
            "meta": {
                "template": True,
                "agent_dir": str(self.agents_root / normalized),
            },
        }

    def save_agent_bundle(
        self,
        *,
        agent_name: str,
        soul_md_text: str,
        agent_config_text: str,
        schema_text: str,
    ) -> Dict[str, Any]:
        normalized = str(agent_name or "").strip()
        if not normalized:
            raise AgentStudioError("agent_name 不能为空")
        agent_dir = self.agents_root / normalized
        agent_dir.mkdir(parents=True, exist_ok=True)

        soul_text = str(soul_md_text or "")
        if not soul_text.strip():
            raise AgentStudioError("SOUL.md 不能为空")

        config_obj = self._parse_required_json(agent_config_text, "agent.json")
        if str(config_obj.get("name") or "").strip() != normalized:
            raise AgentStudioError("agent.json.name 必须与 agent_name 一致")

        schema_obj = self._parse_required_json(schema_text, "schema.json")

        (agent_dir / "SOUL.md").write_text(soul_text, encoding="utf-8")
        (agent_dir / "agent.json").write_text(
            json.dumps(config_obj, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (agent_dir / "schema.json").write_text(
            json.dumps(schema_obj, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.runtime_artifacts.sync_agent(
            normalized,
            source_type="ui",
            changed_by="agent_studio",
        )
        return self.load_agent_bundle(normalized)

    @staticmethod
    def _load_json_if_exists(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _parse_required_json(text: str, label: str) -> Dict[str, Any]:
        try:
            obj = json.loads(text or "{}")
        except Exception as exc:
            raise AgentStudioError(f"{label} 不是合法 JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise AgentStudioError(f"{label} 顶层必须是对象")
        return obj

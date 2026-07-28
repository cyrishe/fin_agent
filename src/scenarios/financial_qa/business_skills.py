from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


def _trim(value: Any) -> str:
    return str(value or "").strip()


class FinanceBusinessSkillCatalog:
    """Read-only registry and loader for Finance CC business methods."""

    def __init__(
        self,
        *,
        root: str | Path = "src/skills/finance-business",
    ) -> None:
        self.root = Path(root)
        self.catalog_path = self.root / "catalog.json"

    def entries(
        self,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> list[Dict[str, Any]]:
        allowed = {
            _trim(item)
            for item in (allowed_skill_ids or [])
            if _trim(item)
        }
        restrict = allowed_skill_ids is not None
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        raw_entries = (
            payload.get("skills")
            if isinstance(payload, Mapping) and isinstance(payload.get("skills"), list)
            else []
        )
        entries: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_entries:
            if not isinstance(raw, Mapping):
                continue
            skill_id = _trim(raw.get("id"))
            relative_path = _trim(raw.get("path"))
            description = _trim(raw.get("description"))
            if (
                not skill_id
                or not relative_path
                or not description
                or skill_id in seen
                or (restrict and skill_id not in allowed)
            ):
                continue
            skill_file = self._skill_file(relative_path)
            if skill_file is None or not skill_file.is_file():
                continue
            seen.add(skill_id)
            entries.append(
                {
                    "id": skill_id,
                    "category": _trim(raw.get("category")),
                    "path": relative_path,
                    "description": description,
                    "_skill_file": skill_file,
                }
            )
        return entries

    def public_entries(
        self,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> list[Dict[str, Any]]:
        return [
            {key: value for key, value in entry.items() if not key.startswith("_")}
            for entry in self.entries(allowed_skill_ids=allowed_skill_ids)
        ]

    def load(
        self,
        skill_id: str,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        normalized = _trim(skill_id)
        entry = next(
            (
                item
                for item in self.entries(allowed_skill_ids=allowed_skill_ids)
                if item["id"] == normalized
            ),
            None,
        )
        if entry is None:
            return {
                "skill_id": normalized,
                "error": "该业务 Skill 未注册、未授权或当前不可用。",
                "guidance": "这不会中断对话；请由 Finance CC 使用现有数据工具继续合理处理。",
            }
        skill_file = entry["_skill_file"]
        try:
            method = skill_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return {
                "skill_id": normalized,
                "error": f"业务 Skill 暂时无法加载：{exc}",
                "guidance": "这不会中断对话；请由 Finance CC 使用现有数据工具继续合理处理。",
            }
        return {
            "skill_id": normalized,
            "description": entry["description"],
            "method": method,
            "control": "Finance CC 继续持有当前会话、工具选择和最终回答。",
        }

    def routing_summary(
        self,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> str:
        return "\n".join(
            f"- {item['id']}: {item['description']}"
            for item in self.public_entries(
                allowed_skill_ids=allowed_skill_ids,
            )
        )

    def qualified_skill_names(
        self,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> list[str]:
        plugin_name = self._plugin_name()
        if not plugin_name:
            return []
        return [
            f"{plugin_name}:{entry['id']}"
            for entry in self.entries(allowed_skill_ids=allowed_skill_ids)
        ]

    def _plugin_name(self) -> str:
        try:
            payload = json.loads(
                (self.root / ".claude-plugin" / "plugin.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, json.JSONDecodeError):
            return ""
        return _trim(payload.get("name")) if isinstance(payload, Mapping) else ""

    def _skill_file(self, relative_path: str) -> Optional[Path]:
        candidate = (self.root / relative_path / "SKILL.md").resolve()
        root = self.root.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

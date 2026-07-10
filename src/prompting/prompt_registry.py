import json
from pathlib import Path
from string import Template
from typing import Any, Dict, List


class PromptRegistryError(ValueError):
    pass


class PromptRegistry:
    def __init__(self, prompts_root: str = "src/prompts") -> None:
        self.prompts_root = Path(prompts_root)
        self._entries = self._load_entries()

    def _load_entries(self) -> Dict[str, Dict[str, Any]]:
        entries: Dict[str, Dict[str, Any]] = {}
        if not self.prompts_root.exists():
            return entries
        for path in sorted(self.prompts_root.rglob("*.json")):
            if path.name.startswith("._"):
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            prompt_key = str(payload.get("prompt_key") or "").strip()
            if not prompt_key:
                raise PromptRegistryError(f"missing prompt_key: {path}")
            if prompt_key in entries:
                raise PromptRegistryError(f"duplicate prompt_key '{prompt_key}'")
            payload["_path"] = str(path)
            entries[prompt_key] = payload
        return entries

    def get(self, prompt_key: str) -> Dict[str, Any]:
        entry = self._entries.get(str(prompt_key).strip())
        if not entry:
            raise PromptRegistryError(f"prompt '{prompt_key}' not found")
        return entry

    def render_messages(self, prompt_key: str, variables: Dict[str, Any]) -> List[Dict[str, str]]:
        entry = self.get(prompt_key)
        messages = entry.get("messages")
        if not isinstance(messages, list) or not messages:
            raise PromptRegistryError(f"prompt '{prompt_key}' has no messages")
        rendered: List[Dict[str, str]] = []
        normalized = {str(key): self._stringify(value) for key, value in (variables or {}).items()}
        for item in messages:
            role = str((item or {}).get("role") or "").strip()
            template = self._resolve_template(entry=entry, item=item)
            if not role:
                raise PromptRegistryError(f"prompt '{prompt_key}' has message without role")
            rendered.append(
                {
                    "role": role,
                    "content": Template(template).safe_substitute(normalized),
                }
            )
        return rendered

    def list_entries(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for prompt_key, payload in sorted(self._entries.items()):
            rows.append(
                {
                    "prompt_key": prompt_key,
                    "layer": str(payload.get("layer") or ""),
                    "owner": str(payload.get("owner") or ""),
                    "version": str(payload.get("version") or ""),
                    "path": str(payload.get("_path") or ""),
                }
            )
        return rows

    def _resolve_template(self, *, entry: Dict[str, Any], item: Dict[str, Any]) -> str:
        inline_template = item.get("template")
        if inline_template is not None:
            return str(inline_template)

        template_path = str((item or {}).get("template_path") or "").strip()
        if not template_path:
            return ""

        prompt_path = Path(str(entry.get("_path") or ""))
        if not prompt_path:
            raise PromptRegistryError("prompt entry missing _path")

        resolved = (prompt_path.parent / template_path).resolve()
        if not resolved.exists() or not resolved.is_file():
            raise PromptRegistryError(f"template_path not found: {template_path} for prompt {entry.get('prompt_key')}")
        return resolved.read_text(encoding="utf-8")

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)


_DEFAULT_REGISTRY: PromptRegistry | None = None


def get_prompt_registry() -> PromptRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = PromptRegistry()
    return _DEFAULT_REGISTRY

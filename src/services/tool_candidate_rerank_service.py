from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_flash_json


class ToolCandidateRerankService:
    VALID_TAGS = {"FULL", "PART", "RELATED", "PASS"}

    def __init__(self) -> None:
        self.registry = get_prompt_registry()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def rerank(
        self,
        *,
        task_desc: str,
        candidate_tools: Optional[List[Dict[str, Any]]] = None,
        enable_llm: bool = True,
    ) -> Dict[str, Any]:
        tools = [item for item in (candidate_tools or []) if isinstance(item, dict) and self._trim(item.get("tool_name"))]
        if not task_desc or not tools:
            return {
                "items": [],
                "selected_tools": [self._trim(item.get("tool_name")) for item in tools if self._trim(item.get("tool_name"))],
                "source": "skipped",
                "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        if enable_llm:
            try:
                messages = self.registry.render_messages(
                    "system.assistant.tool_candidate_rerank",
                    {
                        "task_desc": self._trim(task_desc),
                        "candidate_tools": self._build_prompt_candidates(tools),
                    },
                )
                payload, usage = chat_qwen_flash_json(messages, enable_think=False)
                normalized = self._normalize_payload(payload, tools)
                normalized["selected_tools"] = self._select_tools(normalized["items"], tools)
                normalized["source"] = "llm"
                normalized["llm_usage"] = usage if isinstance(usage, dict) else {}
                normalized["messages"] = messages
                return normalized
            except Exception:
                pass

        fallback_items = [
            {"tool_name": self._trim(item.get("tool_name")), "tag": "RELATED", "reason": ""}
            for item in tools
            if self._trim(item.get("tool_name"))
        ]
        return {
            "items": fallback_items,
            "selected_tools": self._select_tools(fallback_items, tools),
            "source": "fallback",
            "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def _build_prompt_candidates(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in tools[:8]:
            rows.append(
                {
                    "tool_name": self._trim(item.get("tool_name")),
                    "display_name": self._trim(item.get("display_name")),
                    "purpose": self._trim(item.get("purpose")),
                    "best_for": [self._trim(x) for x in (item.get("best_for") or []) if self._trim(x)],
                    "subject_tags": [self._trim(x) for x in (item.get("subject_tags") or []) if self._trim(x)],
                    "tool_priority": int(item.get("tool_priority") or 1),
                    "required_inputs": [self._trim(x) for x in (item.get("required_inputs") or []) if self._trim(x)],
                    "output_fields": [self._trim(x) for x in (item.get("output_fields") or []) if self._trim(x)],
                }
            )
        return rows

    def _normalize_payload(self, payload: Any, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        rows = payload if isinstance(payload, list) else payload.get("items") if isinstance(payload, dict) else []
        allowed = {self._trim(item.get("tool_name")) for item in candidates if self._trim(item.get("tool_name"))}
        normalized: List[Dict[str, str]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            tool_name = self._trim(row.get("tool_name"))
            tag = self._trim(row.get("tag")).upper()
            if not tool_name or tool_name not in allowed or tag not in self.VALID_TAGS:
                continue
            normalized.append(
                {
                    "tool_name": tool_name,
                    "tag": tag,
                    "reason": self._trim(row.get("reason")),
                }
            )
        seen = {item["tool_name"] for item in normalized}
        for item in candidates:
            tool_name = self._trim(item.get("tool_name"))
            if tool_name and tool_name not in seen:
                normalized.append({"tool_name": tool_name, "tag": "PASS", "reason": ""})
        return {"items": normalized}

    def _select_tools(self, tagged_items: List[Dict[str, str]], candidates: List[Dict[str, Any]]) -> List[str]:
        tag_map = {self._trim(item.get("tool_name")): self._trim(item.get("tag")).upper() for item in tagged_items}
        ordered_names = [self._trim(item.get("tool_name")) for item in candidates if self._trim(item.get("tool_name"))]
        strong = [name for name in ordered_names if tag_map.get(name) in {"FULL", "PART"}]
        related = [name for name in ordered_names if tag_map.get(name) == "RELATED"]
        if strong:
            selected = list(strong)
            if len(selected) < 2:
                for name in related:
                    if name not in selected:
                        selected.append(name)
                    if len(selected) >= 4:
                        break
            return selected
        selected = list(related)
        if selected:
            return selected
        # Fallback to first non-pass if model was too conservative.
        for name in ordered_names:
            if tag_map.get(name, "PASS") != "PASS":
                return [name]
        return ordered_names[:2]

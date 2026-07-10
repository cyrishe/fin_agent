from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class ReferenceResolutionService:
    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def resolve_focus(
        self,
        *,
        text: str,
        reference_memory: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str] | None:
        normalized_objects = self._normalize_objects(reference_memory)
        if not normalized_objects:
            return None
        if self._contains_any(text, ["第二个", "第2个"]):
            if len(normalized_objects) >= 2:
                item = normalized_objects[1]
                return item["type"], item["id"]
        if self._contains_any(text, ["最后一个", "最后那个"]):
            item = normalized_objects[-1]
            return item["type"], item["id"]
        if self._contains_any(text, ["刚才那个技能", "刚才那个 skill", "这个技能", "那个技能"]):
            for item in normalized_objects:
                if item["type"] == "skill":
                    return item["type"], item["id"]
        if self._contains_any(text, ["那个公司", "那个股票", "提到的那个公司", "提到的那个股票"]):
            for item in normalized_objects:
                if item["type"] in {"company", "stock"}:
                    return item["type"], item["id"]
        if self._contains_any(text, ["刚才那个", "继续刚才那个", "继续那个", "按上面的继续"]):
            item = normalized_objects[0]
            return item["type"], item["id"]
        return None

    def _normalize_objects(self, reference_memory: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        memory = reference_memory if isinstance(reference_memory, dict) else {}
        objects = memory.get("objects") if isinstance(memory.get("objects"), list) else []
        normalized: List[Dict[str, Any]] = []
        for item in objects:
            if not isinstance(item, dict):
                continue
            item_type = self._trim(item.get("object_type") or item.get("type"))
            item_id = self._trim(item.get("object_id") or item.get("id"))
            if not item_type or not item_id:
                continue
            normalized.append(
                {
                    "type": item_type,
                    "id": item_id,
                    "display_name": self._trim(item.get("display_name")) or item_id,
                    "source": self._trim(item.get("source")),
                    "source_turn_id": self._trim(item.get("source_turn_id")),
                    "order_index": int(item.get("order_index", 0) or 0),
                    "salience_score": float(item.get("salience_score", 0.0) or 0.0),
                    "is_active_focus_candidate": bool(item.get("is_active_focus_candidate", False)),
                }
            )
        normalized.sort(
            key=lambda item: (
                item["order_index"],
                0 if item["is_active_focus_candidate"] else 1,
                -item["salience_score"],
            )
        )
        return normalized

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> bool:
        return any(keyword in text for keyword in keywords)

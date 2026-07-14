from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

class CustomToolDesignProtocolError(ValueError):
    pass


class CustomToolDesignProtocolService:
    """System-owned canonical design and feedback evidence."""

    def __init__(self, *, schema_path: str = "src/skills/financial-tool-requirement-design-v3/schema.json") -> None:
        self.schema_path = Path(schema_path)

    def append_feedback(
        self,
        ledger: Any,
        *,
        text: str,
        design_round: int,
        turn_id: Optional[int] = None,
        kind: str = "feedback",
    ) -> List[Dict[str, Any]]:
        entries = [dict(item) for item in (ledger or []) if isinstance(item, Mapping)]
        normalized = str(text or "").strip()
        digest = hashlib.sha256(f"{design_round}:{turn_id or 0}:{normalized}".encode("utf-8")).hexdigest()[:12]
        entries.append({
            "feedback_id": f"feedback_{digest}",
            "round": int(design_round),
            "turn_id": int(turn_id or 0),
            "kind": str(kind or "feedback"),
            "text": normalized,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        return entries

    @staticmethod
    def canonical_from_state(state: Mapping[str, Any]) -> Dict[str, Any]:
        design = state.get("design_contract") if isinstance(state.get("design_contract"), Mapping) else state.get("partial_design")
        return {
            "status": "review" if str(state.get("status") or "") == "awaiting_design_confirmation" else "clarification",
            "understanding": copy.deepcopy(dict(state.get("understanding") or {})),
            "questions": copy.deepcopy(list(state.get("questions") or [])),
            "design": copy.deepcopy(dict(design or {})),
            "existing_analysis": copy.deepcopy(dict(state.get("existing_analysis") or {})),
        }

    def apply_revision(self, canonical: Mapping[str, Any], revision: Mapping[str, Any]) -> Dict[str, Any]:
        merged = copy.deepcopy(dict(canonical))
        for raw_change in revision.get("changes") or []:
            if not isinstance(raw_change, Mapping):
                raise CustomToolDesignProtocolError("design change must be an object")
            self._apply_change(merged, raw_change)
        merged["status"] = str(revision.get("status") or "clarification")
        merged["questions"] = copy.deepcopy(list(revision.get("questions") or []))
        self.validate_canonical(merged)
        return merged

    def validate_canonical(self, canonical: Mapping[str, Any]) -> None:
        # Import lazily: skill_runtime initializes the tool registry, which imports
        # custom_tool_service and would otherwise create a module-load cycle.
        from src.skill_runtime.schema_validator import SchemaValidator

        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        try:
            SchemaValidator().validate(dict(canonical), schema)
        except Exception as exc:
            raise CustomToolDesignProtocolError(f"merged design is invalid: {exc}") from exc

    def _apply_change(self, canonical: Dict[str, Any], change: Mapping[str, Any]) -> None:
        path = str(change.get("path") or "").strip()
        try:
            value = json.loads(str(change.get("value_json") or "null"))
        except json.JSONDecodeError as exc:
            raise CustomToolDesignProtocolError(f"invalid value_json for {path}") from exc

        parent, field = self._resolve_parent(canonical, path)
        if field not in parent:
            raise CustomToolDesignProtocolError(f"invalid design patch path: {path}")
        parent[field] = value

    @staticmethod
    def _resolve_parent(canonical: Dict[str, Any], path: str) -> tuple[Dict[str, Any], str]:
        parts = path.split(".")
        if len(parts) == 1:
            return canonical, parts[0]
        if len(parts) != 2 or not isinstance(canonical.get(parts[0]), dict):
            raise CustomToolDesignProtocolError(f"invalid design patch path: {path}")
        return canonical[parts[0]], parts[1]

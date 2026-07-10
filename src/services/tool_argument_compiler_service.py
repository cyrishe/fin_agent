from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.tools.registry import normalize_tool_args_for_definition
from src.utils.ai_service import chat_qwen, extract_first_json


class ToolArgumentCompilerService:
    def __init__(
        self,
        *,
        tool_definitions_dir: str = "src/tools/definitions",
        tool_specs_dir: str = "src/tools/specs",
    ) -> None:
        self.registry = get_prompt_registry()
        self.tool_definitions_dir = Path(tool_definitions_dir)
        self.tool_specs_dir = Path(tool_specs_dir)

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def compile_arguments(
        self,
        *,
        tool_name: str,
        user_objective: str,
        step_intent: str = "",
        current_arguments: Optional[Dict[str, Any]] = None,
        current_input_binding: Optional[Dict[str, Any]] = None,
        planner_tool: Optional[Dict[str, Any]] = None,
        enable_llm: bool = True,
    ) -> Dict[str, Any]:
        normalized_tool_name = self._trim(tool_name)
        if not normalized_tool_name:
            return self._empty_result(tool_name="", source="empty_tool_name")
        current_arguments = dict(current_arguments or {})
        current_input_binding = dict(current_input_binding or {})
        if not enable_llm:
            return self._empty_result(tool_name=normalized_tool_name, source="disabled")
        contract = self._build_tool_contract(normalized_tool_name, planner_tool=planner_tool or {})
        if not contract.get("input_schema"):
            return self._empty_result(tool_name=normalized_tool_name, source="no_input_schema")
        messages = self.registry.render_messages(
            "system.agent_runtime.tool_argument_compiler",
            {
                "user_objective": self._trim(user_objective),
                "step_intent": self._trim(step_intent),
                "tool_contract_markdown": self._format_tool_contract_markdown(contract),
                "current_arguments_json": json.dumps(current_arguments, ensure_ascii=False, indent=2),
                "current_input_binding_json": json.dumps(current_input_binding, ensure_ascii=False, indent=2),
            },
        )
        try:
            text, usage = chat_qwen(messages, enable_think=False)
            payload = extract_first_json(text, log_errors=False)
        except Exception as exc:
            return self._empty_result(
                tool_name=normalized_tool_name,
                source="llm_error",
                notes=[str(exc)],
            )
        if not isinstance(payload, dict):
            return self._empty_result(tool_name=normalized_tool_name, source="empty_payload")
        compiled_arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
        merged_arguments = {**compiled_arguments, **current_arguments}
        contract_normalized = normalize_tool_args_for_definition(normalized_tool_name, merged_arguments)
        normalized_arguments = (
            contract_normalized.get("arguments")
            if isinstance(contract_normalized.get("arguments"), dict)
            else merged_arguments
        )
        missing_required = (
            contract_normalized.get("missing_required")
            if isinstance(contract_normalized.get("missing_required"), list)
            else []
        )
        dropped_fields = (
            contract_normalized.get("dropped_fields")
            if isinstance(contract_normalized.get("dropped_fields"), list)
            else []
        )
        used_defaults = (
            contract_normalized.get("used_defaults")
            if isinstance(contract_normalized.get("used_defaults"), dict)
            else {}
        )
        return {
            "tool_name": normalized_tool_name,
            "status": "ready" if normalized_arguments or not missing_required else "empty",
            "source": "llm",
            "arguments": normalized_arguments,
            "missing_arguments": missing_required,
            "dropped_arguments": dropped_fields,
            "defaults_applied_by_schema": used_defaults,
            "reason": self._trim(payload.get("reason")),
            "llm_usage": usage if isinstance(usage, dict) else {},
        }

    def _empty_result(
        self,
        *,
        tool_name: str,
        source: str,
        notes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return {
            "tool_name": tool_name,
            "status": "empty",
            "source": source,
            "arguments": {},
            "missing_arguments": [],
            "dropped_arguments": [],
            "defaults_applied_by_schema": {},
            "reason": "",
            "notes": notes or [],
            "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def _build_tool_contract(self, tool_name: str, *, planner_tool: Dict[str, Any]) -> Dict[str, Any]:
        definition = self._load_json_if_exists(self.tool_definitions_dir / f"{tool_name}.tool.json")
        spec = self._load_json_if_exists(self.tool_specs_dir / f"{tool_name}.spec.json")
        purpose = self._trim((planner_tool or {}).get("purpose")) or self._trim(spec.get("purpose")) or self._trim(((definition.get("identity") or {}) if isinstance(definition.get("identity"), dict) else {}).get("description"))
        best_for = [
            self._trim(item)
            for item in ((planner_tool or {}).get("best_for") or spec.get("best_for") or [])
            if self._trim(item)
        ]
        input_notes = [
            self._trim(item)
            for item in ((planner_tool or {}).get("input_notes") or ((spec.get("input_guidance") or {}) if isinstance(spec.get("input_guidance"), dict) else {}).get("notes") or [])
            if self._trim(item)
        ]
        return {
            "tool_name": tool_name,
            "display_name": self._trim(((definition.get("identity") or {}) if isinstance(definition.get("identity"), dict) else {}).get("display_name")) or tool_name,
            "purpose": purpose,
            "best_for": best_for,
            "invocation_example": self._trim(spec.get("invocation_example")) or self._trim(definition.get("invocation_example")),
            "input_schema": self._load_tool_input_schema_fields(definition),
            "input_notes": input_notes,
        }

    def _load_tool_input_schema_fields(self, definition: Dict[str, Any]) -> List[Dict[str, Any]]:
        schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        input_schema = schemas.get("input") if isinstance(schemas.get("input"), dict) else {}
        properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
        required = {
            self._trim(item)
            for item in (input_schema.get("required") or [])
            if self._trim(item)
        }
        rows: List[Dict[str, Any]] = []
        for field_name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                continue
            rows.append(
                {
                    "name": self._trim(field_name),
                    "type": self._schema_type(field_schema),
                    "required": self._trim(field_name) in required,
                    "desc": self._trim(field_schema.get("description") or field_schema.get("title")),
                    "enum": self._schema_enum(field_schema),
                    "default": field_schema.get("default"),
                    "minimum": field_schema.get("minimum"),
                    "maximum": field_schema.get("maximum"),
                }
            )
        return rows

    def _format_tool_contract_markdown(self, contract: Dict[str, Any]) -> str:
        rows = [f"### 工具：{self._trim(contract.get('tool_name'))}"]
        display_name = self._trim(contract.get("display_name"))
        if display_name:
            rows.append(f"- display_name: {display_name}")
        purpose = self._trim(contract.get("purpose"))
        if purpose:
            rows.append(f"- purpose: {purpose}")
        best_for = [self._trim(item) for item in (contract.get("best_for") or []) if self._trim(item)]
        if best_for:
            rows.append(f"- best_for: {', '.join(best_for)}")
        invocation_example = self._trim(contract.get("invocation_example"))
        if invocation_example:
            rows.append(f"- invocation_example: `{invocation_example}`")
        rows.append("- input_fields:")
        for field in (contract.get("input_schema") or []):
            if not isinstance(field, dict):
                continue
            field_name = self._trim(field.get("name"))
            if not field_name:
                continue
            constraints = self._format_field_constraints(field)
            desc = self._trim(field.get("desc"))
            detail = "；".join([item for item in [desc, constraints] if item])
            rows.append(
                f"  - `{field_name}` | `{self._trim(field.get('type')) or 'unknown'}` | "
                f"`{'required' if field.get('required') else 'optional'}` | {detail}"
            )
        input_notes = [self._trim(item) for item in (contract.get("input_notes") or []) if self._trim(item)]
        if input_notes:
            rows.append(f"- input_notes: {'；'.join(input_notes)}")
        return "\n".join(rows).strip()

    @staticmethod
    def _schema_type(field_schema: Dict[str, Any]) -> str:
        raw_type = field_schema.get("type")
        if isinstance(raw_type, list):
            types = [str(item).strip() for item in raw_type if str(item).strip()]
            return "|".join(types) if types else "unknown"
        return str(raw_type or "unknown").strip()

    @staticmethod
    def _schema_enum(field_schema: Dict[str, Any]) -> List[str]:
        values = field_schema.get("enum")
        if not isinstance(values, list):
            return []
        return [str(item) for item in values if item is not None and str(item).strip()]

    def _format_field_constraints(self, field: Dict[str, Any]) -> str:
        parts: List[str] = []
        enum_values = [self._trim(item) for item in (field.get("enum") or []) if self._trim(item)]
        if enum_values:
            parts.append("allowed: " + ", ".join(enum_values[:8]))
        if field.get("default") not in {None, ""}:
            parts.append(f"default: {field.get('default')}")
        minimum = field.get("minimum")
        maximum = field.get("maximum")
        if minimum is not None or maximum is not None:
            bounds = []
            if minimum is not None:
                bounds.append(f">={minimum}")
            if maximum is not None:
                bounds.append(f"<={maximum}")
            parts.append("range: " + " ".join(bounds))
        return "；".join(parts)

    @staticmethod
    def _load_json_if_exists(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

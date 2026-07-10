from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from src.services.active_tool_registry_service import ActiveToolRegistryService
from src.services.custom_tool_service import CustomToolError, CustomToolStoreService


class ToolRuntimePreflightService:
    """Validates whether a tool call is allowed by the active registry view.

    This service is read-only and does not execute tools. It is intentionally
    separate from ToolPlanRuntimeService so the runtime chain can adopt the gate
    explicitly after focused regression coverage exists.
    """

    def __init__(
        self,
        *,
        active_tool_registry_service: Optional[ActiveToolRegistryService] = None,
        tool_aliases: Optional[Mapping[str, str]] = None,
        custom_tool_store_service: Optional[CustomToolStoreService] = None,
    ) -> None:
        self.active_tool_registry_service = active_tool_registry_service or ActiveToolRegistryService()
        self.tool_aliases = dict(tool_aliases) if isinstance(tool_aliases, Mapping) else self._load_tool_aliases()
        self.custom_tool_store_service = custom_tool_store_service or CustomToolStoreService()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def validate_tool_call(
        self,
        *,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        custom_tool_owner_ids: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        raw_name = self._trim(tool_name)
        canonical_name = self.tool_aliases.get(raw_name, raw_name)
        args = arguments if isinstance(arguments, dict) else {}
        registry_map = {
            self._trim(item.get("tool_name")): item
            for item in self.active_tool_registry_service.list_registry(include_inactive=True)
            if self._trim(item.get("tool_name"))
        }
        registry_item = registry_map.get(canonical_name)
        if not registry_item and self.custom_tool_store_service.exists(canonical_name):
            try:
                bundle = self.custom_tool_store_service.load_for_runtime(
                    canonical_name,
                    owner_ids=custom_tool_owner_ids or [],
                    allow_inactive=False,
                )
            except CustomToolError as exc:
                return self._blocked(
                    tool_name=canonical_name,
                    reason="custom_tool_unavailable",
                    arguments=args,
                    details={"message": str(exc)},
                )
            manifest = bundle.get("manifest") if isinstance(bundle.get("manifest"), dict) else {}
            input_schema = bundle.get("input_schema") if isinstance(bundle.get("input_schema"), dict) else {}
            registry_item = {
                "tool_name": canonical_name,
                "status": "active",
                "enabled": True,
                "planner_visible": True,
                "sync_status": "ready",
                "implementation_kind": "custom_python",
                "implementation_target": canonical_name,
                "required_inputs": input_schema.get("required") if isinstance(input_schema.get("required"), list) else [],
                "input_schema": input_schema,
                "source_manifest": {"custom_tool": True},
                "display_name": self._trim(manifest.get("display_name")) or canonical_name,
            }
        if not registry_item:
            return self._blocked(
                tool_name=canonical_name,
                reason="unknown_tool",
                arguments=args,
                details={"raw_tool_name": raw_name},
            )
        if registry_item.get("planner_visible") is not True:
            return self._blocked(
                tool_name=canonical_name,
                reason="tool_not_active",
                arguments=args,
                details={
                    "status": self._trim(registry_item.get("status")),
                    "enabled": registry_item.get("enabled") is True,
                    "availability": registry_item.get("availability") if isinstance(registry_item.get("availability"), dict) else {},
                    "sync_status": self._trim(registry_item.get("sync_status")),
                    "sync_errors": [
                        self._trim(item)
                        for item in (registry_item.get("sync_errors") or [])
                        if self._trim(item)
                    ],
                },
            )
        missing_required = [
            field
            for field in (registry_item.get("required_inputs") or [])
            if self._is_missing_value(args.get(field))
        ]
        if missing_required:
            return self._blocked(
                tool_name=canonical_name,
                reason="missing_required_input",
                arguments=args,
                details={"missing_required": missing_required},
            )
        schema_error = self._validate_arguments_against_schema(registry_item=registry_item, arguments=args)
        if schema_error:
            return self._blocked(
                tool_name=canonical_name,
                reason=schema_error["reason"],
                arguments=args,
                details=schema_error["details"],
            )
        return {
            "ok": True,
            "status": "ready",
            "tool_name": canonical_name,
            "arguments": dict(args),
            "reason": "",
            "registry": self._registry_summary(registry_item),
        }

    def _blocked(
        self,
        *,
        tool_name: str,
        reason: str,
        arguments: Dict[str, Any],
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "ok": False,
            "status": "blocked",
            "tool_name": tool_name,
            "arguments": dict(arguments),
            "reason": reason,
            "details": details,
        }

    def _registry_summary(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": self._trim(item.get("status")),
            "enabled": item.get("enabled") is True,
            "planner_visible": item.get("planner_visible") is True,
            "sync_status": self._trim(item.get("sync_status")),
            "implementation_kind": self._trim(item.get("implementation_kind")),
            "implementation_target": self._trim(item.get("implementation_target")),
        }

    def _is_missing_value(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, tuple, dict, set)):
            return len(value) == 0
        return False

    def _load_tool_aliases(self) -> Dict[str, str]:
        try:
            from src.tools.registry import TOOL_ALIASES
        except Exception:
            return {}
        return {
            self._trim(name): self._trim(target)
            for name, target in TOOL_ALIASES.items()
            if self._trim(name) and self._trim(target)
        }

    def _validate_arguments_against_schema(
        self,
        *,
        registry_item: Dict[str, Any],
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        input_schema = self._load_input_schema(registry_item)
        properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
        if not properties:
            return {}
        for field_name, value in arguments.items():
            schema = properties.get(field_name)
            if not isinstance(schema, dict):
                continue
            enum_values = schema.get("enum")
            if isinstance(enum_values, list) and enum_values and value not in enum_values:
                return {
                    "reason": "invalid_argument_enum",
                    "details": {
                        "field": field_name,
                        "actual": value,
                        "allowed_values": enum_values,
                    },
                }
            numeric_value = self._coerce_number(value)
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")
            if numeric_value is not None and minimum is not None and numeric_value < float(minimum):
                return {
                    "reason": "invalid_argument_range",
                    "details": {
                        "field": field_name,
                        "actual": value,
                        "minimum": minimum,
                    },
                }
            if numeric_value is not None and maximum is not None and numeric_value > float(maximum):
                return {
                    "reason": "invalid_argument_range",
                    "details": {
                        "field": field_name,
                        "actual": value,
                        "maximum": maximum,
                    },
                }
        return {}

    def _load_input_schema(self, registry_item: Dict[str, Any]) -> Dict[str, Any]:
        inline_schema = registry_item.get("input_schema") if isinstance(registry_item.get("input_schema"), dict) else {}
        if inline_schema:
            return inline_schema
        source_manifest = registry_item.get("source_manifest") if isinstance(registry_item.get("source_manifest"), dict) else {}
        definition_path = self._trim(source_manifest.get("definition_path"))
        if not definition_path:
            return {}
        try:
            definition = json.loads(Path(definition_path).read_text(encoding="utf-8"))
        except Exception:
            return {}
        schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        input_schema = schemas.get("input") if isinstance(schemas.get("input"), dict) else {}
        return input_schema

    def _coerce_number(self, value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

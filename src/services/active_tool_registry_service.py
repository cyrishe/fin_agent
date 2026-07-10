from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


class ActiveToolRegistryService:
    """Builds the planner-facing active tool view from tool definitions.

    This is a read-only registry view. It does not execute tools, write
    artifacts, or replace the runtime `run_tool` registry.
    """

    ACTIVE_STATUS = "active"
    DISABLED_STATUSES = {"disabled", "deprecated", "archived", "retired"}
    VALID_SUBJECT_TAGS = {"stock", "fund", "bond", "industry", "plate", "index", "hot_event", "general"}

    def __init__(
        self,
        *,
        definitions_dir: str = "src/tools/definitions",
        specs_dir: str = "src/tools/specs",
        schemas_dir: str = "src/tools/schemas",
        tool_hub_path: str = "src/tools/tool_hub.json",
        implementation_targets: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.definitions_dir = Path(definitions_dir)
        self.specs_dir = Path(specs_dir)
        self.schemas_dir = Path(schemas_dir)
        self.tool_hub_path = Path(tool_hub_path)
        self._implementation_targets = dict(implementation_targets) if isinstance(implementation_targets, Mapping) else None

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def list_active_tools(self) -> List[Dict[str, Any]]:
        return [item for item in self.list_registry(include_inactive=True) if item.get("planner_visible") is True]

    def list_registry(self, *, include_inactive: bool = False) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not self.definitions_dir.exists():
            return rows
        hub_map = self._load_tool_hub_map()
        implementation_targets = self._load_implementation_targets()
        for path in sorted(self.definitions_dir.glob("*.tool.json")):
            if path.name.startswith("._"):
                continue
            row = self._build_row(
                path=path,
                hub_entry=hub_map.get(path.name.replace(".tool.json", "")) or {},
                implementation_targets=implementation_targets,
            )
            if not row:
                continue
            if include_inactive or row.get("planner_visible") is True:
                rows.append(row)
        return rows

    def _build_row(
        self,
        *,
        path: Path,
        hub_entry: Dict[str, Any],
        implementation_targets: Dict[str, str],
    ) -> Dict[str, Any]:
        tool_name_from_path = path.name.replace(".tool.json", "")
        try:
            definition = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "tool_name": tool_name_from_path,
                "display_name": tool_name_from_path,
                "status": "invalid",
                "enabled": False,
                "availability": self._normalize_availability({}),
                "planner_visible": False,
                "sync_status": "failed",
                "sync_errors": [f"definition_json_error:{type(exc).__name__}"],
            }

        tool_name = self._trim(definition.get("name")) or tool_name_from_path
        identity = definition.get("identity") if isinstance(definition.get("identity"), dict) else {}
        spec = self._load_json(self.specs_dir / f"{tool_name}.spec.json")
        output_schema = self._load_output_schema(tool_name=tool_name, definition=definition)
        input_schema = self._input_schema(definition)
        implementation = self._implementation(definition)
        safety = definition.get("safety") if isinstance(definition.get("safety"), dict) else {}
        match_contract = self._match_contract(definition)
        availability = self._normalize_availability(definition)
        status = self._normalize_status(definition.get("status"))
        enabled = definition.get("enabled") is not False
        registered_target = self._trim(implementation_targets.get(tool_name))
        sync_status, sync_errors = self._sync_status(
            tool_name=tool_name,
            status=status,
            enabled=enabled,
            input_schema=input_schema,
            implementation=implementation,
            registered_target=registered_target,
        )
        planner_visible = self._planner_visible(
            status=status,
            enabled=enabled,
            availability=availability,
            sync_status=sync_status,
        )
        input_guidance = spec.get("input_guidance") if isinstance(spec.get("input_guidance"), dict) else {}
        input_properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
        required_inputs = [
            self._trim(item)
            for item in (input_guidance.get("required_fields") or input_schema.get("required") or [])
            if self._trim(item)
        ]
        optional_inputs = [
            self._trim(item)
            for item in (input_guidance.get("optional_fields") or input_properties.keys())
            if self._trim(item) and self._trim(item) not in set(required_inputs)
        ]
        return {
            "tool_name": tool_name,
            "display_name": self._trim(identity.get("display_name")) or tool_name,
            "purpose": self._trim(spec.get("purpose")) or self._trim(identity.get("description")) or self._trim(hub_entry.get("description")),
            "best_for": [self._trim(x) for x in (spec.get("best_for") or []) if self._trim(x)],
            "description": self._trim(identity.get("description")) or self._trim(hub_entry.get("description")),
            "domain": self._trim(identity.get("domain")),
            "owner": self._trim(identity.get("owner")),
            "version": self._trim(definition.get("version")),
            "status": status,
            "enabled": enabled,
            "availability": availability,
            "planner_visible": planner_visible,
            "sync_status": sync_status,
            "sync_errors": sync_errors,
            "implementation_kind": self._trim(implementation.get("kind")),
            "implementation_target": self._trim(implementation.get("target")),
            "registered_target": registered_target,
            "capabilities": [self._trim(x) for x in (definition.get("capabilities") or []) if self._trim(x)],
            "match_contract": match_contract,
            "subject_tags": self._normalize_subject_tags(definition.get("subject_tags")),
            "tool_priority": self._normalize_tool_priority(definition.get("tool_priority")),
            "tags": [self._trim(x) for x in (definition.get("tags") or []) if self._trim(x)],
            "keywords": [self._trim(x) for x in (hub_entry.get("keywords") or []) if self._trim(x)],
            "side_effect_level": self._trim(safety.get("side_effect")),
            "network": bool(safety.get("network")) if "network" in safety else None,
            "required_inputs": required_inputs,
            "optional_inputs": optional_inputs,
            "input_schema_type": self._trim(input_schema.get("type")),
            "input_property_names": [self._trim(item) for item in input_properties.keys() if self._trim(item)],
            "input_notes": [
                self._trim(item)
                for item in (input_guidance.get("notes") or [])
                if self._trim(item)
            ][:3],
            "output_fields": self._build_tool_output_fields(spec=spec, schema=output_schema),
            "source_manifest": {
                "definition_path": str(path),
                "spec_path": str(self.specs_dir / f"{tool_name}.spec.json"),
                "schema_path": str(self._resolve_output_schema_path(tool_name=tool_name, definition=definition)),
            },
        }

    def _normalize_subject_tags(self, value: Any) -> List[str]:
        result: List[str] = []
        for item in value or []:
            normalized = self._trim(item).lower()
            if normalized in self.VALID_SUBJECT_TAGS and normalized not in result:
                result.append(normalized)
        return result

    def _normalize_tool_priority(self, value: Any) -> int:
        try:
            priority = int(value)
        except (TypeError, ValueError):
            return 1
        return priority if priority > 0 else 1

    def _sync_status(
        self,
        *,
        tool_name: str,
        status: str,
        enabled: bool,
        input_schema: Dict[str, Any],
        implementation: Dict[str, Any],
        registered_target: str,
    ) -> tuple[str, List[str]]:
        errors: List[str] = []
        if not isinstance(input_schema, dict) or self._trim(input_schema.get("type")).lower() != "object":
            errors.append("input_schema_not_object")
        if status in self.DISABLED_STATUSES or enabled is False:
            return "disabled", errors
        implementation_kind = self._trim(implementation.get("kind")).lower()
        implementation_target = self._trim(implementation.get("target"))
        if implementation_kind in {"http", "http_tool", "remote_http"}:
            return ("failed", errors + ["missing_http_target"]) if not implementation_target else ("synced", errors)
        if not registered_target:
            errors.append("missing_runtime_registry_target")
        if implementation_target and registered_target and implementation_target != registered_target:
            errors.append("implementation_target_mismatch")
        if errors:
            if "implementation_target_mismatch" in errors:
                return "drift_detected", errors
            return "failed", errors
        return "synced", errors

    def _planner_visible(
        self,
        *,
        status: str,
        enabled: bool,
        availability: Dict[str, str],
        sync_status: str,
    ) -> bool:
        return (
            status == self.ACTIVE_STATUS
            and enabled is True
            and availability.get("lifecycle") == "active"
            and availability.get("retrieval_mode") == "retrievable"
            and availability.get("visibility") == "visible"
            and sync_status == "synced"
        )

    def _normalize_status(self, value: Any) -> str:
        status = self._trim(value).lower() or self.ACTIVE_STATUS
        if status in self.DISABLED_STATUSES:
            return status
        if status in {"draft", self.ACTIVE_STATUS}:
            return status
        return self.ACTIVE_STATUS

    def _normalize_availability(self, definition: Dict[str, Any]) -> Dict[str, str]:
        availability = definition.get("availability") if isinstance(definition.get("availability"), dict) else {}
        lifecycle = self._trim(availability.get("lifecycle") or definition.get("lifecycle") or "active").lower() or "active"
        retrieval_mode = self._trim(availability.get("retrieval_mode") or definition.get("retrieval_mode") or "retrievable").lower() or "retrievable"
        visibility = self._trim(availability.get("visibility") or definition.get("visibility") or "visible").lower() or "visible"
        if lifecycle not in {"active", "retired"}:
            lifecycle = "active"
        if retrieval_mode not in {"retrievable", "direct_only"}:
            retrieval_mode = "retrievable"
        if visibility not in {"visible", "hidden"}:
            visibility = "visible"
        return {
            "lifecycle": lifecycle,
            "retrieval_mode": retrieval_mode,
            "visibility": visibility,
        }

    def _input_schema(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        input_schema = schemas.get("input") if isinstance(schemas.get("input"), dict) else {}
        return input_schema

    def _implementation(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        profiles = definition.get("profiles") if isinstance(definition.get("profiles"), dict) else {}
        real = profiles.get("real") if isinstance(profiles.get("real"), dict) else {}
        real_impl = real.get("implementation") if isinstance(real.get("implementation"), dict) else {}
        if real_impl:
            return dict(real_impl)
        mock = profiles.get("mock") if isinstance(profiles.get("mock"), dict) else {}
        mock_impl = mock.get("implementation") if isinstance(mock.get("implementation"), dict) else {}
        return dict(mock_impl) if mock_impl else {}

    def _match_contract(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        contract = definition.get("match_contract") if isinstance(definition.get("match_contract"), dict) else {}
        result = {
            "request_families": self._string_list(contract.get("request_families")),
            "work_item_types": self._string_list(contract.get("work_item_types")),
            "capability_classes": self._string_list(contract.get("capability_classes")),
        }
        side_effect_level = self._trim(contract.get("side_effect_level"))
        if side_effect_level:
            result["side_effect_level"] = side_effect_level
        return {key: value for key, value in result.items() if value}

    def _string_list(self, value: Any) -> List[str]:
        return [self._trim(item) for item in (value or []) if self._trim(item)]

    def _load_implementation_targets(self) -> Dict[str, str]:
        if self._implementation_targets is not None:
            return dict(self._implementation_targets)
        try:
            from src.tools.registry import TOOL_REGISTRY
        except Exception:
            return {}
        return {self._trim(name): self._trim(target) for name, target in TOOL_REGISTRY.items() if self._trim(name)}

    def _load_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _load_output_schema(self, *, tool_name: str, definition: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve_output_schema_path(tool_name=tool_name, definition=definition)
        schema = self._load_json(path)
        if schema:
            return schema
        schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        output_schema = schemas.get("output") if isinstance(schemas.get("output"), dict) else {}
        return output_schema

    def _resolve_output_schema_path(self, *, tool_name: str, definition: Dict[str, Any]) -> Path:
        direct_path = self.schemas_dir / f"{tool_name}.schema.json"
        if direct_path.exists():
            return direct_path
        schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        output_schema = schemas.get("output") if isinstance(schemas.get("output"), dict) else {}
        ref = self._trim(output_schema.get("$ref"))
        if not ref:
            return direct_path
        ref_path = Path(ref)
        if ref_path.is_absolute():
            return ref_path
        return ref_path if ref_path.exists() else direct_path

    def _build_tool_output_fields(self, *, spec: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
        seen: List[str] = []
        output_guidance = spec.get("output_guidance") if isinstance(spec.get("output_guidance"), dict) else {}
        for field in output_guidance.get("high_value_for_reasoning") or []:
            normalized = self._trim(field)
            if normalized and normalized not in seen:
                seen.append(normalized)
        for field in output_guidance.get("high_value_for_render") or []:
            normalized = self._trim(field)
            if normalized and normalized not in seen:
                seen.append(normalized)
        for field in self._extract_schema_output_fields(schema):
            normalized = self._trim(field)
            if normalized and normalized not in seen:
                seen.append(normalized)
        return seen[:12]

    def _extract_schema_output_fields(self, schema: Dict[str, Any]) -> List[str]:
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        data_schema = properties.get("data") if isinstance(properties.get("data"), dict) else {}
        data_type = self._trim(data_schema.get("type")).lower()
        if data_type == "array":
            item_schema = data_schema.get("items") if isinstance(data_schema.get("items"), dict) else {}
            item_properties = item_schema.get("properties") if isinstance(item_schema.get("properties"), dict) else {}
            return [f"data[].{self._trim(name)}" for name in item_properties.keys() if self._trim(name)]
        if data_type == "object":
            data_properties = data_schema.get("properties") if isinstance(data_schema.get("properties"), dict) else {}
            return [f"data.{self._trim(name)}" for name in data_properties.keys() if self._trim(name)]
        return []

    def _load_tool_hub_map(self) -> Dict[str, Dict[str, Any]]:
        if not self.tool_hub_path.exists():
            return {}
        try:
            payload = json.loads(self.tool_hub_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        rows = payload.get("tools") if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        result: Dict[str, Dict[str, Any]] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            name = self._trim(item.get("name"))
            if name:
                result[name] = item
        return result

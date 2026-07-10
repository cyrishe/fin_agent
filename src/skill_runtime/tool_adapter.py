import json
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

from src.tools.registry import canonicalize_tool_name, is_tool_definition_disabled, list_tools, run_tool
from src.skill_runtime.models import ToolSpec


class ToolAdapter:
    _INPUT_FIELD_PRIORITY = {
        "query": 10,
        "code": 10,
        "company": 10,
        "name": 10,
        "market": 10,
        "indicator_ids": 20,
        "subject_codes": 20,
        "days": 90,
        "minute_count": 90,
    }

    def __init__(
        self,
        schema_dir: str = "src/tools/schemas",
        definitions_dir: str = "src/tools/definitions",
    ) -> None:
        self.schema_dir = Path(schema_dir)
        self.definitions_dir = Path(definitions_dir)

    def list_tool_specs(self, allowed_tools: List[str] | None = None) -> List[ToolSpec]:
        names = allowed_tools or list_tools()
        specs: List[ToolSpec] = []
        seen_names = set()
        for requested_name in names:
            canonical_name = canonicalize_tool_name(requested_name)
            if not canonical_name or canonical_name in seen_names:
                continue
            if is_tool_definition_disabled(canonical_name):
                continue
            seen_names.add(canonical_name)
            definition = self._load_tool_definition(canonical_name)
            if definition:
                specs.append(self._build_spec_from_definition(name=canonical_name, definition=definition))
                continue
            schema_path = self.schema_dir / f"{canonical_name}.schema.json"
            schema = {}
            if schema_path.exists():
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
            specs.append(ToolSpec(name=canonical_name, schema=schema))
        return specs

    def execute(
        self,
        name: str,
        arguments: Dict[str, object],
        *,
        execution_profile: str = "",
    ) -> Dict[str, object]:
        requested_profile = str(
            execution_profile or (arguments or {}).get("_execution_profile") or "real"
        ).strip() or "real"
        clean_arguments = dict(arguments or {})
        clean_arguments.pop("_execution_profile", None)

        canonical_name = canonicalize_tool_name(name)
        if is_tool_definition_disabled(canonical_name):
            raise ValueError(f"tool '{canonical_name}' is disabled")
        definition = self._load_tool_definition(canonical_name)
        if definition:
            profiles = definition.get("profiles") if isinstance(definition.get("profiles"), dict) else {}
            profile = profiles.get(requested_profile) if isinstance(profiles.get(requested_profile), dict) else {}
            if profile:
                enabled = bool(profile.get("enabled"))
                if not enabled:
                    raise ValueError(f"tool '{canonical_name}' profile '{requested_profile}' is disabled")
                implementation = profile.get("implementation") if isinstance(profile.get("implementation"), dict) else {}
                legacy_impl = profile.get("impl") if isinstance(profile.get("impl"), dict) else {}
                impl = implementation or legacy_impl
                kind = str(impl.get("kind") or "").strip()
                if requested_profile != "real" and kind == "inline_result":
                    return self._normalize_inline_result(
                        tool_name=canonical_name,
                        result=profile.get("result") or {},
                        execution_profile=requested_profile,
                    )
        return run_tool(canonical_name, clean_arguments)

    def _load_tool_definition(self, name: str) -> Dict[str, Any]:
        path = self.definitions_dir / f"{name}.tool.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _build_spec_from_definition(*, name: str, definition: Dict[str, Any]) -> ToolSpec:
        identity = definition.get("identity") if isinstance(definition.get("identity"), dict) else {}
        schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        input_schema = schemas.get("input") if isinstance(schemas.get("input"), dict) else {}
        usage_notes: List[str] = []
        tests = definition.get("tests") if isinstance(definition.get("tests"), dict) else {}
        sample_inputs = tests.get("sample_inputs") if isinstance(tests.get("sample_inputs"), list) else []
        if sample_inputs:
            usage_notes.append("可优先参考 tool definition 中的 sample_inputs")
        return ToolSpec(
            name=name,
            schema=ToolAdapter._normalize_input_schema(input_schema),
            description=str(identity.get("description") or "").strip(),
            usage_notes=usage_notes,
        )

    @staticmethod
    def _normalize_inline_result(*, tool_name: str, result: Dict[str, Any], execution_profile: str) -> Dict[str, Any]:
        normalized = deepcopy(result if isinstance(result, dict) else {})
        normalized["tool"] = str(normalized.get("tool") or tool_name).strip() or tool_name
        normalized["ok"] = True
        normalized["error"] = ""
        meta = normalized.get("meta") if isinstance(normalized.get("meta"), dict) else {}
        meta["execution_profile"] = execution_profile
        normalized["meta"] = meta
        return normalized

    @staticmethod
    def _normalize_input_schema(input_schema: Dict[str, Any]) -> Dict[str, Any]:
        schema = deepcopy(input_schema if isinstance(input_schema, dict) else {})
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        if not properties:
            return schema
        required = [str(item).strip() for item in (schema.get("required") or []) if str(item).strip()]
        ordered = OrderedDict()
        for key in required:
            if key in properties:
                ordered[key] = properties[key]
        remaining_keys = [key for key in properties.keys() if key not in ordered]
        remaining_keys.sort(key=lambda key: (ToolAdapter._INPUT_FIELD_PRIORITY.get(key, 50), key))
        for key in remaining_keys:
            value = properties[key]
            if key not in ordered:
                ordered[key] = value
        schema["properties"] = dict(ordered)
        return schema

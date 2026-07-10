import json
from pathlib import Path
from typing import Any, Dict, List

from src.services.runtime_artifact_service import RuntimeArtifactService
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.skill_runtime.tool_adapter import ToolAdapter


class ToolStudioError(ValueError):
    pass


class ToolStudioService:
    @staticmethod
    def _is_hidden_tool_definition(definition: Dict[str, Any]) -> bool:
        availability = definition.get("availability") if isinstance(definition.get("availability"), dict) else {}
        return str(availability.get("visibility") or "").strip().lower() == "hidden"

    def __init__(
        self,
        *,
        definitions_dir: str = "src/tools/definitions",
        schemas_dir: str = "src/tools/schemas",
        specs_dir: str = "src/tools/specs",
        tool_hub_path: str = "src/tools/tool_hub.json",
        finance_data_catalog_path: str = FinanceDataToolCatalogService.DEFAULT_CATALOG_PATH,
    ) -> None:
        self.definitions_dir = Path(definitions_dir)
        self.schemas_dir = Path(schemas_dir)
        self.specs_dir = Path(specs_dir)
        self.tool_hub_path = Path(tool_hub_path)
        self.finance_data_catalog = FinanceDataToolCatalogService(catalog_path=finance_data_catalog_path)
        self.runtime_artifacts = RuntimeArtifactService(
            tool_definitions_dir=definitions_dir,
            tool_schemas_dir=schemas_dir,
            tool_specs_dir=specs_dir,
            tool_hub_path=tool_hub_path,
        )
        self.tool_adapter = ToolAdapter(
            schema_dir=str(self.schemas_dir),
            definitions_dir=str(self.definitions_dir),
        )

    def load_finance_data_catalog(
        self,
        *,
        subject: str = "",
        dataview: str = "",
    ) -> Dict[str, Any]:
        return self.finance_data_catalog.build_tool_studio_payload(
            subject=str(subject or "").strip() or None,
            dataview=str(dataview or "").strip() or None,
        )

    def save_finance_data_catalog_node(
        self,
        *,
        node_type: str,
        subject: str,
        dataview: str = "",
        path: List[Any] | None = None,
        node: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        normalized_type = str(node_type or "").strip()
        payload = node if isinstance(node, dict) else {}
        if isinstance(path, list) and path:
            return {
                "mode": "path",
                "result": self.finance_data_catalog.save_metadata_node(path=path, node=payload),
            }
        if normalized_type == "subject":
            return {
                "mode": "subject",
                "subject": self.finance_data_catalog.save_subject_node(subject=subject, node=payload),
            }
        if normalized_type == "dataview":
            return {
                "mode": "dataview",
                "subject": str(subject or "").strip(),
                "dataview": self.finance_data_catalog.save_dataview_node(
                    subject=subject,
                    dataview=dataview,
                    node=payload,
                ),
            }
        raise ToolStudioError("node_type must be subject or dataview")

    def list_tools(self, *, include_hidden: bool = False) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        hub_map = self._load_tool_hub_map()
        if not self.definitions_dir.exists():
            return rows
        for path in sorted(self.definitions_dir.glob("*.tool.json")):
            if path.name.startswith("._"):
                continue
            tool_name = path.name.replace(".tool.json", "")
            try:
                definition = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not include_hidden and self._is_hidden_tool_definition(definition):
                continue
            identity = definition.get("identity") if isinstance(definition.get("identity"), dict) else {}
            profiles = definition.get("profiles") if isinstance(definition.get("profiles"), dict) else {}
            rows.append(
                {
                    "tool_name": tool_name,
                    "display_name": str(identity.get("display_name") or tool_name).strip(),
                    "description": str(identity.get("description") or "").strip(),
                    "availability": definition.get("availability") if isinstance(definition.get("availability"), dict) else {"lifecycle": "active", "retrieval_mode": "retrievable"},
                    "capabilities": [str(x).strip() for x in definition.get("capabilities", []) if str(x).strip()],
                    "status": str(definition.get("status") or "").strip(),
                    "version": str(definition.get("version") or "").strip(),
                    "real_enabled": bool(((profiles.get("real") or {}).get("enabled")) if isinstance(profiles.get("real"), dict) else False),
                    "mock_enabled": bool(((profiles.get("mock") or {}).get("enabled")) if isinstance(profiles.get("mock"), dict) else False),
                    "has_spec": (self.specs_dir / f"{tool_name}.spec.json").exists(),
                    "has_output_schema": self._resolve_output_schema_path(tool_name, definition).exists(),
                    "hub_entry": hub_map.get(tool_name) or {},
                }
            )
        return rows

    def load_tool_bundle(self, tool_name: str) -> Dict[str, Any]:
        normalized = str(tool_name or "").strip()
        if not normalized:
            raise ToolStudioError("tool_name 不能为空")
        definition_path = self.definitions_dir / f"{normalized}.tool.json"
        if not definition_path.exists():
            raise FileNotFoundError(f"tool '{normalized}' definition 不存在")

        definition = json.loads(definition_path.read_text(encoding="utf-8"))
        output_schema_path = self._resolve_output_schema_path(normalized, definition)
        output_schema_obj = self._load_json_if_exists(output_schema_path)
        spec_obj = self._load_json_if_exists(self.specs_dir / f"{normalized}.spec.json")
        hub_map = self._load_tool_hub_map()
        hub_entry = hub_map.get(normalized) or {}

        return {
            "tool_name": normalized,
            "files": {
                "definition_text": json.dumps(definition, ensure_ascii=False, indent=2),
                "output_schema_text": json.dumps(output_schema_obj, ensure_ascii=False, indent=2),
                "tool_spec_text": json.dumps(spec_obj, ensure_ascii=False, indent=2),
                "tool_hub_text": json.dumps(hub_entry, ensure_ascii=False, indent=2),
                "definition": definition,
                "output_schema": output_schema_obj,
                "tool_spec": spec_obj,
                "tool_hub": hub_entry,
            },
            "meta": {
                "definition_path": str(definition_path),
                "output_schema_path": str(output_schema_path),
                "tool_spec_path": str(self.specs_dir / f"{normalized}.spec.json"),
                "tool_hub_path": str(self.tool_hub_path),
            },
        }

    def _resolve_output_schema_path(self, tool_name: str, definition: Dict[str, Any]) -> Path:
        direct_path = self.schemas_dir / f"{tool_name}.schema.json"
        if direct_path.exists():
            return direct_path
        schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        output_schema = schemas.get("output") if isinstance(schemas.get("output"), dict) else {}
        ref = str(output_schema.get("$ref") or "").strip()
        if not ref:
            return direct_path
        ref_path = Path(ref)
        if ref_path.is_absolute():
            return ref_path
        candidate = Path(ref)
        return candidate if candidate.exists() else direct_path

    def save_tool_bundle(
        self,
        *,
        tool_name: str,
        definition_text: str,
        output_schema_text: str,
        tool_spec_text: str,
        tool_hub_text: str,
    ) -> Dict[str, Any]:
        normalized = str(tool_name or "").strip()
        if not normalized:
            raise ToolStudioError("tool_name 不能为空")

        definition = self._parse_required_json(definition_text, "tool definition")
        output_schema = self._parse_required_json(output_schema_text, "output schema")
        tool_spec = self._parse_required_json(tool_spec_text, "tool spec")
        tool_hub_entry = self._parse_required_json(tool_hub_text, "tool hub entry")

        if str(definition.get("name") or "").strip() != normalized:
            raise ToolStudioError("definition.name 必须与 tool_name 一致")
        if str(tool_hub_entry.get("name") or "").strip() != normalized:
            raise ToolStudioError("tool_hub.name 必须与 tool_name 一致")
        availability = definition.get("availability") if isinstance(definition.get("availability"), dict) else {}
        definition["availability"] = {
            "lifecycle": str(availability.get("lifecycle") or "active").strip() or "active",
            "retrieval_mode": str(availability.get("retrieval_mode") or "retrievable").strip() or "retrievable",
            "visibility": str(availability.get("visibility") or "visible").strip() or "visible",
        }
        definition["schemas"] = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        definition["schemas"]["output"] = {
            "$ref": f"src/tools/schemas/{normalized}.schema.json",
        }

        self.definitions_dir.mkdir(parents=True, exist_ok=True)
        self.schemas_dir.mkdir(parents=True, exist_ok=True)
        self.specs_dir.mkdir(parents=True, exist_ok=True)

        self._write_json_atomic(self.definitions_dir / f"{normalized}.tool.json", definition)
        self._write_json_atomic(self.schemas_dir / f"{normalized}.schema.json", output_schema)
        self._write_json_atomic(self.specs_dir / f"{normalized}.spec.json", tool_spec)
        self._save_tool_hub_entry(tool_hub_entry)
        sync_status: Dict[str, Any]
        try:
            sync_status = {
                "status": "synced",
                "result": self.runtime_artifacts.sync_tool(normalized, source_type="ui", changed_by="tool_studio"),
            }
        except Exception as exc:
            sync_status = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        bundle = self.load_tool_bundle(normalized)
        bundle.setdefault("meta", {})["runtime_artifact_sync"] = sync_status
        return bundle

    def update_tool_availability(
        self,
        *,
        tool_name: str,
        lifecycle: str = "",
        retrieval_mode: str = "",
    ) -> Dict[str, Any]:
        bundle = self.load_tool_bundle(tool_name)
        files = bundle.get("files") if isinstance(bundle, dict) else {}
        definition = files.get("definition") if isinstance(files.get("definition"), dict) else {}
        availability = definition.get("availability") if isinstance(definition.get("availability"), dict) else {}
        definition["availability"] = {
            "lifecycle": str(lifecycle or availability.get("lifecycle") or "active").strip() or "active",
            "retrieval_mode": str(retrieval_mode or availability.get("retrieval_mode") or "retrievable").strip() or "retrievable",
            "visibility": str(availability.get("visibility") or "visible").strip() or "visible",
        }
        return self.save_tool_bundle(
            tool_name=str(tool_name or "").strip(),
            definition_text=json.dumps(definition, ensure_ascii=False, indent=2),
            output_schema_text=json.dumps(files.get("output_schema") or {}, ensure_ascii=False, indent=2),
            tool_spec_text=json.dumps(files.get("tool_spec") or {}, ensure_ascii=False, indent=2),
            tool_hub_text=json.dumps(files.get("tool_hub") or {}, ensure_ascii=False, indent=2),
        )

    def build_tool_template_bundle(self, tool_name: str) -> Dict[str, Any]:
        normalized = str(tool_name or "").strip()
        if not normalized:
            raise ToolStudioError("tool_name 不能为空")

        definition = {
            "name": normalized,
            "version": "v1",
            "status": "draft",
            "auth": "public",
            "availability": {
                "lifecycle": "active",
                "retrieval_mode": "retrievable",
                "visibility": "visible",
            },
            "identity": {
                "display_name": normalized,
                "description": "",
                "owner": "tools",
                "domain": "generic",
            },
            "capabilities": [],
            "tags": [],
            "schemas": {
                "input": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": f"{normalized}.input.schema.json",
                    "title": f"{normalized} Input",
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "output": {
                    "$ref": f"src/tools/schemas/{normalized}.schema.json",
                },
            },
            "defaults": {},
            "profiles": {
                "real": {
                    "enabled": False,
                    "implementation": {
                        "kind": "python_callable",
                        "target": f"src.tools.{normalized}_tool:run",
                    },
                },
                "mock": {
                    "enabled": True,
                    "implementation": {
                        "kind": "inline_result",
                    },
                    "result": {
                        "tool": normalized,
                        "ok": True,
                        "data": {},
                        "error": "",
                        "meta": {
                            "execution_profile": "mock",
                        },
                    },
                },
            },
            "safety": {
                "side_effect": "none",
                "network": False,
                "persistence": False,
            },
            "runtime_hints": {
                "cost_class": "low",
                "latency_class": "medium",
                "supports_stream": False,
                "supports_async_submit": False,
            },
            "retention": {
                "high_value_for_reasoning": [],
                "high_value_for_render": [],
                "usually_too_large_for_prompt": [],
                "drop_default": [],
                "recommended_reducers": {},
            },
            "post_process": {
                "deterministic": {"enabled": False, "handler": ""},
                "llm": {"enabled": False, "prompt_key": ""},
            },
            "tests": {
                "sample_inputs": [],
                "golden_cases": [],
            },
            "ui_hints": {
                "editable_sections": [
                    "identity",
                    "schemas.input",
                    "schemas.output",
                    "capabilities",
                    "profiles",
                    "retention",
                    "post_process",
                    "tests",
                ],
            },
        }

        output_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"{normalized}.schema.json",
            "title": f"{normalized} Output",
            "type": "object",
            "required": ["tool", "ok", "data", "error"],
            "properties": {
                "tool": {"const": normalized},
                "ok": {"type": "boolean"},
                "data": {"type": "object"},
                "error": {"type": "string"},
            },
            "additionalProperties": True,
        }

        tool_spec = {
            "tool_name": normalized,
            "purpose": "",
            "best_for": [],
            "input_guidance": {
                "required_fields": [],
                "optional_fields": [],
                "notes": [],
            },
            "output_guidance": {
                "high_value_for_reasoning": [],
                "high_value_for_render": [],
                "usually_too_large_for_prompt": [],
                "drop_default": [],
                "recommended_reducers": {},
            },
        }

        tool_hub = {
            "name": normalized,
            "description": "",
            "capabilities": [],
            "keywords": [],
            "priority": 5,
        }

        return {
            "tool_name": normalized,
            "files": {
                "definition_text": json.dumps(definition, ensure_ascii=False, indent=2),
                "output_schema_text": json.dumps(output_schema, ensure_ascii=False, indent=2),
                "tool_spec_text": json.dumps(tool_spec, ensure_ascii=False, indent=2),
                "tool_hub_text": json.dumps(tool_hub, ensure_ascii=False, indent=2),
                "definition": definition,
                "output_schema": output_schema,
                "tool_spec": tool_spec,
                "tool_hub": tool_hub,
            },
            "meta": {
                "template": True,
            },
        }

    def run_tool(
        self,
        *,
        tool_name: str,
        arguments: Dict[str, Any] | None = None,
        execution_profile: str = "mock",
    ) -> Dict[str, Any]:
        normalized = str(tool_name or "").strip()
        if not normalized:
            raise ToolStudioError("tool_name 不能为空")
        definition_path = self.definitions_dir / f"{normalized}.tool.json"
        if not definition_path.exists():
            raise FileNotFoundError(f"tool '{normalized}' definition 不存在")
        args = arguments or {}
        if not isinstance(args, dict):
            raise ToolStudioError("arguments 顶层必须是对象")
        profile = str(execution_profile or "mock").strip() or "mock"
        result = self.tool_adapter.execute(normalized, args, execution_profile=profile)
        return {
            "tool_name": normalized,
            "execution_profile": profile,
            "arguments": args,
            "result": result,
        }

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
            raise ToolStudioError(f"{label} 不是合法 JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise ToolStudioError(f"{label} 顶层必须是对象")
        return obj

    @staticmethod
    def _write_json_atomic(path: Path, payload: Any) -> None:
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)

    def _load_tool_hub_map(self) -> Dict[str, Dict[str, Any]]:
        if not self.tool_hub_path.exists():
            return {}
        items = json.loads(self.tool_hub_path.read_text(encoding="utf-8"))
        result: Dict[str, Dict[str, Any]] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                result[name] = item
        return result

    def _save_tool_hub_entry(self, entry: Dict[str, Any]) -> None:
        items = []
        if self.tool_hub_path.exists():
            raw = json.loads(self.tool_hub_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                items = [item for item in raw if isinstance(item, dict)]
        name = str(entry.get("name") or "").strip()
        if not name:
            raise ToolStudioError("tool_hub entry 缺少 name")
        replaced = False
        for idx, item in enumerate(items):
            if str(item.get("name") or "").strip() == name:
                items[idx] = entry
                replaced = True
                break
        if not replaced:
            items.append(entry)
        self._write_json_atomic(self.tool_hub_path, items)

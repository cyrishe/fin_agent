from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


class FinanceDataToolCatalogError(ValueError):
    pass


class FinanceDataToolCatalogService:
    """Read-only subject/dataview/function catalog for finance data tools."""

    DEFAULT_CATALOG_PATH = "src/tools/finance_data/catalog/api_view_catalog.json"

    def __init__(self, *, catalog_path: str = DEFAULT_CATALOG_PATH) -> None:
        self.catalog_path = Path(catalog_path)

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def load_raw_catalog(self) -> Dict[str, Any]:
        if not self.catalog_path.exists():
            raise FileNotFoundError(f"finance data catalog not found: {self.catalog_path}")
        payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise FinanceDataToolCatalogError("finance data catalog top-level must be an object")
        return payload

    def save_subject_node(self, *, subject: str, node: Mapping[str, Any]) -> Dict[str, Any]:
        normalized = self._trim(subject)
        if not normalized:
            raise FinanceDataToolCatalogError("subject is required")
        catalog = self.load_raw_catalog()
        subjects = catalog.get("subjects") if isinstance(catalog.get("subjects"), dict) else {}
        if normalized not in subjects:
            raise KeyError(f"unknown subject: {normalized}")
        subjects[normalized]["_meta"] = self._subject_meta_from_node(node)
        self._write_catalog(catalog)
        return self.get_subject(normalized)

    def save_dataview_node(self, *, subject: str, dataview: str, node: Mapping[str, Any]) -> Dict[str, Any]:
        normalized_subject = self._trim(subject)
        normalized_dataview = self._trim(dataview)
        if not normalized_subject:
            raise FinanceDataToolCatalogError("subject is required")
        if not normalized_dataview:
            raise FinanceDataToolCatalogError("dataview is required")
        catalog = self.load_raw_catalog()
        subjects = catalog.get("subjects") if isinstance(catalog.get("subjects"), dict) else {}
        subject_cfg = subjects.get(normalized_subject)
        if not isinstance(subject_cfg, dict):
            raise KeyError(f"unknown subject: {normalized_subject}")
        dataview_cfg = subject_cfg.get(normalized_dataview)
        if not isinstance(dataview_cfg, dict):
            raise KeyError(f"unknown dataview: {normalized_subject}.{normalized_dataview}")
        self._apply_dataview_node(dataview_cfg, node)
        self._write_catalog(catalog)
        return self.get_dataview(normalized_subject, normalized_dataview)

    def save_metadata_node(self, *, path: Sequence[Any], node: Mapping[str, Any]) -> Dict[str, Any]:
        if not path:
            raise FinanceDataToolCatalogError("path is required")
        catalog = self.load_raw_catalog()
        parent, key, current = self._resolve_edit_target(catalog, path)
        saved = self._apply_metadata_node(parent=parent, key=key, current=current, node=node)
        self._write_catalog(catalog)
        return {
            "path": list(path),
            "node": saved,
        }

    def build_tree(self) -> Dict[str, Any]:
        catalog = self.load_raw_catalog()
        api_classes = catalog.get("api_class_patterns") if isinstance(catalog.get("api_class_patterns"), dict) else {}
        subjects = catalog.get("subjects") if isinstance(catalog.get("subjects"), dict) else {}
        rows: List[Dict[str, Any]] = []
        for subject_name, subject_cfg in subjects.items():
            if not isinstance(subject_cfg, Mapping):
                continue
            rows.append(self._build_subject_row(str(subject_name), subject_cfg, api_classes))
        return {
            "version": self._trim(catalog.get("version")),
            "catalog_path": str(self.catalog_path),
            "subjects": rows,
            "api_class_patterns": [
                self._build_api_class_row(str(name), cfg)
                for name, cfg in api_classes.items()
                if isinstance(cfg, Mapping)
            ],
            "api_class_count": len(api_classes),
            "subject_count": len(rows),
        }

    def get_subject(self, subject: str) -> Dict[str, Any]:
        normalized = self._trim(subject)
        if not normalized:
            raise FinanceDataToolCatalogError("subject is required")
        for row in self.build_tree()["subjects"]:
            if row.get("name") == normalized:
                return row
        raise KeyError(f"unknown subject: {normalized}")

    def get_dataview(self, subject: str, dataview: str) -> Dict[str, Any]:
        normalized_view = self._trim(dataview)
        if not normalized_view:
            raise FinanceDataToolCatalogError("dataview is required")
        subject_row = self.get_subject(subject)
        for row in subject_row.get("dataviews") or []:
            if row.get("name") == normalized_view:
                return row
        raise KeyError(f"unknown dataview: {subject}.{normalized_view}")

    def _build_subject_row(
        self,
        subject_name: str,
        subject_cfg: Mapping[str, Any],
        api_classes: Mapping[str, Any],
    ) -> Dict[str, Any]:
        meta = subject_cfg.get("_meta") if isinstance(subject_cfg.get("_meta"), Mapping) else {}
        dataviews: List[Dict[str, Any]] = []
        for dataview_name, dataview_cfg in subject_cfg.items():
            if str(dataview_name).startswith("_") or not isinstance(dataview_cfg, Mapping):
                continue
            dataviews.append(self._build_dataview_row(str(dataview_name), dataview_cfg, api_classes))
        return {
            "name": subject_name,
            "desc": self._trim(meta.get("desc")),
            "rules": [self._trim(item) for item in (meta.get("rules") or []) if self._trim(item)],
            "dataviews": dataviews,
            "dataview_count": len(dataviews),
        }

    def _build_dataview_row(
        self,
        dataview_name: str,
        dataview_cfg: Mapping[str, Any],
        api_classes: Mapping[str, Any],
    ) -> Dict[str, Any]:
        fields = dataview_cfg.get("fields") if isinstance(dataview_cfg.get("fields"), Mapping) else {}
        apis = dataview_cfg.get("api") if isinstance(dataview_cfg.get("api"), list) else []
        functions = [
            self._build_function_row(api_row, api_classes)
            for api_row in apis
            if isinstance(api_row, Mapping)
        ]
        return {
            "name": dataview_name,
            "desc": self._trim(dataview_cfg.get("desc")),
            "rules": [self._trim(item) for item in (dataview_cfg.get("rules") or []) if self._trim(item)],
            "examples": [self._trim(item) for item in (dataview_cfg.get("examples") or []) if self._trim(item)],
            "fields": self._build_fields(fields),
            "field_count": len(fields),
            "functions": functions,
            "function_count": len(functions),
            "kd": deepcopy(dataview_cfg.get("kd") or {}),
            "computed": deepcopy(dataview_cfg.get("computed") or {}),
        }

    def _build_api_class_row(self, name: str, cfg: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "name": name,
            "desc": self._trim(cfg.get("desc")),
            "call_pattern": self._trim(cfg.get("call_pattern")),
            "output_rule": self._trim(cfg.get("output_rule")),
            "rules": deepcopy(cfg.get("rules") or []),
            "examples": deepcopy(cfg.get("examples") or []),
            "methods": deepcopy(cfg.get("methods") or []),
            "args": deepcopy(cfg.get("args") or {}),
        }

    def _build_function_row(self, api_row: Mapping[str, Any], api_classes: Mapping[str, Any]) -> Dict[str, Any]:
        api_class = self._trim(api_row.get("api_class"))
        api_class_cfg = api_classes.get(api_class) if isinstance(api_classes.get(api_class), Mapping) else {}
        return {
            "api_name": self._trim(api_row.get("api_name")),
            "api_function": self._trim(api_row.get("api_function")),
            "api_class": api_class,
            "request_pattern": self._trim(api_class_cfg.get("call_pattern")),
            "methods": deepcopy(api_class_cfg.get("methods") or []),
            "args": deepcopy(api_class_cfg.get("args") or {}),
            "rules": deepcopy(api_class_cfg.get("rules") or []),
            "output_rule": self._trim(api_class_cfg.get("output_rule")),
            "examples": deepcopy(api_class_cfg.get("examples") or []),
        }

    def _build_fields(self, fields: Mapping[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for field_name, field_cfg in fields.items():
            aliases = []
            desc = ""
            if isinstance(field_cfg, Mapping):
                aliases = [self._trim(item) for item in (field_cfg.get("aliases") or []) if self._trim(item)]
                desc = self._trim(field_cfg.get("desc"))
            elif isinstance(field_cfg, list):
                aliases = [self._trim(item) for item in field_cfg if self._trim(item)]
            rows.append(
                {
                    "name": str(field_name),
                    "aliases": aliases,
                    "desc": desc,
                }
            )
        return rows

    def _write_catalog(self, payload: Mapping[str, Any]) -> None:
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp_path = self.catalog_path.with_name(f".{self.catalog_path.name}.tmp")
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(self.catalog_path)

    def _subject_meta_from_node(self, node: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "desc": self._trim(node.get("desc")),
            "rules": [self._trim(item) for item in (node.get("rules") or []) if self._trim(item)],
        }

    def _apply_dataview_node(self, dataview_cfg: Dict[str, Any], node: Mapping[str, Any]) -> None:
        dataview_cfg["desc"] = self._trim(node.get("desc"))
        fields = node.get("fields")
        if isinstance(fields, list):
            dataview_cfg["fields"] = self._fields_from_node(fields)
        functions = node.get("functions")
        if isinstance(functions, list):
            dataview_cfg["api"] = self._functions_from_node(functions)
        if isinstance(node.get("kd"), Mapping):
            dataview_cfg["kd"] = deepcopy(node.get("kd"))
        if isinstance(node.get("computed"), Mapping):
            dataview_cfg["computed"] = deepcopy(node.get("computed"))

    def _fields_from_node(self, fields: List[Any]) -> Dict[str, Any]:
        rows: Dict[str, Any] = {}
        for item in fields:
            if not isinstance(item, Mapping):
                continue
            name = self._trim(item.get("name"))
            if not name:
                continue
            field_cfg: Dict[str, Any] = {
                "aliases": [self._trim(alias) for alias in (item.get("aliases") or []) if self._trim(alias)]
            }
            desc = self._trim(item.get("desc"))
            if desc:
                field_cfg["desc"] = desc
            rows[name] = field_cfg
        return rows

    def _functions_from_node(self, functions: List[Any]) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for item in functions:
            if not isinstance(item, Mapping):
                continue
            api_name = self._trim(item.get("api_name"))
            if not api_name:
                continue
            rows.append(
                {
                    "api_name": api_name,
                    "api_function": self._trim(item.get("api_function")),
                    "api_class": self._trim(item.get("api_class")),
                }
            )
        return rows

    def list_subjects(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": row["name"],
                "desc": row.get("desc") or "",
                "dataview_count": row.get("dataview_count") or 0,
                "dataviews": [item.get("name") for item in row.get("dataviews") or []],
            }
            for row in self.build_tree()["subjects"]
        ]

    def build_tool_studio_payload(self, *, subject: Optional[str] = None, dataview: Optional[str] = None) -> Dict[str, Any]:
        if subject and dataview:
            return {
                "mode": "dataview",
                "catalog_path": str(self.catalog_path),
                "subject": self._trim(subject),
                "dataview": self.get_dataview(subject, dataview),
            }
        if subject:
            return {
                "mode": "subject",
                "catalog_path": str(self.catalog_path),
                "subject": self.get_subject(subject),
            }
        return {
            "mode": "tree",
            "catalog": self.build_tree(),
            "raw_catalog": self.load_raw_catalog(),
        }

    def _resolve_edit_target(self, catalog: Dict[str, Any], path: Sequence[Any]) -> tuple[Any, Any, Any]:
        current: Any = catalog
        parent: Any = None
        key: Any = None
        for raw_segment in path:
            parent = current
            if isinstance(parent, list):
                try:
                    key = int(raw_segment)
                except (TypeError, ValueError) as exc:
                    raise FinanceDataToolCatalogError(f"invalid list path segment: {raw_segment}") from exc
                if key < 0 or key >= len(parent):
                    raise KeyError(f"path segment out of range: {raw_segment}")
                current = parent[key]
                continue
            if isinstance(parent, dict):
                key = str(raw_segment)
                if key not in parent:
                    raise KeyError(f"unknown path segment: {key}")
                current = parent[key]
                continue
            raise FinanceDataToolCatalogError("path cannot target a scalar parent")
        return parent, key, current

    def _apply_metadata_node(self, *, parent: Any, key: Any, current: Any, node: Mapping[str, Any]) -> Any:
        if isinstance(current, list):
            aliases = node.get("aliases")
            if aliases is None:
                aliases = node.get("items")
            if not isinstance(aliases, list):
                raise FinanceDataToolCatalogError("list node only supports aliases/items")
            saved = [self._trim(item) for item in aliases if self._trim(item)]
            parent[key] = saved
            return deepcopy(saved)
        if not isinstance(current, dict):
            raise FinanceDataToolCatalogError("metadata node must be an object or list")

        text_keys = {"desc", "api_function", "call_pattern", "output_rule"}
        list_keys = {"rules", "examples", "methods", "aliases"}
        mapping_keys = {"args"}
        for item_key in text_keys:
            if item_key in node:
                current[item_key] = self._trim(node.get(item_key))
        for item_key in list_keys:
            if item_key in node:
                value = node.get(item_key)
                if not isinstance(value, list):
                    raise FinanceDataToolCatalogError(f"{item_key} must be a list")
                current[item_key] = [self._trim(item) for item in value if self._trim(item)]
        for item_key in mapping_keys:
            if item_key in node:
                value = node.get(item_key)
                if not isinstance(value, Mapping):
                    raise FinanceDataToolCatalogError(f"{item_key} must be an object")
                current[item_key] = deepcopy(value)
        return deepcopy(current)

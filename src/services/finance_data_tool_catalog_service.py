from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.experiments.staged_data_protocol.phase2.catalog import (
    CATALOG_PATH,
    operation_examples,
    operation_for_api_pattern,
    validate_catalog_source,
)


class FinanceDataToolCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class _CatalogFileState:
    """Cheap file identity used to decide whether the snapshot needs a refresh."""

    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class _CatalogSnapshot:
    """Private immutable catalog snapshot.

    Values are recursively frozen before being stored. Public methods always thaw
    a fresh copy, so callers cannot accidentally mutate the cached snapshot.
    """

    file_state: _CatalogFileState
    content_digest: str
    raw_catalog: Mapping[str, Any]
    tree: Mapping[str, Any]
    subject_positions: Mapping[str, int]
    dataview_positions: Mapping[tuple[str, str], tuple[int, int]]
    model_dataviews: Mapping[tuple[str, str], Mapping[str, Any]]


def _freeze_catalog_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_catalog_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_catalog_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_catalog_value(item) for item in value)
    return deepcopy(value)


def _thaw_catalog_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_catalog_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_catalog_value(item) for item in value]
    return deepcopy(value)


class FinanceDataToolCatalogService:
    """Read-only subject/dataview/function catalog for finance data tools."""

    DEFAULT_CATALOG_PATH = str(CATALOG_PATH)

    def __init__(self, *, catalog_path: str = DEFAULT_CATALOG_PATH) -> None:
        self.catalog_path = Path(catalog_path)
        self._snapshot_lock = RLock()
        self._snapshot_cache: Optional[_CatalogSnapshot] = None

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def load_raw_catalog(self) -> Dict[str, Any]:
        return _thaw_catalog_value(self._catalog_snapshot().raw_catalog)

    def catalog_revision(self) -> str:
        """Return the content-addressed revision used by model and UI projections."""

        return self._catalog_snapshot().content_digest

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
        return _thaw_catalog_value(self._catalog_snapshot().tree)

    def _build_tree_from_catalog(self, catalog: Mapping[str, Any]) -> Dict[str, Any]:
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
        snapshot = self._catalog_snapshot()
        position = snapshot.subject_positions.get(normalized)
        if position is None:
            raise KeyError(f"unknown subject: {normalized}")
        return _thaw_catalog_value(snapshot.tree["subjects"][position])

    def get_dataview(self, subject: str, dataview: str) -> Dict[str, Any]:
        normalized_subject = self._trim(subject)
        if not normalized_subject:
            raise FinanceDataToolCatalogError("subject is required")
        normalized_view = self._trim(dataview)
        if not normalized_view:
            raise FinanceDataToolCatalogError("dataview is required")
        snapshot = self._catalog_snapshot()
        subject_position = snapshot.subject_positions.get(normalized_subject)
        if subject_position is None:
            raise KeyError(f"unknown subject: {normalized_subject}")
        position = snapshot.dataview_positions.get((normalized_subject, normalized_view))
        if position is None:
            raise KeyError(f"unknown dataview: {normalized_subject}.{normalized_view}")
        subject_index, dataview_index = position
        return _thaw_catalog_value(
            snapshot.tree["subjects"][subject_index]["dataviews"][dataview_index]
        )

    def get_model_dataview(
        self,
        subject: str,
        dataview: str,
        operation: str = "",
    ) -> Dict[str, Any]:
        """Return one model-facing dataview without repeated API-class contracts.

        The full catalog/tree APIs intentionally keep their historical shape for
        editors and renderers. This projection only removes empty/count values and
        moves shared API-class details behind references from ``functions``.
        """

        normalized_subject = self._trim(subject)
        if not normalized_subject:
            raise FinanceDataToolCatalogError("subject is required")
        normalized_view = self._trim(dataview)
        if not normalized_view:
            raise FinanceDataToolCatalogError("dataview is required")
        snapshot = self._catalog_snapshot()
        if normalized_subject not in snapshot.subject_positions:
            raise KeyError(f"unknown subject: {normalized_subject}")
        row = snapshot.model_dataviews.get((normalized_subject, normalized_view))
        if row is None:
            raise KeyError(f"unknown dataview: {normalized_subject}.{normalized_view}")
        model = _thaw_catalog_value(row)
        normalized_operation = self._trim(operation)
        if not normalized_operation:
            return model
        if normalized_operation not in {"query", "aggregate", "window", "compute"}:
            raise FinanceDataToolCatalogError(
                f"unsupported finance catalog operation: {normalized_operation}"
            )
        functions = [
            item
            for item in model.get("functions") or []
            if isinstance(item, Mapping)
            and self._trim(item.get("operation")) == normalized_operation
        ]
        if not functions:
            available = sorted(
                {
                    self._trim(item.get("operation"))
                    for item in model.get("functions") or []
                    if isinstance(item, Mapping) and self._trim(item.get("operation"))
                }
            )
            raise FinanceDataToolCatalogError(
                f"operation={normalized_operation} is not available for "
                f"{normalized_subject}.{normalized_view}; available={available}"
            )
        api_classes = {
            self._trim(item.get("api_class"))
            for item in functions
            if self._trim(item.get("api_class"))
        }
        model["functions"] = functions
        model["api_classes"] = {
            name: value
            for name, value in (model.get("api_classes") or {}).items()
            if name in api_classes
        }
        operation_metadata = {
            "query": {"computed", "value_domains"},
            "aggregate": {"aggregate_fields", "value_domains"},
            "window": {"kd", "value_domains"},
            "compute": {"computed", "value_domains"},
        }
        for key in {"kd", "computed", "aggregate_fields", "value_domains"}:
            if key not in operation_metadata[normalized_operation]:
                model.pop(key, None)
        model.pop("examples", None)
        model["selected_operation"] = normalized_operation
        return model

    def _catalog_snapshot(self) -> _CatalogSnapshot:
        with self._snapshot_lock:
            current_state = self._catalog_file_state()
            cached = self._snapshot_cache
            if cached is not None and cached.file_state == current_state:
                return cached

            file_state, content = self._read_stable_catalog_bytes()
            content_digest = sha256(content).hexdigest()
            if cached is not None and cached.content_digest == content_digest:
                refreshed = _CatalogSnapshot(
                    file_state=file_state,
                    content_digest=content_digest,
                    raw_catalog=cached.raw_catalog,
                    tree=cached.tree,
                    subject_positions=cached.subject_positions,
                    dataview_positions=cached.dataview_positions,
                    model_dataviews=cached.model_dataviews,
                )
                self._snapshot_cache = refreshed
                return refreshed

            payload = json.loads(content.decode("utf-8"))
            if not isinstance(payload, dict):
                raise FinanceDataToolCatalogError("finance data catalog top-level must be an object")
            validate_catalog_source(payload)
            tree = self._build_tree_from_catalog(payload)
            subject_positions: Dict[str, int] = {}
            dataview_positions: Dict[tuple[str, str], tuple[int, int]] = {}
            for subject_index, subject_row in enumerate(tree["subjects"]):
                subject_name = str(subject_row["name"])
                subject_positions[subject_name] = subject_index
                for dataview_index, dataview_row in enumerate(subject_row.get("dataviews") or []):
                    dataview_positions[(subject_name, str(dataview_row["name"]))] = (
                        subject_index,
                        dataview_index,
                    )

            api_classes = (
                payload.get("api_class_patterns")
                if isinstance(payload.get("api_class_patterns"), Mapping)
                else {}
            )
            subjects = payload.get("subjects") if isinstance(payload.get("subjects"), Mapping) else {}
            model_dataviews: Dict[tuple[str, str], Dict[str, Any]] = {}
            for subject_name, subject_cfg in subjects.items():
                if not isinstance(subject_cfg, Mapping):
                    continue
                subject_meta = (
                    subject_cfg.get("_meta")
                    if isinstance(subject_cfg.get("_meta"), Mapping)
                    else {}
                )
                for dataview_name, dataview_cfg in subject_cfg.items():
                    if str(dataview_name).startswith("_") or not isinstance(dataview_cfg, Mapping):
                        continue
                    key = (str(subject_name), str(dataview_name))
                    model_dataviews[key] = self._build_model_dataview_row(
                        str(dataview_name),
                        dataview_cfg,
                        api_classes,
                        subject_guidance=subject_meta.get("rules"),
                    )

            snapshot = _CatalogSnapshot(
                file_state=file_state,
                content_digest=content_digest,
                raw_catalog=_freeze_catalog_value(payload),
                tree=_freeze_catalog_value(tree),
                subject_positions=_freeze_catalog_value(subject_positions),
                dataview_positions=_freeze_catalog_value(dataview_positions),
                model_dataviews=_freeze_catalog_value(model_dataviews),
            )
            self._snapshot_cache = snapshot
            return snapshot

    def _catalog_file_state(self) -> _CatalogFileState:
        try:
            stat = self.catalog_path.stat()
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"finance data catalog not found: {self.catalog_path}"
            ) from exc
        return _CatalogFileState(
            device=stat.st_dev,
            inode=stat.st_ino,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )

    def _read_stable_catalog_bytes(self) -> tuple[_CatalogFileState, bytes]:
        for _ in range(3):
            before = self._catalog_file_state()
            content = self.catalog_path.read_bytes()
            after = self._catalog_file_state()
            if before == after and len(content) == after.size:
                return after, content
        raise FinanceDataToolCatalogError(
            f"finance data catalog changed while being read: {self.catalog_path}"
        )

    def _build_model_dataview_row(
        self,
        dataview_name: str,
        dataview_cfg: Mapping[str, Any],
        api_classes: Mapping[str, Any],
        *,
        subject_guidance: Any = None,
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {"name": dataview_name}
        self._put_model_value(row, "subject_guidance", subject_guidance)
        self._put_model_value(row, "desc", self._trim(dataview_cfg.get("desc")))
        self._put_model_value(
            row,
            "rules",
            [self._trim(item) for item in (dataview_cfg.get("rules") or []) if self._trim(item)],
        )
        self._put_model_value(
            row,
            "examples",
            [self._trim(item) for item in (dataview_cfg.get("examples") or []) if self._trim(item)],
        )

        fields = dataview_cfg.get("fields") if isinstance(dataview_cfg.get("fields"), Mapping) else {}
        model_fields: Dict[str, Dict[str, Any]] = {}
        for field in self._build_fields(fields):
            compact_field: Dict[str, Any] = {}
            self._put_model_value(compact_field, "aliases", field.get("aliases"))
            self._put_model_value(compact_field, "desc", field.get("desc"))
            model_fields[str(field["name"])] = compact_field
        row["fields"] = model_fields

        functions: List[Dict[str, Any]] = []
        referenced_api_classes: List[str] = []
        apis = dataview_cfg.get("api") if isinstance(dataview_cfg.get("api"), list) else []
        for api_row in apis:
            if not isinstance(api_row, Mapping):
                continue
            function: Dict[str, Any] = {}
            self._put_model_value(function, "api_name", self._trim(api_row.get("api_name")))
            self._put_model_value(function, "api_function", self._trim(api_row.get("api_function")))
            api_class = self._trim(api_row.get("api_class"))
            self._put_model_value(function, "api_class", api_class)
            self._put_model_value(
                function,
                "operation",
                self._operation_for_api_name(self._trim(api_row.get("api_name"))),
            )
            class_cfg = (
                api_classes.get(api_class)
                if isinstance(api_classes.get(api_class), Mapping)
                else {}
            )
            self._put_model_value(
                function,
                "examples",
                self._function_examples(api_row, class_cfg),
            )
            self._put_model_value(function, "guidance", api_row.get("guidance"))
            functions.append(function)
            if api_class and api_class not in referenced_api_classes:
                referenced_api_classes.append(api_class)
        row["functions"] = functions

        contracts: Dict[str, Dict[str, Any]] = {}
        for api_class in referenced_api_classes:
            cfg = api_classes.get(api_class) if isinstance(api_classes.get(api_class), Mapping) else {}
            contract: Dict[str, Any] = {}
            self._put_model_value(contract, "desc", self._trim(cfg.get("desc")))
            self._put_model_value(contract, "request_pattern", self._trim(cfg.get("call_pattern")))
            self._put_model_value(contract, "methods", cfg.get("methods"))
            self._put_model_value(contract, "args", cfg.get("args"))
            self._put_model_value(contract, "rules", cfg.get("rules"))
            self._put_model_value(contract, "output_rule", self._trim(cfg.get("output_rule")))
            contracts[api_class] = contract
        self._put_model_value(row, "api_classes", contracts)

        for key in ("kd", "computed", "aggregate_fields", "value_domains"):
            self._put_model_value(row, key, dataview_cfg.get(key))
        return row

    @classmethod
    def _put_model_value(cls, target: Dict[str, Any], key: str, value: Any) -> None:
        compact = cls._compact_model_value(value)
        if compact is not None:
            target[key] = compact

    @classmethod
    def _compact_model_value(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        if isinstance(value, Mapping):
            result = {
                key: compact
                for key, item in value.items()
                if (compact := cls._compact_model_value(item)) is not None
            }
            return result or None
        if isinstance(value, (list, tuple)):
            result = [
                compact
                for item in value
                if (compact := cls._compact_model_value(item)) is not None
            ]
            return result or None
        return deepcopy(value)

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
        description = self._trim(dataview_cfg.get("desc"))
        return {
            "name": dataview_name,
            "desc": description,
            # Backward-compatible projection only. ``desc`` is the sole
            # persisted capability description and therefore the sole truth.
            "route_summary": description,
            "rules": [self._trim(item) for item in (dataview_cfg.get("rules") or []) if self._trim(item)],
            "examples": [self._trim(item) for item in (dataview_cfg.get("examples") or []) if self._trim(item)],
            "fields": self._build_fields(fields),
            "field_count": len(fields),
            "functions": functions,
            "function_count": len(functions),
            "kd": deepcopy(dataview_cfg.get("kd") or {}),
            "computed": deepcopy(dataview_cfg.get("computed") or {}),
            "aggregate_fields": deepcopy(dataview_cfg.get("aggregate_fields") or {}),
            "value_domains": deepcopy(dataview_cfg.get("value_domains") or {}),
        }

    def _build_api_class_row(self, name: str, cfg: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "name": name,
            "desc": self._trim(cfg.get("desc")),
            "call_pattern": self._trim(cfg.get("call_pattern")),
            "output_rule": self._trim(cfg.get("output_rule")),
            "rules": deepcopy(cfg.get("rules") or []),
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
            "operation": self._operation_for_api_name(self._trim(api_row.get("api_name"))),
            "request_pattern": self._trim(api_class_cfg.get("call_pattern")),
            "methods": deepcopy(api_class_cfg.get("methods") or []),
            "args": deepcopy(api_class_cfg.get("args") or {}),
            "rules": deepcopy(api_class_cfg.get("rules") or []),
            "output_rule": self._trim(api_class_cfg.get("output_rule")),
            "examples": self._function_examples(api_row, api_class_cfg),
            "guidance": deepcopy(api_row.get("guidance") or []),
        }

    @staticmethod
    def _operation_for_api_name(api_name: str) -> str:
        return operation_for_api_pattern(api_name)

    @classmethod
    def _function_examples(
        cls,
        api_row: Mapping[str, Any],
        api_class_cfg: Mapping[str, Any],
    ) -> List[str]:
        return list(dict.fromkeys(operation_examples(api_row, api_class_cfg)))

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
        with self._snapshot_lock:
            validate_catalog_source(payload)
            self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            tmp_path = self.catalog_path.with_name(f".{self.catalog_path.name}.tmp")
            tmp_path.write_text(text, encoding="utf-8")
            tmp_path.replace(self.catalog_path)
            self._snapshot_cache = None

    def _subject_meta_from_node(self, node: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "desc": self._trim(node.get("desc")),
            "rules": [self._trim(item) for item in (node.get("rules") or []) if self._trim(item)],
        }

    def _apply_dataview_node(self, dataview_cfg: Dict[str, Any], node: Mapping[str, Any]) -> None:
        description = self._trim(node.get("desc"))
        if not description and "desc" not in node:
            # Accept the former field as an input alias without persisting a
            # second independently editable description.
            description = self._trim(node.get("route_summary"))
        dataview_cfg["desc"] = description
        dataview_cfg.pop("route_summary", None)
        fields = node.get("fields")
        if isinstance(fields, list):
            dataview_cfg["fields"] = self._fields_from_node(fields)
        functions = node.get("functions")
        if isinstance(functions, list):
            existing_functions = {
                self._trim(item.get("api_name")): item
                for item in dataview_cfg.get("api") or []
                if isinstance(item, Mapping) and self._trim(item.get("api_name"))
            }
            dataview_cfg["api"] = self._functions_from_node(
                functions,
                existing=existing_functions,
            )
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

    def _functions_from_node(
        self,
        functions: List[Any],
        *,
        existing: Mapping[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in functions:
            if not isinstance(item, Mapping):
                continue
            api_name = self._trim(item.get("api_name"))
            if not api_name:
                continue
            row: Dict[str, Any] = {
                "api_name": api_name,
                "api_function": self._trim(item.get("api_function")),
                "api_class": self._trim(item.get("api_class")),
            }
            examples = item.get("examples")
            if not isinstance(examples, list):
                previous = (existing or {}).get(api_name)
                examples = previous.get("examples") if isinstance(previous, Mapping) else []
            if isinstance(examples, list) and examples:
                row["examples"] = deepcopy(examples)
            guidance = item.get("guidance")
            if not isinstance(guidance, list):
                previous = (existing or {}).get(api_name)
                guidance = (
                    previous.get("guidance")
                    if isinstance(previous, Mapping)
                    else []
                )
            if isinstance(guidance, list) and guidance:
                row["guidance"] = deepcopy(guidance)
            rows.append(row)
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

        text_keys = {
            "desc",
            "route_summary",
            "api_function",
            "call_pattern",
            "output_rule",
        }
        list_keys = {"rules", "examples", "guidance", "methods", "aliases"}
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

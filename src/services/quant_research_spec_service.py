from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence


class QuantResearchSpecError(ValueError):
    def __init__(self, failure_kind: str, message: str) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind
        self.message = message


class QuantResearchSpecService:
    FACTOR_REQUIRED_FIELDS = ("factor_id", "version", "family", "inputs", "params_schema", "formula", "outputs")
    STRATEGY_REQUIRED_FIELDS = ("strategy_id", "version", "universe", "factors", "ranking")
    SQL_TEMPLATE_REQUIRED_FIELDS = (
        "template_id",
        "version",
        "target_db",
        "sql_template",
        "params_schema",
        "output_schema",
        "source_tables",
    )

    _IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{1,96}$")

    def normalize_factor_spec(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        spec = self._require_mapping(payload, "factor_spec")
        self._require_fields(spec, self.FACTOR_REQUIRED_FIELDS, "factor_spec")
        factor_id = self._identifier(spec.get("factor_id"), "factor_id")
        version = self._version(spec.get("version"))
        inputs = self._string_list(spec.get("inputs"), "inputs", min_items=1)
        outputs = self._string_list(spec.get("outputs"), "outputs", min_items=1)
        params_schema = self._schema_mapping(spec.get("params_schema"), "params_schema")
        formula = self._non_empty_string(spec.get("formula"), "formula")

        return {
            "factor_id": factor_id,
            "version": version,
            "family": self._non_empty_string(spec.get("family"), "family"),
            "display_name": self._optional_string(spec.get("display_name")),
            "inputs": inputs,
            "params_schema": params_schema,
            "formula": formula,
            "outputs": outputs,
            "direction": self._enum(
                spec.get("direction") or "higher_better",
                "direction",
                {"higher_better", "lower_better", "neutral"},
            ),
            "default_params": self._mapping_or_empty(spec.get("default_params"), "default_params"),
            "risk_notes": self._string_list(spec.get("risk_notes") or [], "risk_notes", min_items=0),
            "status": self._enum(spec.get("status") or "draft", "status", {"draft", "verified", "deprecated"}),
            "tags": self._string_list(spec.get("tags") or [], "tags", min_items=0),
        }

    def normalize_strategy_spec(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        spec = self._require_mapping(payload, "strategy_spec")
        self._require_fields(spec, self.STRATEGY_REQUIRED_FIELDS, "strategy_spec")
        factors = self._normalize_strategy_factors(spec.get("factors"))
        ranking = self._normalize_ranking(spec.get("ranking"))
        filters = self._normalize_filters(spec.get("filters") or [])

        return {
            "strategy_id": self._identifier(spec.get("strategy_id"), "strategy_id"),
            "version": self._version(spec.get("version")),
            "display_name": self._optional_string(spec.get("display_name")),
            "universe": self._normalize_universe(spec.get("universe")),
            "date_policy": self._mapping_or_empty(spec.get("date_policy"), "date_policy"),
            "filters": filters,
            "factors": factors,
            "ranking": ranking,
            "output": self._mapping_or_empty(spec.get("output"), "output"),
            "risk_notes": self._string_list(spec.get("risk_notes") or [], "risk_notes", min_items=0),
            "status": self._enum(spec.get("status") or "draft", "status", {"draft", "verified", "deprecated"}),
            "tags": self._string_list(spec.get("tags") or [], "tags", min_items=0),
        }

    def normalize_sql_template_spec(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        spec = self._require_mapping(payload, "sql_template_spec")
        self._require_fields(spec, self.SQL_TEMPLATE_REQUIRED_FIELDS, "sql_template_spec")
        sql_template = self._non_empty_string(spec.get("sql_template"), "sql_template")
        self._validate_read_only_sql(sql_template)

        return {
            "template_id": self._identifier(spec.get("template_id"), "template_id"),
            "version": self._version(spec.get("version")),
            "target_db": self._identifier(spec.get("target_db"), "target_db"),
            "sql_template": sql_template,
            "params_schema": self._schema_mapping(spec.get("params_schema"), "params_schema"),
            "output_schema": self._schema_mapping(spec.get("output_schema"), "output_schema"),
            "source_tables": self._string_list(spec.get("source_tables"), "source_tables", min_items=1),
            "status": self._enum(spec.get("status") or "draft", "status", {"draft", "verified", "deprecated"}),
            "created_by": self._optional_string(spec.get("created_by")),
            "audit": self._mapping_or_empty(spec.get("audit"), "audit"),
        }

    def prepare_sql_template_run(
        self,
        template_spec: Mapping[str, Any],
        params: Mapping[str, Any],
        *,
        require_verified: bool = False,
    ) -> Dict[str, Any]:
        template = self.normalize_sql_template_spec(template_spec)
        if require_verified and template["status"] != "verified":
            raise QuantResearchSpecError("template_not_verified", "sql template must be verified before runtime execution")

        normalized_params = self._require_mapping(params, "params")
        params_schema = template["params_schema"]
        properties = params_schema.get("properties") if isinstance(params_schema.get("properties"), Mapping) else {}
        required = params_schema.get("required") if isinstance(params_schema.get("required"), list) else []
        placeholders = self._extract_sql_params(template["sql_template"])
        required_names = sorted({str(name) for name in required} | set(placeholders))
        missing = [name for name in required_names if name not in normalized_params]
        if missing:
            raise QuantResearchSpecError("missing_template_param", f"missing sql template param: {missing[0]}")
        if properties:
            unknown = sorted(str(key) for key in normalized_params.keys() if str(key) not in properties)
            if unknown:
                raise QuantResearchSpecError("unknown_template_param", f"unknown sql template param: {unknown[0]}")
            for key, schema in properties.items():
                if key in normalized_params and isinstance(schema, Mapping):
                    self._validate_template_param_type(key, normalized_params[key], schema)

        bound_params = {name: normalized_params[name] for name in required_names if name in normalized_params}
        return {
            "template_id": template["template_id"],
            "template_version": template["version"],
            "target_db": template["target_db"],
            "sql_template": template["sql_template"],
            "params": bound_params,
            "placeholder_params": placeholders,
            "source_tables": template["source_tables"],
            "output_schema": template["output_schema"],
            "status": template["status"],
            "spec_hash": self.spec_hash(template),
        }

    def compile_strategy_data_needs(
        self,
        strategy_spec: Mapping[str, Any],
        factor_specs: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        strategy = self.normalize_strategy_spec(strategy_spec)
        factor_map = self._build_factor_map(factor_specs)
        required_data: List[str] = []
        factor_refs: List[Dict[str, Any]] = []
        missing_factors: List[str] = []

        for item in strategy["factors"]:
            factor_id = item["factor_id"]
            factor = factor_map.get(factor_id)
            if not factor:
                missing_factors.append(factor_id)
                continue
            for source_id in factor["inputs"]:
                if source_id not in required_data:
                    required_data.append(source_id)
            factor_refs.append(
                {
                    "factor_id": factor_id,
                    "version": factor["version"],
                    "weight": item["weight"],
                    "params": item["params"],
                    "inputs": factor["inputs"],
                    "outputs": factor["outputs"],
                }
            )

        return {
            "strategy_id": strategy["strategy_id"],
            "strategy_version": strategy["version"],
            "required_data": required_data,
            "factor_refs": factor_refs,
            "missing_factors": missing_factors,
            "can_compile": not missing_factors,
            "spec_hash": self.spec_hash(strategy),
        }

    def spec_hash(self, payload: Mapping[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _build_factor_map(
        self,
        factor_specs: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        items: Iterable[Mapping[str, Any]]
        if isinstance(factor_specs, Mapping):
            items = factor_specs.values()
        else:
            items = factor_specs
        result: Dict[str, Dict[str, Any]] = {}
        for item in items:
            factor = self.normalize_factor_spec(item)
            result[factor["factor_id"]] = factor
        return result

    def _normalize_strategy_factors(self, raw: Any) -> List[Dict[str, Any]]:
        if not isinstance(raw, list) or not raw:
            raise QuantResearchSpecError("invalid_factor_refs", "strategy factors must be a non-empty list")
        result: List[Dict[str, Any]] = []
        seen = set()
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                raise QuantResearchSpecError("invalid_factor_ref", f"strategy factor at index {index} must be an object")
            factor_id = self._identifier(item.get("factor_id") or item.get("name"), f"factors[{index}].factor_id")
            if factor_id in seen:
                raise QuantResearchSpecError("duplicate_factor_ref", f"duplicate factor reference: {factor_id}")
            seen.add(factor_id)
            result.append(
                {
                    "factor_id": factor_id,
                    "weight": self._number(item.get("weight"), f"factors[{index}].weight", default=1.0),
                    "params": self._mapping_or_empty(item.get("params"), f"factors[{index}].params"),
                }
            )
        return result

    def _normalize_filters(self, raw: Any) -> List[Dict[str, Any]]:
        if not isinstance(raw, list):
            raise QuantResearchSpecError("invalid_filters", "filters must be a list")
        result: List[Dict[str, Any]] = []
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                raise QuantResearchSpecError("invalid_filter", f"filter at index {index} must be an object")
            field = self._non_empty_string(item.get("field") or item.get("factor_id"), f"filters[{index}].field")
            result.append(
                {
                    "field": field,
                    "op": self._enum(item.get("op"), f"filters[{index}].op", {"=", "!=", ">", ">=", "<", "<=", "in", "not_in"}),
                    "value": item.get("value"),
                }
            )
        return result

    def _normalize_ranking(self, raw: Any) -> Dict[str, Any]:
        ranking = self._require_mapping(raw, "ranking")
        method = self._enum(ranking.get("method") or "weighted_score", "ranking.method", {"weighted_score", "factor_value"})
        top_n = self._int(ranking.get("top_n"), "ranking.top_n", default=20, minimum=1, maximum=500)
        return {
            "method": method,
            "sort": self._enum(ranking.get("sort") or "desc", "ranking.sort", {"asc", "desc"}),
            "top_n": top_n,
            "primary_factor": self._optional_string(ranking.get("primary_factor")),
        }

    def _normalize_universe(self, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, str):
            universe_type = raw.strip() or "all_a_share"
            return {"type": universe_type}
        universe = self._require_mapping(raw, "universe")
        universe_type = self._non_empty_string(universe.get("type"), "universe.type")
        result = dict(universe)
        result["type"] = universe_type
        return result

    def _validate_read_only_sql(self, sql: str) -> None:
        stripped = sql.strip().lower()
        if not (stripped.startswith("select") or stripped.startswith("with")):
            raise QuantResearchSpecError("unsafe_sql", "sql_template must start with SELECT or WITH")
        forbidden = {"insert", "update", "delete", "drop", "alter", "truncate", "create", "replace"}
        tokens = set(re.findall(r"[a-z_]+", stripped))
        found = sorted(tokens & forbidden)
        if found:
            raise QuantResearchSpecError("unsafe_sql", f"sql_template contains forbidden keyword: {found[0]}")

    def _extract_sql_params(self, sql: str) -> List[str]:
        names = re.findall(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)", sql)
        return sorted(set(names))

    def _validate_template_param_type(self, key: str, value: Any, schema: Mapping[str, Any]) -> None:
        raw_type = schema.get("type")
        if isinstance(raw_type, list):
            allowed_types = {str(item) for item in raw_type}
        else:
            allowed_types = {str(raw_type)} if raw_type else set()
        if not allowed_types:
            return
        if value is None and "null" in allowed_types:
            return
        if "string" in allowed_types and isinstance(value, str):
            return
        if "integer" in allowed_types and isinstance(value, int) and not isinstance(value, bool):
            return
        if "number" in allowed_types and isinstance(value, (int, float)) and not isinstance(value, bool):
            return
        if "boolean" in allowed_types and isinstance(value, bool):
            return
        raise QuantResearchSpecError("invalid_template_param_type", f"sql template param `{key}` has invalid type")

    def _require_mapping(self, value: Any, field_name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise QuantResearchSpecError("invalid_spec", f"{field_name} must be an object")
        return value

    def _require_fields(self, spec: Mapping[str, Any], fields: Sequence[str], context: str) -> None:
        missing = [field for field in fields if field not in spec]
        if missing:
            raise QuantResearchSpecError("missing_required_field", f"{context} missing required field: {missing[0]}")

    def _identifier(self, value: Any, field_name: str) -> str:
        text = self._non_empty_string(value, field_name)
        if not self._IDENTIFIER_RE.match(text):
            raise QuantResearchSpecError("invalid_identifier", f"{field_name} must be a lowercase snake_case identifier")
        return text

    def _version(self, value: Any) -> str:
        text = self._non_empty_string(value, "version")
        if not re.match(r"^v[0-9]+(?:_[0-9]+)?$", text):
            raise QuantResearchSpecError("invalid_version", "version must look like v1 or v1_1")
        return text

    def _schema_mapping(self, value: Any, field_name: str) -> Dict[str, Any]:
        schema = self._require_mapping(value, field_name)
        return dict(schema)

    def _mapping_or_empty(self, value: Any, field_name: str) -> Dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise QuantResearchSpecError("invalid_mapping", f"{field_name} must be an object")
        return dict(value)

    def _string_list(self, value: Any, field_name: str, *, min_items: int) -> List[str]:
        if not isinstance(value, list):
            raise QuantResearchSpecError("invalid_list", f"{field_name} must be a list")
        result = [self._non_empty_string(item, field_name) for item in value]
        if len(result) < min_items:
            raise QuantResearchSpecError("invalid_list", f"{field_name} must contain at least {min_items} item(s)")
        return result

    def _enum(self, value: Any, field_name: str, allowed: set[str]) -> str:
        text = self._non_empty_string(value, field_name)
        if text not in allowed:
            raise QuantResearchSpecError("invalid_enum", f"{field_name} must be one of: {sorted(allowed)}")
        return text

    def _number(self, value: Any, field_name: str, *, default: float) -> float:
        if value is None:
            return float(default)
        try:
            return float(value)
        except Exception as exc:
            raise QuantResearchSpecError("invalid_number", f"{field_name} must be numeric") from exc

    def _int(self, value: Any, field_name: str, *, default: int, minimum: int, maximum: int) -> int:
        if value is None:
            parsed = default
        else:
            try:
                parsed = int(value)
            except Exception as exc:
                raise QuantResearchSpecError("invalid_integer", f"{field_name} must be an integer") from exc
        return max(minimum, min(maximum, parsed))

    def _non_empty_string(self, value: Any, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise QuantResearchSpecError("invalid_string", f"{field_name} must be a non-empty string")
        return text

    def _optional_string(self, value: Any) -> str:
        return str(value or "").strip()


class QuantResearchCatalogService:
    DEFAULT_ROOT = Path("src/quant_research")

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        spec_service: QuantResearchSpecService | None = None,
    ) -> None:
        self.root = Path(root) if root else self.DEFAULT_ROOT
        self.spec_service = spec_service or QuantResearchSpecService()

    def load_factors(self) -> Dict[str, Dict[str, Any]]:
        return self._load_specs(
            directory=self.root / "factors",
            normalizer=self.spec_service.normalize_factor_spec,
            id_field="factor_id",
            artifact_type="factor",
        )

    def load_strategies(self) -> Dict[str, Dict[str, Any]]:
        return self._load_specs(
            directory=self.root / "strategies",
            normalizer=self.spec_service.normalize_strategy_spec,
            id_field="strategy_id",
            artifact_type="strategy",
        )

    def load_sql_templates(self) -> Dict[str, Dict[str, Any]]:
        return self._load_specs(
            directory=self.root / "sql_templates",
            normalizer=self.spec_service.normalize_sql_template_spec,
            id_field="template_id",
            artifact_type="sql_template",
        )

    def load_catalog(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        return {
            "factors": self.load_factors(),
            "strategies": self.load_strategies(),
            "sql_templates": self.load_sql_templates(),
        }

    def compile_strategy_data_needs(self, strategy_id: str) -> Dict[str, Any]:
        normalized_strategy_id = self.spec_service._identifier(strategy_id, "strategy_id")
        catalog = self.load_catalog()
        strategy = catalog["strategies"].get(normalized_strategy_id)
        if not strategy:
            raise QuantResearchSpecError("strategy_not_found", f"strategy not found: {normalized_strategy_id}")
        return self.spec_service.compile_strategy_data_needs(strategy, catalog["factors"])

    def prepare_sql_template_run(
        self,
        template_id: str,
        params: Mapping[str, Any],
        *,
        require_verified: bool = False,
    ) -> Dict[str, Any]:
        normalized_template_id = self.spec_service._identifier(template_id, "template_id")
        template = self.load_sql_templates().get(normalized_template_id)
        if not template:
            raise QuantResearchSpecError("template_not_found", f"sql template not found: {normalized_template_id}")
        return self.spec_service.prepare_sql_template_run(
            template,
            params,
            require_verified=require_verified,
        )

    def _load_specs(
        self,
        *,
        directory: Path,
        normalizer: Any,
        id_field: str,
        artifact_type: str,
    ) -> Dict[str, Dict[str, Any]]:
        if not directory.exists():
            return {}
        result: Dict[str, Dict[str, Any]] = {}
        for path in sorted(directory.glob("*.json")):
            if path.name.startswith("._"):
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise QuantResearchSpecError("invalid_catalog_json", f"invalid {artifact_type} JSON: {path}") from exc
            spec = normalizer(payload)
            spec_id = str(spec[id_field])
            if spec_id in result:
                raise QuantResearchSpecError("duplicate_catalog_id", f"duplicate {artifact_type} id: {spec_id}")
            spec_hash = self.spec_service.spec_hash(spec)
            spec["catalog_path"] = str(path)
            spec["spec_hash"] = spec_hash
            result[spec_id] = spec
        return result


class SqlTemplateRegistryService:
    def __init__(
        self,
        *,
        root: str | Path | None = None,
        spec_service: QuantResearchSpecService | None = None,
    ) -> None:
        self.root = Path(root) if root else QuantResearchCatalogService.DEFAULT_ROOT
        self.spec_service = spec_service or QuantResearchSpecService()

    def save_draft(self, template_spec: Mapping[str, Any], *, overwrite: bool = False) -> Dict[str, Any]:
        spec = self.spec_service.normalize_sql_template_spec({**dict(template_spec), "status": "draft"})
        path = self._path_for(spec)
        if path.exists() and not overwrite:
            raise QuantResearchSpecError("template_already_exists", f"sql template already exists: {spec['template_id']} {spec['version']}")
        spec["audit"] = {
            **spec.get("audit", {}),
            "registry_state": "draft",
        }
        self._write_template(path, spec)
        return {
            "template_id": spec["template_id"],
            "version": spec["version"],
            "status": spec["status"],
            "path": str(path),
            "spec_hash": self.spec_service.spec_hash(spec),
        }

    def publish_template(
        self,
        template_id: str,
        *,
        version: str = "v1",
        verification: Mapping[str, Any],
    ) -> Dict[str, Any]:
        normalized_template_id = self.spec_service._identifier(template_id, "template_id")
        normalized_version = self.spec_service._version(version)
        path = self._path_for_id(normalized_template_id, normalized_version)
        if not path.exists():
            raise QuantResearchSpecError("template_not_found", f"sql template not found: {normalized_template_id} {normalized_version}")
        verified = self._require_verification(verification)
        payload = json.loads(path.read_text(encoding="utf-8"))
        spec = self.spec_service.normalize_sql_template_spec(payload)
        spec["status"] = "verified"
        spec["audit"] = {
            **spec.get("audit", {}),
            "registry_state": "verified",
            "verification": verified,
        }
        self._write_template(path, spec)
        return {
            "template_id": spec["template_id"],
            "version": spec["version"],
            "status": spec["status"],
            "path": str(path),
            "spec_hash": self.spec_service.spec_hash(spec),
        }

    def _require_verification(self, verification: Mapping[str, Any]) -> Dict[str, Any]:
        payload = self.spec_service._require_mapping(verification, "verification")
        if payload.get("checked") is not True:
            raise QuantResearchSpecError("template_verification_required", "verification.checked must be true before publish")
        checked_by = self.spec_service._non_empty_string(payload.get("checked_by"), "verification.checked_by")
        return {
            **dict(payload),
            "checked": True,
            "checked_by": checked_by,
        }

    def _path_for(self, spec: Mapping[str, Any]) -> Path:
        return self._path_for_id(str(spec["template_id"]), str(spec["version"]))

    def _path_for_id(self, template_id: str, version: str) -> Path:
        return self.root / "sql_templates" / f"{template_id}_{version}.json"

    def _write_template(self, path: Path, spec: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized = self.spec_service.normalize_sql_template_spec(spec)
        path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

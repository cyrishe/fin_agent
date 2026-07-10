from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from src.services.quant_research_spec_service import QuantResearchSpecService
from src.services.quant_run_artifact_service import QuantRunArtifactError, QuantRunArtifactService


class QuantResearchPublicationError(ValueError):
    def __init__(self, failure_kind: str, message: str) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind
        self.message = message


class QuantResearchPublicationService:
    DEFAULT_ROOT = Path("src/quant_research/publications")
    REQUIRED_FIELDS = (
        "capability_id",
        "version",
        "capability_type",
        "entrypoint",
        "spec_refs",
        "run_refs",
        "params_schema",
    )
    CAPABILITY_TYPES = {"strategy_pipeline", "backtest_report"}
    ENTRYPOINT_NAMES = {"run_strategy_pipeline", "run_backtest"}
    SPEC_KINDS = {"factor", "strategy", "sql_template", "backtest"}

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        spec_service: QuantResearchSpecService | None = None,
        run_artifact_service: QuantRunArtifactService | None = None,
    ) -> None:
        self.root = Path(root) if root else self.DEFAULT_ROOT
        self.spec_service = spec_service or QuantResearchSpecService()
        self.run_artifact_service = run_artifact_service or QuantRunArtifactService()

    def normalize_publication_spec(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        spec = self._require_mapping(payload, "publication_spec")
        missing = [field for field in self.REQUIRED_FIELDS if field not in spec]
        if missing:
            raise QuantResearchPublicationError("missing_publication_field", f"publication missing field: {missing[0]}")
        return {
            "capability_id": self.spec_service._identifier(spec.get("capability_id"), "capability_id"),
            "version": self.spec_service._version(spec.get("version")),
            "display_name": str(spec.get("display_name") or "").strip(),
            "capability_type": self._enum(spec.get("capability_type"), "capability_type", self.CAPABILITY_TYPES),
            "entrypoint": self._normalize_entrypoint(spec.get("entrypoint")),
            "spec_refs": self._normalize_spec_refs(spec.get("spec_refs")),
            "run_refs": self._normalize_run_refs(spec.get("run_refs")),
            "params_schema": dict(self._require_mapping(spec.get("params_schema"), "params_schema")),
            "status": self._enum(spec.get("status") or "draft", "status", {"draft", "verified", "deprecated"}),
            "enabled": bool(spec.get("enabled", False)),
            "audit": dict(self._require_mapping(spec.get("audit") or {}, "audit")),
        }

    def save_draft(self, publication_spec: Mapping[str, Any], *, overwrite: bool = False) -> Dict[str, Any]:
        spec = self.normalize_publication_spec({**dict(publication_spec), "status": "draft", "enabled": False})
        path = self._path_for(spec["capability_id"], spec["version"])
        if path.exists() and not overwrite:
            raise QuantResearchPublicationError("publication_already_exists", f"publication already exists: {spec['capability_id']} {spec['version']}")
        spec["audit"] = {**spec.get("audit", {}), "registry_state": "draft"}
        self._write_publication(path, spec)
        return self._summary(spec, path)

    def publish_publication(
        self,
        capability_id: str,
        *,
        version: str = "v1",
        verification: Mapping[str, Any],
    ) -> Dict[str, Any]:
        normalized_id = self.spec_service._identifier(capability_id, "capability_id")
        normalized_version = self.spec_service._version(version)
        path = self._path_for(normalized_id, normalized_version)
        if not path.exists():
            raise QuantResearchPublicationError("publication_not_found", f"publication not found: {normalized_id} {normalized_version}")
        verification_payload = self._require_verification(verification)
        spec = self.normalize_publication_spec(json.loads(path.read_text(encoding="utf-8")))
        if not spec["spec_refs"]:
            raise QuantResearchPublicationError("publication_verification_required", "publication must bind at least one spec ref before publish")
        if not spec["run_refs"]:
            raise QuantResearchPublicationError("publication_verification_required", "publication must bind at least one run ref before publish")
        spec["status"] = "verified"
        spec["enabled"] = bool(verification_payload.get("enabled", True))
        spec["audit"] = {
            **spec.get("audit", {}),
            "registry_state": "verified",
            "verification": verification_payload,
        }
        self._write_publication(path, spec)
        return self._summary(spec, path)

    def load_publications(self) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        if not self.root.exists():
            return result
        for path in sorted(self.root.glob("*.json")):
            if path.name.startswith("._"):
                continue
            spec = self.normalize_publication_spec(json.loads(path.read_text(encoding="utf-8")))
            key = f"{spec['capability_id']}:{spec['version']}"
            if key in result:
                raise QuantResearchPublicationError("duplicate_publication", f"duplicate publication: {key}")
            spec["catalog_path"] = str(path)
            spec["publication_hash"] = self.spec_service.spec_hash(spec)
            result[key] = spec
        return result

    def load_active_publications(self) -> Dict[str, Dict[str, Any]]:
        return {
            key: value
            for key, value in self.load_publications().items()
            if value.get("status") == "verified" and value.get("enabled") is True
        }

    def _normalize_entrypoint(self, raw: Any) -> Dict[str, Any]:
        entrypoint = self._require_mapping(raw, "entrypoint")
        entry_type = self._enum(entrypoint.get("type") or "internal_service", "entrypoint.type", {"internal_service"})
        name = self._enum(entrypoint.get("name"), "entrypoint.name", self.ENTRYPOINT_NAMES)
        return {"type": entry_type, "name": name}

    def _normalize_spec_refs(self, raw: Any) -> List[Dict[str, str]]:
        if not isinstance(raw, list):
            raise QuantResearchPublicationError("invalid_spec_refs", "spec_refs must be a list")
        result: List[Dict[str, str]] = []
        seen = set()
        for index, item in enumerate(raw):
            ref = self._require_mapping(item, f"spec_refs[{index}]")
            kind = self._enum(ref.get("kind"), f"spec_refs[{index}].kind", self.SPEC_KINDS)
            ref_id = self.spec_service._identifier(ref.get("id"), f"spec_refs[{index}].id")
            version = self.spec_service._version(ref.get("version"))
            key = (kind, ref_id, version)
            if key in seen:
                raise QuantResearchPublicationError("duplicate_spec_ref", f"duplicate spec ref: {kind}:{ref_id}:{version}")
            seen.add(key)
            result.append({"kind": kind, "id": ref_id, "version": version})
        return result

    def _normalize_run_refs(self, raw: Any) -> List[Dict[str, str]]:
        if not isinstance(raw, list):
            raise QuantResearchPublicationError("invalid_run_refs", "run_refs must be a list")
        result: List[Dict[str, str]] = []
        seen = set()
        for index, item in enumerate(raw):
            ref = self._require_mapping(item, f"run_refs[{index}]")
            run_type = self._enum(ref.get("run_type"), f"run_refs[{index}].run_type", QuantRunArtifactService.ALLOWED_RUN_TYPES)
            try:
                run_ref = self.run_artifact_service.run_ref(self.run_artifact_service.run_id_from_ref(str(ref.get("run_ref") or "")))
            except QuantRunArtifactError as exc:
                raise QuantResearchPublicationError(exc.failure_kind, exc.message) from exc
            key = (run_type, run_ref)
            if key in seen:
                raise QuantResearchPublicationError("duplicate_run_ref", f"duplicate run ref: {run_type}:{run_ref}")
            seen.add(key)
            result.append({"run_type": run_type, "run_ref": run_ref})
        return result

    def _require_verification(self, verification: Mapping[str, Any]) -> Dict[str, Any]:
        payload = self._require_mapping(verification, "verification")
        if payload.get("checked") is not True:
            raise QuantResearchPublicationError("publication_verification_required", "verification.checked must be true before publish")
        checked_by = str(payload.get("checked_by") or "").strip()
        if not checked_by:
            raise QuantResearchPublicationError("publication_verification_required", "verification.checked_by is required")
        return {**dict(payload), "checked": True, "checked_by": checked_by}

    def _path_for(self, capability_id: str, version: str) -> Path:
        return self.root / f"{capability_id}_{version}.json"

    def _write_publication(self, path: Path, spec: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized = self.normalize_publication_spec(spec)
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _summary(self, spec: Mapping[str, Any], path: Path) -> Dict[str, Any]:
        return {
            "capability_id": spec["capability_id"],
            "version": spec["version"],
            "status": spec["status"],
            "enabled": spec["enabled"],
            "path": str(path),
            "publication_hash": self.spec_service.spec_hash(dict(spec)),
        }

    def _enum(self, value: Any, field_name: str, allowed: set[str]) -> str:
        text = str(value or "").strip()
        if text not in allowed:
            raise QuantResearchPublicationError("invalid_publication_enum", f"{field_name} must be one of: {sorted(allowed)}")
        return text

    def _require_mapping(self, value: Any, field_name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise QuantResearchPublicationError("invalid_publication_spec", f"{field_name} must be an object")
        return value

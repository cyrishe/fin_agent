from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
from pathlib import Path
from typing import Any, Dict, Mapping


class QuantRunArtifactError(ValueError):
    def __init__(self, failure_kind: str, message: str) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind
        self.message = message


class QuantRunArtifactService:
    SCHEMA_VERSION = "quant_run_artifact.v1"
    RUN_REF_PREFIX = "quant://run/"
    ALLOWED_RUN_TYPES = {"FactorRun", "StrategyRun", "QuantResearchPipelineRun", "BacktestRun"}
    _RUN_ID_RE = re.compile(r"^qrun_[A-Za-z0-9_-]{8,80}$")

    def __init__(self, *, root: str | Path = "data/quant_research_runs") -> None:
        self.root = Path(root)

    def write_run(
        self,
        run_payload: Mapping[str, Any],
        *,
        run_id: str = "",
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        payload = self._validate_run_payload(run_payload)
        normalized_run_id = self._normalize_run_id(run_id) if run_id else self._new_run_id(payload)
        run_dir = self._run_dir(normalized_run_id)
        if run_dir.exists() and not overwrite:
            raise QuantRunArtifactError("run_artifact_exists", f"quant run artifact already exists: {normalized_run_id}")
        run_dir.mkdir(parents=True, exist_ok=True)

        payload_hash = self._payload_hash(payload)
        run_path = run_dir / "run.json"
        run_path.write_text(self._json_dumps(payload), encoding="utf-8")
        manifest = {
            "schema_version": self.SCHEMA_VERSION,
            "run_id": normalized_run_id,
            "run_ref": self.run_ref(normalized_run_id),
            "run_type": payload["run_type"],
            "status": str(payload.get("status") or ""),
            "payload_sha256": payload_hash,
            "files": {"run": "run.json"},
            "created_at": self._now(),
        }
        (run_dir / "manifest.json").write_text(self._json_dumps(manifest), encoding="utf-8")
        return manifest

    def read_run(self, run_ref_or_id: str) -> Dict[str, Any]:
        run_id = self.run_id_from_ref(run_ref_or_id)
        run_dir = self._run_dir(run_id, must_exist=True)
        manifest_path = run_dir / "manifest.json"
        run_path = run_dir / "run.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload = json.loads(run_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise QuantRunArtifactError("invalid_run_artifact", f"quant run artifact is unreadable: {exc}") from exc
        if not isinstance(manifest, dict) or not isinstance(payload, dict):
            raise QuantRunArtifactError("invalid_run_artifact", "quant run manifest and payload must be objects")
        self._validate_manifest(run_id, manifest)
        payload = self._validate_run_payload(payload)
        expected_hash = str(manifest.get("payload_sha256") or "")
        actual_hash = self._payload_hash(payload)
        if expected_hash != actual_hash:
            raise QuantRunArtifactError("run_payload_hash_mismatch", "quant run payload hash does not match manifest")
        return {"manifest": manifest, "run": payload}

    def run_id_from_ref(self, run_ref_or_id: str) -> str:
        value = str(run_ref_or_id or "").strip()
        if value.startswith(self.RUN_REF_PREFIX):
            value = value[len(self.RUN_REF_PREFIX):].strip()
        return self._normalize_run_id(value)

    def run_ref(self, run_id: str) -> str:
        return f"{self.RUN_REF_PREFIX}{self._normalize_run_id(run_id)}"

    def _validate_run_payload(self, run_payload: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(run_payload, Mapping):
            raise QuantRunArtifactError("invalid_run_payload", "run payload must be an object")
        payload = dict(run_payload)
        run_type = str(payload.get("run_type") or "").strip()
        if run_type not in self.ALLOWED_RUN_TYPES:
            raise QuantRunArtifactError("unsupported_run_type", f"unsupported quant run type: {run_type}")
        return payload

    def _validate_manifest(self, run_id: str, manifest: Mapping[str, Any]) -> None:
        if manifest.get("schema_version") != self.SCHEMA_VERSION:
            raise QuantRunArtifactError("invalid_run_artifact", "quant run manifest schema_version is unsupported")
        if str(manifest.get("run_id") or "") != run_id:
            raise QuantRunArtifactError("invalid_run_artifact", "quant run manifest id mismatch")
        if str(manifest.get("run_ref") or "") != self.run_ref(run_id):
            raise QuantRunArtifactError("invalid_run_artifact", "quant run manifest ref mismatch")
        files = manifest.get("files") if isinstance(manifest.get("files"), Mapping) else {}
        if files.get("run") != "run.json":
            raise QuantRunArtifactError("invalid_run_artifact", "quant run manifest files.run must be run.json")

    def _new_run_id(self, payload: Mapping[str, Any]) -> str:
        digest = self._payload_hash(payload)[len("sha256:"):][:12]
        return f"qrun_{dt.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{digest}_{secrets.token_hex(4)}"

    def _normalize_run_id(self, value: str) -> str:
        run_id = str(value or "").strip()
        if not self._RUN_ID_RE.match(run_id):
            raise QuantRunArtifactError("invalid_run_id", "quant run id must be qrun_<safe id>")
        return run_id

    def _run_dir(self, run_id: str, *, must_exist: bool = False) -> Path:
        normalized = self._normalize_run_id(run_id)
        root = self.root.resolve()
        path = (root / normalized).resolve()
        if root not in path.parents and path != root:
            raise QuantRunArtifactError("invalid_run_id", "quant run id escapes artifact root")
        if must_exist and not path.exists():
            raise QuantRunArtifactError("run_artifact_not_found", f"quant run artifact not found: {normalized}")
        return path

    def _payload_hash(self, payload: Mapping[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _json_dumps(self, payload: Mapping[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def _now(self) -> str:
        return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

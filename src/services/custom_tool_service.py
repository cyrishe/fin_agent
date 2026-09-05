from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from src.services.codex_exec_skill_harness import (
    CodexCustomToolCoder,
    CodexCustomToolDesigner,
    CodexCustomToolEditCoder,
    CodexCustomToolEditPlanner,
    CodexCustomToolTester,
)
from src.services.agent_providers import build_agent_skill_harness
from src.services.python_execution_runtime import PythonExecutionRuntime
from src.services.custom_tool_design_protocol_service import CustomToolDesignProtocolService
from src.services.design_narrative_service import compose_design_narrative
from src.experiments.staged_data_protocol.phase2.trade_date_resolver import TradeDateResolver
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService
from src.services.strategy_revision_contract_service import (
    StrategyRevisionContractError,
    StrategyRevisionContractService,
)
from src.services.finance_tool_profile_service import (
    FinanceToolProfileError,
    FinanceToolProfileService,
)


class CustomToolError(ValueError):
    pass


class CustomToolStrategyContractError(CustomToolError):
    pass


class CustomToolFinanceProfileError(CustomToolError):
    pass


DEFAULT_MAX_TEST_TURNS = 4

def _trim(value: Any) -> str:
    return str(value or "").strip()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _revision_companions(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: dict(value[key])
        for key in (
            "finance_tool_profile",
            "strategy_runtime_profile",
            "selection_output_profile",
        )
        if isinstance(value.get(key), Mapping) and value.get(key)
    }


def _normalized_executable_revision(value: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(value)
    try:
        profile = FinanceToolProfileService().normalize(
            result.get("finance_tool_profile"),
            strategy_runtime_profile=result.get("strategy_runtime_profile"),
            selection_output_profile=result.get("selection_output_profile"),
        )
        FinanceToolProfileService.assert_implementation_allowed(profile)
    except FinanceToolProfileError as exc:
        raise CustomToolFinanceProfileError(str(exc)) from exc
    if profile is None:
        result.pop("finance_tool_profile", None)
    else:
        result["finance_tool_profile"] = profile
    return result


def _synchronized_modules(design: Mapping[str, Any]) -> List[Dict[str, Any]]:
    modules = [
        dict(item)
        for item in design.get("modules") or []
        if isinstance(item, Mapping)
    ]
    code = _trim(design.get("code"))
    if not modules:
        return (
            [
                {
                    "module_id": "main",
                    "language": "python",
                    "entrypoint": "run",
                    "source_code": code,
                }
            ]
            if code
            else []
        )
    if code:
        modules[0]["source_code"] = code
    return modules


class CustomToolStoreService:
    def __init__(self, *, root_dir: Optional[str] = None, backend: str = "") -> None:
        storage_backend = _trim(backend or os.environ.get("FIN_AGENT_CUSTOM_TOOL_STORAGE") or "database").lower()
        self._database_store = None
        if root_dir is None and storage_backend == "database":
            from src.services.database_custom_tool_store_service import DatabaseCustomToolStoreService

            self._database_store = DatabaseCustomToolStoreService(error_type=CustomToolError)
        self.root_dir = Path(root_dir or "data/custom_tools")

    def tool_dir(self, tool_name: str) -> Path:
        if self._database_store is not None:
            raise CustomToolError("database custom tools do not have user-visible directories")
        name = self.normalize_tool_name(tool_name)
        if not name:
            raise CustomToolError("tool_name is required")
        return self.root_dir / name

    @staticmethod
    def normalize_tool_name(value: Any) -> str:
        raw = _trim(value).lower()
        raw = re.sub(r"[^a-z0-9_]+", "_", raw)
        raw = re.sub(r"_+", "_", raw).strip("_")
        if raw and not raw.startswith("ct_"):
            raw = f"ct_{raw}"
        return raw[:64]

    def exists(self, tool_name: str) -> bool:
        if self._database_store is not None:
            return self._database_store.exists(tool_name)
        try:
            return (self.tool_dir(tool_name) / "manifest.json").exists()
        except CustomToolError:
            return False

    def save_draft(self, design: Mapping[str, Any], *, owner_id: str = "") -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.save_draft(design, owner_id=owner_id)
        design = _normalized_executable_revision(design)
        manifest = dict(design.get("manifest") or {})
        tool_name = self.normalize_tool_name(manifest.get("tool_name"))
        if not tool_name:
            raise CustomToolError("manifest.tool_name is required")
        manifest["tool_name"] = tool_name
        manifest["status"] = "draft"
        manifest["owner_id"] = _trim(owner_id)
        manifest.setdefault("visibility", "personal")
        code = _trim(design.get("code"))
        if not code:
            raise CustomToolError("code is required")
        root = self.tool_dir(tool_name)
        root.mkdir(parents=True, exist_ok=True)
        revision_no = self._next_revision(root)
        manifest["current_revision"] = revision_no
        manifest["code_hash"] = hashlib.sha256(code.encode("utf-8")).hexdigest()
        root.joinpath("manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        root.joinpath("input_schema.json").write_text(_json_text(design.get("input_schema") or {}), encoding="utf-8")
        root.joinpath("output_schema.json").write_text(_json_text(design.get("output_schema") or {}), encoding="utf-8")
        spec = {
            "sample_input": dict(design.get("sample_input") or {}),
            "modules": _synchronized_modules(design),
            "proposed_tests": [dict(item) for item in design.get("proposed_tests") or [] if isinstance(item, Mapping)],
            "implementation_explanation": dict(design.get("implementation_explanation") or {}),
            "implementation_review": dict(design.get("implementation_review") or {}),
            "design_contract": dict(design.get("design_contract") or {}),
            "design_provenance": dict(design.get("design_provenance") or {}),
            "design_feedback_evidence": [
                dict(item) for item in design.get("design_feedback_evidence") or [] if isinstance(item, Mapping)
            ],
            **_revision_companions(design),
        }
        root.joinpath("spec.json").write_text(_json_text(spec), encoding="utf-8")
        root.joinpath("tool.py").write_text(code + "\n", encoding="utf-8")
        rev_dir = root / "revisions" / str(revision_no)
        rev_dir.mkdir(parents=True, exist_ok=True)
        rev_dir.joinpath("manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        rev_dir.joinpath("input_schema.json").write_text(
            _json_text(design.get("input_schema") or {}), encoding="utf-8"
        )
        rev_dir.joinpath("output_schema.json").write_text(
            _json_text(design.get("output_schema") or {}), encoding="utf-8"
        )
        rev_dir.joinpath("spec.json").write_text(_json_text(spec), encoding="utf-8")
        rev_dir.joinpath("tool.py").write_text(code + "\n", encoding="utf-8")
        return self.load(tool_name)

    def save_candidate_revision(
        self,
        design: Mapping[str, Any],
        *,
        owner_id: str = "",
        tool_name: str = "",
    ) -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.save_candidate_revision(
                design,
                owner_id=owner_id,
                tool_name=tool_name,
            )
        design = _normalized_executable_revision(design)
        manifest = dict(design.get("manifest") or {})
        manifest_name = self.normalize_tool_name(manifest.get("tool_name"))
        target_name = self.normalize_tool_name(tool_name) or manifest_name
        if not target_name:
            raise CustomToolError("manifest.tool_name is required")
        if manifest_name and manifest_name != target_name:
            raise CustomToolError("candidate custom tool identity changed")
        code = _trim(design.get("code"))
        if not code:
            raise CustomToolError("code is required")
        root = self.tool_dir(target_name)
        with self._filesystem_revision_lock(root):
            active = self.load(target_name)
            active_manifest = dict(active["manifest"])
            if _trim(active_manifest.get("owner_id")) != _trim(owner_id):
                raise CustomToolError("custom tool is not owned by current user")
            revision_no = self._next_candidate_revision(root)
            manifest.update(
                {
                    "tool_name": target_name,
                    "status": "draft",
                    "owner_id": _trim(owner_id),
                    "visibility": _trim(manifest.get("visibility"))
                    or _trim(active_manifest.get("visibility"))
                    or "personal",
                    "current_revision": revision_no,
                    "active_revision": int(
                        active_manifest.get("current_revision") or 0
                    ),
                    "base_revision": int(
                        active_manifest.get("current_revision") or 0
                    ),
                    "code_hash": hashlib.sha256(code.encode("utf-8")).hexdigest(),
                }
            )
            spec = self._filesystem_spec(design)
            rev_dir = root / "revisions" / str(revision_no)
            if rev_dir.exists():
                raise CustomToolError(
                    f"custom tool revision already exists: {target_name}@{revision_no}"
                )
            rev_dir.mkdir(parents=True)
            rev_dir.joinpath("manifest.json").write_text(
                _json_text(manifest), encoding="utf-8"
            )
            rev_dir.joinpath("input_schema.json").write_text(
                _json_text(design.get("input_schema") or {}), encoding="utf-8"
            )
            rev_dir.joinpath("output_schema.json").write_text(
                _json_text(design.get("output_schema") or {}), encoding="utf-8"
            )
            rev_dir.joinpath("spec.json").write_text(
                _json_text(spec), encoding="utf-8"
            )
            rev_dir.joinpath("tool.py").write_text(code + "\n", encoding="utf-8")
        return self.load_revision(target_name, revision_no)

    def load(self, tool_name: str) -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.load(tool_name)
        root = self.tool_dir(tool_name)
        if not (root / "manifest.json").exists():
            raise CustomToolError(f"custom tool not found: {tool_name}")
        # The root manifest is the only active pointer. Read all versioned assets
        # from that immutable snapshot so a concurrent activation cannot expose
        # an old manifest together with new code/schema files.
        active_manifest = json.loads(
            root.joinpath("manifest.json").read_text(encoding="utf-8")
        )
        active_revision = int(active_manifest.get("current_revision") or 0)
        revision_root = root / "revisions" / str(active_revision)
        revision_manifest = self._load_json(revision_root / "manifest.json")
        manifest = revision_manifest or dict(active_manifest)
        manifest.update(
            {
                "tool_name": _trim(active_manifest.get("tool_name")),
                "status": _trim(active_manifest.get("status")) or "draft",
                "owner_id": _trim(active_manifest.get("owner_id")),
                "visibility": _trim(active_manifest.get("visibility"))
                or _trim(manifest.get("visibility"))
                or "personal",
                "current_revision": active_revision,
                "active_revision": active_revision,
                "last_test": dict(active_manifest.get("last_test") or {}),
            }
        )
        spec_path = revision_root / "spec.json"
        input_schema_path = revision_root / "input_schema.json"
        output_schema_path = revision_root / "output_schema.json"
        code_path = revision_root / "tool.py"
        spec = self._load_json(spec_path) if spec_path.exists() else self._load_json(root / "spec.json")
        return {
            "manifest": manifest,
            "input_schema": (
                self._load_json(input_schema_path)
                if input_schema_path.exists()
                else self._load_json(root / "input_schema.json")
            ),
            "output_schema": (
                self._load_json(output_schema_path)
                if output_schema_path.exists()
                else self._load_json(root / "output_schema.json")
            ),
            "code": (
                code_path.read_text(encoding="utf-8")
                if code_path.exists()
                else root.joinpath("tool.py").read_text(encoding="utf-8")
                if (root / "tool.py").exists()
                else ""
            ),
            "modules": [dict(item) for item in spec.get("modules") or [] if isinstance(item, Mapping)],
            "sample_input": dict(spec.get("sample_input") or {}),
            "proposed_tests": [dict(item) for item in spec.get("proposed_tests") or [] if isinstance(item, Mapping)],
            "implementation_explanation": dict(spec.get("implementation_explanation") or {}),
            "implementation_review": dict(spec.get("implementation_review") or {}),
            "design_contract": dict(spec.get("design_contract") or {}),
            "design_provenance": dict(spec.get("design_provenance") or {}),
            "design_feedback_evidence": [
                dict(item) for item in spec.get("design_feedback_evidence") or [] if isinstance(item, Mapping)
            ],
            **_revision_companions(spec),
            "root": str(root),
        }

    def load_revision(self, tool_name: str, revision_no: int) -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.load_revision(tool_name, revision_no)
        name = self.normalize_tool_name(tool_name)
        requested_revision = int(revision_no or 0)
        if requested_revision < 1:
            raise CustomToolError("revision_no must be a positive integer")
        root = self.tool_dir(name)
        active_manifest = self._load_json(root / "manifest.json")
        if not active_manifest:
            raise CustomToolError(f"custom tool not found: {name}")
        rev_dir = root / "revisions" / str(requested_revision)
        revision_manifest = self._load_json(rev_dir / "manifest.json")
        if not revision_manifest:
            raise CustomToolError(
                f"custom tool revision not found: {name}@{requested_revision}"
            )
        spec = self._load_json(rev_dir / "spec.json")
        active_revision = int(active_manifest.get("current_revision") or 0)
        is_active = requested_revision == active_revision
        revision_manifest.update(
            {
                "tool_name": name,
                "status": _trim(active_manifest.get("status"))
                if is_active
                else "draft",
                "owner_id": _trim(active_manifest.get("owner_id")),
                "current_revision": requested_revision,
                "active_revision": active_revision,
                "last_test": (
                    dict(active_manifest.get("last_test") or {})
                    if is_active
                    else dict(revision_manifest.get("last_test") or {})
                ),
            }
        )
        input_schema_path = rev_dir / "input_schema.json"
        output_schema_path = rev_dir / "output_schema.json"
        return {
            "manifest": revision_manifest,
            "input_schema": (
                self._load_json(input_schema_path)
                if input_schema_path.exists()
                else self._load_json(root / "input_schema.json")
                if is_active
                else {}
            ),
            "output_schema": (
                self._load_json(output_schema_path)
                if output_schema_path.exists()
                else self._load_json(root / "output_schema.json")
                if is_active
                else {}
            ),
            "code": (
                rev_dir.joinpath("tool.py").read_text(encoding="utf-8")
                if rev_dir.joinpath("tool.py").exists()
                else ""
            ),
            "modules": [
                dict(item)
                for item in spec.get("modules") or []
                if isinstance(item, Mapping)
            ],
            "sample_input": dict(spec.get("sample_input") or {}),
            "proposed_tests": [
                dict(item)
                for item in spec.get("proposed_tests") or []
                if isinstance(item, Mapping)
            ],
            "implementation_explanation": dict(
                spec.get("implementation_explanation") or {}
            ),
            "implementation_review": dict(spec.get("implementation_review") or {}),
            "design_contract": dict(spec.get("design_contract") or {}),
            "design_provenance": dict(spec.get("design_provenance") or {}),
            "design_feedback_evidence": [
                dict(item)
                for item in spec.get("design_feedback_evidence") or []
                if isinstance(item, Mapping)
            ],
            **_revision_companions(spec),
            "root": str(root),
            "storage": {
                "kind": "filesystem",
                "revision": requested_revision,
                "is_active": is_active,
            },
        }

    def load_revision_for_runtime(
        self,
        tool_name: str,
        revision_no: int,
        *,
        owner_ids: Sequence[str],
    ) -> Dict[str, Any]:
        """Load one exact revision after server-resolved visibility checks."""

        allowed_owners = self._normalize_owner_ids(list(owner_ids))
        if not allowed_owners:
            raise CustomToolError(
                "server-resolved owner identity is required for revision runtime"
            )
        bundle = self.load_revision(tool_name, revision_no)
        manifest = bundle.get("manifest")
        if not isinstance(manifest, Mapping):
            raise CustomToolError("custom tool revision manifest is unavailable")
        if not self._is_visible_to_owner(manifest, allowed_owners):
            raise CustomToolError("custom tool is not visible to current user")
        return bundle

    def activate_revision(
        self,
        tool_name: str,
        candidate_revision: int,
        *,
        expected_active_revision: int,
        owner_id: str,
    ) -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.activate_revision(
                tool_name,
                candidate_revision,
                expected_active_revision=expected_active_revision,
                owner_id=owner_id,
            )
        name = self.normalize_tool_name(tool_name)
        root = self.tool_dir(name)
        with self._filesystem_revision_lock(root):
            active_manifest = self._load_json(root / "manifest.json")
            if not active_manifest:
                raise CustomToolError(f"custom tool not found: {name}")
            if _trim(active_manifest.get("owner_id")) != _trim(owner_id):
                raise CustomToolError("custom tool is not owned by current user")
            current_revision = int(active_manifest.get("current_revision") or 0)
            expected_active = int(expected_active_revision or 0)
            if current_revision != expected_active:
                raise CustomToolError(
                    "active custom tool revision changed: "
                    f"expected {expected_active}, current {current_revision}"
                )
            candidate = _normalized_executable_revision(
                self.load_revision(name, candidate_revision)
            )
            candidate_manifest = dict(candidate["manifest"])
            candidate_base = int(candidate_manifest.get("base_revision") or 0)
            if candidate_base and candidate_base != expected_active:
                raise CustomToolError(
                    "candidate custom tool base revision changed: "
                    f"expected {candidate_base}, current {expected_active}"
                )
            candidate_manifest.update(
                {
                    "status": "active",
                    "owner_id": _trim(active_manifest.get("owner_id")),
                    "current_revision": int(candidate_revision),
                    "active_revision": int(candidate_revision),
                }
            )
            if not isinstance(candidate_manifest.get("last_test"), Mapping):
                candidate_manifest.pop("last_test", None)
            self._atomic_write(
                root / "input_schema.json", _json_text(candidate["input_schema"])
            )
            self._atomic_write(
                root / "output_schema.json", _json_text(candidate["output_schema"])
            )
            self._atomic_write(
                root / "spec.json", _json_text(self._filesystem_spec(candidate))
            )
            self._atomic_write(root / "tool.py", _trim(candidate.get("code")) + "\n")
            # The manifest is the active pointer and is intentionally replaced last.
            self._atomic_write(root / "manifest.json", _json_text(candidate_manifest))
        return self.load(name)

    def list_tools(
        self,
        *,
        include_inactive: bool = False,
        owner_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        if self._database_store is not None:
            return self._database_store.list_tools(include_inactive=include_inactive, owner_ids=owner_ids)
        if not self.root_dir.exists():
            return []
        allowed_owners = self._normalize_owner_ids(owner_ids)
        rows: List[Dict[str, Any]] = []
        for path in sorted(self.root_dir.iterdir()):
            if not path.is_dir() or not (path / "manifest.json").exists():
                continue
            try:
                manifest = json.loads(path.joinpath("manifest.json").read_text(encoding="utf-8"))
            except Exception:
                continue
            if not include_inactive and _trim(manifest.get("status")) != "active":
                continue
            if owner_ids is not None and not self._is_visible_to_owner(manifest, allowed_owners):
                continue
            rows.append(manifest)
        return rows

    def load_for_runtime(
        self,
        tool_name: str,
        *,
        owner_ids: Optional[List[str]] = None,
        allow_inactive: bool = False,
    ) -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.load_for_runtime(tool_name, owner_ids=owner_ids, allow_inactive=allow_inactive)
        bundle = self.load(tool_name)
        manifest = dict(bundle["manifest"])
        if not allow_inactive and _trim(manifest.get("status")) != "active":
            raise CustomToolError("custom tool is not active")
        if owner_ids is not None and not self._is_visible_to_owner(manifest, self._normalize_owner_ids(owner_ids)):
            raise CustomToolError("custom tool is not visible to current user")
        return bundle

    def commit(self, tool_name: str, *, owner_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.commit(tool_name, owner_ids=owner_ids)
        bundle = _normalized_executable_revision(
            self.load_for_runtime(
                tool_name, owner_ids=owner_ids, allow_inactive=True
            )
        )
        manifest = dict(bundle["manifest"])
        manifest["status"] = "active"
        root = self.tool_dir(manifest["tool_name"])
        root.joinpath("manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        return self.load(manifest["tool_name"])

    def record_test(self, tool_name: str, result: Mapping[str, Any]) -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.record_test(tool_name, result)
        bundle = self.load(tool_name)
        manifest = dict(bundle["manifest"])
        manifest["last_test"] = {
            "ok": bool(result.get("ok")),
            "execution_ok": bool(result.get("execution_ok", result.get("ok"))),
            "contract_ok": bool(result.get("contract_ok", result.get("ok"))),
            "error": _trim(result.get("error")),
            "backend": _trim(((result.get("meta") or {}).get("diagnostics") or {}).get("backend")),
        }
        root = self.tool_dir(manifest["tool_name"])
        root.joinpath("manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        return self.load(manifest["tool_name"])

    def publish(
        self,
        tool_name: str,
        *,
        owner_ids: Optional[List[str]] = None,
        actor_id: str = "",
        actor_scopes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.publish(
                tool_name,
                owner_ids=owner_ids,
                actor_id=actor_id,
                actor_scopes=actor_scopes,
            )
        if "custom_tool:publish" not in {_trim(item) for item in actor_scopes or []}:
            raise CustomToolError("public publication requires custom_tool:publish permission")
        bundle = _normalized_executable_revision(
            self.load_for_runtime(
                tool_name, owner_ids=owner_ids, allow_inactive=False
            )
        )
        manifest = dict(bundle["manifest"])
        if (manifest.get("last_test") or {}).get("execution_ok") is not True:
            raise CustomToolError("custom tool must complete a technical run before publication")
        manifest["visibility"] = "public"
        manifest["published_by"] = _trim(actor_id)
        root = self.tool_dir(manifest["tool_name"])
        root.joinpath("manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        return self.load(manifest["tool_name"])

    def _next_revision(self, root: Path) -> int:
        manifest = self._load_json(root / "manifest.json")
        current = int(manifest.get("current_revision") or 0) if manifest else 0
        return current + 1

    def _next_candidate_revision(self, root: Path) -> int:
        current = int(
            self._load_json(root / "manifest.json").get("current_revision") or 0
        )
        revisions_root = root / "revisions"
        stored = [
            int(path.name)
            for path in revisions_root.iterdir()
            if path.is_dir() and path.name.isdigit()
        ] if revisions_root.exists() else []
        return max([current, *stored], default=0) + 1

    @staticmethod
    def _filesystem_spec(design: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "sample_input": dict(design.get("sample_input") or {}),
            "modules": _synchronized_modules(design),
            "proposed_tests": [
                dict(item)
                for item in design.get("proposed_tests") or []
                if isinstance(item, Mapping)
            ],
            "implementation_explanation": dict(
                design.get("implementation_explanation") or {}
            ),
            "implementation_review": dict(design.get("implementation_review") or {}),
            "design_contract": dict(design.get("design_contract") or {}),
            "design_provenance": dict(design.get("design_provenance") or {}),
            "design_feedback_evidence": [
                dict(item)
                for item in design.get("design_feedback_evidence") or []
                if isinstance(item, Mapping)
            ],
            **_revision_companions(design),
        }

    @staticmethod
    @contextmanager
    def _filesystem_revision_lock(root: Path):
        import fcntl

        root.mkdir(parents=True, exist_ok=True)
        with root.joinpath(".revision.lock").open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    @staticmethod
    def _normalize_owner_ids(owner_ids: Optional[List[str]]) -> set[str]:
        return {_trim(item) for item in (owner_ids or []) if _trim(item)}

    @staticmethod
    def _is_visible_to_owner(manifest: Mapping[str, Any], owner_ids: set[str]) -> bool:
        if _trim(manifest.get("visibility")) != "personal":
            return True
        owner_id = _trim(manifest.get("owner_id"))
        return bool(owner_id and owner_id in owner_ids)

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))


class CustomToolDesigner:
    """Small, replaceable designer for natural-language custom tool drafts."""

    def design(self, requirement_text: str) -> Dict[str, Any]:
        text = _trim(requirement_text)
        if not text:
            return {
                "status": "need_more_info",
                "missing": ["工具目标", "输入", "输出", "计算逻辑"],
                "message": "请补充这个工具要解决什么问题、输入是什么、输出是什么、核心计算逻辑是什么。",
            }
        return {
            "status": "need_more_info",
            "missing": ["工具英文名", "中文名和描述", "输入 schema", "输出 schema", "计算逻辑", "样例输入"],
            "message": (
                "自定义工具需要先形成稳定设计：英文名、中文名、描述、输入、输出、计算逻辑和样例。"
                "当前默认 designer 不做领域硬匹配；后续由 LLM designer 生成设计，或由测试注入具体 designer。"
            ),
        }


class CustomToolRuntimeService:
    HISTORICAL_FORMAL_BACKENDS = {"bwrap", "docker", "podman"}

    def __init__(
        self,
        *,
        store: Optional[CustomToolStoreService] = None,
        python_runtime: Optional[PythonExecutionRuntime] = None,
        runtime_root: str = "data/runtime_custom_tools",
        finance_query_fixture: Optional[Mapping[str, Any]] = None,
        max_finance_queries: int = 4,
        finance_runtime: Optional[FinanceDataToolRuntimeService] = None,
    ) -> None:
        self.store = store or CustomToolStoreService()
        self.python_runtime = python_runtime or PythonExecutionRuntime(allow_unsafe_backends=True)
        self.runtime_root = Path(runtime_root)
        self.finance_query_fixture = dict(finance_query_fixture) if isinstance(finance_query_fixture, Mapping) else None
        self.max_finance_queries = max(1, min(int(max_finance_queries or 4), 8))
        self.finance_runtime = finance_runtime or FinanceDataToolRuntimeService(
            trade_date_resolver=TradeDateResolver()
        )

    def run(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        owner_ids: Optional[List[str]] = None,
        allow_inactive: bool = True,
        progress_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        try:
            bundle = self.store.load_for_runtime(
                tool_name,
                owner_ids=owner_ids,
                allow_inactive=allow_inactive,
            )
        except CustomToolError as exc:
            return self._error(tool_name, "permission_or_lifecycle_error", str(exc))
        return self._run_loaded_bundle(
            bundle=bundle,
            arguments=arguments,
            progress_sink=progress_sink,
        )

    def run_revision(
        self,
        tool_name: str,
        revision_no: int,
        arguments: Mapping[str, Any] | None = None,
        *,
        owner_ids: Optional[List[str]] = None,
        progress_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Run one immutable revision for an owner-scoped interactive test.

        Candidate revisions must be testable before activation.  Loading the
        tool by name alone can resolve to the currently active revision, so the
        test workbench uses this explicit revision boundary.
        """

        requested_revision = int(revision_no or 0)
        if requested_revision < 1:
            return self._error(
                tool_name,
                "invalid_revision",
                "revision_no must be a positive integer",
            )
        try:
            bundle = self.store.load_revision(tool_name, requested_revision)
        except CustomToolError as exc:
            return self._error(
                tool_name,
                "permission_or_lifecycle_error",
                str(exc),
            )
        manifest = (
            bundle.get("manifest")
            if isinstance(bundle.get("manifest"), Mapping)
            else {}
        )
        if owner_ids is not None and _trim(manifest.get("visibility")) == "personal":
            allowed = {_trim(item) for item in owner_ids if _trim(item)}
            if _trim(manifest.get("owner_id")) not in allowed:
                return self._error(
                    tool_name,
                    "permission_or_lifecycle_error",
                    "custom tool is not visible to current user",
                )
        return self._run_loaded_bundle(
            bundle=bundle,
            arguments=arguments,
            progress_sink=progress_sink,
        )

    def run_loaded_bundle(
        self,
        *,
        bundle: Mapping[str, Any],
        arguments: Mapping[str, Any] | None,
        effective_as_of: dt.date | str,
        allowed_symbols: Sequence[str],
        runtime_backend: str,
    ) -> Dict[str, Any]:
        """Execute a host-authorized immutable revision under a replay scope."""

        manifest = bundle.get("manifest") if isinstance(bundle, Mapping) else {}
        tool_name = _trim(
            manifest.get("tool_name") if isinstance(manifest, Mapping) else ""
        )
        if self.finance_query_fixture is not None:
            return self._error(
                tool_name,
                "historical_fixture_denied",
                "fixture finance responses cannot be used for historical replay",
            )
        backend = _trim(runtime_backend)
        if (
            backend not in self.HISTORICAL_FORMAL_BACKENDS
            or self.python_runtime.resolve_backend({"backend": backend}) != backend
        ):
            return self._error(
                tool_name,
                "historical_runtime_isolation_required",
                "historical replay requires an available formal sandbox backend",
            )
        return self._run_loaded_bundle(
            bundle=bundle,
            arguments=arguments,
            effective_as_of=effective_as_of,
            allowed_symbols=allowed_symbols,
            runtime_backend=backend,
        )

    def preflight_historical_replay(
        self,
        *,
        bundle: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Prove that replay code cannot bypass the host data boundary."""

        manifest = bundle.get("manifest") if isinstance(bundle, Mapping) else {}
        if not isinstance(manifest, Mapping):
            raise CustomToolError("custom tool revision manifest is unavailable")
        profile = self._runtime_profile(
            manifest,
            backend_override="auto",
        )
        profile["backend_candidates"] = ["bwrap", "docker", "podman"]
        backend = self.python_runtime.resolve_backend(profile)
        if backend not in self.HISTORICAL_FORMAL_BACKENDS:
            raise CustomToolError(
                "historical replay requires an available formal sandbox backend"
            )
        return {
            "formal_sandbox": True,
            "backend": backend,
            "network": "none",
            "workspace_access": "none",
        }

    def _run_loaded_bundle(
        self,
        *,
        bundle: Mapping[str, Any],
        arguments: Mapping[str, Any] | None,
        effective_as_of: dt.date | str | None = None,
        allowed_symbols: Sequence[str] = (),
        runtime_backend: str = "",
        progress_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        manifest_value = bundle.get("manifest") if isinstance(bundle, Mapping) else {}
        if not isinstance(manifest_value, Mapping):
            return self._error(
                "",
                "invalid_revision_bundle",
                "custom tool revision manifest is unavailable",
            )
        manifest = dict(manifest_value)
        finance_profile = bundle.get("finance_tool_profile")
        if (
            isinstance(finance_profile, Mapping)
            and _trim(finance_profile.get("family")) == "action"
        ):
            return self._error(
                _trim(manifest.get("tool_name")),
                "action_not_executable",
                "action finance Tools are planned but not executable",
            )
        args = dict(arguments or {})
        code = self._wrap_code(bundle.get("code") or "", tool_name=manifest["tool_name"])
        run_dir = Path(tempfile.mkdtemp(prefix=f"{manifest['tool_name']}_", dir=str(self._ensure_runtime_root())))
        input_dir = run_dir / "input"
        output_dir = run_dir / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        input_dir.joinpath("input.json").write_text(json.dumps(args, ensure_ascii=False, indent=2), encoding="utf-8")
        profile = self._runtime_profile(
            manifest,
            backend_override=runtime_backend,
        )
        runtime_result: Dict[str, Any] = {}
        finance_responses: Dict[str, Any] = {}
        finance_bridge_rounds = 0
        finance_bridge_errors: List[str] = []
        emitted_execution_logs: set[str] = set()
        for attempt in range(1, self.max_finance_queries + 2):
            for child in output_dir.iterdir():
                if child.is_file():
                    child.unlink()
            input_dir.joinpath("finance_responses.json").write_text(
                json.dumps(finance_responses, ensure_ascii=False),
                encoding="utf-8",
            )
            runtime_result = self.python_runtime.execute(
                code=code,
                input_dir=str(input_dir),
                output_dir=str(output_dir),
                profile=profile,
                attempt=attempt,
            )
            attempt_logs = self._load_execution_logs(
                output_dir / "execution_logs.jsonl"
            )
            if callable(progress_sink):
                for log in attempt_logs:
                    log_key = json.dumps(log, ensure_ascii=False, sort_keys=True)
                    if log_key in emitted_execution_logs:
                        continue
                    emitted_execution_logs.add(log_key)
                    try:
                        progress_sink(dict(log))
                    except Exception:
                        # UI streaming is advisory and must not affect execution.
                        pass
            pending_requests = self._load_finance_requests(output_dir / "finance_requests.json")
            unresolved = [
                item for item in pending_requests if item["key"] not in finance_responses
            ]
            if not unresolved:
                break
            if len(finance_responses) + len(unresolved) > self.max_finance_queries:
                return self._error(
                    manifest["tool_name"],
                    "finance_query_limit",
                    f"custom tool exceeded finance query limit ({self.max_finance_queries})",
                )
            finance_bridge_rounds += 1
            for item in unresolved:
                request = item["request"]
                bindings = item["bindings"]
                request_key = item["key"]
                if len(request) > 4000:
                    return self._error(manifest["tool_name"], "finance_query_invalid", "finance query is too long")
                allowed, denial = self._finance_request_allowed(request, bundle)
                if not allowed:
                    return self._error(manifest["tool_name"], "finance_query_denied", denial)
                try:
                    if effective_as_of is None:
                        finance_response = self.finance_runtime.execute_request(
                            request=request,
                            bindings=bindings,
                        )
                    else:
                        finance_response = self.finance_runtime.execute_historical_request(
                            request=request,
                            effective_as_of=effective_as_of,
                            allowed_symbols=list(allowed_symbols),
                            bindings=bindings,
                        )
                    finance_responses[request_key] = finance_response
                    if effective_as_of is not None and finance_response.get("ok") is not True:
                        execution = (
                            finance_response.get("execution")
                            if isinstance(finance_response.get("execution"), Mapping)
                            else {}
                        )
                        return self._error(
                            manifest["tool_name"],
                            _trim(execution.get("status"))
                            or "historical_finance_query_failed",
                            _trim(execution.get("reason"))
                            or "historical finance query failed",
                        )
                except Exception:
                    finance_bridge_errors.append("finance API execution failed")
                    if effective_as_of is not None:
                        return self._error(
                            manifest["tool_name"],
                            "historical_finance_query_failed",
                            "historical finance API execution failed",
                        )
                    finance_responses[request_key] = {
                        "ok": False,
                        "error": "finance API execution failed",
                        "data": [],
                    }
        else:
            return self._error(manifest["tool_name"], "finance_query_round_limit", "finance query bridge did not converge")
        diagnostics = dict(runtime_result.get("diagnostics") or {})
        execution_logs = self._load_execution_logs(output_dir / "execution_logs.jsonl")
        diagnostics.update({
            "finance_query_count": len(finance_responses),
            "finance_bridge_rounds": finance_bridge_rounds,
            "finance_bridge_errors": finance_bridge_errors,
            "execution_log_count": len(execution_logs),
        })
        if effective_as_of is not None:
            diagnostics["historical_replay"] = {
                "effective_as_of": str(effective_as_of),
                "symbol_count": len(tuple(allowed_symbols)),
            }
        if execution_logs:
            diagnostics["execution_logs"] = execution_logs
        if not runtime_result.get("ok"):
            return self._error(manifest["tool_name"], _trim(runtime_result.get("failure_kind")) or "runtime_error", _trim(diagnostics.get("stderr")), diagnostics=diagnostics)
        output_path = output_dir / "output.json"
        if not output_path.exists():
            return self._error(manifest["tool_name"], "output_missing", "custom tool did not write output.json", diagnostics=diagnostics)
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return self._error(manifest["tool_name"], "output_json_error", str(exc), diagnostics=diagnostics)
        result = {
            "tool": manifest["tool_name"],
            "ok": True,
            "data": payload if isinstance(payload, dict) else {"value": payload},
            "error": "",
            "meta": {
                "custom_tool": True,
                "display_name": manifest.get("display_name"),
                "revision": manifest.get("current_revision"),
                "execution_logs": execution_logs,
                "diagnostics": diagnostics,
            },
        }
        return result

    @staticmethod
    def _runtime_profile(
        manifest: Mapping[str, Any],
        *,
        backend_override: str = "",
    ) -> Dict[str, Any]:
        runtime_cfg = (
            manifest.get("runtime")
            if isinstance(manifest.get("runtime"), Mapping)
            else {}
        )
        return {
            "name": "custom_tool_python_v1",
            "backend": (
                _trim(backend_override)
                or _trim(runtime_cfg.get("backend"))
                or "auto"
            ),
            "network": "none",
            "workspace_access": "none",
            "limits": {"timeout_ms": int(runtime_cfg.get("timeout_ms") or 5000)},
        }

    def _ensure_runtime_root(self) -> Path:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        return self.runtime_root

    @staticmethod
    def _load_finance_requests(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        requests: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in payload:
            if isinstance(item, str):
                request = item.strip()
                bindings: Dict[str, Any] = {}
            elif isinstance(item, Mapping):
                request = _trim(item.get("request"))
                raw_bindings = item.get("bindings")
                bindings = dict(raw_bindings) if isinstance(raw_bindings, Mapping) else {}
            else:
                continue
            if not request:
                continue
            key = CustomToolRuntimeService._finance_request_key(
                request=request,
                bindings=bindings,
            )
            if key in seen:
                continue
            seen.add(key)
            requests.append({"key": key, "request": request, "bindings": bindings})
        return requests

    @staticmethod
    def _finance_request_key(*, request: str, bindings: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            {"request": str(request or "").strip(), "bindings": dict(bindings)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _load_execution_logs(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        logs: List[Dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        for line in lines[:50]:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict) or item.get("level") not in {"info", "debug"}:
                continue
            logs.append({
                "level": str(item.get("level")),
                "message": _trim(item.get("message"))[:160],
                "data": dict(item.get("data") or {}) if isinstance(item.get("data"), Mapping) else {},
            })
        return logs

    @classmethod
    def _finance_request_allowed(cls, request: str, bundle: Mapping[str, Any]) -> tuple[bool, str]:
        """Allow system Finance APIs; Design does not act as a runtime allowlist."""
        try:
            from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
            from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call

            api_call = parse_api_call(request)
            api_name = _trim(api_call.api)
            if api_name and resolve_api(api_name):
                return True, ""
        except Exception:
            return False, "finance query could not be parsed"
        return False, f"finance API {api_name or '-'} is not available in the system API catalog"

    def _wrap_code(self, code: str, *, tool_name: str) -> str:
        code_body = self._strip_future_imports(code)
        fixture_json = json.dumps(self.finance_query_fixture, ensure_ascii=False) if self.finance_query_fixture is not None else ""
        fixture_branch = (
            f"_raw = json.loads({fixture_json!r})"
            if fixture_json
            else """request_text = str(request or \"\").strip()
    binding_values = dict(bindings or {}) if isinstance(bindings, dict) else {}
    request_key = hashlib.sha256(
        json.dumps(
            {\"request\": request_text, \"bindings\": binding_values},
            ensure_ascii=False,
            sort_keys=True,
            separators=(\",\", \":\"),
        ).encode(\"utf-8\")
    ).hexdigest()
    try:
        with open(os.path.join(os.environ[\"CODE_INPUT_DIR\"], \"finance_responses.json\"), \"r\", encoding=\"utf-8\") as cache_file:
            response_cache = json.load(cache_file)
    except Exception:
        response_cache = {}
    if isinstance(response_cache, dict) and request_key in response_cache:
        _raw = response_cache[request_key]
    else:
        request_path = os.path.join(os.environ[\"CODE_OUTPUT_DIR\"], \"finance_requests.json\")
        try:
            with open(request_path, \"r\", encoding=\"utf-8\") as request_file:
                requests = json.load(request_file)
        except Exception:
            requests = []
        if not isinstance(requests, list):
            requests = []
        request_record = {\"request\": request_text, \"bindings\": binding_values}
        if request_text and request_record not in requests:
            requests.append(request_record)
        with open(request_path, \"w\", encoding=\"utf-8\") as request_file:
            json.dump(requests, request_file, ensure_ascii=False)
        raise _FinanceQueryPending(\"finance query pending host execution\")"""
        )
        return f'''from __future__ import annotations

import json
import hashlib
import os
import types
import sys


class _FinanceQueryPending(RuntimeError):
    pass


def _normalize_finance_query_result(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {{"ok": False, "error": "finance query returned an invalid envelope", "data": [], "rows": [], "columns": []}}
    normalized = dict(raw)
    validation = raw.get("validation") if isinstance(raw.get("validation"), dict) else {{}}
    result = raw.get("result") if isinstance(raw.get("result"), dict) else {{}}
    provider_data = result.get("data")
    if isinstance(provider_data, list):
        rows = provider_data
    elif isinstance(provider_data, dict) and isinstance(provider_data.get("rows"), list):
        rows = provider_data.get("rows") or []
    elif isinstance(raw.get("rows"), list):
        rows = raw.get("rows") or []
    elif isinstance(raw.get("data"), list):
        rows = raw.get("data") or []
    else:
        rows = []
    errors = validation.get("errors") if isinstance(validation.get("errors"), list) else []
    explicit_error = str(raw.get("error") or "").strip()
    ok = raw.get("ok") is not False and validation.get("ok") is not False and not explicit_error
    normalized.update({{
        "ok": bool(ok),
        "error": explicit_error or ("; ".join(str(item) for item in errors) if errors else ""),
        "data": rows,
        "rows": rows,
        "columns": list(result.get("columns") or raw.get("columns") or []),
    }})
    return normalized


def finance_query(request: str, bindings: dict | None = None) -> dict:
    {fixture_branch}
    return _normalize_finance_query_result(_raw)


def web_search(query: str, limit: int = 5) -> dict:
    return {{
        "ok": False,
        "error": "web_search provider is not configured for custom_tool_sdk",
        "query": str(query or ""),
        "limit": int(limit or 5),
        "items": [],
    }}


_execution_log_count = 0


def _execution_log(level: str, message: str, data: object = None) -> None:
    global _execution_log_count
    if _execution_log_count >= 50:
        return
    normalized_data = data if isinstance(data, dict) else ({{"value": data}} if data is not None else {{}})
    try:
        normalized_data = json.loads(json.dumps(normalized_data, ensure_ascii=False, default=str))
    except Exception:
        normalized_data = {{"value": str(normalized_data)}}
    record = {{
        "level": level,
        "message": str(message or "")[:160],
        "data": normalized_data,
    }}
    line = json.dumps(record, ensure_ascii=False)
    if len(line) > 8000:
        record["data"] = {{"summary": str(normalized_data)[:6000], "truncated": True}}
        line = json.dumps(record, ensure_ascii=False)
    with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "execution_logs.jsonl"), "a", encoding="utf-8") as log_file:
        log_file.write(line + "\\n")
    _execution_log_count += 1


def info(message: str, data: object = None) -> None:
    _execution_log("info", message, data)


def debug(message: str, data: object = None) -> None:
    _execution_log("debug", message, data)


_sdk = types.ModuleType("custom_tool_sdk")
_sdk.finance_query = finance_query
_sdk.web_search = web_search
_sdk.info = info
_sdk.debug = debug
sys.modules["custom_tool_sdk"] = _sdk

_custom_tool_namespace = {{
    "__name__": "__custom_tool__",
    "info": info,
    "debug": debug,
}}
exec(compile({code_body!r}, "<custom-tool>", "exec"), _custom_tool_namespace)
_custom_tool_entrypoint = _custom_tool_namespace.get("run")
if not callable(_custom_tool_entrypoint):
    raise TypeError("custom tool entrypoint run(inputs) is missing or not callable")

with open(os.environ["CODE_INPUT_JSON"], "r", encoding="utf-8") as _runtime_input_file:
    _runtime_inputs = json.load(_runtime_input_file)
try:
    _runtime_output = _custom_tool_entrypoint(_runtime_inputs)
except _FinanceQueryPending:
    _runtime_output = None
if _runtime_output is not None:
    with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "output.json"), "w", encoding="utf-8") as _runtime_output_file:
        json.dump(_runtime_output, _runtime_output_file, ensure_ascii=False)
'''

    @staticmethod
    def _strip_future_imports(code: str) -> str:
        lines = []
        for line in str(code or "").splitlines():
            if line.strip().startswith("from __future__ import "):
                continue
            lines.append(line)
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _error(tool_name: str, kind: str, message: str, *, diagnostics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "tool": tool_name,
            "ok": False,
            "data": {},
            "error": message or kind,
            "meta": {"custom_tool": True, "failure_kind": kind, "diagnostics": diagnostics or {}},
        }


class CustomToolAgentService:
    def __init__(
        self,
        *,
        store: Optional[CustomToolStoreService] = None,
        designer: Optional[CustomToolDesigner] = None,
        coder: Optional[Any] = None,
        edit_planner: Optional[Any] = None,
        edit_coder: Optional[Any] = None,
        tester: Optional[Any] = None,
        runtime: Optional[CustomToolRuntimeService] = None,
        design_protocol: Optional[CustomToolDesignProtocolService] = None,
        use_codex: Optional[bool] = None,
        agent_provider: str = "",
        design_provider: str = "",
        coding_provider: str = "",
        design_complexity: str = "",
        coding_complexity: str = "",
        edit_plan_complexity: str = "",
        edit_coding_complexity: str = "",
        finance_cc_service: Optional[Any] = None,
    ) -> None:
        self.store = store or CustomToolStoreService()
        enabled_setting = os.environ.get("CUSTOM_TOOL_AGENT_ENABLED")
        if enabled_setting is None:
            enabled_setting = os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX", "1")
        agent_enabled = bool(use_codex) if use_codex is not None else _trim(enabled_setting) not in {"0", "false", "False", "no"}
        explicit_provider = _trim(agent_provider).lower()
        legacy_provider = _trim(os.environ.get("CUSTOM_TOOL_AGENT_PROVIDER")).lower()
        legacy_complexity = _trim(os.environ.get("CUSTOM_TOOL_AGENT_COMPLEXITY")).lower()
        self.design_provider = _trim(
            design_provider or explicit_provider or os.environ.get("CUSTOM_TOOL_DESIGN_PROVIDER") or legacy_provider or "claude"
        ).lower()
        self.coding_provider = _trim(
            coding_provider or explicit_provider or os.environ.get("CUSTOM_TOOL_CODING_PROVIDER") or legacy_provider or "codex"
        ).lower()
        self.design_complexity = _trim(
            design_complexity or os.environ.get("CUSTOM_TOOL_DESIGN_COMPLEXITY") or legacy_complexity or "fast"
        ).lower()
        self.coding_complexity = _trim(
            coding_complexity or os.environ.get("CUSTOM_TOOL_CODING_COMPLEXITY") or legacy_complexity or "mid"
        ).lower()
        self.edit_plan_complexity = _trim(
            edit_plan_complexity
            or os.environ.get("CUSTOM_TOOL_EDIT_PLAN_COMPLEXITY")
            or "fastest"
        ).lower()
        self.edit_coding_complexity = _trim(
            edit_coding_complexity
            or os.environ.get("CUSTOM_TOOL_EDIT_CODING_COMPLEXITY")
            or "fastest"
        ).lower()
        self.agent_provider = explicit_provider or legacy_provider or self.design_provider
        self.designer = designer or (self._default_agent_designer() if agent_enabled else CustomToolDesigner())
        self.coder = coder or (self._default_agent_coder() if agent_enabled else None)
        self.edit_planner = edit_planner or (
            self._default_agent_edit_planner() if agent_enabled else None
        )
        self.edit_coder = edit_coder or (
            self._default_agent_edit_coder() if agent_enabled else self.coder
        )
        self.tester = tester or (self._default_agent_tester() if agent_enabled else None)
        self.runtime = runtime or CustomToolRuntimeService(store=self.store)
        self.design_protocol = design_protocol or CustomToolDesignProtocolService()
        self.finance_cc_service = finance_cc_service

    @property
    def finance_cc_enabled(self) -> bool:
        return (
            self.finance_cc_service is not None
            and _trim(os.environ.get("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED")).lower() in {"1", "true", "yes", "on"}
        )

    def set_finance_cc_service(self, service: Any) -> None:
        self.finance_cc_service = service

    @staticmethod
    def finance_cc_runtime_context(
        *,
        state: Optional[Mapping[str, Any]] = None,
        ui_action: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        current_state = dict(state or {})
        return {
            "selected_agent": "investment_analyst",
            "turn_mode": "tool_development",
            "entry": "custom_tool_flow",
            "custom_tool_flow_id": _trim(current_state.get("custom_tool_flow_id")),
            "has_custom_tool_state": any(
                key != "custom_tool_flow_id" for key in current_state
            ),
            "custom_tool_state": current_state,
            "custom_tool_name": _trim(current_state.get("tool_name")),
            "ui_action": dict(ui_action or {}),
        }

    @staticmethod
    def _with_custom_tool_flow_id(
        state: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        next_state = dict(state or {})
        if not _trim(next_state.get("custom_tool_flow_id")):
            next_state["custom_tool_flow_id"] = uuid.uuid4().hex
        return next_state

    @staticmethod
    def _clean_state_for_context(state: Mapping[str, Any] | None) -> Dict[str, Any]:
        source = dict(state or {})
        for key in ("status", "events", "coding_events", "raw", "last_message", "raw_stdout", "raw_stderr"):
            source.pop(key, None)
        return source

    def _default_agent_harness(self, provider: str, complexity: str) -> Any:
        from src.services.agent_providers import AgentCapabilityPolicy

        runner = _trim(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX_RUNNER") or "sdk")
        if provider == "codex" and runner == "exec":
            return None
        harness = build_agent_skill_harness(
            provider,
            cwd=".",
            complexity=complexity,
            capabilities=AgentCapabilityPolicy(),
        )
        return harness if provider != "codex" or harness.available() else None

    def _default_agent_designer(self) -> CodexCustomToolDesigner:
        harness = self._default_agent_harness(self.design_provider, self.design_complexity)
        return CodexCustomToolDesigner(harness=harness) if harness is not None else CodexCustomToolDesigner()

    def _default_agent_coder(self) -> CodexCustomToolCoder:
        harness = self._default_agent_harness(self.coding_provider, self.coding_complexity)
        return CodexCustomToolCoder(harness=harness) if harness is not None else CodexCustomToolCoder()

    def _default_agent_edit_planner(self) -> CodexCustomToolEditPlanner:
        harness = self._default_agent_harness(
            self.design_provider,
            self.edit_plan_complexity,
        )
        return (
            CodexCustomToolEditPlanner(harness=harness)
            if harness is not None
            else CodexCustomToolEditPlanner()
        )

    def _default_agent_edit_coder(self) -> CodexCustomToolEditCoder:
        harness = self._default_agent_harness(
            self.coding_provider,
            self.edit_coding_complexity,
        )
        return (
            CodexCustomToolEditCoder(harness=harness)
            if harness is not None
            else CodexCustomToolEditCoder()
        )

    def _default_agent_tester(self) -> CodexCustomToolTester:
        harness = self._default_agent_harness(self.design_provider, self.design_complexity)
        return CodexCustomToolTester(harness=harness) if harness is not None else CodexCustomToolTester()

    # Backward-compatible private helpers for existing tests and integrations.
    def _default_codex_harness(self) -> Any:
        return self._default_agent_harness(self.coding_provider, self.coding_complexity)

    def _default_codex_designer(self) -> CodexCustomToolDesigner:
        return self._default_agent_designer()

    def _default_codex_coder(self) -> CodexCustomToolCoder:
        return self._default_agent_coder()

    def start_create(
        self,
        requirement_text: str,
        *,
        owner_id: str = "",
        state: Optional[Mapping[str, Any]] = None,
        selected_skills: Optional[List[str]] = None,
        turn_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Enter the same LLM-planned tool workflow used by every later turn."""
        initial_state = self._with_custom_tool_flow_id(state)
        if selected_skills is None:
            return self.handle_turn(
                requirement_text,
                state=initial_state,
                owner_id=owner_id,
                turn_id=turn_id,
                thread_id=thread_id,
                event_sink=event_sink,
            )
        return self._run_design_skills(
            requirement_text,
            owner_id=owner_id,
            state=initial_state,
            selected_skills=selected_skills,
            turn_id=turn_id,
            event_sink=event_sink,
        )

    def start_edit(
        self,
        tool_name: str,
        requirement_text: str,
        *,
        owner_id: str = "",
        owner_ids: Optional[List[str]] = None,
        turn_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Plan one edit against the active tool, then build a reviewable candidate.

        Clear local edits skip the full Design loop.  Contract or broad strategy
        changes keep the existing Design-first workflow.  Neither path changes
        the active revision before the user confirms the candidate.
        """
        name = self.store.normalize_tool_name(tool_name)
        requirement = _trim(requirement_text)
        if not name:
            raise CustomToolError("请选择要修改的个人工具。")
        if not requirement:
            raise CustomToolError("请选择工具后说明本轮修改要求。")
        allowed_owner_ids = list(dict.fromkeys(
            _trim(item)
            for item in (owner_ids if owner_ids is not None else [owner_id])
            if _trim(item)
        ))
        bundle = self.store.load_for_runtime(
            name,
            owner_ids=allowed_owner_ids or None,
            allow_inactive=True,
        )
        manifest = bundle.get("manifest") if isinstance(bundle.get("manifest"), Mapping) else {}
        artifact_owner = _trim(manifest.get("owner_id"))
        if allowed_owner_ids and artifact_owner not in set(allowed_owner_ids):
            raise CustomToolError("只能修改当前账号创建的个人工具。")

        design_contract = (
            dict(bundle.get("design_contract") or {})
            if isinstance(bundle.get("design_contract"), Mapping)
            else {}
        )
        if not design_contract:
            design_contract = {
                "tool_name": name,
                "display_name": _trim(manifest.get("display_name")) or name,
                "goal": _trim(manifest.get("description")),
                "input_schema": dict(bundle.get("input_schema") or {}),
                "output_schema": dict(bundle.get("output_schema") or {}),
                "modules": [
                    dict(item)
                    for item in bundle.get("modules") or []
                    if isinstance(item, Mapping)
                ],
            }
        design_contract.setdefault("tool_name", name)
        existing_requirement = (
            _trim(manifest.get("description"))
            or _trim(design_contract.get("goal"))
            or f"维护个人工具 {name}"
        )
        initial_state = self._with_custom_tool_flow_id({
            "owner_id": owner_id,
            "tool_name": name,
            "requirement_text": existing_requirement,
            "requirement_brief": existing_requirement,
            "design_contract": design_contract,
            "design_round": max(1, int(manifest.get("current_revision") or 1)),
        })
        initial_state.update(self._requirement_artifact_identity_from_state(initial_state))
        initial_state["confirmed_requirement_revision"] = int(
            initial_state.get("requirement_revision") or 1
        )
        initial_state.update(
            self._design_artifact_identity(design_contract, state=initial_state)
        )
        edit_target = self._edit_target_from_bundle(bundle, owner_id=artifact_owner)
        initial_state["edit_target"] = edit_target
        if self.edit_planner is None:
            return self.start_create(
                requirement,
                owner_id=owner_id,
                state=initial_state,
                turn_id=turn_id,
                thread_id=thread_id,
                event_sink=event_sink,
            )

        plan_result = self.edit_planner.plan(
            requirement,
            manifest=manifest,
            design=design_contract,
            schema={
                "input": dict(bundle.get("input_schema") or {}),
                "output": dict(bundle.get("output_schema") or {}),
            },
            context={
                "active_revision": edit_target["base_revision"],
                "active_code_hash": edit_target["base_code_hash"],
            },
            run_id=_trim(initial_state.get("custom_tool_flow_id")),
            event_sink=event_sink,
        )
        plan_events = [
            dict(item)
            for item in plan_result.get("events") or []
            if isinstance(item, Mapping)
        ]
        if not plan_result.get("ok"):
            error_value = plan_result.get("error")
            error_summary = (
                _trim(error_value.get("summary"))
                if isinstance(error_value, Mapping)
                else _trim(error_value)
            ) or "本轮没有形成可靠的修改范围。"
            retry_state = self._clean_state_for_context(initial_state)
            return {
                "message": f"修改范围分析未完成：{error_summary} 当前已启用版本没有变化。",
                "error": error_summary,
                "events": plan_events,
                "state": retry_state,
                "thread_context_patch": {"custom_tool_state": retry_state},
            }

        edit_plan = self._compact_edit_plan(plan_result)
        planned_state = {
            **initial_state,
            "requirement_text": requirement,
            "requirement_brief": requirement,
            "edit_plan": edit_plan,
        }
        if edit_plan["route"] == "full_revision":
            result = self.start_create(
                requirement,
                owner_id=owner_id,
                state=planned_state,
                turn_id=turn_id,
                thread_id=thread_id,
                event_sink=event_sink,
            )
            result.setdefault("events", [])
            result["events"] = plan_events + [
                dict(item)
                for item in result.get("events") or []
                if isinstance(item, Mapping)
            ]
            result["edit_plan"] = edit_plan
            return result

        patched_design = CodexCustomToolEditPlanner.apply_design_replacements(
            design_contract,
            edit_plan.get("design_replacements") or [],
        )
        if not isinstance(patched_design, Mapping):
            raise CustomToolError("local EditPlan did not preserve the Design object")
        planned_state["design_contract"] = dict(patched_design)
        planned_state.update(
            self._design_artifact_identity(
                planned_state["design_contract"],
                state=planned_state,
            )
        )
        if "implementation" in set(edit_plan.get("affected_assets") or []):
            result = self._confirm_and_code(
                state=planned_state,
                owner_id=owner_id,
                event_sink=event_sink,
            )
            result["events"] = plan_events + [
                dict(item)
                for item in result.get("events") or []
                if isinstance(item, Mapping)
            ]
            return result
        return self._save_noncode_edit_candidate(
            active_bundle=bundle,
            state=planned_state,
            owner_id=owner_id,
            events=plan_events,
        )

    @staticmethod
    def _stable_payload_hash(value: Any) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _edit_target_from_bundle(
        self,
        bundle: Mapping[str, Any],
        *,
        owner_id: str,
    ) -> Dict[str, Any]:
        manifest = bundle.get("manifest") if isinstance(bundle.get("manifest"), Mapping) else {}
        code = _trim(bundle.get("code"))
        return {
            "tool_name": self.store.normalize_tool_name(manifest.get("tool_name")),
            "owner_id": _trim(owner_id),
            "base_revision": int(manifest.get("current_revision") or 0),
            "base_code_hash": _trim(manifest.get("code_hash"))
            or hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "base_design_hash": self._stable_payload_hash(
                dict(bundle.get("design_contract") or {})
            ),
            "base_contract_hash": self._stable_payload_hash({
                "input": dict(bundle.get("input_schema") or {}),
                "output": dict(bundle.get("output_schema") or {}),
            }),
        }

    @staticmethod
    def _compact_edit_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "route": _trim(plan.get("route")),
            "affected_assets": [
                _trim(item)
                for item in plan.get("affected_assets") or []
                if _trim(item)
            ],
            "impact_summary": _trim(plan.get("impact_summary")),
            "metadata_patch": {
                key: (_trim(value) if value is not None else None)
                for key, value in dict(plan.get("metadata_patch") or {}).items()
                if key in {"display_name", "description"}
            },
            "design_replacements": [
                {
                    "before": str(item.get("before") or ""),
                    "after": str(item.get("after") or ""),
                    "reason": _trim(item.get("reason")),
                }
                for item in plan.get("design_replacements") or []
                if isinstance(item, Mapping)
            ],
            "implementation_instruction": _trim(
                plan.get("implementation_instruction")
            ),
            **(
                {"fallback_reason": _trim(plan.get("fallback_reason"))}
                if _trim(plan.get("fallback_reason"))
                else {}
            ),
        }

    def _run_design_skills(
        self,
        requirement_text: str,
        *,
        owner_id: str = "",
        state: Optional[Mapping[str, Any]] = None,
        selected_skills: List[str],
        turn_id: Optional[int] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        requirement = _trim(requirement_text)
        if not requirement:
            raise CustomToolError("创建工具时请先描述目标、输入、输出或核心规则。")
        prior_state = self._with_custom_tool_flow_id(state)
        has_prior_business_state = any(
            key != "custom_tool_flow_id" for key in prior_state
        )
        design_round = max(1, int(prior_state.get("design_round") or 0) + 1)
        feedback_ledger = self.design_protocol.append_feedback(
            prior_state.get("feedback_ledger"),
            text=requirement,
            design_round=design_round,
            turn_id=turn_id,
            kind="initial_requirement" if not has_prior_business_state else "feedback",
        )
        design_result = self._call_designer(
            requirement,
            state=prior_state,
            owner_id=owner_id,
            selected_skills=selected_skills,
            turn_id=turn_id,
            event_sink=event_sink,
        )
        if not design_result.get("ok"):
            return {
                "message": _trim(design_result.get("message")) or "Design 调用失败，当前设计和实现均未改变。",
                "design_status": "design_failed",
                "error": design_result.get("error") or "design execution failed",
                "events": design_result.get("events") or [],
                "state": prior_state,
                "thread_context_patch": {"custom_tool_state": prior_state},
            }
        if isinstance(design_result.get("design"), Mapping) and design_result.get("design", {}).get("code"):
            return self._return_legacy_design(requirement, owner_id=owner_id, design=design_result["design"])
        understanding = design_result.get("understanding") if isinstance(design_result.get("understanding"), Mapping) else {}
        existing_analysis = design_result.get("existing_analysis") if isinstance(design_result.get("existing_analysis"), Mapping) else {}
        questions = design_result.get("questions") if isinstance(design_result.get("questions"), list) else []
        design = design_result.get("design") if isinstance(design_result.get("design"), Mapping) else {}
        design_ready = bool(design)
        design_artifact = self._design_artifact_identity(design, state=prior_state) if design_ready else {}
        canonical_requirement = _trim(prior_state.get("requirement_text")) if prior_state else requirement
        if not canonical_requirement:
            canonical_requirement = requirement
        design_context = {
            "round": design_round,
            "is_first_round": not has_prior_business_state,
        }
        if not design_ready:
            narration = compose_design_narrative(understanding, questions, design)
            next_state = {
                "custom_tool_flow_id": prior_state["custom_tool_flow_id"],
                "requirement_text": canonical_requirement,
                "latest_feedback_text": requirement,
                "feedback_ledger": feedback_ledger,
                "owner_id": owner_id,
                "design_round": design_round,
                "understanding": dict(understanding),
                "questions": questions,
                "existing_analysis": dict(existing_analysis),
                **design_artifact,
            }
            return {
                "message": design_result.get("message") or narration,
                "design_status": "clarification",
                "understanding": dict(understanding),
                "questions": questions or design_result.get("missing") or [],
                "design": design,
                "design_artifact": design_artifact,
                "design_context": design_context,
                "existing_analysis": dict(existing_analysis),
                "events": design_result.get("events") or [],
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        next_state = {
            "custom_tool_flow_id": prior_state["custom_tool_flow_id"],
            "requirement_text": canonical_requirement,
            "latest_feedback_text": requirement,
            "feedback_ledger": feedback_ledger,
            "owner_id": owner_id,
            "design_round": design_round,
            "design_contract": design,
            "understanding": dict(understanding),
            "existing_analysis": dict(existing_analysis),
            "questions": questions,
            **design_artifact,
        }
        message = design_result.get("message") or compose_design_narrative(understanding, questions, design)
        return {
            "message": message,
            "design_status": "review",
            "understanding": dict(understanding),
            "state": next_state,
            "design": design,
            "design_artifact": design_artifact,
            "design_context": design_context,
            "existing_analysis": dict(existing_analysis),
            "events": design_result.get("events") or [],
            "thread_context_patch": {"custom_tool_state": next_state},
        }

    def handle_turn(
        self,
        text: str,
        *,
        state: Mapping[str, Any],
        ui_action: Optional[Mapping[str, Any]] = None,
        owner_id: str = "",
        turn_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Plan and execute one natural-language turn in the financial-tool domain."""
        raw = _trim(text)
        current_state = self._with_custom_tool_flow_id(
            self._clean_state_for_context(state)
        )
        if not raw:
            return {"message": "请说明本轮希望查看或调整的内容。", "state": current_state}
        if not self.finance_cc_enabled:
            return {
                "message": "Finance CC 当前不可用，已有需求、设计和实现均未改变。",
                "state": current_state,
                "error": "Finance CC controller is unavailable",
                "thread_context_patch": {"custom_tool_state": current_state},
            }
        return self._handle_finance_cc_turn(
            raw,
            state=current_state,
            ui_action=ui_action,
            owner_id=owner_id,
            thread_id=thread_id,
            turn_id=turn_id,
            event_sink=event_sink,
        )

    def _handle_finance_cc_turn(
        self,
        text: str,
        *,
        state: Mapping[str, Any],
        ui_action: Optional[Mapping[str, Any]],
        owner_id: str,
        thread_id: Optional[int],
        turn_id: Optional[int],
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Dict[str, Any]:
        """Adapt one Finance CC result into the existing conversation contract."""
        current_state = self._with_custom_tool_flow_id(state)
        current_state.update(
            self._requirement_artifact_identity_from_state(current_state)
        )
        needs_initial_requirement_asset = (
            int(current_state.get("requirement_revision") or 0) < 1
            and not (
                isinstance(current_state.get("design_contract"), Mapping)
                and bool(current_state.get("design_contract"))
            )
            and not _trim(current_state.get("tool_name"))
        )
        action_id = _trim((ui_action or {}).get("action_id"))
        requirement_confirmation_submitted = (
            action_id == "custom_tool.submit_clarification"
        )
        if requirement_confirmation_submitted:
            requirement_revision = int(
                current_state.get("requirement_revision") or 0
            )
            if requirement_revision < 1:
                raise CustomToolError(
                    "current requirement does not have a reviewable revision"
                )
            self._validate_expected_revision(
                expected_revision=(ui_action or {}).get("expected_revision"),
                current_revision=requirement_revision,
                artifact_name="requirement",
            )
            current_state["confirmed_requirement_revision"] = (
                requirement_revision
            )
        context = self.finance_cc_runtime_context(
            state=current_state,
            ui_action=ui_action,
        )
        is_retry = action_id == "custom_tool.retry_design"
        effective_text = (
            _trim(current_state.get("latest_feedback_text"))
            or _trim(current_state.get("requirement_text"))
            or text
            if is_retry
            else text
        )
        progress_event_factory = getattr(
            self.finance_cc_service,
            "initial_progress_event",
            None,
        )
        if callable(progress_event_factory) and callable(event_sink):
            event_sink(progress_event_factory(context))
            # The orchestration layer owns the first user-visible event because
            # it can be emitted before session locking/client acquisition. Tell
            # the CC runtime not to publish the same progress node again.
            context["_initial_progress_emitted"] = True
        cc_result = self.finance_cc_service.run_turn(
            thread_id=thread_id or 0,
            turn_id=turn_id or "",
            owner_id=owner_id,
            user_text=effective_text,
            context=context,
            event_sink=event_sink,
        )
        next_state = dict(current_state)
        feedback_ledger = (
            [
                dict(item)
                for item in next_state.get("feedback_ledger") or []
                if isinstance(item, Mapping)
            ]
            if is_retry
            else self.design_protocol.append_feedback(
                next_state.get("feedback_ledger"),
                text=text,
                design_round=max(1, int(next_state.get("design_round") or 0) + 1),
                turn_id=turn_id,
                kind=(
                    "initial_requirement"
                    if not next_state.get("feedback_ledger")
                    and not _trim(next_state.get("requirement_text"))
                    else "feedback"
                ),
            )
        )
        next_state["feedback_ledger"] = feedback_ledger
        next_state["owner_id"] = owner_id or _trim(next_state.get("owner_id"))
        next_state.setdefault("requirement_text", effective_text)

        design_status = ""
        requirement_updated = False
        design_updated = False
        notice: List[str] = []
        questions: List[Dict[str, Any]] = []
        test_evidence: Dict[str, Any] = {}
        artifact_updates = [
            dict(update)
            for update in cc_result.get("artifact_updates") or []
            if isinstance(update, Mapping)
        ]
        accepted_artifact_types: List[str] = []
        blocked_design_artifact = False
        incomplete_design_artifact = False

        # Requirement is the authoritative input to every later artifact.
        # Apply it first so tool-call ordering cannot let a design bypass the
        # confirmation boundary.
        for update in artifact_updates:
            artifact_type = _trim(update.get("artifact_type"))
            if artifact_type != "requirement":
                continue
            payload = (
                update.get("payload")
                if isinstance(update.get("payload"), Mapping)
                else {}
            )
            requirement_updated = True
            brief = _trim(payload.get("requirement_brief"))
            if brief:
                previous_fingerprint = _trim(
                    next_state.get("requirement_fingerprint")
                )
                requirement_identity = self._requirement_artifact_identity(
                    brief,
                    state=next_state,
                )
                next_state["requirement_brief"] = brief
                next_state.update(requirement_identity)
                next_state.pop("understanding", None)
                if (
                    previous_fingerprint
                    and previous_fingerprint
                    != requirement_identity["requirement_fingerprint"]
                ):
                    for key in (
                        "design_contract",
                        "design_artifact_id",
                        "design_revision",
                        "design_fingerprint",
                        "tool_name",
                    ):
                        next_state.pop(key, None)
            notice = [
                _trim(item)
                for item in payload.get("notice") or []
                if _trim(item)
            ]
            questions = [
                dict(item)
                for item in payload.get("questions") or []
                if isinstance(item, Mapping)
            ]
            if questions:
                next_state["notice"] = notice
                next_state["questions"] = questions
            else:
                # The final requirement brief is the only design input.
                # Interaction details remain in the persisted turn, not the next stage state.
                next_state.pop("notice", None)
                next_state.pop("questions", None)
            design_status = "clarification"
            accepted_artifact_types.append("requirement")

        # The first provider turn may return only narrative/interaction text or
        # incorrectly jump straight to design. Preserve the user's own wording
        # as the reviewable requirement asset so every new flow has a concrete
        # confirmation surface without inventing any business semantics.
        if (
            needs_initial_requirement_asset
            and int(next_state.get("requirement_revision") or 0) < 1
        ):
            fallback_brief = (
                _trim(next_state.get("requirement_text"))
                or _trim(effective_text)
            )
            if fallback_brief:
                next_state["requirement_brief"] = fallback_brief
                next_state.update(
                    self._requirement_artifact_identity(
                        fallback_brief,
                        state=next_state,
                    )
                )
                requirement_updated = True
                design_status = "clarification"

        # Submitting the trusted requirement surface confirms the semantic
        # asset represented by the submitted brief plus the user's answers.
        # If CC canonicalizes those answers into a new brief in this same turn,
        # that resulting revision is the one the action confirms.
        if (
            requirement_confirmation_submitted
            and int(next_state.get("requirement_revision") or 0) > 0
        ):
            next_state["confirmed_requirement_revision"] = int(
                next_state.get("requirement_revision") or 0
            )

        requirement_revision = int(
            next_state.get("requirement_revision") or 0
        )
        requirement_confirmed = (
            requirement_revision > 0
            and int(next_state.get("confirmed_requirement_revision") or 0)
            == requirement_revision
        )
        if requirement_revision > 0 and not requirement_confirmed:
            for key in (
                "design_contract",
                "design_artifact_id",
                "design_revision",
                "design_fingerprint",
                "tool_name",
            ):
                next_state.pop(key, None)

        pending_design: Optional[Dict[str, Any]] = None
        pending_flow = ""
        design_received = False
        flow_received = False
        for update in artifact_updates:
            if not isinstance(update, Mapping):
                continue
            artifact_type = _trim(update.get("artifact_type"))
            payload = update.get("payload") if isinstance(update.get("payload"), Mapping) else {}
            if artifact_type == "requirement":
                continue
            if artifact_type in {"design", "flow"} and not requirement_confirmed:
                blocked_design_artifact = True
                design_status = "clarification"
                continue
            if artifact_type == "design":
                design_value = payload.get("design")
                candidate_design = (
                    {"document": _trim(design_value)}
                    if isinstance(design_value, str) and _trim(design_value)
                    else dict(design_value)
                    if isinstance(design_value, Mapping)
                    else {}
                )
                if candidate_design:
                    finance_tool_profile = payload.get("finance_tool_profile")
                    if (
                        isinstance(finance_tool_profile, Mapping)
                        and finance_tool_profile
                    ):
                        candidate_design["finance_tool_profile"] = dict(
                            finance_tool_profile
                        )
                    # Design text and flow are produced by separate Skills. A
                    # revised design invalidates the previous diagram until a
                    # new flow artifact is saved in this turn.
                    candidate_design.pop("mermaid", None)
                    pending_design = candidate_design
                    design_received = True
                    accepted_artifact_types.append("design")
            elif artifact_type == "flow":
                mermaid = _trim(payload.get("mermaid"))
                if mermaid:
                    pending_flow = mermaid
                    flow_received = True
                    accepted_artifact_types.append("flow")
            elif artifact_type == "test_evidence":
                test_evidence = dict(payload)
                design_status = "test"
                accepted_artifact_types.append("test_evidence")

        if design_received or flow_received:
            design = (
                dict(pending_design or {})
                if design_received
                else dict(next_state.get("design_contract") or {})
            )
            if pending_flow and design:
                design["mermaid"] = pending_flow
            if design:
                next_state["design_contract"] = design
                next_state["tool_name"] = (
                    _trim(design.get("tool_name"))
                    or _trim(next_state.get("tool_name"))
                )
            if design and _trim(design.get("mermaid")):
                next_state.update(
                    self._design_artifact_identity(design, state=next_state)
                )
                design_updated = True
                design_status = "review"
            elif design:
                incomplete_design_artifact = True
                design_status = "design_draft"

        implementation_runs = [
            dict(item) for item in cc_result.get("implementation_runs") or [] if isinstance(item, Mapping)
        ]
        latest_implementation = implementation_runs[-1] if implementation_runs else {}
        if isinstance(latest_implementation.get("state"), Mapping):
            next_state.update(dict(latest_implementation.get("state") or {}))

        interaction_requests = [
            dict(item) for item in cc_result.get("interaction_requests") or [] if isinstance(item, Mapping)
        ]
        if interaction_requests and not questions:
            questions = [
                dict(item)
                for request in interaction_requests
                for item in request.get("questions") or []
                if isinstance(item, Mapping)
            ]
            next_state["questions"] = questions

        design = dict(next_state.get("design_contract") or {})
        requirement_brief = _trim(next_state.get("requirement_brief"))
        legacy_understanding = (
            dict(next_state.get("understanding") or {})
            if isinstance(next_state.get("understanding"), Mapping)
            else {}
        )
        understanding = (
            {"requirement_brief": requirement_brief}
            if requirement_brief
            else legacy_understanding
        )
        response_message = _trim(cc_result.get("result")) or "本轮处理已完成。"
        if blocked_design_artifact:
            response_message = "我已整理当前需求；请先确认需求，再继续形成设计方案。"
        elif incomplete_design_artifact:
            response_message = (
                "设计正文已经保存，但流程图尚未完成；当前方案不会进入确认或 Coding。"
                "请继续生成并保存流程图。"
            )
        response = {
            "message": response_message,
            "state": next_state,
            "thread_context_patch": {"custom_tool_state": next_state},
            "design_status": design_status or (
                "review"
                if design and _trim(design.get("mermaid"))
                else "design_draft"
                if design
                else "clarification"
            ),
            "understanding": (
                understanding
                if requirement_updated or questions or blocked_design_artifact
                else {}
            ),
            "notice": notice if requirement_updated or questions else [],
            "questions": questions,
            "design": design if design_updated else {},
            "design_artifact": (
                {
                    "design_artifact_id": _trim(next_state.get("design_artifact_id")),
                    "design_revision": int(next_state.get("design_revision") or 0),
                }
                if design_updated
                else {}
            ),
            "requirement_artifact": (
                {
                    "requirement_artifact_id": _trim(
                        next_state.get("requirement_artifact_id")
                    ),
                    "requirement_revision": int(
                        next_state.get("requirement_revision") or 0
                    ),
                }
                if requirement_brief
                else {}
            ),
            "finance_cc": cc_result,
        }
        view_assets = [
            {"type": _trim(item.get("asset_type")), "payload": item.get("payload")}
            for item in cc_result.get("asset_reads") or []
            if isinstance(item, Mapping)
            and _trim(item.get("asset_type")) in {"design", "flow", "code", "tests", "tool_contract"}
            and item.get("payload") is not None
        ]
        if view_assets:
            response["view_assets"] = view_assets
        if test_evidence:
            response["test_evidence"] = test_evidence
        if latest_implementation:
            coding_status = _trim(latest_implementation.get("coding_status"))
            if coding_status:
                response["coding_status"] = coding_status
            response["coding_error"] = dict(latest_implementation.get("coding_error") or {})
            response["test_result"] = dict(latest_implementation.get("test_result") or {})
            response["tool"] = dict(latest_implementation.get("tool") or {})
            response["implementation_meta"] = dict(latest_implementation.get("implementation_meta") or {})
            response["implementation_explanation"] = dict(
                latest_implementation.get("implementation_explanation") or {}
            )
            response["implementation_review"] = dict(
                latest_implementation.get("implementation_review") or {}
            )
            response["coding_tests"] = [
                dict(item)
                for item in latest_implementation.get("coding_tests") or []
                if isinstance(item, Mapping)
            ]
            response["message"] = (
                _trim(latest_implementation.get("message"))
                or "代码实现结果已保存。"
            )
            if response["coding_error"]:
                response["message"] = (
                    _trim(latest_implementation.get("message"))
                    or _trim(response["coding_error"].get("summary"))
                    or "本轮实现未完成，当前设计和工作区已保留。"
                )
        transport_error = _trim(cc_result.get("error"))
        if transport_error:
            error = transport_error
            saved_types = list(dict.fromkeys(accepted_artifact_types))
            if latest_implementation:
                response.setdefault("diagnostic_warning", error)
            elif saved_types:
                response["message"] = (
                    f"本轮已保存 {', '.join(dict.fromkeys(saved_types))}，可以从当前结果继续。"
                )
                response["diagnostic_warning"] = error
            else:
                response["error"] = error
                response["message"] = (
                    "已进入自定义工具创建，但工具设计服务本轮未及时返回。"
                    "你的需求已经保留，已有业务资产未改变，可以直接重试当前阶段。"
                    if "timed out" in error.lower()
                    else
                    "已进入自定义工具创建，但工具设计服务本轮没有完成。"
                    "你的需求已经保留，已有业务资产未改变，可以直接重试当前阶段。"
                )
        return response

    def continue_flow_action(
        self,
        action_id: str,
        *,
        state: Mapping[str, Any],
        expected_revision: Optional[int] = None,
        owner_id: str = "",
        turn_id: Optional[int] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Resolve a trusted UI action without executing model-provided commands."""
        normalized_action = _trim(action_id)
        if normalized_action == "custom_tool.confirm_design":
            current_revision = int(state.get("design_revision") or 0)
            if current_revision < 1 or not isinstance(
                state.get("design_contract"), Mapping
            ):
                raise CustomToolError(
                    "current design does not have a reviewable revision"
                )
            self._validate_expected_revision(
                expected_revision=expected_revision,
                current_revision=current_revision,
                artifact_name="design",
            )
            design_contract = dict(state.get("design_contract") or {})
            if not _trim(design_contract.get("mermaid")):
                raise CustomToolError(
                    "current design is not reviewable because its flow artifact is missing"
                )
            requirement_revision = int(
                state.get("requirement_revision") or 0
            )
            if (
                requirement_revision > 0
                and int(state.get("confirmed_requirement_revision") or 0)
                != requirement_revision
            ):
                raise CustomToolError(
                    "current requirement revision is not confirmed"
                )
            confirmed_state = dict(state)
            confirmed_state["feedback_ledger"] = self.design_protocol.append_feedback(
                state.get("feedback_ledger"),
                text="确认设计并进入 Coding",
                design_round=int(state.get("design_round") or state.get("design_revision") or 1),
                turn_id=turn_id,
                kind="confirmation",
            )
            return self._confirm_and_code(state=confirmed_state, owner_id=owner_id, event_sink=event_sink)
        if normalized_action == "custom_tool.activate_draft":
            tool_name = _trim(state.get("tool_name"))
            if not tool_name:
                raise CustomToolError("draft state does not include tool_name")
            current_revision = int(state.get("implementation_revision") or 0)
            if expected_revision is not None and int(expected_revision) != current_revision:
                raise CustomToolError(
                    f"implementation revision changed: expected {int(expected_revision)}, current {current_revision}"
                )
            edit_target = (
                state.get("edit_target")
                if isinstance(state.get("edit_target"), Mapping)
                else {}
            )
            if edit_target:
                candidate = self.store.load_revision(tool_name, current_revision)
                candidate_test = (candidate.get("manifest") or {}).get("last_test") or {}
                if not isinstance(candidate_test, Mapping) or candidate_test.get("execution_ok") is not True:
                    raise CustomToolError("候选版本尚未通过聚焦验证，不能启用。")
                committed = self.store.activate_revision(
                    tool_name,
                    current_revision,
                    expected_active_revision=int(edit_target.get("base_revision") or 0),
                    owner_id=owner_id or _trim(state.get("owner_id")),
                )
            else:
                committed = self.commit(tool_name, owner_ids=[owner_id] if owner_id else None)
            return {
                "message": f"{_trim((committed.get('manifest') or {}).get('display_name')) or tool_name} 已确认并启用。",
                "state": {},
                "tool": committed,
                "activation": {
                    "status": "active",
                    "tool_name": tool_name,
                    "implementation_revision": int((committed.get("manifest") or {}).get("current_revision") or 0),
                },
                "thread_context_patch": {"custom_tool_state": None},
            }
        if normalized_action == "custom_tool.retry_coding":
            retry_state = self._clean_state_for_context(state)
            return self._confirm_and_code(state=retry_state, owner_id=owner_id, event_sink=event_sink)
        raise CustomToolError(f"unknown custom tool action: {normalized_action or '-'}")

    def call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        owner_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return self.runtime.run(
            tool_name,
            arguments or {},
            owner_ids=owner_ids,
            allow_inactive=True,
        )

    def commit(self, tool_name: str, *, owner_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        return self.store.commit(tool_name, owner_ids=owner_ids)

    def _call_designer(
        self,
        requirement_text: str,
        *,
        state: Optional[Mapping[str, Any]],
        owner_id: str,
        selected_skills: Optional[List[str]],
        turn_id: Optional[int],
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            "_workspace_identity": {
                "owner_id": owner_id,
                "turn_id": int(turn_id) if turn_id is not None else 0,
            },
        }
        if selected_skills:
            context["selected_skills"] = list(dict.fromkeys(_trim(item) for item in selected_skills if _trim(item)))
        prior = dict(state or {})
        requirement_brief = _trim(prior.get("requirement_brief"))
        if requirement_brief:
            context["requirement_brief"] = requirement_brief
        elif isinstance(prior.get("understanding"), Mapping) and prior.get("understanding"):
            context["requirement_brief"] = dict(prior.get("understanding") or {})
        current_design = (
            prior.get("design_contract")
            if isinstance(prior.get("design_contract"), Mapping)
            else prior.get("partial_design")
            if isinstance(prior.get("partial_design"), Mapping)
            else {}
        )
        if current_design:
            context["current_design"] = dict(current_design)
        try:
            return self.designer.design(requirement_text, context=context, event_sink=event_sink)
        except TypeError:
            return self.designer.design(requirement_text)

    def _call_coder(
        self,
        coder: Any,
        design_contract: Mapping[str, Any],
        *,
        requirement_text: str,
        coding_context: Mapping[str, Any],
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Dict[str, Any]:
        try:
            return coder.code(
                design_contract,
                requirement_text=requirement_text,
                context=dict(coding_context),
                event_sink=event_sink,
            )
        except TypeError:
            return coder.code(
                design_contract,
                requirement_text=requirement_text,
                context=dict(coding_context),
            )

    def _coding_implementation_meta(
        self,
        *,
        coder: Any,
        coding_results: Sequence[Mapping[str, Any]],
        agent_runtime: Mapping[str, Any],
        is_local_edit: bool,
    ) -> Dict[str, Any]:
        raw_results = [
            item.get("raw") if isinstance(item.get("raw"), Mapping) else {}
            for item in coding_results
        ]
        context_bundle: Mapping[str, Any] = {}
        for raw in reversed(raw_results):
            candidate = raw.get("context_bundle")
            if isinstance(candidate, Mapping) and candidate:
                context_bundle = candidate
                break
        return {
            "provider": self.coding_provider,
            "complexity": self.edit_coding_complexity if is_local_edit else self.coding_complexity,
            "model": _trim(getattr(getattr(coder, "harness", None), "model", "")),
            "reasoning_effort": _trim(
                getattr(getattr(coder, "harness", None), "reasoning_effort", "")
            ),
            "session_id": _trim(agent_runtime.get("session_id")),
            "provider_session_id": _trim(agent_runtime.get("provider_session_id")),
            "duration_ms": sum(int(raw.get("duration_ms") or 0) for raw in raw_results),
            "contract_repair_attempted": len(coding_results) > 1,
            "context_bundle": {
                key: context_bundle.get(key)
                for key in (
                    "bundle_id", "owner_scope", "bundle_dir", "api_index", "api_task_context",
                    "api_sources", "api_dependencies", "runtime_contract", "custom_tool_sdk", "coding_guide",
                    "module_template", "coding_workspace",
                )
                if context_bundle.get(key)
            },
        }

    @staticmethod
    def _strategy_contract_repair_feedback(error: str) -> str:
        return (
            "系统在保存前发现本轮公开输出与策略伴随契约不一致："
            f"{_trim(error)}\n"
            "请继续使用当前 Coding 工作区修正实现并重新做聚焦验证。"
            "selection_output_profile 只能映射本轮 tool_contract.outputs 中真实存在的公开输出；"
            "candidate_path 指向完整候选数组，output_date_path 为空或指向一个唯一标量日期。"
            "所有路径只用点分隔字段，禁止 [0] 等数组下标；如果需要核对日期，"
            "即使候选为空，run 也必须返回可解析的顶层日期字段。Host 结果信封前缀由系统统一处理。"
            "完成后只提交修正后的最终结果。"
        )

    @staticmethod
    def _combined_coding_events(
        coding_results: Sequence[Mapping[str, Any]],
    ) -> List[Dict[str, Any]]:
        return [
            dict(event)
            for result in coding_results
            for event in result.get("events") or []
            if isinstance(event, Mapping)
        ]

    def _confirm_and_code(
        self,
        *,
        state: Mapping[str, Any],
        owner_id: str,
        selected_skills: Optional[List[str]] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        actual_owner = owner_id or _trim(state.get("owner_id"))
        edit_plan = state.get("edit_plan") if isinstance(state.get("edit_plan"), Mapping) else {}
        is_local_edit = (
            _trim(edit_plan.get("route")) == "local_patch"
            and isinstance(state.get("edit_target"), Mapping)
        )
        active_coder = self.edit_coder if is_local_edit else self.coder
        legacy_design = state.get("design") if isinstance(state.get("design"), Mapping) else {}
        design_contract = state.get("design_contract") if isinstance(state.get("design_contract"), Mapping) else {}
        profile_source = design_contract or legacy_design
        try:
            FinanceToolProfileService.assert_implementation_allowed(
                profile_source.get("finance_tool_profile")
                if isinstance(profile_source.get("finance_tool_profile"), Mapping)
                else None
            )
        except FinanceToolProfileError:
            next_state = self._clean_state_for_context(state)
            return {
                "message": (
                    "当前方案属于高风险外部动作工具，设计已保留；"
                    "本系统暂不进入 Coding、注册或执行。"
                ),
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        if legacy_design and legacy_design.get("code"):
            return self._save_test_and_return(
                legacy_design,
                owner_id=actual_owner,
                state=state,
            )
        if not design_contract:
            next_state = self._clean_state_for_context(state)
            return {
                "message": "当前设计稿为空，请补充需求后重新生成设计。",
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        if isinstance(state.get("edit_target"), Mapping):
            # Fail before starting a model turn when the active base changed.
            self._assert_edit_base_current(state, owner_id=actual_owner)
        if active_coder is None:
            next_state = self._clean_state_for_context(state)
            return {
                "message": "当前未启用 Agent coding，请先配置 coding runner 或补充可执行代码。",
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        coding_context: Dict[str, Any] = {}
        agent_runtime = (
            dict(state.get("agent_runtime") or {})
            if isinstance(state.get("agent_runtime"), Mapping)
            else {}
        )
        agent_runtime.setdefault("session_id", uuid.uuid4().hex)
        coding_context["_agent_runtime"] = agent_runtime
        coding_feedback = _trim(state.get("coding_feedback"))
        if not coding_feedback and is_local_edit:
            coding_feedback = _trim(edit_plan.get("implementation_instruction"))
        if coding_feedback:
            coding_context["coding_feedback"] = coding_feedback
        test_feedback = state.get("test_feedback")
        if isinstance(test_feedback, Mapping) and test_feedback:
            coding_context["test_feedback"] = dict(test_feedback)
        if selected_skills:
            coding_context["selected_skills"] = list(dict.fromkeys(
                _trim(item) for item in selected_skills if _trim(item)
            ))
        current_tool_name = _trim(state.get("tool_name"))
        if current_tool_name and self.store.exists(current_tool_name):
            current_bundle = self.store.load(current_tool_name)
            coding_context["current_implementation"] = {
                "revision": int((current_bundle.get("manifest") or {}).get("current_revision") or 0),
                "modules": [dict(item) for item in current_bundle.get("modules") or [] if isinstance(item, Mapping)],
                "last_test": dict((current_bundle.get("manifest") or {}).get("last_test") or {}),
            }
        coding_context["_workspace_identity"] = {
            "owner_id": actual_owner,
            "tool_name": current_tool_name or _trim(design_contract.get("tool_name")),
        }
        requirement_text = (
            _trim(state.get("requirement_brief"))
            or _trim(state.get("requirement_text"))
        )
        preserved_edit_revision: Mapping[str, Any] | None = None
        if isinstance(state.get("edit_target"), Mapping):
            edit_plan = (
                state.get("edit_plan")
                if isinstance(state.get("edit_plan"), Mapping)
                else {}
            )
            if _trim(edit_plan.get("route")) == "local_patch":
                preserved_edit_revision = self._assert_edit_base_current(
                    state,
                    owner_id=actual_owner,
                )
        coding_results: List[Dict[str, Any]] = []
        bundle_design: Dict[str, Any] | None = None
        coding_result: Dict[str, Any] = {}
        for attempt in range(2):
            coding_result = self._call_coder(
                active_coder,
                design_contract,
                requirement_text=requirement_text,
                coding_context=coding_context,
                event_sink=event_sink,
            )
            coding_results.append(coding_result)
            agent_runtime = (
                dict(coding_result.get("agent_runtime") or {})
                if isinstance(coding_result.get("agent_runtime"), Mapping)
                else dict(coding_context.get("_agent_runtime") or {})
            )
            coding_context["_agent_runtime"] = agent_runtime
            implementation_meta = self._coding_implementation_meta(
                coder=active_coder,
                coding_results=coding_results,
                agent_runtime=agent_runtime,
                is_local_edit=is_local_edit,
            )
            if not coding_result.get("ok"):
                coding_error = coding_result.get("error") if isinstance(coding_result.get("error"), Mapping) else {}
                next_state = {
                    **self._clean_state_for_context(state),
                    "agent_runtime": agent_runtime,
                    "coding_feedback": coding_result.get("message"),
                    "coding_error": dict(coding_error),
                }
                return {
                    "message": coding_result.get("message") or "本次代码实现没有产生可执行模块。",
                    "coding_status": "coding_failed",
                    "coding_error": dict(coding_error),
                    "events": self._combined_coding_events(coding_results),
                    "implementation_meta": implementation_meta,
                    "state": next_state,
                    "thread_context_patch": {"custom_tool_state": next_state},
                }
            coding_final = coding_result.get("final") if isinstance(coding_result.get("final"), Mapping) else {}
            try:
                bundle_design = self._bundle_from_coding_final(
                    design_contract,
                    coding_final,
                    preserved_revision=preserved_edit_revision,
                )
                break
            except CustomToolStrategyContractError as exc:
                if attempt == 0:
                    feedback = self._strategy_contract_repair_feedback(str(exc))
                    coding_context["coding_feedback"] = feedback
                    repair_event = {
                        "source": "system",
                        "type": "contract_repair",
                        "content": "公开输出与策略运行映射不一致，正在当前 Coding 会话中自动修正并复测。",
                        "metadata": {"stage": "coding", "status": "running"},
                    }
                    coding_result.setdefault("events", []).append(repair_event)
                    if event_sink is not None:
                        try:
                            event_sink(dict(repair_event))
                        except Exception:
                            pass
                    continue
                next_state = {
                    **self._clean_state_for_context(state),
                    "agent_runtime": agent_runtime,
                    "coding_feedback": str(exc),
                    "coding_error": {
                        "code": "strategy_contract_invalid",
                        "summary": str(exc),
                    },
                }
                return {
                    "message": "实现已生成，但自动修正后策略运行契约仍无法安全执行，可以继续重试当前 Coding。",
                    "coding_status": "coding_failed",
                    "coding_error": dict(next_state["coding_error"]),
                    "events": self._combined_coding_events(coding_results),
                    "implementation_meta": implementation_meta,
                    "state": next_state,
                    "thread_context_patch": {"custom_tool_state": next_state},
                }
            except CustomToolFinanceProfileError as exc:
                next_state = {
                    **self._clean_state_for_context(state),
                    "agent_runtime": agent_runtime,
                    "coding_feedback": str(exc),
                    "coding_error": {
                        "code": "finance_tool_profile_invalid",
                        "summary": str(exc),
                    },
                }
                return {
                    "message": (
                        "实现已生成，但金融工具画像与运行契约不一致，"
                        "请在当前 Coding 会话中修正后重试。"
                    ),
                    "coding_status": "coding_failed",
                    "coding_error": dict(next_state["coding_error"]),
                    "events": self._combined_coding_events(coding_results),
                    "implementation_meta": implementation_meta,
                    "state": next_state,
                    "thread_context_patch": {"custom_tool_state": next_state},
                }
            except CustomToolError as exc:
                next_state = {
                    **self._clean_state_for_context(state),
                    "agent_runtime": agent_runtime,
                    "coding_feedback": str(exc),
                    "coding_error": {
                        "code": "coding_bundle_invalid",
                        "summary": str(exc),
                    },
                }
                return {
                    "message": "实现已生成，但无法形成可执行工具包，请在当前 Coding 会话中修正后重试。",
                    "coding_status": "coding_failed",
                    "coding_error": dict(next_state["coding_error"]),
                    "events": self._combined_coding_events(coding_results),
                    "implementation_meta": implementation_meta,
                    "state": next_state,
                    "thread_context_patch": {"custom_tool_state": next_state},
                }
        if bundle_design is None:
            raise CustomToolError("coding did not produce an executable bundle")
        if isinstance(state.get("edit_target"), Mapping):
            bundle_design = self._lock_edit_candidate_bundle(
                bundle_design,
                state=state,
                owner_id=actual_owner,
            )
        bundle_design["requirement_text"] = _trim(state.get("requirement_text"))
        bundle_design["design_provenance"] = {
            "artifact_id": _trim(state.get("design_artifact_id")),
            "revision": int(state.get("design_revision") or 0),
            "fingerprint": _trim(state.get("design_fingerprint")),
        }
        bundle_design["design_feedback_evidence"] = [
            dict(item) for item in state.get("feedback_ledger") or [] if isinstance(item, Mapping)
        ]
        coding_events = self._combined_coding_events(coding_results)
        result = self._save_test_and_return(
            bundle_design,
            owner_id=actual_owner,
            events=coding_events,
            coding_evidence=(
                coding_final.get("coding_test_evidence")
                if isinstance(coding_final.get("coding_test_evidence"), Mapping)
                else self._coding_test_evidence(coding_events)
            ),
            state=state,
        )
        result["implementation_meta"] = implementation_meta
        result["coding_status"] = "implemented"
        result["implementation_review"] = self._implementation_review(coding_final)
        result["implementation_explanation"] = self._implementation_explanation(coding_final)
        result["coding_tests"] = [
            dict(item)
            for item in self._execution_examples(coding_final)
        ]
        # Preserve system-owned assets and feedback; no workflow status gates the next turn.
        next_state = {
            **self._clean_state_for_context(state),
            **dict(result.get("state") or {}),
            "agent_runtime": agent_runtime,
        }
        result["state"] = next_state
        result["thread_context_patch"] = {"custom_tool_state": next_state}
        return result

    def implement_dynamic_tool(
        self,
        *,
        state: Mapping[str, Any],
        owner_id: str,
        instruction: str = "",
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Let Codex implement, validate, and review one dynamic-tool revision."""
        coding_state = self._clean_state_for_context(state)
        if _trim(instruction):
            coding_state["coding_feedback"] = _trim(instruction)
        return self._confirm_and_code(
            state=coding_state,
            owner_id=owner_id,
            event_sink=event_sink,
        )

    def _run_existing_test(
        self,
        *,
        request: str,
        state: Mapping[str, Any],
        owner_id: str,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        tool_name = _trim(state.get("tool_name"))
        if not tool_name or not self.store.exists(tool_name):
            return {
                "message": "当前还没有可运行的工具实现。",
                "state": dict(state),
                "thread_context_patch": {"custom_tool_state": dict(state)},
            }
        from src.services.asset_invocation_service import AssetInvocationError, AssetInvocationService

        bundle = self.store.load(tool_name)
        if self.tester is None:
            return {
                "message": "当前未启用测试 Skill，工具实现未改变。",
                "test_status": "failed",
                "state": dict(state),
                "thread_context_patch": {"custom_tool_state": dict(state)},
            }
        tool_contract = {
            "manifest": dict(bundle.get("manifest") or {}),
            "input_schema": dict(bundle.get("input_schema") or {}),
            "output_schema": dict(bundle.get("output_schema") or {}),
            "sample_input": dict(bundle.get("sample_input") or {}),
        }
        max_test_turns = max(1, int(os.environ.get("CUSTOM_TOOL_TEST_MAX_TURNS") or DEFAULT_MAX_TEST_TURNS))
        invocation_service = AssetInvocationService(custom_tool_store=self.store)
        cases: List[Dict[str, Any]] = []
        invocations: List[Dict[str, Any]] = []
        test_history: List[Dict[str, Any]] = []
        all_events: List[Dict[str, Any]] = []
        presentation: Dict[str, Any] = {}
        assessment = ""
        plan_message = ""
        planning_error = ""
        planning_feedback = ""
        finished_by_skill = False
        max_turns_reached = False
        planner_turns = 0

        for test_turn in range(1, max_test_turns + 1):
            planner_turns = test_turn
            test_plan = self.tester.plan(
                request,
                context={
                    "tool_contract": tool_contract,
                    "test_history": test_history,
                    "test_turn": test_turn,
                    "max_test_turns": max_test_turns,
                    **({"planning_feedback": planning_feedback} if planning_feedback else {}),
                    "_workspace_identity": {"owner_id": owner_id, "tool_name": tool_name},
                },
                event_sink=event_sink,
            )
            all_events.extend(test_plan.get("events") or [])
            if not test_plan.get("ok"):
                planning_error = _trim(test_plan.get("error") or test_plan.get("message")) or "测试样例规划失败"
                planning_feedback = f"上一轮测试规划未产生可执行结果：{planning_error}。请根据现有真实记录重新规划。"
                if test_turn < max_test_turns:
                    continue
                max_turns_reached = True
                break

            planning_error = ""
            planning_feedback = ""
            plan_message = _trim(test_plan.get("message")) or plan_message
            assessment = _trim(test_plan.get("assessment")) or assessment
            if isinstance(test_plan.get("presentation"), Mapping):
                presentation = dict(test_plan.get("presentation") or {})
            planned_cases = [dict(item) for item in test_plan.get("cases") or [] if isinstance(item, Mapping)]
            next_action = _trim(test_plan.get("next_action"))
            if next_action == "finish":
                finished_by_skill = True
                break
            if not planned_cases:
                planning_error = "测试 Skill 要求继续，但没有给出下一批测试样例"
                planning_feedback = planning_error + "。请给出至少一个可执行 case，或在证据充分时结束。"
                if test_turn < max_test_turns:
                    continue
                max_turns_reached = True
                break

            turn_cases: List[Dict[str, Any]] = []
            for planned_index, planned_case in enumerate(planned_cases):
                case_request = _trim(planned_case.get("request")) or request
                test_id_prefix = f"interactive_{test_turn}_{planned_index + 1}"
                try:
                    invocation = invocation_service.plan(
                        text=case_request,
                        selected_asset={"kind": "tool", "name": tool_name},
                        owner_ids=[owner_id] if owner_id else None,
                        allow_inactive=True,
                    )
                except AssetInvocationError as exc:
                    turn_cases.append({
                        "test_id": test_id_prefix,
                        "category": "interactive_run",
                        "status": "failed",
                        "input": {},
                        "actual": {},
                        "logs": [],
                        "purpose": _trim(planned_case.get("purpose")) or case_request,
                        "error": str(exc),
                    })
                    continue
                invocations.append(invocation)
                if invocation.get("status") != "ready":
                    turn_cases.append({
                        "test_id": test_id_prefix,
                        "category": "interactive_run",
                        "status": "failed",
                        "input": {},
                        "actual": {},
                        "logs": [],
                        "purpose": _trim(planned_case.get("purpose")) or case_request,
                        "error": _trim(invocation.get("message")) or "测试输入不完整",
                    })
                    continue
                for call_index, arguments in enumerate(invocation.get("calls") or []):
                    if not isinstance(arguments, Mapping):
                        continue
                    run_result = self.runtime.run(
                        tool_name,
                        dict(arguments),
                        owner_ids=[owner_id] if owner_id else None,
                        allow_inactive=True,
                    )
                    logs = [
                        dict(item)
                        for item in ((run_result.get("meta") or {}).get("execution_logs") or [])
                        if isinstance(item, Mapping)
                    ]
                    turn_cases.append({
                        "test_id": f"{test_id_prefix}_{call_index + 1}",
                        "category": "interactive_run",
                        "status": "passed" if run_result.get("ok") else "failed",
                        "input": dict(arguments),
                        "actual": dict(run_result.get("data") or {}),
                        "logs": logs,
                        "purpose": _trim(planned_case.get("purpose")) or case_request,
                        "error": _trim(run_result.get("error")),
                    })
            cases.extend(turn_cases)
            test_history.append({
                "turn": test_turn,
                "planned_cases": planned_cases,
                "executions": turn_cases,
            })
            if test_turn == max_test_turns:
                max_turns_reached = True

        execution_ok = bool(cases) and all(item["status"] == "passed" for item in cases)
        evidence_status = "sufficient" if finished_by_skill and bool(cases) else "inconclusive"
        technical_summary = f"{sum(item['status'] == 'passed' for item in cases)} / {len(cases)} 项技术运行成功"
        summary_parts = [technical_summary]
        if assessment:
            summary_parts.append(assessment)
        if max_turns_reached:
            summary_parts.append(f"已达到最多 {max_test_turns} 轮测试，基于现有证据结束")
        if planning_error:
            summary_parts.append(planning_error)
        test_result = {
            "ok": execution_ok,
            "execution_ok": execution_ok,
            "contract_ok": execution_ok,
            "evidence_status": evidence_status,
            "assessment": assessment,
            "test_turns": planner_turns,
            "max_test_turns": max_test_turns,
            "max_turns_reached": max_turns_reached,
            "error": planning_error or ("" if execution_ok else next((item["error"] for item in cases if item["error"]), "工具运行失败")),
            "cases": cases,
            "summary": "；".join(summary_parts) + "；业务结果请用户确认",
        }
        saved = self.store.record_test(tool_name, test_result)
        next_state = {
            **self._clean_state_for_context(state),
            "tool_name": tool_name,
            "owner_id": owner_id or _trim(state.get("owner_id")),
        }
        return {
            "message": "\n".join(item for item in [plan_message, test_result["summary"]] if item),
            "test_status": "passed" if execution_ok else "failed",
            "test_result": test_result,
            "invocations": invocations,
            "presentation": dict(presentation),
            "events": all_events,
            "tool": saved,
            "state": next_state,
            "thread_context_patch": {"custom_tool_state": next_state},
        }

    def _return_legacy_design(self, requirement_text: str, *, owner_id: str, design: Mapping[str, Any]) -> Dict[str, Any]:
        next_state = {
            "requirement_text": requirement_text,
            "owner_id": owner_id,
            "design": dict(design),
        }
        manifest = design["manifest"]
        message = (
            f"已生成自定义工具设计：{manifest['tool_name']} / {manifest['display_name']}\n"
            f"描述：{manifest['description']}\n"
            f"实现逻辑：{manifest['implementation_logic']}\n"
            "回复“确认实现”后生成 draft；需要调整则直接说明修改点。"
        )
        return {
            "message": message,
            "state": next_state,
            "design": dict(design),
            "thread_context_patch": {"custom_tool_state": next_state},
        }

    def _assert_edit_base_current(
        self,
        state: Mapping[str, Any],
        *,
        owner_id: str,
    ) -> Dict[str, Any]:
        target = state.get("edit_target") if isinstance(state.get("edit_target"), Mapping) else {}
        tool_name = self.store.normalize_tool_name(target.get("tool_name"))
        if not tool_name:
            raise CustomToolError("edit target does not include tool_name")
        active = self.store.load(tool_name)
        manifest = active.get("manifest") if isinstance(active.get("manifest"), Mapping) else {}
        if _trim(manifest.get("owner_id")) != _trim(owner_id):
            raise CustomToolError("只能修改当前账号创建的个人工具。")
        base_revision = int(target.get("base_revision") or 0)
        current_revision = int(manifest.get("current_revision") or 0)
        if current_revision != base_revision:
            raise CustomToolError(
                "active custom tool revision changed: "
                f"expected {base_revision}, current {current_revision}"
            )
        current_code = _trim(active.get("code"))
        current_hash = _trim(manifest.get("code_hash")) or hashlib.sha256(
            current_code.encode("utf-8")
        ).hexdigest()
        expected_hash = _trim(target.get("base_code_hash"))
        if expected_hash and current_hash != expected_hash:
            raise CustomToolError("active custom tool code changed during edit")
        return active

    def _lock_edit_candidate_bundle(
        self,
        candidate: Mapping[str, Any],
        *,
        state: Mapping[str, Any],
        owner_id: str,
    ) -> Dict[str, Any]:
        """Apply system-owned identity and local-edit invariants to model output."""
        active = self._assert_edit_base_current(state, owner_id=owner_id)
        active_manifest = (
            dict(active.get("manifest") or {})
            if isinstance(active.get("manifest"), Mapping)
            else {}
        )
        target = state.get("edit_target") if isinstance(state.get("edit_target"), Mapping) else {}
        plan = state.get("edit_plan") if isinstance(state.get("edit_plan"), Mapping) else {}
        route = _trim(plan.get("route"))
        affected = set(plan.get("affected_assets") or [])
        result = dict(candidate)
        candidate_manifest = (
            dict(result.get("manifest") or {})
            if isinstance(result.get("manifest"), Mapping)
            else {}
        )
        metadata_patch = (
            dict(plan.get("metadata_patch") or {})
            if isinstance(plan.get("metadata_patch"), Mapping)
            else {}
        )
        tool_name = self.store.normalize_tool_name(target.get("tool_name"))
        locked_manifest = {
            **active_manifest,
            **candidate_manifest,
            "tool_name": tool_name,
            "owner_id": _trim(owner_id),
            "visibility": _trim(active_manifest.get("visibility")) or "personal",
        }
        if route == "local_patch":
            locked_manifest["display_name"] = (
                _trim(metadata_patch.get("display_name"))
                or _trim(active_manifest.get("display_name"))
                or tool_name
            )
            locked_manifest["description"] = (
                _trim(metadata_patch.get("description"))
                or _trim(active_manifest.get("description"))
            )
            result["input_schema"] = dict(active.get("input_schema") or {})
            result["output_schema"] = dict(active.get("output_schema") or {})
            if "implementation" not in affected:
                result["code"] = _trim(active.get("code"))
                result["modules"] = [
                    dict(item)
                    for item in active.get("modules") or []
                    if isinstance(item, Mapping)
                ]
            for companion_key in (
                "finance_tool_profile",
                "strategy_runtime_profile",
                "selection_output_profile",
            ):
                if not isinstance(result.get(companion_key), Mapping) or not result.get(
                    companion_key
                ):
                    active_companion = active.get(companion_key)
                    if isinstance(active_companion, Mapping) and active_companion:
                        result[companion_key] = dict(active_companion)
        result = _normalized_executable_revision(result)
        capability_source = (
            active_manifest.get("capabilities")
            if route == "local_patch"
            else candidate_manifest.get("capabilities")
        )
        capabilities = [
            _trim(item)
            for item in capability_source or ["custom_tool"]
            if _trim(item) and _trim(item) not in {"strategy", "action"}
        ]
        if "custom_tool" not in capabilities:
            capabilities.insert(0, "custom_tool")
        if isinstance(result.get("strategy_runtime_profile"), Mapping) and result.get(
            "strategy_runtime_profile"
        ):
            capabilities.append("strategy")
        locked_manifest["capabilities"] = list(dict.fromkeys(capabilities))
        result["manifest"] = locked_manifest
        result["design_contract"] = dict(state.get("design_contract") or {})
        return result

    def _save_noncode_edit_candidate(
        self,
        *,
        active_bundle: Mapping[str, Any],
        state: Mapping[str, Any],
        owner_id: str,
        events: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        candidate = self._lock_edit_candidate_bundle(
            {
                **dict(active_bundle),
                "design_contract": dict(state.get("design_contract") or {}),
            },
            state=state,
            owner_id=owner_id,
        )
        target = dict(state.get("edit_target") or {})
        base_revision = int(target.get("base_revision") or 0)
        cases = [
            {
                "test_id": "edit_identity_guard",
                "category": "edit_invariant",
                "status": "passed",
                "purpose": "确认修改仍归属于原工具，未生成新的工具 ID。",
                "input": {},
                "expected": {"tool_name": target.get("tool_name")},
                "actual": {
                    "tool_name": (candidate.get("manifest") or {}).get("tool_name")
                },
                "logs": [],
                "error": "",
            },
            {
                "test_id": "implementation_unchanged",
                "category": "edit_invariant",
                "status": "passed",
                "purpose": "本轮没有修改业务实现，继续使用当前已启用代码。",
                "input": {},
                "expected": {"code_hash": target.get("base_code_hash")},
                "actual": {
                    "code_hash": hashlib.sha256(
                        _trim(candidate.get("code")).encode("utf-8")
                    ).hexdigest()
                },
                "logs": [],
                "error": "",
            },
            {
                "test_id": "public_contract_unchanged",
                "category": "edit_invariant",
                "status": "passed",
                "purpose": "确认输入输出契约没有被局部修改意外改写。",
                "input": {},
                "expected": {"contract_hash": target.get("base_contract_hash")},
                "actual": {
                    "contract_hash": self._stable_payload_hash({
                        "input": dict(candidate.get("input_schema") or {}),
                        "output": dict(candidate.get("output_schema") or {}),
                    })
                },
                "logs": [],
                "error": "",
            },
        ]
        execution_ok = all(
            item["expected"] == item["actual"] for item in cases
        )
        for item in cases:
            if item["expected"] != item["actual"]:
                item["status"] = "failed"
                item["error"] = "候选版本违反局部修改保护。"
        test_result = {
            "ok": execution_ok,
            "execution_ok": execution_ok,
            "contract_ok": execution_ok,
            "summary": (
                "修改范围校验通过；业务实现未变，无需重复扫描市场数据。"
                if execution_ok
                else "修改范围校验失败，候选版本不会启用。"
            ),
            "cases": cases,
            "evidence_source": "edit_invariant_checks",
            "error": "" if execution_ok else "edit invariant check failed",
        }
        manifest = dict(candidate.get("manifest") or {})
        manifest["last_test"] = {
            "ok": execution_ok,
            "execution_ok": execution_ok,
            "contract_ok": execution_ok,
            "error": _trim(test_result.get("error")),
        }
        candidate["manifest"] = manifest
        saved = self.store.save_candidate_revision(
            candidate,
            owner_id=owner_id,
            tool_name=_trim(target.get("tool_name")),
        )
        candidate_revision = int((saved.get("manifest") or {}).get("current_revision") or 0)
        next_state = {
            **self._clean_state_for_context(state),
            "tool_name": _trim(target.get("tool_name")),
            "owner_id": owner_id,
            "implementation_revision": candidate_revision,
        }
        edit_summary = self._build_edit_summary(
            active_bundle=active_bundle,
            candidate_bundle=saved,
            state=next_state,
            test_result=test_result,
        )
        return {
            "message": (
                f"已为 {_trim((saved.get('manifest') or {}).get('display_name')) or target.get('tool_name')} "
                f"生成候选版本 {candidate_revision}；当前启用版本仍是 {base_revision}。\n"
                f"{test_result['summary']} 请检查变更后再确认启用。"
            ),
            "coding_status": "implemented",
            "test_status": "passed" if execution_ok else "failed",
            "test_result": test_result,
            "tool": saved,
            "edit_summary": edit_summary,
            "events": events or [],
            "state": next_state,
            "thread_context_patch": {"custom_tool_state": next_state},
        }

    def _build_edit_summary(
        self,
        *,
        active_bundle: Mapping[str, Any],
        candidate_bundle: Mapping[str, Any],
        state: Mapping[str, Any],
        test_result: Mapping[str, Any],
    ) -> Dict[str, Any]:
        plan = state.get("edit_plan") if isinstance(state.get("edit_plan"), Mapping) else {}
        target = state.get("edit_target") if isinstance(state.get("edit_target"), Mapping) else {}
        active_manifest = active_bundle.get("manifest") if isinstance(active_bundle.get("manifest"), Mapping) else {}
        candidate_manifest = candidate_bundle.get("manifest") if isinstance(candidate_bundle.get("manifest"), Mapping) else {}
        changes: List[Dict[str, Any]] = []
        metadata_patch = plan.get("metadata_patch") if isinstance(plan.get("metadata_patch"), Mapping) else {}
        for field, title in (("display_name", "工具名称"), ("description", "工具说明")):
            if metadata_patch.get(field) is None:
                continue
            changes.append({
                "title": title,
                "asset": "metadata",
                "before": active_manifest.get(field),
                "after": candidate_manifest.get(field),
                "reason": "按本轮明确要求更新。",
            })
        for replacement in plan.get("design_replacements") or []:
            if not isinstance(replacement, Mapping):
                continue
            changes.append({
                "title": "策略设计",
                "asset": "design",
                "before": replacement.get("before"),
                "after": replacement.get("after"),
                "reason": _trim(replacement.get("reason")),
            })
        instruction = _trim(plan.get("implementation_instruction"))
        if instruction:
            changes.append({
                "title": "核心实现",
                "asset": "implementation",
                "before": "当前已启用实现",
                "after": instruction,
                "reason": "只修改与本轮要求直接相关的实现，并用合成样本验证。",
            })
        execution_ok = test_result.get("execution_ok") is True
        return {
            "tool_name": _trim(candidate_manifest.get("tool_name")),
            "display_name": _trim(candidate_manifest.get("display_name")),
            "route": _trim(plan.get("route")),
            "impact_summary": _trim(plan.get("impact_summary")),
            "base_revision": int(target.get("base_revision") or 0),
            "candidate_revision": int(candidate_manifest.get("current_revision") or 0),
            "affected_assets": list(plan.get("affected_assets") or []),
            "changes": changes,
            "verification": {
                "status": "passed" if execution_ok else "failed",
                "summary": _trim(test_result.get("summary")),
                "cases": [
                    dict(item)
                    for item in test_result.get("cases") or []
                    if isinstance(item, Mapping)
                ],
            },
        }

    @staticmethod
    def _runtime_business_ok(result: Mapping[str, Any]) -> bool:
        if result.get("ok") is not True:
            return False
        data = result.get("data")
        return not (isinstance(data, Mapping) and data.get("ok") is False)

    def _save_test_and_return(
        self,
        design: Mapping[str, Any],
        *,
        owner_id: str,
        events: Optional[List[Dict[str, Any]]] = None,
        coding_evidence: Optional[Mapping[str, Any]] = None,
        state: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        edit_state = state if isinstance(state, Mapping) else {}
        edit_target = (
            edit_state.get("edit_target")
            if isinstance(edit_state.get("edit_target"), Mapping)
            else {}
        )
        if edit_target:
            active_bundle = self._assert_edit_base_current(
                edit_state,
                owner_id=owner_id,
            )
            evidence_cases: List[Dict[str, Any]] = []
            if isinstance(coding_evidence, Mapping):
                raw_cases = (
                    coding_evidence.get("cases")
                    if isinstance(coding_evidence.get("cases"), list)
                    else [coding_evidence]
                )
                for item in raw_cases:
                    if not isinstance(item, Mapping) or not isinstance(item.get("input"), Mapping):
                        continue
                    actual = item.get("actual")
                    if not isinstance(actual, Mapping):
                        continue
                    declared_status = _trim(item.get("status") or "passed").lower()
                    business_ok = actual.get("ok") is not False
                    passed = (
                        declared_status not in {"fail", "failed", "error"}
                        and business_ok
                    )
                    evidence_cases.append({
                        "test_id": f"synthetic_edit_case_{len(evidence_cases) + 1}",
                        "category": "synthetic_fixture",
                        "status": "passed" if passed else "failed",
                        "input": dict(item["input"]),
                        "expected": {
                            "business_result": "符合该样本对应的策略预期，且不返回 ok=false"
                        },
                        "actual": dict(actual),
                        "logs": [],
                        "purpose": "使用构造的正例、反例或边界样本验证本轮策略修改。",
                        "error": "" if passed else (
                            _trim(actual.get("error"))
                            or "构造样本执行结果未通过。"
                        ),
                    })
            execution_ok = bool(evidence_cases) and all(
                item.get("status") == "passed" for item in evidence_cases
            )
            test_result = {
                "ok": execution_ok,
                "execution_ok": execution_ok,
                "contract_ok": execution_ok,
                "data": (
                    dict(evidence_cases[0].get("actual") or {})
                    if evidence_cases
                    else {}
                ),
                "cases": evidence_cases,
                "summary": (
                    f"构造数据聚焦验证通过（{len(evidence_cases)} 组实际运行）；未扫描真实市场全量数据。"
                    if execution_ok
                    else "未取得完整且成功的构造样本证据，候选版本不会启用。"
                ),
                "evidence_source": "isolated_synthetic_fixture",
                "error": "" if execution_ok else "focused edit verification failed",
            }
            candidate = dict(design)
            candidate_manifest = dict(candidate.get("manifest") or {})
            candidate_manifest["last_test"] = {
                "ok": execution_ok,
                "execution_ok": execution_ok,
                "contract_ok": execution_ok,
                "error": _trim(test_result.get("error")),
            }
            candidate["manifest"] = candidate_manifest
            saved = self.store.save_candidate_revision(
                candidate,
                owner_id=owner_id,
                tool_name=_trim(edit_target.get("tool_name")),
            )
            manifest = saved["manifest"]
            next_state = {
                **self._clean_state_for_context(edit_state),
                "tool_name": manifest["tool_name"],
                "owner_id": owner_id,
                "implementation_revision": int(manifest.get("current_revision") or 0),
                "requirement_text": _trim(design.get("requirement_text")),
                "design_contract": dict(design.get("design_contract") or {}),
            }
            edit_summary = self._build_edit_summary(
                active_bundle=active_bundle,
                candidate_bundle=saved,
                state=next_state,
                test_result=test_result,
            )
            return {
                "message": (
                    f"已生成候选版本 {manifest.get('current_revision')}，当前启用版本仍是 "
                    f"{edit_target.get('base_revision')}。\n{test_result['summary']} "
                    + ("请检查关键差异后确认启用。" if execution_ok else "请根据失败证据继续修改。")
                ),
                "test_status": "passed" if execution_ok else "failed",
                "test_result": test_result,
                "tool": saved,
                "edit_summary": edit_summary,
                "events": events or [],
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }

        saved = self.store.save_draft(design, owner_id=owner_id)
        manifest = saved["manifest"]
        sample_input = design.get("sample_input") if isinstance(design.get("sample_input"), Mapping) else {}
        proposed_tests = [dict(item) for item in design.get("proposed_tests") or [] if isinstance(item, Mapping)]
        next_state = {
            "tool_name": manifest["tool_name"],
            "owner_id": owner_id,
            "implementation_revision": int(manifest.get("current_revision") or 0),
            "requirement_text": _trim(design.get("requirement_text")),
            "design_contract": dict(design.get("design_contract") or {}),
        }
        evidence_cases = []
        if isinstance(coding_evidence, Mapping):
            raw_cases = (
                coding_evidence.get("cases")
                if isinstance(coding_evidence.get("cases"), list)
                else [coding_evidence]
            )
            for item in raw_cases:
                if not isinstance(item, Mapping) or not isinstance(item.get("input"), Mapping):
                    continue
                actual = item.get("actual")
                if not isinstance(actual, Mapping):
                    continue
                case_status = _trim(item.get("status") or "passed").lower()
                evidence_cases.append({
                    "test_id": f"coding_functional_test_{len(evidence_cases) + 1}",
                    "category": "representative",
                    "status": case_status,
                    "input": dict(item["input"]),
                    "expected": {},
                    "actual": dict(actual),
                    "logs": [],
                    "purpose": "展示 Coding 阶段实际运行的代表性功能测试。",
                    "error": "",
                })
        if not sample_input and evidence_cases:
            coding_evidence_ok = all(
                _trim(item.get("status")).lower() not in {"fail", "failed", "error"}
                for item in evidence_cases
            )
            representative_input = dict(evidence_cases[0]["input"])
            runtime_result = self.runtime.run(
                manifest["tool_name"],
                representative_input,
                owner_ids=[owner_id] if owner_id else None,
                allow_inactive=True,
            )
            runtime_ok = self._runtime_business_ok(runtime_result)
            execution_ok = coding_evidence_ok and runtime_ok
            runtime_actual = (
                dict(runtime_result.get("data") or {})
                if isinstance(runtime_result.get("data"), Mapping)
                else {}
            )
            runtime_case = {
                **evidence_cases[0],
                "test_id": "production_runtime_smoke",
                "category": "runtime_compatibility",
                "status": "passed" if execution_ok else "failed",
                "actual": runtime_actual,
                "logs": [
                    dict(item)
                    for item in ((runtime_result.get("meta") or {}).get("execution_logs") or [])
                    if isinstance(item, Mapping)
                ],
                "purpose": "使用正式运行包装器验证动态加载、沙箱执行和代表性输入。",
                "error": _trim(runtime_result.get("error")),
            }
            test_result = {
                **runtime_result,
                "ok": execution_ok,
                "execution_ok": execution_ok,
                "contract_ok": execution_ok,
                "data": runtime_actual,
                "cases": [runtime_case],
                "coding_cases": evidence_cases,
                "proposed_cases": proposed_tests,
                "summary": (
                    "正式运行兼容性验证通过"
                    if execution_ok
                    else "正式运行兼容性验证失败"
                ),
                "evidence_source": "production_runtime",
            }
            saved = self.store.record_test(manifest["tool_name"], test_result)
            return {
                "message": (
                    f"已生成 draft：{manifest['tool_name']}。\n"
                    + (
                        "正式运行兼容性验证通过，实际结果和核心过程信息供你确认。"
                        if execution_ok
                        else f"正式运行兼容性验证失败：{_trim(runtime_result.get('error')) or '运行失败'}"
                    )
                ),
                "state": next_state,
                "tool": saved,
                "test_result": test_result,
                "events": events or [],
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        if not sample_input:
            return {
                "message": f"已生成 draft：{manifest['tool_name']}。代码实现和 Coding 检查结果已保存。",
                "state": next_state,
                "tool": saved,
                "events": events or [],
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        test_result = self.runtime.run(
            manifest["tool_name"],
            sample_input,
            owner_ids=[owner_id] if owner_id else None,
            allow_inactive=True,
        )
        expected = self._expected_for_sample(proposed_tests, sample_input)
        execution_ok = self._runtime_business_ok(test_result)
        contract_ok = execution_ok
        test_result.update({
            "execution_ok": execution_ok,
            "contract_ok": contract_ok,
        })
        execution_logs = [
            dict(item)
            for item in ((test_result.get("meta") or {}).get("execution_logs") or [])
            if isinstance(item, Mapping)
        ]
        actual_output = test_result.get("data") if isinstance(test_result.get("data"), Mapping) else {}
        if not actual_output:
            diagnostics = (test_result.get("meta") or {}).get("diagnostics") or {}
            if isinstance(diagnostics.get("actual_output"), Mapping):
                actual_output = dict(diagnostics["actual_output"])
        test_result["cases"] = [{
            "test_id": "sample_smoke",
            "category": "happy_path",
            "status": "passed" if execution_ok else "failed",
            "input": sample_input,
            "expected": expected or {"business_result": "no top-level error and no ok=false"},
            "actual": dict(actual_output),
            "logs": execution_logs,
            "purpose": "验证动态加载、沙箱执行和代表性输入能够完整走通。",
            "error": _trim(test_result.get("error")),
        }]
        test_result["proposed_cases"] = proposed_tests
        test_result["summary"] = "1 / 1 项技术运行成功" if execution_ok else "0 / 1 项技术运行成功"
        saved = self.store.record_test(manifest["tool_name"], test_result)
        if not execution_ok:
            next_state["test_feedback"] = self._test_feedback(
                test_result,
                sample_input=sample_input,
                expected=expected,
                execution_logs=execution_logs,
            )
        return {
            "message": (
                f"已生成 draft：{manifest['tool_name']}。\n"
                f"样例技术运行：{'成功' if execution_ok else '失败'}"
                + (f"\n错误：{test_result.get('error') or '运行失败'}" if not execution_ok else "")
                + (
                    "\n以下实际结果和核心日志供你确认业务逻辑；确认后可启用。"
                    if execution_ok
                    else "\n实现和失败现场均已保存，可根据真实错误继续修改。"
                )
            ),
            "state": next_state,
            "tool": saved,
            "test_result": test_result,
            "events": events or [],
            "thread_context_patch": {"custom_tool_state": next_state},
        }

    def _bundle_from_coding_final(
        self,
        design: Mapping[str, Any],
        final: Mapping[str, Any],
        *,
        preserved_revision: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        tool_contract = (
            dict(final.get("tool_contract") or {})
            if isinstance(final.get("tool_contract"), Mapping)
            else {}
        )
        tool_name = self.store.normalize_tool_name(
            tool_contract.get("tool_name") or design.get("tool_name")
        )
        display_name = (
            _trim(tool_contract.get("display_name"))
            or _trim(design.get("display_name"))
            or tool_name
        )
        description = _trim(tool_contract.get("description")) or _trim(design.get("description"))
        input_fields = (
            tool_contract.get("inputs")
            if isinstance(tool_contract.get("inputs"), list)
            else design.get("inputs")
            if isinstance(design.get("inputs"), list)
            else []
        )
        output_fields = (
            tool_contract.get("outputs")
            if isinstance(tool_contract.get("outputs"), list)
            else design.get("outputs")
            if isinstance(design.get("outputs"), list)
            else []
        )
        output_fields = self._with_key_process_info_output(output_fields)
        input_schema = self._schema_from_fields(input_fields)
        output_schema = self._schema_from_fields(output_fields)
        preserved_revision = (
            preserved_revision
            if isinstance(preserved_revision, Mapping)
            else {}
        )
        runtime_profile = final.get("strategy_runtime_profile")
        if not isinstance(runtime_profile, Mapping) or not runtime_profile:
            runtime_profile = preserved_revision.get("strategy_runtime_profile")
        selection_output_profile = final.get("selection_output_profile")
        if (
            not isinstance(selection_output_profile, Mapping)
            or not selection_output_profile
        ):
            selection_output_profile = preserved_revision.get(
                "selection_output_profile"
            )
        contract_input_schema = (
            preserved_revision.get("input_schema")
            if preserved_revision
            else input_schema
        )
        contract_output_schema = (
            preserved_revision.get("output_schema")
            if preserved_revision
            else output_schema
        )
        strategy_contract_service = StrategyRevisionContractService()
        try:
            strategy_contracts = strategy_contract_service.normalize(
                runtime_profile=runtime_profile,
                selection_output_profile=selection_output_profile,
                input_schema=(
                    contract_input_schema
                    if isinstance(contract_input_schema, Mapping)
                    else input_schema
                ),
                output_schema=(
                    contract_output_schema
                    if isinstance(contract_output_schema, Mapping)
                    else output_schema
                ),
            )
        except StrategyRevisionContractError as exc:
            if preserved_revision:
                raise CustomToolStrategyContractError(
                    f"invalid strategy revision contract: {exc}"
                ) from exc
            # Strategy replay/backtest companions are optional capabilities of
            # an otherwise executable Custom Tool.  Keep a valid runtime
            # companion when only the optional selection mapping is malformed;
            # otherwise omit both and let the direct Tool remain usable.
            try:
                strategy_contracts = strategy_contract_service.normalize(
                    runtime_profile=runtime_profile,
                    selection_output_profile=None,
                    input_schema=input_schema,
                    output_schema=output_schema,
                )
            except StrategyRevisionContractError:
                strategy_contracts = strategy_contract_service.normalize(
                    runtime_profile=None,
                    selection_output_profile=None,
                    input_schema=input_schema,
                    output_schema=output_schema,
                )
        strategy_bundle_fields = strategy_contracts.to_bundle_fields()
        design_finance_profile = design.get("finance_tool_profile")
        final_finance_profile = final.get("finance_tool_profile")
        if isinstance(design_finance_profile, Mapping) and design_finance_profile:
            raw_finance_profile = design_finance_profile
        elif isinstance(final_finance_profile, Mapping) and final_finance_profile:
            raw_finance_profile = final_finance_profile
        else:
            raw_finance_profile = preserved_revision.get("finance_tool_profile")
        try:
            finance_tool_profile = FinanceToolProfileService().normalize(
                raw_finance_profile,
                strategy_runtime_profile=strategy_bundle_fields.get(
                    "strategy_runtime_profile"
                ),
                selection_output_profile=strategy_bundle_fields.get(
                    "selection_output_profile"
                ),
            )
            FinanceToolProfileService.assert_implementation_allowed(
                finance_tool_profile
            )
        except FinanceToolProfileError as exc:
            if preserved_revision or _trim(
                (raw_finance_profile or {}).get("family")
                if isinstance(raw_finance_profile, Mapping)
                else ""
            ).lower() == "action":
                raise CustomToolFinanceProfileError(
                    f"invalid finance Tool profile: {exc}"
                ) from exc
            # A contradictory optional strategy companion must not prevent a
            # valid direct Tool from being saved.  Drop that capability and
            # retain the independently valid semantic profile when possible.
            strategy_bundle_fields = {}
            try:
                finance_tool_profile = FinanceToolProfileService().normalize(
                    raw_finance_profile,
                )
            except FinanceToolProfileError:
                finance_tool_profile = None
        code = self._select_code(final)
        if not code:
            raise CustomToolError("coding final does not include python code")
        legacy_implementation = (
            final.get("implementation")
            if isinstance(final.get("implementation"), Mapping)
            else {}
        )
        return {
            "manifest": {
                "tool_name": tool_name,
                "display_name": display_name,
                "description": description,
                "visibility": "personal",
                "capabilities": [
                    "custom_tool",
                    *(
                        ["strategy"]
                        if strategy_bundle_fields.get("strategy_runtime_profile")
                        else []
                    ),
                ],
                "implementation_logic": (
                    self._logic_text(design)
                    or _trim(final.get("implementation_summary"))
                    or _trim(legacy_implementation.get("summary"))
                ),
                "runtime": {"kind": "python_sandbox", "backend": "local_dev", "timeout_ms": 30000},
            },
            "input_schema": input_schema,
            "output_schema": output_schema,
            "code": code,
            "sample_input": self._sample_input(final),
            "modules": [dict(item) for item in legacy_implementation.get("modules") or [] if isinstance(item, Mapping)],
            "proposed_tests": self._execution_examples(final),
            "implementation_explanation": self._implementation_explanation(final),
            "implementation_review": self._implementation_review(final),
            "design_contract": dict(design),
            **(
                {"finance_tool_profile": finance_tool_profile}
                if finance_tool_profile is not None
                else {}
            ),
            **strategy_bundle_fields,
        }

    @staticmethod
    def _with_key_process_info_output(fields: List[Any]) -> List[Dict[str, Any]]:
        """Apply the one platform-wide explainability field without judging its business contents."""
        normalized = [dict(item) for item in fields if isinstance(item, Mapping)]
        for index, item in enumerate(normalized):
            if _trim(item.get("name")) != "key_process_info":
                continue
            normalized[index] = {
                **item,
                "name": "key_process_info",
                "type": "object",
                "required": True,
                "description": (
                    _trim(item.get("description"))
                    or "解释本次结果所需的核心中间指标、样本和判断条件。"
                ),
            }
            return normalized
        normalized.append({
            "name": "key_process_info",
            "type": "object",
            "required": True,
            "description": "解释本次结果所需的核心中间指标、样本和判断条件。",
        })
        return normalized

    @staticmethod
    def _implementation_explanation(final: Mapping[str, Any]) -> Dict[str, Any]:
        legacy = final.get("implementation_explanation")
        if isinstance(legacy, Mapping):
            return dict(legacy)
        summary = _trim(final.get("implementation_summary"))
        return {"summary": summary} if summary else {}

    @staticmethod
    def _implementation_review(final: Mapping[str, Any]) -> Dict[str, Any]:
        legacy = final.get("implementation_review")
        if isinstance(legacy, Mapping):
            return dict(legacy)
        legacy = final.get("technical_summary")
        if isinstance(legacy, Mapping):
            return dict(legacy)
        summary = _trim(final.get("implementation_summary")) or _trim(final.get("verification"))
        return {"summary": summary} if summary else {}

    @staticmethod
    def _expected_for_sample(tests: List[Dict[str, Any]], sample_input: Mapping[str, Any]) -> Dict[str, Any]:
        for item in tests:
            if _trim(item.get("category")) != "happy_path":
                continue
            input_value = item.get("input") if isinstance(item.get("input"), Mapping) else None
            expected_value = item.get("expected") if isinstance(item.get("expected"), Mapping) else None
            if input_value is None or dict(input_value) == dict(sample_input):
                return dict(expected_value or {})
        return {}

    @staticmethod
    def _logic_text(design: Mapping[str, Any]) -> str:
        document = _trim(design.get("document"))
        if document:
            return document
        plan = _trim(design.get("plan"))
        if plan:
            return plan
        logic = design.get("logic")
        if isinstance(logic, list):
            return "\n".join(_trim(item) for item in logic if _trim(item))
        rules = design.get("rules") if isinstance(design.get("rules"), list) else []
        rule_lines = [
            f"{_trim(item.get('name'))}: {_trim(item.get('logic'))}".strip(": ")
            for item in rules
            if isinstance(item, Mapping) and (_trim(item.get("name")) or _trim(item.get("logic")))
        ]
        if rule_lines:
            return "\n".join(rule_lines)
        modules = design.get("modules") if isinstance(design.get("modules"), list) else []
        module_lines = [
            f"{_trim(item.get('name'))}: {_trim(item.get('responsibility'))}".strip(": ")
            for item in modules
            if isinstance(item, Mapping) and (_trim(item.get("name")) or _trim(item.get("responsibility")))
        ]
        if module_lines:
            return "\n".join(module_lines)
        return _trim(logic)

    @staticmethod
    def _requirement_artifact_identity(
        requirement_brief: str,
        *,
        state: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        canonical_brief = _trim(requirement_brief)
        current_fingerprint = hashlib.sha256(
            canonical_brief.encode("utf-8")
        ).hexdigest()
        previous_state = state if isinstance(state, Mapping) else {}
        previous_fingerprint = _trim(
            previous_state.get("requirement_fingerprint")
        )
        previous_revision = int(
            previous_state.get("requirement_revision") or 0
        )
        revision = (
            previous_revision
            if previous_fingerprint == current_fingerprint
            else previous_revision + 1
        )
        if revision < 1:
            revision = 1
        artifact_id = _trim(previous_state.get("requirement_artifact_id"))
        if not artifact_id:
            flow_id = _trim(previous_state.get("custom_tool_flow_id"))
            seed = flow_id[:24] if flow_id else uuid.uuid4().hex[:24]
            artifact_id = f"finance_tool_requirement_{seed}"
        return {
            "requirement_artifact_id": artifact_id,
            "requirement_revision": revision,
            "requirement_fingerprint": current_fingerprint,
        }

    @classmethod
    def _requirement_artifact_identity_from_state(
        cls,
        state: Mapping[str, Any],
    ) -> Dict[str, Any]:
        requirement_brief = _trim(state.get("requirement_brief"))
        if not requirement_brief:
            return {}
        return cls._requirement_artifact_identity(
            requirement_brief,
            state=state,
        )

    @staticmethod
    def _validate_expected_revision(
        *,
        expected_revision: Any,
        current_revision: int,
        artifact_name: str,
    ) -> None:
        if expected_revision is None:
            raise CustomToolError(
                f"{artifact_name} expected revision is required"
            )
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError) as exc:
            raise CustomToolError(
                f"{artifact_name} expected revision is invalid"
            ) from exc
        if expected != int(current_revision):
            raise CustomToolError(
                f"{artifact_name} revision changed: "
                f"expected {expected}, current {int(current_revision)}"
            )

    @staticmethod
    def _design_artifact_identity(
        design: Mapping[str, Any],
        *,
        state: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        # Mermaid is a derived presentation asset. Updating only the diagram
        # must not create a new business-design revision or invalidate a user's
        # review of unchanged rules.
        business_design = dict(design)
        business_design.pop("mermaid", None)
        current_text = json.dumps(
            business_design,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        current_fingerprint = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
        previous_state = state if isinstance(state, Mapping) else {}
        previous_fingerprint = _trim(previous_state.get("design_fingerprint"))
        previous_revision = int(previous_state.get("design_revision") or 0)
        revision = previous_revision if previous_fingerprint == current_fingerprint else previous_revision + 1
        if revision < 1:
            revision = 1
        artifact_id = _trim(previous_state.get("design_artifact_id"))
        if not artifact_id:
            seed = _trim(design.get("tool_name")) or current_fingerprint[:16]
            normalized_seed = re.sub(r"[^a-zA-Z0-9_]+", "_", seed).strip("_") or current_fingerprint[:16]
            artifact_id = f"finance_tool_spec_{normalized_seed[:48]}"
        return {
            "design_artifact_id": artifact_id,
            "design_revision": revision,
            "design_fingerprint": current_fingerprint,
        }

    @staticmethod
    def _schema_from_fields(fields: List[Any]) -> Dict[str, Any]:
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for item in fields:
            if not isinstance(item, Mapping):
                continue
            name = _trim(item.get("name"))
            if not name:
                continue
            field_type = _trim(item.get("type")) or "string"
            json_type = "object" if field_type == "dict" else field_type
            if json_type not in {"string", "number", "boolean", "array", "object", "integer"}:
                json_type = "string"
            field_schema: Dict[str, Any] = {"type": json_type, "description": _trim(item.get("description"))}
            if json_type == "array":
                item_type = _trim(item.get("items_type"))
                if item_type in {"string", "number", "boolean", "object", "integer"}:
                    field_schema["items"] = {"type": item_type}
            label = _trim(item.get("label"))
            if label:
                field_schema["title"] = label
            raw_values = item.get("values") or []
            if json_type == "string":
                values = [value for value in raw_values if isinstance(value, str)]
            elif json_type == "integer":
                values = [value for value in raw_values if isinstance(value, int) and not isinstance(value, bool)]
            elif json_type == "number":
                values = [value for value in raw_values if isinstance(value, (int, float)) and not isinstance(value, bool)]
            elif json_type == "boolean":
                values = [value for value in raw_values if isinstance(value, bool)]
            else:
                values = []
            if values:
                field_schema["enum"] = values
            properties[name] = field_schema
            if item.get("required") is True:
                required.append(name)
        schema: Dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": True}
        if required:
            schema["required"] = required
        return schema

    @staticmethod
    def _test_feedback(
        test_result: Mapping[str, Any],
        *,
        sample_input: Mapping[str, Any],
        expected: Mapping[str, Any],
        execution_logs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Keep the concrete failed-run evidence for the next Coding turn."""
        actual = test_result.get("data") if isinstance(test_result.get("data"), Mapping) else {}
        if not actual:
            diagnostics = (test_result.get("meta") or {}).get("diagnostics") or {}
            if isinstance(diagnostics.get("actual_output"), Mapping):
                actual = dict(diagnostics["actual_output"])
        return {
            "summary": _trim(test_result.get("summary")) or "样例测试未通过",
            "execution_ok": bool(test_result.get("execution_ok")),
            "contract_ok": bool(test_result.get("contract_ok")),
            "error": _trim(test_result.get("error")),
            "input": dict(sample_input),
            "expected": dict(expected),
            "actual": dict(actual),
            "logs": [dict(item) for item in execution_logs],
        }

    @staticmethod
    def _select_code(final: Mapping[str, Any]) -> str:
        code = _trim(final.get("code"))
        if code:
            return code
        implementation = final.get("implementation") if isinstance(final.get("implementation"), Mapping) else {}
        entry_module = _trim(implementation.get("entry_module"))
        modules = [item for item in implementation.get("modules") or [] if isinstance(item, Mapping)]
        for item in modules:
            if _trim(item.get("module_id")) == entry_module and _trim(item.get("source_code")):
                return _trim(item.get("source_code"))
        for item in modules:
            if _trim(item.get("source_code")):
                return _trim(item.get("source_code"))
        return ""

    @staticmethod
    def _sample_input(final: Mapping[str, Any]) -> Dict[str, Any]:
        examples = CustomToolAgentService._raw_execution_examples(final)
        for item in examples:
            if not isinstance(item, Mapping):
                continue
            value = item.get("input")
            if isinstance(value, Mapping):
                return dict(value)
        if isinstance(final.get("sample_input"), Mapping):
            return dict(final.get("sample_input") or {})
        tests = final.get("tests") if isinstance(final.get("tests"), list) else []
        for item in tests:
            if isinstance(item, Mapping) and isinstance(item.get("input"), Mapping):
                return dict(item.get("input") or {})
        return {}

    @staticmethod
    def _execution_examples(final: Mapping[str, Any]) -> List[Dict[str, Any]]:
        examples: List[Dict[str, Any]] = []
        for index, item in enumerate(CustomToolAgentService._raw_execution_examples(final)):
            if not isinstance(item, Mapping):
                continue
            input_value = item.get("input")
            output_value = item.get("output")
            if not isinstance(input_value, Mapping) or not isinstance(output_value, Mapping):
                continue
            examples.append({
                "test_id": f"coding_example_{index + 1}",
                "category": "representative",
                "input": dict(input_value),
                "expected": dict(output_value),
                "actual": dict(output_value),
            })
        return examples

    @staticmethod
    def _coding_test_evidence(events: List[Dict[str, Any]]) -> Dict[str, Any]:
        marker = "CUSTOM_TOOL_TEST_EVIDENCE="
        decoder = json.JSONDecoder()
        for event in reversed(events):
            content = _trim(event.get("content"))
            if marker not in content:
                continue
            payload_text = content.split(marker, 1)[1].lstrip()
            try:
                payload, _ = decoder.raw_decode(payload_text)
            except Exception:
                continue
            if not isinstance(payload, Mapping):
                continue
            input_value = payload.get("input")
            actual_value = payload.get("actual")
            if isinstance(input_value, Mapping) and isinstance(actual_value, Mapping):
                return {
                    "input": dict(input_value),
                    "actual": dict(actual_value),
                }
        return {}

    @staticmethod
    def _raw_execution_examples(final: Mapping[str, Any]) -> List[Dict[str, Any]]:
        value = final.get("execution_examples")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                return []
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, Mapping)]

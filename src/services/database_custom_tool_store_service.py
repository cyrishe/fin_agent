from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Mapping, Optional

import pymysql

from src.utils.system_db_utils import connect_system_db


ARTIFACT_TABLE = "aiia_runtime_artifact"
REVISION_TABLE = "aiia_runtime_artifact_revision"
TEST_RUN_TABLE = "aiia_custom_tool_test_run"


def _trim(value: Any) -> str:
    return str(value or "").strip()


class DatabaseCustomToolStoreService:
    """Persist dynamic custom-tool modules in the runtime artifact registry."""

    def __init__(
        self,
        *,
        connection_factory: Optional[Callable[[], Any]] = None,
        error_type: type[Exception] = ValueError,
    ) -> None:
        self.connection_factory = connection_factory or self._connect
        self.error_type = error_type

    @classmethod
    def normalize_tool_name(cls, value: Any) -> str:
        """Match the store facade contract for dependency-injected workflows."""
        return cls._normalize_name(value)

    @staticmethod
    def _connect() -> Any:
        return connect_system_db(
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    def exists(self, tool_name: str) -> bool:
        name = self._normalize_name(tool_name)
        if not name:
            return False
        try:
            db = self.connection_factory()
        except RuntimeError:
            return False
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"SELECT artifact_id FROM {ARTIFACT_TABLE} WHERE artifact_type='custom_tool' AND name=%s AND version='v1' LIMIT 1",
                    (name,),
                )
                return bool(cursor.fetchone())
        finally:
            db.close()

    def save_draft(self, design: Mapping[str, Any], *, owner_id: str = "") -> Dict[str, Any]:
        manifest = dict(design.get("manifest") or {})
        tool_name = self._normalize_name(manifest.get("tool_name"))
        code = _trim(design.get("code"))
        if not tool_name:
            self._raise("manifest.tool_name is required")
        if not code:
            self._raise("code module source is required")
        normalized_owner = _trim(owner_id)
        manifest.update({
            "tool_name": tool_name,
            "status": "draft",
            "owner_id": normalized_owner,
            "visibility": _trim(manifest.get("visibility")) or "personal",
            "storage_kind": "database_code_module",
        })
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        manifest["code_hash"] = code_hash
        schema_payload = {
            "input": dict(design.get("input_schema") or {}),
            "output": dict(design.get("output_schema") or {}),
        }
        supplied_modules = [dict(item) for item in design.get("modules") or [] if isinstance(item, Mapping)]
        implementation_payload = {
            "implementation": {
                "kind": "database_code_module",
                "entry_module": _trim((supplied_modules[0] if supplied_modules else {}).get("module_id")) or "main",
                "modules": supplied_modules or [{
                    "module_id": "main",
                    "language": "python",
                    "entrypoint": "run",
                    "source_code": code,
                }],
            },
            "sample_input": dict(design.get("sample_input") or {}),
            "proposed_tests": [dict(item) for item in design.get("proposed_tests") or [] if isinstance(item, Mapping)],
            "implementation_explanation": dict(design.get("implementation_explanation") or {}),
            "implementation_review": dict(design.get("implementation_review") or {}),
            "design_contract": dict(design.get("design_contract") or {}),
            "design_provenance": dict(design.get("design_provenance") or {}),
            "design_feedback_evidence": [dict(item) for item in design.get("design_feedback_evidence") or [] if isinstance(item, Mapping)],
        }
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"SELECT artifact_id, owner, current_revision_no, source_manifest_json FROM {ARTIFACT_TABLE} WHERE artifact_type='custom_tool' AND name=%s AND version='v1' LIMIT 1 FOR UPDATE",
                    (tool_name,),
                )
                row = cursor.fetchone()
                if row and _trim(row.get("owner")) and normalized_owner and _trim(row.get("owner")) != normalized_owner:
                    tool_name = self._owner_scoped_name(tool_name, normalized_owner)
                    manifest["tool_name"] = tool_name
                    cursor.execute(
                        f"SELECT artifact_id, owner, current_revision_no, source_manifest_json FROM {ARTIFACT_TABLE} WHERE artifact_type='custom_tool' AND name=%s AND version='v1' LIMIT 1 FOR UPDATE",
                        (tool_name,),
                    )
                    row = cursor.fetchone()
                    if row and _trim(row.get("owner")) and _trim(row.get("owner")) != normalized_owner:
                        self._raise("custom tool identity collision")
                revision_no = int((row or {}).get("current_revision_no") or 0) + 1
                manifest["current_revision"] = revision_no
                source_meta = self._json_dict((row or {}).get("source_manifest_json"))
                source_meta.update({
                    "storage_kind": "database_code_module",
                    "visibility": manifest["visibility"],
                    "runtime": dict(manifest.get("runtime") or {}),
                    "code_hash": code_hash,
                })
                retrieval_text = "\n".join(filter(None, [tool_name, _trim(manifest.get("display_name")), _trim(manifest.get("description"))]))
                if row:
                    artifact_id = int(row["artifact_id"])
                    cursor.execute(
                        f"""
                        UPDATE {ARTIFACT_TABLE}
                        SET status='draft', enabled=0, display_name=%s, description=%s,
                            owner=%s, domain='finance', capabilities_json=%s,
                            implementation_kind='database_code_module', implementation_target=%s,
                            source_manifest_json=%s, retrieval_text=%s,
                            current_revision_no=%s, sync_status='synced', updated_by=%s, updated_at=NOW()
                        WHERE artifact_id=%s
                        """,
                        (
                            _trim(manifest.get("display_name")) or tool_name,
                            _trim(manifest.get("description")),
                            normalized_owner,
                            json.dumps(manifest.get("capabilities") or ["custom_tool"], ensure_ascii=False),
                            f"artifact_revision:{revision_no}",
                            json.dumps(source_meta, ensure_ascii=False),
                            retrieval_text,
                            revision_no,
                            normalized_owner or "system",
                            artifact_id,
                        ),
                    )
                else:
                    cursor.execute(
                        f"""
                        INSERT INTO {ARTIFACT_TABLE} (
                          artifact_type, name, version, status, display_name, description,
                          owner, domain, capabilities_json, tags_json, keywords_json,
                          side_effect_level, enabled, implementation_kind, implementation_target,
                          source_manifest_json, retrieval_text, current_revision_no, sync_status,
                          last_synced_at, created_by, updated_by
                        ) VALUES (
                          'custom_tool', %s, 'v1', 'draft', %s, %s,
                          %s, 'finance', %s, '[]', '[]',
                          'read_only', 0, 'database_code_module', %s,
                          %s, %s, %s, 'synced', NOW(), %s, %s
                        )
                        """,
                        (
                            tool_name,
                            _trim(manifest.get("display_name")) or tool_name,
                            _trim(manifest.get("description")),
                            normalized_owner,
                            json.dumps(manifest.get("capabilities") or ["custom_tool"], ensure_ascii=False),
                            f"artifact_revision:{revision_no}",
                            json.dumps(source_meta, ensure_ascii=False),
                            retrieval_text,
                            revision_no,
                            normalized_owner or "system",
                            normalized_owner or "system",
                        ),
                    )
                    artifact_id = int(cursor.lastrowid)
                cursor.execute(
                    f"""
                    INSERT INTO {REVISION_TABLE} (
                      artifact_id, revision_no, source_type, definition_json, schema_json,
                      spec_json, markdown_text, content_hash, change_summary, created_by
                    ) VALUES (%s, %s, 'agent_coding', %s, %s, %s, '', %s, %s, %s)
                    """,
                    (
                        artifact_id,
                        revision_no,
                        json.dumps(manifest, ensure_ascii=False),
                        json.dumps(schema_payload, ensure_ascii=False),
                        json.dumps(implementation_payload, ensure_ascii=False),
                        code_hash,
                        f"coding draft revision {revision_no}",
                        normalized_owner or "system",
                    ),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return self.load(tool_name)

    def load(self, tool_name: str) -> Dict[str, Any]:
        name = self._normalize_name(tool_name)
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {ARTIFACT_TABLE} WHERE artifact_type='custom_tool' AND name=%s AND version='v1' LIMIT 1",
                    (name,),
                )
                artifact = cursor.fetchone()
                if not artifact:
                    self._raise(f"custom tool not found: {name}")
                cursor.execute(
                    f"SELECT * FROM {REVISION_TABLE} WHERE artifact_id=%s AND revision_no=%s LIMIT 1",
                    (int(artifact["artifact_id"]), int(artifact.get("current_revision_no") or 0)),
                )
                revision = cursor.fetchone()
                if not revision:
                    self._raise(f"custom tool revision not found: {name}")
            manifest = self._json_dict(revision.get("definition_json"))
            schemas = self._json_dict(revision.get("schema_json"))
            spec = self._json_dict(revision.get("spec_json"))
            source_meta = self._json_dict(artifact.get("source_manifest_json"))
            manifest.update({
                "tool_name": name,
                "status": _trim(artifact.get("status")) or "draft",
                "owner_id": _trim(artifact.get("owner")),
                "current_revision": int(artifact.get("current_revision_no") or 0),
                "storage_kind": "database_code_module",
                "last_test": source_meta.get("last_test") if isinstance(source_meta.get("last_test"), Mapping) else {},
                "visibility": _trim(source_meta.get("visibility")) or _trim(manifest.get("visibility")) or "personal",
            })
            modules = ((spec.get("implementation") or {}).get("modules") or []) if isinstance(spec.get("implementation"), Mapping) else []
            entry_module = _trim((spec.get("implementation") or {}).get("entry_module")) if isinstance(spec.get("implementation"), Mapping) else ""
            source_code = ""
            for module in modules:
                if isinstance(module, Mapping) and (_trim(module.get("module_id")) == entry_module or not source_code):
                    source_code = _trim(module.get("source_code"))
                    if _trim(module.get("module_id")) == entry_module:
                        break
            return {
                "manifest": manifest,
                "input_schema": dict(schemas.get("input") or {}),
                "output_schema": dict(schemas.get("output") or {}),
                "code": source_code,
                "modules": [dict(item) for item in modules if isinstance(item, Mapping)],
                "sample_input": dict(spec.get("sample_input") or {}),
                "proposed_tests": [dict(item) for item in spec.get("proposed_tests") or [] if isinstance(item, Mapping)],
                "implementation_explanation": dict(spec.get("implementation_explanation") or {}),
                "implementation_review": dict(spec.get("implementation_review") or {}),
                "design_contract": dict(spec.get("design_contract") or {}),
                "design_provenance": dict(spec.get("design_provenance") or {}),
                "design_feedback_evidence": [dict(item) for item in spec.get("design_feedback_evidence") or [] if isinstance(item, Mapping)],
                "storage": {
                    "kind": "database_code_module",
                    "artifact_id": int(artifact["artifact_id"]),
                    "revision_id": int(revision["revision_id"]),
                },
            }
        finally:
            db.close()

    def list_tools(self, *, include_inactive: bool = False, owner_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        try:
            db = self.connection_factory()
        except RuntimeError:
            return []
        try:
            with db.cursor() as cursor:
                sql = f"SELECT name FROM {ARTIFACT_TABLE} WHERE artifact_type='custom_tool' AND version='v1'"
                params: list[Any] = []
                if not include_inactive:
                    sql += " AND status='active' AND enabled=1"
                if owner_ids is not None:
                    owners = [_trim(item) for item in owner_ids if _trim(item)]
                    if not owners:
                        return []
                    sql += " AND (owner IN (" + ",".join(["%s"] * len(owners)) + ") OR JSON_UNQUOTE(JSON_EXTRACT(source_manifest_json, '$.visibility'))='public')"
                    params.extend(owners)
                sql += " ORDER BY updated_at DESC, artifact_id DESC"
                cursor.execute(sql, tuple(params))
                names = [_trim(row.get("name")) for row in (cursor.fetchall() or [])]
            return [self.load(name)["manifest"] for name in names if name]
        finally:
            db.close()

    def load_for_runtime(self, tool_name: str, *, owner_ids: Optional[List[str]] = None, allow_inactive: bool = False) -> Dict[str, Any]:
        bundle = self.load(tool_name)
        manifest = bundle["manifest"]
        if not allow_inactive and _trim(manifest.get("status")) != "active":
            self._raise("custom tool is not active")
        if owner_ids is not None and _trim(manifest.get("visibility")) == "personal":
            allowed = {_trim(item) for item in owner_ids if _trim(item)}
            if _trim(manifest.get("owner_id")) not in allowed:
                self._raise("custom tool is not visible to current user")
        return bundle

    def commit(self, tool_name: str, *, owner_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        bundle = self.load_for_runtime(tool_name, owner_ids=owner_ids, allow_inactive=True)
        manifest = bundle["manifest"]
        if (manifest.get("last_test") or {}).get("execution_ok") is not True:
            self._raise("custom tool must complete a technical run before activation")
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {ARTIFACT_TABLE} SET status='active', enabled=1, updated_at=NOW() WHERE artifact_type='custom_tool' AND name=%s AND version='v1'",
                    (_trim(manifest.get("tool_name")),),
                )
            db.commit()
        finally:
            db.close()
        return self.load(_trim(manifest.get("tool_name")))

    def record_test(self, tool_name: str, result: Mapping[str, Any]) -> Dict[str, Any]:
        bundle = self.load(tool_name)
        artifact_id = int((bundle.get("storage") or {}).get("artifact_id") or 0)
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(f"SELECT source_manifest_json FROM {ARTIFACT_TABLE} WHERE artifact_id=%s FOR UPDATE", (artifact_id,))
                row = cursor.fetchone() or {}
                source_meta = self._json_dict(row.get("source_manifest_json"))
                source_meta["last_test"] = {
                    "ok": bool(result.get("ok")),
                    "execution_ok": bool(result.get("execution_ok", result.get("ok"))),
                    "contract_ok": bool(result.get("contract_ok", result.get("ok"))),
                    "error": _trim(result.get("error")),
                    "backend": _trim((((result.get("meta") or {}).get("diagnostics") or {}).get("backend"))),
                }
                cursor.execute(
                    f"UPDATE {ARTIFACT_TABLE} SET source_manifest_json=%s, updated_at=NOW() WHERE artifact_id=%s",
                    (json.dumps(source_meta, ensure_ascii=False), artifact_id),
                )
                if self._table_exists(cursor, TEST_RUN_TABLE):
                    aggregate_execution_ok = bool(result.get("execution_ok", result.get("ok")))
                    aggregate_contract_ok = bool(result.get("contract_ok", result.get("ok")))
                    aggregate_passed = aggregate_execution_ok and aggregate_contract_ok
                    supplied_cases = [dict(item) for item in result.get("cases") or [] if isinstance(item, Mapping)]
                    cases = supplied_cases or [{
                        "test_kind": "sample_smoke",
                        "status": "passed" if aggregate_passed else "failed",
                        "input": {},
                        "actual": dict(result.get("data") or {}),
                    }]
                    for case in cases:
                        test_kind = _trim(case.get("test_kind")) or "sample_smoke"
                        if test_kind not in {"sample_smoke", "fixture", "real_data", "regression"}:
                            test_kind = "regression"
                        execution_ok = bool(case.get("execution_ok", aggregate_execution_ok))
                        contract_ok = bool(case.get("contract_ok", aggregate_contract_ok))
                        # Legacy non-null storage column only; it is not read as a business gate.
                        legacy_business_ok = True
                        status = _trim(case.get("status")) or (
                            "passed" if execution_ok and contract_ok else "failed"
                        )
                        if status not in {"passed", "failed", "blocked"}:
                            status = "failed"
                        diagnostics = dict(case.get("diagnostics") or {})
                        if not diagnostics:
                            diagnostics = dict((result.get("meta") or {}).get("diagnostics") or {})
                        diagnostics.update({
                            "test_id": _trim(case.get("test_id")),
                            "category": _trim(case.get("category")),
                        })
                        cursor.execute(
                            f"""
                            INSERT INTO {TEST_RUN_TABLE} (
                              artifact_id, revision_no, test_kind, status,
                              execution_ok, contract_ok, business_ok,
                              input_json, output_json, error_text, diagnostics_json, created_by
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                artifact_id,
                                int((bundle.get("manifest") or {}).get("current_revision") or 0),
                                test_kind,
                                status,
                                int(execution_ok),
                                int(contract_ok),
                                int(legacy_business_ok),
                                json.dumps(case.get("input") or {}, ensure_ascii=False),
                                json.dumps(case.get("actual") or result.get("data") or {}, ensure_ascii=False),
                                _trim(case.get("error")) or _trim(result.get("error")),
                                json.dumps(diagnostics, ensure_ascii=False),
                                _trim((bundle.get("manifest") or {}).get("owner_id")) or "system",
                            ),
                        )
            db.commit()
        finally:
            db.close()
        return self.load(tool_name)

    def publish(
        self,
        tool_name: str,
        *,
        owner_ids: Optional[List[str]] = None,
        actor_id: str = "",
        actor_scopes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if "custom_tool:publish" not in {_trim(item) for item in actor_scopes or []}:
            self._raise("public publication requires custom_tool:publish permission")
        bundle = self.load_for_runtime(tool_name, owner_ids=owner_ids, allow_inactive=False)
        manifest = bundle["manifest"]
        if (manifest.get("last_test") or {}).get("execution_ok") is not True:
            self._raise("custom tool must complete a technical run before publication")
        artifact_id = int((bundle.get("storage") or {}).get("artifact_id") or 0)
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(f"SELECT source_manifest_json FROM {ARTIFACT_TABLE} WHERE artifact_id=%s FOR UPDATE", (artifact_id,))
                row = cursor.fetchone() or {}
                source_meta = self._json_dict(row.get("source_manifest_json"))
                source_meta.update({"visibility": "public", "published_by": _trim(actor_id) or "system"})
                cursor.execute(
                    f"UPDATE {ARTIFACT_TABLE} SET source_manifest_json=%s, updated_by=%s, updated_at=NOW() WHERE artifact_id=%s",
                    (json.dumps(source_meta, ensure_ascii=False), _trim(actor_id) or "system", artifact_id),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return self.load(tool_name)

    @staticmethod
    def _table_exists(cursor: Any, table_name: str) -> bool:
        cursor.execute("SELECT COUNT(*) AS count FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=%s", (table_name,))
        row = cursor.fetchone() or {}
        return int(row.get("count") or 0) > 0

    @staticmethod
    def _normalize_name(value: Any) -> str:
        import re
        raw = re.sub(r"[^a-z0-9_]+", "_", _trim(value).lower())
        raw = re.sub(r"_+", "_", raw).strip("_")
        if raw and not raw.startswith("ct_"):
            raw = f"ct_{raw}"
        return raw[:64]

    @staticmethod
    def _owner_scoped_name(tool_name: str, owner_id: str) -> str:
        suffix = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:8]
        return f"{tool_name[:55].rstrip('_')}_{suffix}"

    @staticmethod
    def _json_dict(value: Any) -> Dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, (str, bytes, bytearray)) and value:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        return {}

    def _raise(self, message: str) -> None:
        raise self.error_type(message)

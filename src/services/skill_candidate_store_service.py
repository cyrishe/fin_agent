from __future__ import annotations

from copy import deepcopy
import datetime as dt
import json
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional

import pymysql

from src.utils.system_db_utils import connect_system_db


ARTIFACT_TABLE = "aiia_runtime_artifact"
REVISION_TABLE = "aiia_runtime_artifact_revision"
SKILL_ARTIFACT_TYPE = "skill_v2"


def _trim(value: Any) -> str:
    return str(value or "").strip()


class SkillCandidateStoreError(RuntimeError):
    pass


class SkillCandidateNotFoundError(SkillCandidateStoreError):
    pass


class SkillCandidateConflictError(SkillCandidateStoreError):
    pass


class DatabaseSkillCandidateStoreService:
    """Owner-scoped immutable candidate revisions on the runtime registry.

    ``current_revision_no`` remains the published/active pointer. Candidate
    writes only move ``source_manifest_json.candidate_revision_no`` so an
    authoring request can never activate a Skill by accident.
    """

    def __init__(
        self,
        *,
        connection_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.connection_factory = connection_factory or self._connect

    @staticmethod
    def _connect() -> Any:
        return connect_system_db(
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    def create_candidate(
        self,
        candidate: Mapping[str, Any],
        *,
        owner_id: str,
    ) -> Dict[str, Any]:
        normalized_owner = self._require_owner(owner_id)
        payload = self._normalize_candidate(candidate, expected_revision=1)
        skill_id = payload["skill_id"]
        source_manifest = {
            "authoring_system": "skill_v2",
            "visibility": "private",
            "candidate_revision_no": 1,
            "active_revision_no": 0,
        }
        db = self._open_db()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {ARTIFACT_TABLE} (
                      artifact_type, name, version, status, display_name,
                      description, owner, domain, capabilities_json, tags_json,
                      keywords_json, side_effect_level, enabled,
                      implementation_kind, implementation_target,
                      source_manifest_json, retrieval_text, current_revision_no,
                      sync_status, created_by, updated_by
                    ) VALUES (
                      %s, %s, 'v1', 'draft', %s, %s, %s, 'skill_system',
                      %s, %s, %s, 'none', 0, 'cc_native_skill_candidate', '',
                      %s, %s, 0, 'pending', %s, %s
                    )
                    """,
                    (
                        SKILL_ARTIFACT_TYPE,
                        skill_id,
                        payload["display_name"][:128],
                        payload["description"],
                        normalized_owner,
                        json.dumps(["skill_method"], ensure_ascii=False),
                        json.dumps(["candidate", "cc_native"], ensure_ascii=False),
                        json.dumps([], ensure_ascii=False),
                        json.dumps(source_manifest, ensure_ascii=False),
                        self._retrieval_text(payload),
                        normalized_owner,
                        normalized_owner,
                    ),
                )
                artifact_id = int(getattr(cursor, "lastrowid", 0) or 0)
                if artifact_id < 1:
                    raise SkillCandidateStoreError("candidate artifact was not created")
                self._insert_revision(
                    cursor,
                    artifact_id=artifact_id,
                    candidate=payload,
                    owner_id=normalized_owner,
                )
            db.commit()
        except SkillCandidateStoreError:
            db.rollback()
            raise
        except pymysql.err.IntegrityError as exc:
            db.rollback()
            raise SkillCandidateConflictError(
                "Skill candidate identity already exists"
            ) from exc
        except Exception as exc:
            db.rollback()
            raise SkillCandidateStoreError("Skill candidate storage is unavailable") from exc
        finally:
            db.close()
        return self.load_revision(skill_id, 1, owner_id=normalized_owner)

    def save_revision(
        self,
        candidate: Mapping[str, Any],
        *,
        owner_id: str,
        expected_base_revision: int,
    ) -> Dict[str, Any]:
        normalized_owner = self._require_owner(owner_id)
        expected_base = int(expected_base_revision or 0)
        if expected_base < 1:
            raise SkillCandidateConflictError("base revision must be positive")
        skill_id = _trim(candidate.get("skill_id"))
        db = self._open_db()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT * FROM {ARTIFACT_TABLE}
                    WHERE artifact_type=%s AND name=%s AND version='v1'
                      AND owner=%s
                    LIMIT 1 FOR UPDATE
                    """,
                    (SKILL_ARTIFACT_TYPE, skill_id, normalized_owner),
                )
                artifact = cursor.fetchone()
                if not artifact:
                    raise SkillCandidateNotFoundError(
                        "Skill candidate does not exist or is not owned by this user"
                    )
                source_manifest = self._json_dict(artifact.get("source_manifest_json"))
                current_candidate = int(source_manifest.get("candidate_revision_no") or 0)
                if current_candidate != expected_base:
                    raise SkillCandidateConflictError(
                        f"Skill candidate changed: expected {expected_base}, current {current_candidate}"
                    )
                revision_no = current_candidate + 1
                payload = self._normalize_candidate(
                    candidate,
                    expected_revision=revision_no,
                    expected_skill_id=skill_id,
                )
                self._insert_revision(
                    cursor,
                    artifact_id=int(artifact["artifact_id"]),
                    candidate=payload,
                    owner_id=normalized_owner,
                )
                source_manifest["candidate_revision_no"] = revision_no
                source_manifest["active_revision_no"] = int(
                    artifact.get("current_revision_no") or 0
                )
                cursor.execute(
                    f"""
                    UPDATE {ARTIFACT_TABLE}
                    SET display_name=%s, description=%s, source_manifest_json=%s,
                        retrieval_text=%s, updated_by=%s, updated_at=NOW()
                    WHERE artifact_id=%s
                    """,
                    (
                        payload["display_name"][:128],
                        payload["description"],
                        json.dumps(source_manifest, ensure_ascii=False),
                        self._retrieval_text(payload),
                        normalized_owner,
                        int(artifact["artifact_id"]),
                    ),
                )
            db.commit()
        except SkillCandidateStoreError:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise SkillCandidateStoreError("Skill candidate storage is unavailable") from exc
        finally:
            db.close()
        return self.load_revision(
            skill_id,
            expected_base + 1,
            owner_id=normalized_owner,
        )

    def load_latest(self, skill_id: str, *, owner_id: str) -> Dict[str, Any]:
        normalized_owner = self._require_owner(owner_id)
        db = self._open_db()
        try:
            with db.cursor() as cursor:
                artifact = self._load_artifact_row(
                    cursor,
                    skill_id=skill_id,
                    owner_id=normalized_owner,
                )
                source_manifest = self._json_dict(artifact.get("source_manifest_json"))
                revision_no = int(source_manifest.get("candidate_revision_no") or 0)
                if revision_no < 1:
                    raise SkillCandidateNotFoundError("Skill candidate has no revision")
                revision = self._load_revision_row(
                    cursor,
                    artifact_id=int(artifact["artifact_id"]),
                    revision_no=revision_no,
                )
            return self._candidate_from_rows(artifact, revision)
        except SkillCandidateStoreError:
            raise
        except Exception as exc:
            raise SkillCandidateStoreError("Skill candidate storage is unavailable") from exc
        finally:
            db.close()

    def load_revision(
        self,
        skill_id: str,
        revision_no: int,
        *,
        owner_id: str,
    ) -> Dict[str, Any]:
        normalized_owner = self._require_owner(owner_id)
        requested_revision = int(revision_no or 0)
        if requested_revision < 1:
            raise SkillCandidateNotFoundError("Skill candidate revision is invalid")
        db = self._open_db()
        try:
            with db.cursor() as cursor:
                artifact = self._load_artifact_row(
                    cursor,
                    skill_id=skill_id,
                    owner_id=normalized_owner,
                )
                revision = self._load_revision_row(
                    cursor,
                    artifact_id=int(artifact["artifact_id"]),
                    revision_no=requested_revision,
                )
            return self._candidate_from_rows(artifact, revision)
        except SkillCandidateStoreError:
            raise
        except Exception as exc:
            raise SkillCandidateStoreError("Skill candidate storage is unavailable") from exc
        finally:
            db.close()

    def list_candidates(
        self,
        *,
        owner_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        normalized_owner = self._require_owner(owner_id)
        resolved_limit = min(50, max(1, int(limit or 20)))
        db = self._open_db()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT * FROM {ARTIFACT_TABLE}
                    WHERE artifact_type=%s AND version='v1' AND owner=%s
                    ORDER BY updated_at DESC, artifact_id DESC
                    LIMIT %s
                    """,
                    (SKILL_ARTIFACT_TYPE, normalized_owner, resolved_limit),
                )
                artifacts = list(cursor.fetchall() or [])
                results: List[Dict[str, Any]] = []
                for artifact in artifacts:
                    source_manifest = self._json_dict(artifact.get("source_manifest_json"))
                    revision_no = int(source_manifest.get("candidate_revision_no") or 0)
                    if revision_no < 1:
                        continue
                    try:
                        revision = self._load_revision_row(
                            cursor,
                            artifact_id=int(artifact["artifact_id"]),
                            revision_no=revision_no,
                        )
                    except SkillCandidateNotFoundError:
                        continue
                    results.append(self._candidate_from_rows(artifact, revision))
            return results
        except SkillCandidateStoreError:
            raise
        except Exception as exc:
            raise SkillCandidateStoreError("Skill candidate storage is unavailable") from exc
        finally:
            db.close()

    @staticmethod
    def _require_owner(value: Any) -> str:
        owner = _trim(value)
        if not owner:
            raise SkillCandidateStoreError("owner identity is required")
        return owner

    def _open_db(self) -> Any:
        try:
            return self.connection_factory()
        except Exception as exc:
            raise SkillCandidateStoreError(
                "Skill candidate storage is unavailable"
            ) from exc

    @classmethod
    def _normalize_candidate(
        cls,
        candidate: Mapping[str, Any],
        *,
        expected_revision: int,
        expected_skill_id: str = "",
    ) -> Dict[str, Any]:
        payload = deepcopy(dict(candidate or {}))
        skill_id = _trim(payload.get("skill_id"))
        if not skill_id or (expected_skill_id and skill_id != expected_skill_id):
            raise SkillCandidateConflictError("Skill candidate identity changed")
        if int(payload.get("revision_no") or 0) != int(expected_revision):
            raise SkillCandidateConflictError("Skill candidate revision changed")
        if not _trim(payload.get("skill_markdown")):
            raise SkillCandidateStoreError("Skill markdown is required")
        if not _trim(payload.get("content_hash")):
            raise SkillCandidateStoreError("Skill content hash is required")
        payload["display_name"] = _trim(payload.get("display_name")) or skill_id
        payload["description"] = _trim(payload.get("description"))
        payload["change_summary"] = _trim(payload.get("change_summary"))[:240]
        payload["requirement"] = _trim(payload.get("requirement"))
        payload["feedback"] = _trim(payload.get("feedback"))
        payload["control_manifest"] = dict(payload.get("control_manifest") or {})
        payload["flowchart"] = dict(payload.get("flowchart") or {})
        payload["authoring_evidence"] = dict(payload.get("authoring_evidence") or {})
        payload["resolution_notes"] = [
            _trim(item)
            for item in payload.get("resolution_notes") or []
            if _trim(item)
        ]
        return payload

    @classmethod
    def _insert_revision(
        cls,
        cursor: Any,
        *,
        artifact_id: int,
        candidate: Mapping[str, Any],
        owner_id: str,
    ) -> None:
        definition = {
            "skill_id": candidate["skill_id"],
            "display_name": candidate["display_name"],
            "description": candidate["description"],
            "base_revision_no": int(candidate.get("base_revision_no") or 0),
            "control_manifest": dict(candidate.get("control_manifest") or {}),
            "flowchart": dict(candidate.get("flowchart") or {}),
        }
        authoring_spec = {
            "requirement": candidate.get("requirement") or "",
            "feedback": candidate.get("feedback") or "",
            "authoring_evidence": dict(candidate.get("authoring_evidence") or {}),
            "resolution_notes": list(candidate.get("resolution_notes") or []),
        }
        cursor.execute(
            f"""
            INSERT INTO {REVISION_TABLE} (
              artifact_id, revision_no, source_type, definition_json,
              schema_json, spec_json, markdown_text, content_hash,
              change_summary, created_by
            ) VALUES (%s, %s, 'cc_skill_authoring', %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(artifact_id),
                int(candidate["revision_no"]),
                json.dumps(definition, ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
                json.dumps(authoring_spec, ensure_ascii=False),
                candidate["skill_markdown"],
                candidate["content_hash"],
                _trim(candidate.get("change_summary"))[:255],
                owner_id,
            ),
        )

    @classmethod
    def _load_artifact_row(
        cls,
        cursor: Any,
        *,
        skill_id: str,
        owner_id: str,
    ) -> Mapping[str, Any]:
        cursor.execute(
            f"""
            SELECT * FROM {ARTIFACT_TABLE}
            WHERE artifact_type=%s AND name=%s AND version='v1' AND owner=%s
            LIMIT 1
            """,
            (SKILL_ARTIFACT_TYPE, _trim(skill_id), owner_id),
        )
        artifact = cursor.fetchone()
        if not artifact:
            raise SkillCandidateNotFoundError(
                "Skill candidate does not exist or is not owned by this user"
            )
        return artifact

    @staticmethod
    def _load_revision_row(
        cursor: Any,
        *,
        artifact_id: int,
        revision_no: int,
    ) -> Mapping[str, Any]:
        cursor.execute(
            f"""
            SELECT * FROM {REVISION_TABLE}
            WHERE artifact_id=%s AND revision_no=%s
            LIMIT 1
            """,
            (int(artifact_id), int(revision_no)),
        )
        revision = cursor.fetchone()
        if not revision:
            raise SkillCandidateNotFoundError("Skill candidate revision does not exist")
        return revision

    @classmethod
    def _candidate_from_rows(
        cls,
        artifact: Mapping[str, Any],
        revision: Mapping[str, Any],
    ) -> Dict[str, Any]:
        definition = cls._json_dict(revision.get("definition_json"))
        spec = cls._json_dict(revision.get("spec_json"))
        source_manifest = cls._json_dict(artifact.get("source_manifest_json"))
        created_at = revision.get("created_at")
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        return {
            "skill_id": _trim(artifact.get("name")),
            "display_name": _trim(definition.get("display_name"))
            or _trim(artifact.get("display_name")),
            "description": _trim(definition.get("description"))
            or _trim(artifact.get("description")),
            "revision_no": int(revision.get("revision_no") or 0),
            "base_revision_no": int(definition.get("base_revision_no") or 0),
            "active_revision_no": int(artifact.get("current_revision_no") or 0),
            "candidate_revision_no": int(
                source_manifest.get("candidate_revision_no") or 0
            ),
            "owner_id": _trim(artifact.get("owner")),
            "skill_markdown": _trim(revision.get("markdown_text")),
            "control_manifest": dict(definition.get("control_manifest") or {}),
            "flowchart": dict(definition.get("flowchart") or {}),
            "requirement": _trim(spec.get("requirement")),
            "feedback": _trim(spec.get("feedback")),
            "change_summary": _trim(revision.get("change_summary")),
            "content_hash": _trim(revision.get("content_hash")),
            "authoring_evidence": dict(spec.get("authoring_evidence") or {}),
            "resolution_notes": list(spec.get("resolution_notes") or []),
            "created_at": created_at or "",
            "published": False,
        }

    @staticmethod
    def _json_dict(value: Any) -> Dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", errors="replace")
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        return {}

    @staticmethod
    def _retrieval_text(candidate: Mapping[str, Any]) -> str:
        return "\n".join(
            item
            for item in (
                _trim(candidate.get("skill_id")),
                _trim(candidate.get("display_name")),
                _trim(candidate.get("description")),
                _trim(candidate.get("requirement")),
            )
            if item
        )


class InMemorySkillCandidateStoreService:
    """Small deterministic store for focused tests and local service probes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, Dict[int, Dict[str, Any]]] = {}

    def create_candidate(
        self,
        candidate: Mapping[str, Any],
        *,
        owner_id: str,
    ) -> Dict[str, Any]:
        payload = deepcopy(dict(candidate))
        skill_id = _trim(payload.get("skill_id"))
        owner = _trim(owner_id)
        with self._lock:
            if not skill_id or skill_id in self._records:
                raise SkillCandidateConflictError("Skill candidate identity already exists")
            payload.update(
                {
                    "owner_id": owner,
                    "candidate_revision_no": 1,
                    "active_revision_no": 0,
                    "published": False,
                    "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            )
            self._records[skill_id] = {1: payload}
        return deepcopy(payload)

    def save_revision(
        self,
        candidate: Mapping[str, Any],
        *,
        owner_id: str,
        expected_base_revision: int,
    ) -> Dict[str, Any]:
        payload = deepcopy(dict(candidate))
        skill_id = _trim(payload.get("skill_id"))
        with self._lock:
            revisions = self._records.get(skill_id)
            if not revisions or _trim(next(iter(revisions.values())).get("owner_id")) != _trim(owner_id):
                raise SkillCandidateNotFoundError(
                    "Skill candidate does not exist or is not owned by this user"
                )
            current = max(revisions)
            if current != int(expected_base_revision or 0):
                raise SkillCandidateConflictError(
                    f"Skill candidate changed: expected {expected_base_revision}, current {current}"
                )
            revision_no = current + 1
            if int(payload.get("revision_no") or 0) != revision_no:
                raise SkillCandidateConflictError("Skill candidate revision changed")
            payload.update(
                {
                    "owner_id": _trim(owner_id),
                    "candidate_revision_no": revision_no,
                    "active_revision_no": 0,
                    "published": False,
                    "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            )
            revisions[revision_no] = payload
        return deepcopy(payload)

    def load_latest(self, skill_id: str, *, owner_id: str) -> Dict[str, Any]:
        with self._lock:
            revisions = self._owned(skill_id, owner_id)
            return deepcopy(revisions[max(revisions)])

    def load_revision(
        self,
        skill_id: str,
        revision_no: int,
        *,
        owner_id: str,
    ) -> Dict[str, Any]:
        with self._lock:
            revisions = self._owned(skill_id, owner_id)
            record = revisions.get(int(revision_no or 0))
            if not record:
                raise SkillCandidateNotFoundError("Skill candidate revision does not exist")
            return deepcopy(record)

    def list_candidates(
        self,
        *,
        owner_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [
                deepcopy(revisions[max(revisions)])
                for revisions in self._records.values()
                if revisions
                and _trim(next(iter(revisions.values())).get("owner_id")) == _trim(owner_id)
            ]
        rows.sort(key=lambda item: _trim(item.get("created_at")), reverse=True)
        return rows[: min(50, max(1, int(limit or 20)))]

    def _owned(self, skill_id: str, owner_id: str) -> Dict[int, Dict[str, Any]]:
        revisions = self._records.get(_trim(skill_id))
        if not revisions or _trim(next(iter(revisions.values())).get("owner_id")) != _trim(owner_id):
            raise SkillCandidateNotFoundError(
                "Skill candidate does not exist or is not owned by this user"
            )
        return revisions

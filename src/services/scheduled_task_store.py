from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
from copy import deepcopy
from typing import Any, Callable, Dict, Mapping, Protocol

import pymysql

from src.services.scheduled_task_protocol import CronExpression, ensure_utc
from src.utils.system_db_utils import connect_system_db


SCHEDULE_TABLE = "aiia_scheduled_task"
RUN_TABLE = "aiia_scheduled_task_run"


class ScheduledTaskStore(Protocol):
    def create(
        self,
        *,
        owner_user_id: str,
        draft: Mapping[str, Any],
        idempotency_key: str = "",
    ) -> Dict[str, Any]: ...

    def list_for_owner(self, *, owner_user_id: str) -> list[Dict[str, Any]]: ...

    def get_for_owner(self, *, schedule_id: str, owner_user_id: str) -> Dict[str, Any] | None: ...

    def update(
        self,
        *,
        schedule_id: str,
        owner_user_id: str,
        draft: Mapping[str, Any] | None = None,
        enabled: bool | None = None,
        now: dt.datetime | None = None,
    ) -> Dict[str, Any] | None: ...

    def materialize_due(self, *, now: dt.datetime | None = None, limit: int = 100) -> int: ...

    def enqueue_manual(
        self,
        *,
        schedule_id: str,
        owner_user_id: str,
        now: dt.datetime | None = None,
    ) -> Dict[str, Any] | None: ...

    def list_runs_for_owner(
        self,
        *,
        schedule_id: str,
        owner_user_id: str,
        limit: int = 50,
    ) -> list[Dict[str, Any]]: ...

    def get_run_for_owner(
        self,
        *,
        run_id: str,
        owner_user_id: str,
    ) -> Dict[str, Any] | None: ...

    def claim(
        self,
        *,
        worker_id: str,
        now: dt.datetime | None = None,
        lease_seconds: int = 3600,
    ) -> Dict[str, Any] | None: ...

    def renew_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        now: dt.datetime | None = None,
        lease_seconds: int = 3600,
    ) -> bool: ...

    def finish(
        self,
        *,
        run_id: str,
        worker_id: str,
        result: Mapping[str, Any] | None = None,
        error_text: str = "",
        now: dt.datetime | None = None,
    ) -> bool: ...


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _db_time(value: dt.datetime | None) -> dt.datetime:
    return ensure_utc(value).replace(tzinfo=None)


def _utc_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, dt.datetime):
        return None
    return ensure_utc(value)


def _json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except Exception:
            return {}
    return {}


def _schedule_from_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schedule_id": str(row.get("schedule_id") or ""),
        "owner_user_id": str(row.get("owner_user_id") or ""),
        "schema_version": "scheduled_task.v1",
        "requirement_brief": str(row.get("requirement_brief") or ""),
        "trigger": {
            "cron": str(row.get("cron_expr") or ""),
            "timezone": str(row.get("timezone") or "Asia/Shanghai"),
        },
        "execution_plan": _json_dict(row.get("execution_plan_json")),
        "enabled": bool(row.get("enabled")),
        "revision_no": int(row.get("revision_no") or 1),
        "next_run_at": _utc_time(row.get("next_run_at")),
        "last_run_at": _utc_time(row.get("last_run_at")),
        "created_at": _utc_time(row.get("created_at")),
        "updated_at": _utc_time(row.get("updated_at")),
    }


def _run_from_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": str(row.get("run_id") or ""),
        "schedule_id": str(row.get("schedule_id") or ""),
        "owner_user_id": str(row.get("owner_user_id") or ""),
        "schedule_revision_no": int(row.get("schedule_revision_no") or 1),
        "requirement_brief": str(row.get("requirement_brief") or ""),
        "execution_plan": _json_dict(row.get("execution_plan_json")),
        "scheduled_for": _utc_time(row.get("scheduled_for")),
        "status": str(row.get("status") or ""),
        "lease_owner": str(row.get("lease_owner") or ""),
        "lease_until": _utc_time(row.get("lease_until")),
        "result": _json_dict(row.get("result_json")),
        "error_text": str(row.get("error_text") or ""),
        "started_at": _utc_time(row.get("started_at")),
        "finished_at": _utc_time(row.get("finished_at")),
        "created_at": _utc_time(row.get("created_at")),
        "updated_at": _utc_time(row.get("updated_at")),
    }


class MySqlScheduledTaskStore:
    """Durable schedule store. All public lookups include owner scope."""

    def __init__(self, connection_factory: Callable[[], Any] | None = None) -> None:
        self.connection_factory = connection_factory or self._connect

    @staticmethod
    def _connect() -> Any:
        db = connect_system_db(
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
        with db.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")
        return db

    def create(
        self,
        *,
        owner_user_id: str,
        draft: Mapping[str, Any],
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        schedule_id = _new_id("sch")
        key = str(idempotency_key or "").strip() or None
        trigger = dict(draft["trigger"])
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                if key:
                    cursor.execute(
                        f"SELECT * FROM {SCHEDULE_TABLE} WHERE owner_user_id=%s AND idempotency_key=%s LIMIT 1",
                        (owner_user_id, key),
                    )
                    existing = cursor.fetchone()
                    if existing:
                        db.rollback()
                        return _schedule_from_row(existing)
                cursor.execute(
                    f"""
                    INSERT INTO {SCHEDULE_TABLE} (
                      schedule_id, owner_user_id, requirement_brief, timezone,
                      cron_expr, execution_plan_json, enabled, revision_no,
                      next_run_at, idempotency_key
                    ) VALUES (%s, %s, %s, %s, %s, %s, 1, 1, %s, %s)
                    """,
                    (
                        schedule_id,
                        owner_user_id,
                        str(draft["requirement_brief"]),
                        str(trigger["timezone"]),
                        str(trigger["cron"]),
                        json.dumps(draft["execution_plan"], ensure_ascii=False),
                        _db_time(draft["next_run_at"]),
                        key,
                    ),
                )
                cursor.execute(
                    f"SELECT * FROM {SCHEDULE_TABLE} WHERE schedule_id=%s",
                    (schedule_id,),
                )
                row = cursor.fetchone()
            db.commit()
            return _schedule_from_row(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_for_owner(self, *, owner_user_id: str) -> list[Dict[str, Any]]:
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {SCHEDULE_TABLE} WHERE owner_user_id=%s ORDER BY updated_at DESC",
                    (owner_user_id,),
                )
                return [_schedule_from_row(row) for row in cursor.fetchall()]
        finally:
            db.close()

    def get_for_owner(self, *, schedule_id: str, owner_user_id: str) -> Dict[str, Any] | None:
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {SCHEDULE_TABLE} WHERE schedule_id=%s AND owner_user_id=%s LIMIT 1",
                    (schedule_id, owner_user_id),
                )
                row = cursor.fetchone()
                return _schedule_from_row(row) if row else None
        finally:
            db.close()

    def update(
        self,
        *,
        schedule_id: str,
        owner_user_id: str,
        draft: Mapping[str, Any] | None = None,
        enabled: bool | None = None,
        now: dt.datetime | None = None,
    ) -> Dict[str, Any] | None:
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {SCHEDULE_TABLE} WHERE schedule_id=%s AND owner_user_id=%s LIMIT 1 FOR UPDATE",
                    (schedule_id, owner_user_id),
                )
                row = cursor.fetchone()
                if not row:
                    db.rollback()
                    return None
                new_enabled = bool(row["enabled"]) if enabled is None else bool(enabled)
                if draft is not None:
                    trigger = dict(draft["trigger"])
                    next_run_at = draft["next_run_at"] if new_enabled else None
                    cursor.execute(
                        f"""
                        UPDATE {SCHEDULE_TABLE}
                        SET requirement_brief=%s, timezone=%s, cron_expr=%s,
                            execution_plan_json=%s, enabled=%s,
                            revision_no=revision_no+1, next_run_at=%s
                        WHERE schedule_id=%s AND owner_user_id=%s
                        """,
                        (
                            str(draft["requirement_brief"]),
                            str(trigger["timezone"]),
                            str(trigger["cron"]),
                            json.dumps(draft["execution_plan"], ensure_ascii=False),
                            int(new_enabled),
                            _db_time(next_run_at) if next_run_at else None,
                            schedule_id,
                            owner_user_id,
                        ),
                    )
                else:
                    if new_enabled:
                        cron = CronExpression(str(row["cron_expr"]))
                        next_run_at = cron.next_after(
                            ensure_utc(now),
                            timezone=str(row["timezone"]),
                        )
                    else:
                        next_run_at = None
                    cursor.execute(
                        f"""
                        UPDATE {SCHEDULE_TABLE}
                        SET enabled=%s, next_run_at=%s
                        WHERE schedule_id=%s AND owner_user_id=%s
                        """,
                        (
                            int(new_enabled),
                            _db_time(next_run_at) if next_run_at else None,
                            schedule_id,
                            owner_user_id,
                        ),
                    )
                cursor.execute(
                    f"SELECT * FROM {SCHEDULE_TABLE} WHERE schedule_id=%s",
                    (schedule_id,),
                )
                updated = cursor.fetchone()
            db.commit()
            return _schedule_from_row(updated)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def materialize_due(self, *, now: dt.datetime | None = None, limit: int = 100) -> int:
        current = ensure_utc(now)
        created = 0
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT * FROM {SCHEDULE_TABLE}
                    WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=%s
                    ORDER BY next_run_at ASC
                    LIMIT %s FOR UPDATE SKIP LOCKED
                    """,
                    (_db_time(current), max(1, min(int(limit), 1000))),
                )
                rows = list(cursor.fetchall())
                for row in rows:
                    scheduled_for = _utc_time(row["next_run_at"]) or current
                    cursor.execute(
                        f"""
                        INSERT IGNORE INTO {RUN_TABLE} (
                          run_id, schedule_id, owner_user_id, schedule_revision_no,
                          requirement_brief, execution_plan_json, scheduled_for, status
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                        """,
                        (
                            _new_id("run"),
                            row["schedule_id"],
                            row["owner_user_id"],
                            int(row["revision_no"]),
                            row["requirement_brief"],
                            row["execution_plan_json"],
                            _db_time(scheduled_for),
                        ),
                    )
                    created += int(cursor.rowcount > 0)
                    next_run = CronExpression(str(row["cron_expr"])).next_after(
                        current,
                        timezone=str(row["timezone"]),
                    )
                    cursor.execute(
                        f"""
                        UPDATE {SCHEDULE_TABLE}
                        SET last_run_at=%s, next_run_at=%s
                        WHERE schedule_id=%s
                        """,
                        (
                            _db_time(scheduled_for),
                            _db_time(next_run),
                            row["schedule_id"],
                        ),
                    )
            db.commit()
            return created
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def enqueue_manual(
        self,
        *,
        schedule_id: str,
        owner_user_id: str,
        now: dt.datetime | None = None,
    ) -> Dict[str, Any] | None:
        scheduled_for = ensure_utc(now)
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {SCHEDULE_TABLE} WHERE schedule_id=%s AND owner_user_id=%s LIMIT 1",
                    (schedule_id, owner_user_id),
                )
                row = cursor.fetchone()
                if not row:
                    db.rollback()
                    return None
                run_id = _new_id("run")
                cursor.execute(
                    f"""
                    INSERT INTO {RUN_TABLE} (
                      run_id, schedule_id, owner_user_id, schedule_revision_no,
                      requirement_brief, execution_plan_json, scheduled_for, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                    """,
                    (
                        run_id,
                        schedule_id,
                        owner_user_id,
                        int(row["revision_no"]),
                        row["requirement_brief"],
                        row["execution_plan_json"],
                        _db_time(scheduled_for),
                    ),
                )
                cursor.execute(f"SELECT * FROM {RUN_TABLE} WHERE run_id=%s", (run_id,))
                run = cursor.fetchone()
            db.commit()
            return _run_from_row(run)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_runs_for_owner(
        self,
        *,
        schedule_id: str,
        owner_user_id: str,
        limit: int = 50,
    ) -> list[Dict[str, Any]]:
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT * FROM {RUN_TABLE}
                    WHERE schedule_id=%s AND owner_user_id=%s
                    ORDER BY scheduled_for DESC LIMIT %s
                    """,
                    (schedule_id, owner_user_id, max(1, min(int(limit), 200))),
                )
                return [_run_from_row(row) for row in cursor.fetchall()]
        finally:
            db.close()

    def get_run_for_owner(
        self,
        *,
        run_id: str,
        owner_user_id: str,
    ) -> Dict[str, Any] | None:
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {RUN_TABLE} WHERE run_id=%s AND owner_user_id=%s LIMIT 1",
                    (run_id, owner_user_id),
                )
                row = cursor.fetchone()
                return _run_from_row(row) if row else None
        finally:
            db.close()

    def claim(
        self,
        *,
        worker_id: str,
        now: dt.datetime | None = None,
        lease_seconds: int = 3600,
    ) -> Dict[str, Any] | None:
        current = ensure_utc(now)
        lease_until = current + dt.timedelta(seconds=max(30, int(lease_seconds)))
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT * FROM {RUN_TABLE}
                    WHERE status='pending'
                       OR (status='running' AND lease_until<%s)
                    ORDER BY scheduled_for ASC
                    LIMIT 1 FOR UPDATE SKIP LOCKED
                    """,
                    (_db_time(current),),
                )
                row = cursor.fetchone()
                if not row:
                    db.rollback()
                    return None
                cursor.execute(
                    f"""
                    UPDATE {RUN_TABLE}
                    SET status='running', lease_owner=%s, lease_until=%s,
                        started_at=COALESCE(started_at, %s), error_text=NULL
                    WHERE run_id=%s
                    """,
                    (
                        worker_id,
                        _db_time(lease_until),
                        _db_time(current),
                        row["run_id"],
                    ),
                )
                cursor.execute(f"SELECT * FROM {RUN_TABLE} WHERE run_id=%s", (row["run_id"],))
                claimed = cursor.fetchone()
            db.commit()
            return _run_from_row(claimed)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def renew_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        now: dt.datetime | None = None,
        lease_seconds: int = 3600,
    ) -> bool:
        lease_until = ensure_utc(now) + dt.timedelta(seconds=max(30, int(lease_seconds)))
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {RUN_TABLE} SET lease_until=%s
                    WHERE run_id=%s AND status='running' AND lease_owner=%s
                    """,
                    (_db_time(lease_until), run_id, worker_id),
                )
                updated = cursor.rowcount == 1
            db.commit()
            return updated
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def finish(
        self,
        *,
        run_id: str,
        worker_id: str,
        result: Mapping[str, Any] | None = None,
        error_text: str = "",
        now: dt.datetime | None = None,
    ) -> bool:
        status = "failed" if str(error_text or "").strip() else "completed"
        db = self.connection_factory()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {RUN_TABLE}
                    SET status=%s, result_json=%s, error_text=%s,
                        finished_at=%s, lease_owner=NULL, lease_until=NULL
                    WHERE run_id=%s AND status='running' AND lease_owner=%s
                    """,
                    (
                        status,
                        json.dumps(dict(result or {}), ensure_ascii=False),
                        str(error_text or "").strip() or None,
                        _db_time(now),
                        run_id,
                        worker_id,
                    ),
                )
                updated = cursor.rowcount == 1
            db.commit()
            return updated
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class InMemoryScheduledTaskStore:
    """Contract-equivalent store used by protocol and worker tests."""

    def __init__(self) -> None:
        self.schedules: Dict[str, Dict[str, Any]] = {}
        self.runs: Dict[str, Dict[str, Any]] = {}
        self._idempotency: Dict[tuple[str, str], str] = {}
        self._lock = threading.RLock()

    def create(
        self,
        *,
        owner_user_id: str,
        draft: Mapping[str, Any],
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        with self._lock:
            key = str(idempotency_key or "").strip()
            if key and (owner_user_id, key) in self._idempotency:
                return deepcopy(self.schedules[self._idempotency[(owner_user_id, key)]])
            now = ensure_utc(None)
            schedule_id = _new_id("sch")
            record = {
                "schedule_id": schedule_id,
                "owner_user_id": owner_user_id,
                "schema_version": "scheduled_task.v1",
                "requirement_brief": str(draft["requirement_brief"]),
                "trigger": deepcopy(dict(draft["trigger"])),
                "execution_plan": deepcopy(dict(draft["execution_plan"])),
                "enabled": True,
                "revision_no": 1,
                "next_run_at": ensure_utc(draft["next_run_at"]),
                "last_run_at": None,
                "created_at": now,
                "updated_at": now,
            }
            self.schedules[schedule_id] = record
            if key:
                self._idempotency[(owner_user_id, key)] = schedule_id
            return deepcopy(record)

    def list_for_owner(self, *, owner_user_id: str) -> list[Dict[str, Any]]:
        with self._lock:
            rows = [
                deepcopy(item)
                for item in self.schedules.values()
                if item["owner_user_id"] == owner_user_id
            ]
            return sorted(rows, key=lambda item: item["updated_at"], reverse=True)

    def get_for_owner(self, *, schedule_id: str, owner_user_id: str) -> Dict[str, Any] | None:
        with self._lock:
            item = self.schedules.get(schedule_id)
            return deepcopy(item) if item and item["owner_user_id"] == owner_user_id else None

    def update(
        self,
        *,
        schedule_id: str,
        owner_user_id: str,
        draft: Mapping[str, Any] | None = None,
        enabled: bool | None = None,
        now: dt.datetime | None = None,
    ) -> Dict[str, Any] | None:
        with self._lock:
            item = self.schedules.get(schedule_id)
            if not item or item["owner_user_id"] != owner_user_id:
                return None
            if enabled is not None:
                item["enabled"] = bool(enabled)
            if draft is not None:
                item.update(
                    {
                        "requirement_brief": str(draft["requirement_brief"]),
                        "trigger": deepcopy(dict(draft["trigger"])),
                        "execution_plan": deepcopy(dict(draft["execution_plan"])),
                        "next_run_at": ensure_utc(draft["next_run_at"]) if item["enabled"] else None,
                        "revision_no": int(item["revision_no"]) + 1,
                    }
                )
            elif item["enabled"]:
                item["next_run_at"] = CronExpression(item["trigger"]["cron"]).next_after(
                    ensure_utc(now),
                    timezone=item["trigger"]["timezone"],
                )
            else:
                item["next_run_at"] = None
            item["updated_at"] = ensure_utc(now)
            return deepcopy(item)

    def materialize_due(self, *, now: dt.datetime | None = None, limit: int = 100) -> int:
        current = ensure_utc(now)
        created = 0
        with self._lock:
            due = sorted(
                (
                    item for item in self.schedules.values()
                    if item["enabled"] and item["next_run_at"] and item["next_run_at"] <= current
                ),
                key=lambda item: item["next_run_at"],
            )[: max(1, int(limit))]
            for item in due:
                slot = item["next_run_at"]
                exists = any(
                    run["schedule_id"] == item["schedule_id"] and run["scheduled_for"] == slot
                    for run in self.runs.values()
                )
                if not exists:
                    self._create_run(item=item, scheduled_for=slot)
                    created += 1
                item["last_run_at"] = slot
                item["next_run_at"] = CronExpression(item["trigger"]["cron"]).next_after(
                    current,
                    timezone=item["trigger"]["timezone"],
                )
            return created

    def enqueue_manual(
        self,
        *,
        schedule_id: str,
        owner_user_id: str,
        now: dt.datetime | None = None,
    ) -> Dict[str, Any] | None:
        with self._lock:
            item = self.schedules.get(schedule_id)
            if not item or item["owner_user_id"] != owner_user_id:
                return None
            return deepcopy(self._create_run(item=item, scheduled_for=ensure_utc(now)))

    def list_runs_for_owner(
        self,
        *,
        schedule_id: str,
        owner_user_id: str,
        limit: int = 50,
    ) -> list[Dict[str, Any]]:
        with self._lock:
            rows = [
                deepcopy(run)
                for run in self.runs.values()
                if run["schedule_id"] == schedule_id
                and run["owner_user_id"] == owner_user_id
            ]
            return sorted(
                rows,
                key=lambda run: run["scheduled_for"],
                reverse=True,
            )[: max(1, min(int(limit), 200))]

    def get_run_for_owner(
        self,
        *,
        run_id: str,
        owner_user_id: str,
    ) -> Dict[str, Any] | None:
        with self._lock:
            run = self.runs.get(run_id)
            return deepcopy(run) if run and run["owner_user_id"] == owner_user_id else None

    def claim(
        self,
        *,
        worker_id: str,
        now: dt.datetime | None = None,
        lease_seconds: int = 3600,
    ) -> Dict[str, Any] | None:
        current = ensure_utc(now)
        with self._lock:
            candidates = sorted(
                (
                    run for run in self.runs.values()
                    if run["status"] == "pending"
                    or (
                        run["status"] == "running"
                        and run["lease_until"]
                        and run["lease_until"] < current
                    )
                ),
                key=lambda run: run["scheduled_for"],
            )
            if not candidates:
                return None
            run = candidates[0]
            run["status"] = "running"
            run["lease_owner"] = worker_id
            run["lease_until"] = current + dt.timedelta(seconds=max(30, int(lease_seconds)))
            run["started_at"] = run["started_at"] or current
            run["error_text"] = ""
            run["updated_at"] = current
            return deepcopy(run)

    def renew_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        now: dt.datetime | None = None,
        lease_seconds: int = 3600,
    ) -> bool:
        with self._lock:
            run = self.runs.get(run_id)
            if not run or run["status"] != "running" or run["lease_owner"] != worker_id:
                return False
            run["lease_until"] = ensure_utc(now) + dt.timedelta(seconds=max(30, int(lease_seconds)))
            return True

    def finish(
        self,
        *,
        run_id: str,
        worker_id: str,
        result: Mapping[str, Any] | None = None,
        error_text: str = "",
        now: dt.datetime | None = None,
    ) -> bool:
        with self._lock:
            run = self.runs.get(run_id)
            if not run or run["status"] != "running" or run["lease_owner"] != worker_id:
                return False
            run["status"] = "failed" if str(error_text or "").strip() else "completed"
            run["result"] = deepcopy(dict(result or {}))
            run["error_text"] = str(error_text or "").strip()
            run["finished_at"] = ensure_utc(now)
            run["lease_owner"] = ""
            run["lease_until"] = None
            run["updated_at"] = ensure_utc(now)
            return True

    def _create_run(
        self,
        *,
        item: Mapping[str, Any],
        scheduled_for: dt.datetime,
    ) -> Dict[str, Any]:
        now = ensure_utc(None)
        run_id = _new_id("run")
        run = {
            "run_id": run_id,
            "schedule_id": item["schedule_id"],
            "owner_user_id": item["owner_user_id"],
            "schedule_revision_no": int(item["revision_no"]),
            "requirement_brief": item["requirement_brief"],
            "execution_plan": deepcopy(dict(item["execution_plan"])),
            "scheduled_for": scheduled_for,
            "status": "pending",
            "lease_owner": "",
            "lease_until": None,
            "result": {},
            "error_text": "",
            "started_at": None,
            "finished_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self.runs[run_id] = run
        return run

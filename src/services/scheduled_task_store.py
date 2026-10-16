from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

import pymysql

from src.services.scheduled_task_protocol import CronExpression, ensure_utc
from src.utils.system_db_utils import connect_system_db

SCHEDULE_TABLE = "aiia_scheduled_task"
RUN_TABLE = "aiia_scheduled_task_run"


class TaskIdempotencyConflict(ValueError):
    """An existing request key cannot be reused for a different task."""


class ScheduledTaskStore(Protocol):
    def create(self, **kwargs) -> dict: ...
    def claim(self, **kwargs) -> dict | None: ...
    def renew_lease(self, **kwargs) -> bool: ...
    def finish(self, **kwargs) -> bool: ...


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _time(value=None):
    return ensure_utc(value).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def _utc(value):
    if not value:
        return None
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ensure_utc(value)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _dict(value):
    return dict(value) if isinstance(value, Mapping) else json.loads(value or "{}")


def _fingerprint(draft):
    return hashlib.sha256(_json({k: draft.get(k) for k in ("requirement_brief", "trigger", "execution_plan", "budget", "source_ref")}).encode()).hexdigest()


def _definition(row):
    if not row:
        return None
    row = dict(row)
    trigger = ({"cron": row["cron_expr"], "timezone": row["timezone"]} if row.get("cron_expr")
               else {"at": _utc(row["run_at"]).isoformat(), "timezone": row["timezone"]} if row.get("run_at") else {})
    return {
        "schema_version": "scheduled_task.v1", "task_id": row["schedule_id"],
        "schedule_id": row["schedule_id"], "owner_user_id": row["owner_user_id"],
        "requirement_brief": row["requirement_brief"], "trigger": trigger,
        "execution_plan": _dict(row["execution_plan_json"]), "budget": _dict(row.get("budget_json")),
        "source_ref": row.get("source_ref") or "", "enabled": bool(row["enabled"]),
        "revision_no": row["revision_no"], "initial_run_id": row.get("initial_run_id") or "",
        **{key: _utc(row.get(key)) for key in ("next_run_at", "last_run_at", "created_at", "updated_at")},
    }


def _run(row):
    if not row:
        return None
    row = dict(row)
    result = {key: row.get(key) for key in ("run_id", "schedule_id", "owner_user_id", "schedule_revision_no", "requirement_brief", "status")}
    result.update(task_id=row["schedule_id"], execution_plan=_dict(row["execution_plan_json"]),
                  budget=_dict(row.get("budget_json")), result=_dict(row.get("result_json")),
                  progress=_dict(row.get("progress_json")), checkpoint=_dict(row.get("checkpoint_json")),
                  lease_owner=row.get("lease_owner") or "", lease_token=row.get("lease_token") or "",
                  error_text=row.get("error_text") or "")
    result.update({key: _utc(row.get(key)) for key in ("scheduled_for", "lease_until", "deadline_at", "cancel_requested_at", "started_at", "finished_at", "created_at", "updated_at")})
    return result


class _Cursor:
    def __init__(self, cursor, sqlite):
        self.cursor, self.sqlite = cursor, sqlite

    def execute(self, sql, args=()):
        if self.sqlite:
            sql = sql.replace("%s", "?").replace(" FOR UPDATE SKIP LOCKED", "").replace(" FOR UPDATE", "")
            sql = sql.replace("INSERT IGNORE", "INSERT OR IGNORE")
        self.cursor.execute(sql, args)
        return self

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]

    @property
    def rowcount(self):
        return self.cursor.rowcount


class MySqlScheduledTaskStore:
    """One durable queue shared by immediate, one-off and recurring user tasks.

    State writes require a live claim token. Tool side effects remain at-least-once;
    only committed completed steps are skipped after worker recovery.
    """
    sqlite = False

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or self._connect
        self._lock = threading.RLock()

    @staticmethod
    def _connect():
        db = connect_system_db(cursorclass=pymysql.cursors.DictCursor, autocommit=False)
        with db.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")
        return db

    @contextmanager
    def _transaction(self):
        # SQLite serializes writers; MySQL uses row locks across worker processes.
        with self._lock:
            db = self.connection_factory()
            try:
                if self.sqlite:
                    db.execute("BEGIN IMMEDIATE")
                cur = _Cursor(db.cursor(), self.sqlite)
                yield cur
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                if not getattr(self, "_keep_connection", False):
                    db.close()

    def create(self, *, owner_user_id, draft, idempotency_key=""):
        for attempt in range(3):
            try:
                return self._create(owner_user_id=owner_user_id, draft=draft, idempotency_key=idempotency_key)
            except (pymysql.err.IntegrityError, pymysql.err.OperationalError) as exc:
                if not idempotency_key or exc.args[0] not in {1062, 1205, 1213} or attempt == 2:
                    raise
        raise RuntimeError("任务创建未完成")

    def _create(self, *, owner_user_id, draft, idempotency_key=""):
        key = str(idempotency_key or "").strip() or None
        fingerprint = str(draft.get("request_fingerprint") or _fingerprint(draft))
        with self._transaction() as c:
            if key:
                c.execute(f"SELECT * FROM {SCHEDULE_TABLE} WHERE owner_user_id=%s AND idempotency_key=%s FOR UPDATE", (owner_user_id, key))
                old = c.fetchone()
                if old:
                    if old.get("request_fingerprint") and old["request_fingerprint"] != fingerprint:
                        raise TaskIdempotencyConflict("此幂等键已用于不同的任务要求")
                    return _definition(old)
            now = _time()
            task_id = _new_id("sch")
            trigger = dict(draft.get("trigger") or {})
            next_at = _time(_utc(draft["next_run_at"]))
            c.execute(f"""INSERT INTO {SCHEDULE_TABLE}
                (schedule_id,owner_user_id,requirement_brief,timezone,cron_expr,run_at,
                 execution_plan_json,budget_json,source_ref,enabled,revision_no,next_run_at,
                 idempotency_key,request_fingerprint,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,1,%s,%s,%s,%s,%s)""",
                (task_id, owner_user_id, draft["requirement_brief"], trigger.get("timezone", "Asia/Shanghai"),
                 trigger.get("cron"), _time(_utc(trigger["at"])) if trigger.get("at") else None,
                 _json(draft["execution_plan"]), _json(draft.get("budget") or {"max_runtime_seconds":3600}),
                 draft.get("source_ref") or "", next_at, key, fingerprint, now, now))
            c.execute(f"SELECT * FROM {SCHEDULE_TABLE} WHERE schedule_id=%s", (task_id,))
            row = c.fetchone()
            if not trigger.get("cron"):
                run = self._insert_run(c, row, _utc(draft["next_run_at"]))
                c.execute(f"UPDATE {SCHEDULE_TABLE} SET initial_run_id=%s,next_run_at=NULL,last_run_at=%s WHERE schedule_id=%s", (run["run_id"], next_at, task_id))
                row.update(initial_run_id=run["run_id"], next_run_at=None, last_run_at=next_at)
            return _definition(row)

    def find_idempotent(self, *, owner_user_id, idempotency_key):
        with self._transaction() as c:
            c.execute(f"SELECT * FROM {SCHEDULE_TABLE} WHERE owner_user_id=%s AND idempotency_key=%s", (owner_user_id,idempotency_key))
            row = c.fetchone()
            return (_definition(row), row.get("request_fingerprint")) if row else (None,None)

    def list_for_owner(self, *, owner_user_id):
        with self._transaction() as c:
            c.execute(f"SELECT * FROM {SCHEDULE_TABLE} WHERE owner_user_id=%s ORDER BY updated_at DESC LIMIT 200", (owner_user_id,))
            return [_definition(r) for r in c.fetchall()]

    def get_for_owner(self, *, schedule_id, owner_user_id):
        with self._transaction() as c:
            c.execute(f"SELECT * FROM {SCHEDULE_TABLE} WHERE schedule_id=%s AND owner_user_id=%s", (schedule_id, owner_user_id))
            return _definition(c.fetchone())

    def update(self, *, schedule_id, owner_user_id, draft=None, enabled=None, now=None):
        with self._transaction() as c:
            c.execute(f"SELECT * FROM {SCHEDULE_TABLE} WHERE schedule_id=%s AND owner_user_id=%s FOR UPDATE", (schedule_id,owner_user_id))
            row = c.fetchone()
            if not row:
                return None
            is_enabled = bool(row["enabled"]) if enabled is None else enabled
            current = ensure_utc(now)
            if draft:
                trigger = dict(draft.get("trigger") or {})
                row.update(requirement_brief=draft["requirement_brief"], timezone=trigger.get("timezone","Asia/Shanghai"),
                           cron_expr=trigger.get("cron"), run_at=_time(_utc(trigger["at"])) if trigger.get("at") else None,
                           execution_plan_json=_json(draft["execution_plan"]), budget_json=_json(draft.get("budget") or {}),
                           source_ref=draft.get("source_ref") or "", revision_no=row["revision_no"]+1)
            # Revising a one-off definition does not create another execution. Run now is explicit.
            next_at = CronExpression(row["cron_expr"]).next_after(current, timezone=row["timezone"]) if is_enabled and row.get("cron_expr") else None
            c.execute(f"""UPDATE {SCHEDULE_TABLE} SET requirement_brief=%s,timezone=%s,cron_expr=%s,run_at=%s,
                execution_plan_json=%s,budget_json=%s,source_ref=%s,revision_no=%s,enabled=%s,next_run_at=%s,updated_at=%s
                WHERE schedule_id=%s AND owner_user_id=%s""",
                (row["requirement_brief"],row["timezone"],row.get("cron_expr"),row.get("run_at"),row["execution_plan_json"],row.get("budget_json"),
                 row.get("source_ref"),row["revision_no"],int(is_enabled),_time(next_at) if next_at else None,_time(current),schedule_id,owner_user_id))
            c.execute(f"SELECT * FROM {SCHEDULE_TABLE} WHERE schedule_id=%s", (schedule_id,))
            return _definition(c.fetchone())

    def _insert_run(self, c, row, when, request_key=None):
        run_id = _new_id("run")
        now = _time()
        c.execute(f"""INSERT INTO {RUN_TABLE} (run_id,schedule_id,owner_user_id,schedule_revision_no,
            requirement_brief,execution_plan_json,budget_json,scheduled_for,status,request_key,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s)""",
            (run_id,row["schedule_id"],row["owner_user_id"],row["revision_no"],row["requirement_brief"],
             row["execution_plan_json"],row.get("budget_json") or "{}",_time(when),request_key,now,now))
        c.execute(f"SELECT * FROM {RUN_TABLE} WHERE run_id=%s", (run_id,))
        return _run(c.fetchone())

    def materialize_due(self, *, now=None, limit=100):
        current=ensure_utc(now)
        created=0
        with self._transaction() as c:
            c.execute(f"""SELECT * FROM {SCHEDULE_TABLE} WHERE enabled=1 AND next_run_at<=%s
                AND NOT EXISTS (SELECT 1 FROM {RUN_TABLE} active WHERE active.schedule_id={SCHEDULE_TABLE}.schedule_id AND active.status IN ('pending','running'))
                ORDER BY next_run_at LIMIT %s FOR UPDATE SKIP LOCKED""", (_time(current),max(1,min(limit,1000))))
            for row in c.fetchall():
                c.execute(f"SELECT run_id FROM {RUN_TABLE} WHERE schedule_id=%s AND status IN ('pending','running') LIMIT 1", (row["schedule_id"],))
                # Retain one due slot until an active run finishes, coalescing the backlog.
                if c.fetchone():
                    continue
                self._insert_run(c,row,_utc(row["next_run_at"]))
                created+=1
                next_at=CronExpression(row["cron_expr"]).next_after(current,timezone=row["timezone"]) if row.get("cron_expr") else None
                c.execute(f"UPDATE {SCHEDULE_TABLE} SET last_run_at=next_run_at,next_run_at=%s,updated_at=%s WHERE schedule_id=%s", (_time(next_at) if next_at else None,_time(current),row["schedule_id"]))
        return created

    def enqueue_manual(self, *, schedule_id, owner_user_id, now=None, idempotency_key=""):
        with self._transaction() as c:
            c.execute(f"SELECT * FROM {SCHEDULE_TABLE} WHERE schedule_id=%s AND owner_user_id=%s FOR UPDATE", (schedule_id,owner_user_id))
            row=c.fetchone()
            if not row:
                return None
            key=str(idempotency_key or "").strip() or None
            if key:
                c.execute(f"SELECT * FROM {RUN_TABLE} WHERE schedule_id=%s AND request_key=%s", (schedule_id,key))
                old=c.fetchone()
                if old:
                    return _run(old)
            # Explicit repeated clicks while active return the same execution.
            c.execute(f"SELECT * FROM {RUN_TABLE} WHERE schedule_id=%s AND status IN ('pending','running') ORDER BY scheduled_for LIMIT 1", (schedule_id,))
            active=c.fetchone()
            if active:
                if active["status"] == "pending" and _utc(active["scheduled_for"]) > ensure_utc(now):
                    active["scheduled_for"] = _time(now)
                    c.execute(f"UPDATE {RUN_TABLE} SET scheduled_for=%s,updated_at=%s WHERE run_id=%s", (active["scheduled_for"],_time(now),active["run_id"]))
                if key and not active.get("request_key"):
                    c.execute(f"UPDATE {RUN_TABLE} SET request_key=%s WHERE run_id=%s", (key,active["run_id"]))
                return _run(active)
            return self._insert_run(c,row,ensure_utc(now),key)

    def list_runs_for_owner(self, *, owner_user_id, schedule_id="", limit=50):
        with self._transaction() as c:
            condition=" AND schedule_id=%s" if schedule_id else ""
            args=(owner_user_id,schedule_id) if schedule_id else (owner_user_id,)
            c.execute(f"SELECT * FROM {RUN_TABLE} WHERE owner_user_id=%s{condition} ORDER BY scheduled_for DESC LIMIT %s", (*args,max(1,min(int(limit),200))))
            return [_run(r) for r in c.fetchall()]

    def get_run_for_owner(self, *, run_id, owner_user_id):
        with self._transaction() as c:
            c.execute(f"SELECT * FROM {RUN_TABLE} WHERE run_id=%s AND owner_user_id=%s", (run_id,owner_user_id))
            return _run(c.fetchone())

    def claim(self, *, worker_id, now=None, lease_seconds=3600):
        current=ensure_utc(now)
        with self._transaction() as c:
            c.execute(f"""SELECT * FROM {RUN_TABLE} WHERE scheduled_for<=%s AND
                (status='pending' OR (status='running' AND lease_until<%s))
                ORDER BY scheduled_for LIMIT 1 FOR UPDATE SKIP LOCKED""", (_time(current),_time(current)))
            row=c.fetchone()
            if not row:
                return None
            token=uuid.uuid4().hex
            deadline=_utc(row.get("deadline_at")) or current+dt.timedelta(seconds=int(_dict(row.get("budget_json")).get("max_runtime_seconds",3600)))
            c.execute(f"""UPDATE {RUN_TABLE} SET status='running',lease_owner=%s,lease_token=%s,lease_until=%s,
                deadline_at=%s,started_at=COALESCE(started_at,%s),updated_at=%s WHERE run_id=%s""",
                (worker_id,token,_time(current+dt.timedelta(seconds=max(1,lease_seconds))),_time(deadline),_time(current),_time(current),row["run_id"]))
            c.execute(f"SELECT * FROM {RUN_TABLE} WHERE run_id=%s", (row["run_id"],))
            return _run(c.fetchone())

    def _live(self, c, run_id, worker_id, lease_token, now):
        if not lease_token:
            return None
        c.execute(f"SELECT * FROM {RUN_TABLE} WHERE run_id=%s AND status='running' AND lease_owner=%s AND lease_token=%s AND lease_until>=%s FOR UPDATE", (run_id,worker_id,lease_token,_time(now)))
        return c.fetchone()

    def renew_lease(self, *, run_id, worker_id, lease_token="", now=None, lease_seconds=3600):
        current=ensure_utc(now)
        with self._transaction() as c:
            if not self._live(c,run_id,worker_id,lease_token,current):
                return False
            c.execute(f"UPDATE {RUN_TABLE} SET lease_until=%s,updated_at=%s WHERE run_id=%s", (_time(current+dt.timedelta(seconds=max(1,lease_seconds))),_time(current),run_id))
            return True

    def save_progress(self, *, run_id, worker_id, lease_token, progress=None, checkpoint=None, result=None, now=None):
        current=ensure_utc(now)
        with self._transaction() as c:
            row=self._live(c,run_id,worker_id,lease_token,current)
            if not row:
                return False
            c.execute(f"UPDATE {RUN_TABLE} SET progress_json=%s,checkpoint_json=%s,result_json=%s,updated_at=%s WHERE run_id=%s", (
                _json(progress) if progress is not None else row.get("progress_json"),
                _json(checkpoint) if checkpoint is not None else row.get("checkpoint_json"),
                _json(result) if result is not None else row.get("result_json"),_time(current),run_id))
            return True

    def finish(self, *, run_id, worker_id, lease_token="", result=None, error_text="", cancelled=False, now=None):
        current=ensure_utc(now)
        with self._transaction() as c:
            row=self._live(c,run_id,worker_id,lease_token,current)
            if not row:
                return False
            status="cancelled" if cancelled or row.get("cancel_requested_at") else "failed" if error_text else "completed"
            c.execute(f"""UPDATE {RUN_TABLE} SET status=%s,result_json=%s,error_text=%s,finished_at=%s,updated_at=%s,
                lease_owner=NULL,lease_token=NULL,lease_until=NULL WHERE run_id=%s""",
                (status,_json(result) if result is not None else row.get("result_json"),error_text or None,_time(current),_time(current),run_id))
            return True

    def release(self, *, run_id, worker_id, lease_token, now=None):
        """Release an interrupted attempt after its child is stopped; keep checkpoints."""
        current=ensure_utc(now)
        with self._transaction() as c:
            if not self._live(c,run_id,worker_id,lease_token,current):
                return False
            c.execute(f"UPDATE {RUN_TABLE} SET lease_until=%s,updated_at=%s WHERE run_id=%s", (_time(current-dt.timedelta(microseconds=1)),_time(current),run_id))
            return True

    def cancel(self, *, run_id, owner_user_id, now=None):
        current=ensure_utc(now)
        with self._transaction() as c:
            c.execute(f"SELECT * FROM {RUN_TABLE} WHERE run_id=%s AND owner_user_id=%s FOR UPDATE", (run_id,owner_user_id))
            row=c.fetchone()
            if not row:
                return None
            if row["status"] in {"pending","running"}:
                pending=row["status"]=="pending"
                c.execute(f"UPDATE {RUN_TABLE} SET cancel_requested_at=COALESCE(cancel_requested_at,%s),status=%s,finished_at=%s,updated_at=%s WHERE run_id=%s",
                    (_time(current),"cancelled" if pending else "running",_time(current) if pending else None,_time(current),run_id))
            c.execute(f"SELECT * FROM {RUN_TABLE} WHERE run_id=%s", (run_id,))
            return _run(c.fetchone())


_SQLITE_SCHEMA=f"""
CREATE TABLE IF NOT EXISTS {SCHEDULE_TABLE} (
 schedule_id TEXT PRIMARY KEY,owner_user_id TEXT NOT NULL,requirement_brief TEXT NOT NULL,
 timezone TEXT NOT NULL,cron_expr TEXT,run_at TEXT,execution_plan_json TEXT NOT NULL,budget_json TEXT,
 source_ref TEXT,enabled INTEGER NOT NULL,revision_no INTEGER NOT NULL,next_run_at TEXT,last_run_at TEXT,
 initial_run_id TEXT,idempotency_key TEXT,request_fingerprint TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(owner_user_id,idempotency_key));
CREATE INDEX IF NOT EXISTS idx_schedule_due ON {SCHEDULE_TABLE}(enabled,next_run_at);
CREATE INDEX IF NOT EXISTS idx_schedule_owner ON {SCHEDULE_TABLE}(owner_user_id,updated_at);
CREATE TABLE IF NOT EXISTS {RUN_TABLE} (
 run_id TEXT PRIMARY KEY,schedule_id TEXT NOT NULL REFERENCES {SCHEDULE_TABLE}(schedule_id),owner_user_id TEXT NOT NULL,
 schedule_revision_no INTEGER NOT NULL,requirement_brief TEXT NOT NULL,execution_plan_json TEXT NOT NULL,
 budget_json TEXT,scheduled_for TEXT NOT NULL,status TEXT NOT NULL,lease_owner TEXT,lease_token TEXT,lease_until TEXT,
 deadline_at TEXT,cancel_requested_at TEXT,result_json TEXT,progress_json TEXT,checkpoint_json TEXT,error_text TEXT,
 request_key TEXT,started_at TEXT,finished_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(schedule_id,scheduled_for),UNIQUE(schedule_id,request_key));
CREATE INDEX IF NOT EXISTS idx_run_claim ON {RUN_TABLE}(status,lease_until,scheduled_for);
CREATE INDEX IF NOT EXISTS idx_run_owner ON {RUN_TABLE}(owner_user_id,created_at);
"""


class SqliteScheduledTaskStore(MySqlScheduledTaskStore):
    """Local persistent store using the same queue semantics, suitable for one host."""
    sqlite=True

    def __init__(self, path):
        self.path=":memory:" if str(path) == ":memory:" else str(Path(path).expanduser().resolve())
        self._lock=threading.RLock()
        self._keep_connection=self.path==":memory:"
        if self._keep_connection:
            self._connection=sqlite3.connect(":memory:",check_same_thread=False,isolation_level=None)
            self._connection.row_factory=sqlite3.Row
            self.connection_factory=lambda:self._connection
        else:
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True,exist_ok=True)
            self.connection_factory=self._connect_sqlite
        db=self.connection_factory()
        db.executescript(_SQLITE_SCHEMA)
        if not self._keep_connection:
            db.close()

    def _connect_sqlite(self):
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db


class InMemoryScheduledTaskStore(SqliteScheduledTaskStore):
    def __init__(self):
        super().__init__(":memory:")

    @property
    def runs(self):
        with self._transaction() as c:
            c.execute(f"SELECT * FROM {RUN_TABLE} ORDER BY created_at")
            return {r["run_id"]:_run(r) for r in c.fetchall()}

    @property
    def schedules(self):
        with self._transaction() as c:
            c.execute(f"SELECT * FROM {SCHEDULE_TABLE}")
            return {r["schedule_id"]:_definition(r) for r in c.fetchall()}


def default_task_store():
    path=os.environ.get("TASK_SQLITE_PATH", "").strip()
    return SqliteScheduledTaskStore(path) if path else MySqlScheduledTaskStore()

import datetime as dt
import json
import time
from typing import Any, Callable, Dict, Optional

import pymysql

from src.services.session_variable_store_service import SessionVariableStoreService
from src.utils.system_db_utils import SystemDbUtils


THREAD_TABLE = "aiia_runtime_thread"
TASK_TABLE = "aiia_runtime_task"
EVENT_TABLE = "aiia_runtime_event"
INVOCATION_TABLE = "aiia_runtime_invocation"
ARTIFACT_TABLE = "aiia_runtime_artifact"


class RuntimeExecutionService:
    def __init__(self, *, session_variable_store: Optional[SessionVariableStoreService] = None) -> None:
        self.session_variable_store = session_variable_store or SessionVariableStoreService()

    def _json_text(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _summary_text(payload: Any, *, limit: int = 255) -> str:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        return text[:limit]

    def _ensure_thread(self, cursor: pymysql.cursors.Cursor, *, runtime_ctx: Dict[str, Any], artifact_name: str) -> int:
        thread_id = runtime_ctx.get("thread_id")
        if thread_id:
            return int(thread_id)
        cursor.execute(
            f"""
            INSERT INTO {THREAD_TABLE} (
              thread_type, title, owner_type, owner_id, status,
              context_summary, metadata_json, last_event_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                self._trim(runtime_ctx.get("thread_type")) or "tool_run",
                self._trim(runtime_ctx.get("thread_title")) or f"artifact:{artifact_name}",
                self._trim(runtime_ctx.get("owner_type")) or "system",
                self._trim(runtime_ctx.get("owner_id")) or "tool_runtime",
                "active",
                self._trim(runtime_ctx.get("context_summary")) or f"执行 {artifact_name}",
                self._json_text(
                    {
                        "source_type": self._trim(runtime_ctx.get("source_type")) or "tool_call",
                        "artifact_name": artifact_name,
                    }
                ),
            ),
        )
        return int(cursor.lastrowid)

    def _ensure_task(
        self,
        cursor: pymysql.cursors.Cursor,
        *,
        runtime_ctx: Dict[str, Any],
        thread_id: int,
        artifact_id: Optional[int],
        artifact_name: str,
        args: Dict[str, Any],
    ) -> int:
        task_id = runtime_ctx.get("task_id")
        if task_id:
            return int(task_id)
        cursor.execute(
            f"""
            INSERT INTO {TASK_TABLE} (
              thread_id, parent_task_id, task_type, goal, input_json, status,
              assigned_artifact_id, assigned_agent, priority, started_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                thread_id,
                runtime_ctx.get("parent_task_id"),
                self._trim(runtime_ctx.get("task_type")) or "tool_call",
                self._trim(runtime_ctx.get("goal")) or f"执行 {artifact_name}",
                self._json_text(args),
                "running",
                artifact_id,
                self._trim(runtime_ctx.get("assigned_agent")) or "tool_runtime",
                int(runtime_ctx.get("priority") or 0),
            ),
        )
        task_id = int(cursor.lastrowid)
        cursor.execute(
            f"""
            UPDATE {THREAD_TABLE}
            SET active_task_id = %s, updated_at = NOW()
            WHERE thread_id = %s
            """,
            (task_id, thread_id),
        )
        return task_id

    def _next_sequence_no(self, cursor: pymysql.cursors.Cursor, *, thread_id: int) -> int:
        cursor.execute(
            f"SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM {EVENT_TABLE} WHERE thread_id = %s",
            (thread_id,),
        )
        row = cursor.fetchone()
        return int((row or [1])[0] or 1)

    def _insert_event(
        self,
        cursor: pymysql.cursors.Cursor,
        *,
        thread_id: int,
        task_id: Optional[int],
        turn_id: Optional[int],
        event_type: str,
        actor_type: str,
        actor_id: str,
        payload: Dict[str, Any],
    ) -> int:
        sequence_no = self._next_sequence_no(cursor, thread_id=thread_id)
        cursor.execute(
            f"""
            INSERT INTO {EVENT_TABLE} (
              thread_id, turn_id, task_id, sequence_no, event_type,
              actor_type, actor_id, payload_json, summary_text
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                thread_id,
                turn_id,
                task_id,
                sequence_no,
                event_type,
                actor_type,
                actor_id,
                self._json_text(payload),
                self._summary_text(payload),
            ),
        )
        cursor.execute(
            f"UPDATE {THREAD_TABLE} SET last_event_at = NOW(), updated_at = NOW() WHERE thread_id = %s",
            (thread_id,),
        )
        return int(cursor.lastrowid)

    def _lookup_artifact(self, cursor: pymysql.cursors.Cursor, *, artifact_type: str, artifact_name: str) -> Dict[str, Any]:
        cursor.execute(
            f"""
            SELECT artifact_id, artifact_type, name, version
            FROM {ARTIFACT_TABLE}
            WHERE artifact_type = %s AND name = %s
            ORDER BY artifact_id DESC
            LIMIT 1
            """,
            (artifact_type, artifact_name),
        )
        row = cursor.fetchone()
        if not row:
            return {
                "artifact_id": None,
                "artifact_type": artifact_type,
                "name": artifact_name,
                "version": "",
            }
        return {
            "artifact_id": int(row[0]),
            "artifact_type": str(row[1] or artifact_type),
            "name": str(row[2] or artifact_name),
            "version": str(row[3] or ""),
        }

    def begin_artifact_run(
        self,
        *,
        artifact_type: str,
        artifact_name: str,
        input_payload: Dict[str, Any],
        runtime_ctx: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ctx = dict(runtime_ctx or {})
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                artifact = self._lookup_artifact(
                    cursor,
                    artifact_type=self._trim(artifact_type) or "tool",
                    artifact_name=self._trim(artifact_name) or "artifact",
                )
                thread_id = self._ensure_thread(cursor, runtime_ctx=ctx, artifact_name=artifact["name"])
                task_id = self._ensure_task(
                    cursor,
                    runtime_ctx=ctx,
                    thread_id=thread_id,
                    artifact_id=artifact["artifact_id"],
                    artifact_name=artifact["name"],
                    args=input_payload,
                )
            db.conn.commit()
            return {
                "artifact_id": artifact["artifact_id"],
                "thread_id": thread_id,
                "task_id": task_id,
            }
        finally:
            db.close_db()

    def append_event(
        self,
        *,
        thread_id: int,
        task_id: Optional[int],
        event_type: str,
        actor_type: str,
        actor_id: str,
        payload: Dict[str, Any],
        turn_id: Optional[int] = None,
    ) -> int:
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                event_id = self._insert_event(
                    cursor,
                    thread_id=int(thread_id),
                    task_id=int(task_id) if task_id else None,
                    turn_id=int(turn_id) if turn_id else None,
                    event_type=self._trim(event_type) or "runtime_event",
                    actor_type=self._trim(actor_type) or "system",
                    actor_id=self._trim(actor_id) or "runtime",
                    payload=payload,
                )
            db.conn.commit()
            return event_id
        finally:
            db.close_db()

    def finish_task(
        self,
        *,
        thread_id: Optional[int],
        task_id: Optional[int],
        status: str,
        result_summary: str = "",
        error_text: str = "",
    ) -> None:
        if not task_id:
            return
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {TASK_TABLE}
                    SET status = %s,
                        result_summary = %s,
                        error_text = %s,
                        finished_at = NOW(),
                        updated_at = NOW()
                    WHERE task_id = %s
                    """,
                    (
                        self._trim(status) or "completed",
                        self._trim(result_summary),
                        self._trim(error_text),
                        int(task_id),
                    ),
                )
                if thread_id:
                    cursor.execute(
                        f"""
                        UPDATE {THREAD_TABLE}
                        SET updated_at = NOW(),
                            active_task_id = CASE WHEN active_task_id = %s THEN NULL ELSE active_task_id END
                        WHERE thread_id = %s
                        """,
                        (int(task_id), int(thread_id)),
                    )
            db.conn.commit()
        finally:
            db.close_db()

    def execute_tool(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        executor: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        runtime_ctx = dict((args or {}).get("_runtime") or {})
        clean_args = dict(args or {})
        clean_args.pop("_runtime", None)

        db = None
        thread_id = None
        task_id = None
        turn_id = runtime_ctx.get("turn_id")
        invocation_id = None
        started_event_id = None
        started_at = time.time()
        artifact = {
            "artifact_id": None,
            "artifact_type": "tool",
            "name": tool_name,
            "version": "",
        }

        try:
            db = SystemDbUtils()
            with db.conn.cursor() as cursor:
                artifact = self._lookup_artifact(cursor, artifact_type="tool", artifact_name=tool_name)
                thread_id = self._ensure_thread(cursor, runtime_ctx=runtime_ctx, artifact_name=tool_name)
                task_id = self._ensure_task(
                    cursor,
                    runtime_ctx=runtime_ctx,
                    thread_id=thread_id,
                    artifact_id=artifact["artifact_id"],
                    artifact_name=tool_name,
                    args=clean_args,
                )
                started_event_id = self._insert_event(
                    cursor,
                    thread_id=thread_id,
                    task_id=task_id,
                    turn_id=turn_id,
                    event_type="tool_call",
                    actor_type="tool",
                    actor_id=tool_name,
                    payload={
                        "tool_name": tool_name,
                        "arguments": clean_args,
                    },
                )
                cursor.execute(
                    f"""
                    INSERT INTO {INVOCATION_TABLE} (
                      thread_id, turn_id, task_id, event_id, artifact_id,
                      artifact_type, artifact_name, artifact_version,
                      input_json, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'running')
                    """,
                    (
                        thread_id,
                        turn_id,
                        task_id,
                        started_event_id,
                        artifact["artifact_id"],
                        artifact["artifact_type"],
                        artifact["name"],
                        artifact["version"],
                        self._json_text(clean_args),
                    ),
                )
                invocation_id = int(cursor.lastrowid)
            db.conn.commit()
        except Exception:
            if db:
                try:
                    db.conn.rollback()
                except Exception:
                    pass

        try:
            result = executor(clean_args)
        except Exception as exc:
            latency_ms = int((time.time() - started_at) * 1000)
            if db and invocation_id and thread_id:
                try:
                    with db.conn.cursor() as cursor:
                        self._insert_event(
                            cursor,
                            thread_id=thread_id,
                            task_id=task_id,
                            turn_id=turn_id,
                            event_type="tool_result",
                            actor_type="tool",
                            actor_id=tool_name,
                            payload={
                                "tool_name": tool_name,
                                "status": "failed",
                                "error": str(exc),
                            },
                        )
                        cursor.execute(
                            f"""
                            UPDATE {INVOCATION_TABLE}
                            SET status = 'failed',
                                latency_ms = %s,
                                error_text = %s,
                                updated_at = NOW()
                            WHERE invocation_id = %s
                            """,
                            (latency_ms, str(exc), invocation_id),
                        )
                        if task_id:
                            cursor.execute(
                                f"""
                                UPDATE {TASK_TABLE}
                                SET status = 'failed',
                                    error_text = %s,
                                    finished_at = NOW(),
                                    updated_at = NOW()
                                WHERE task_id = %s
                                """,
                                (str(exc), task_id),
                            )
                    db.conn.commit()
                except Exception:
                    pass
                finally:
                    db.close_db()
            raise

        self._attach_session_variable(
            tool_name=tool_name,
            args=clean_args,
            result=result,
            runtime_ctx=runtime_ctx,
            thread_id=thread_id,
            task_id=task_id,
            turn_id=turn_id,
        )

        latency_ms = int((time.time() - started_at) * 1000)
        if db and invocation_id and thread_id:
            try:
                with db.conn.cursor() as cursor:
                    self._insert_event(
                        cursor,
                        thread_id=thread_id,
                        task_id=task_id,
                        turn_id=turn_id,
                        event_type="tool_result",
                        actor_type="tool",
                        actor_id=tool_name,
                        payload={
                            "tool_name": tool_name,
                            "status": "completed",
                            "result_meta": result.get("meta") if isinstance(result, dict) else {},
                            "ok": bool((result or {}).get("ok")) if isinstance(result, dict) else True,
                        },
                    )
                    cursor.execute(
                        f"""
                        UPDATE {INVOCATION_TABLE}
                        SET output_json = %s,
                            status = 'completed',
                            latency_ms = %s,
                            updated_at = NOW()
                        WHERE invocation_id = %s
                        """,
                        (self._json_text(result), latency_ms, invocation_id),
                    )
                    if task_id:
                        cursor.execute(
                            f"""
                            UPDATE {TASK_TABLE}
                            SET status = 'completed',
                                result_summary = %s,
                                finished_at = NOW(),
                                updated_at = NOW()
                            WHERE task_id = %s
                            """,
                            (self._summary_text({"tool_name": tool_name, "ok": result.get("ok") if isinstance(result, dict) else True}), task_id),
                        )
                db.conn.commit()
            except Exception:
                pass
            finally:
                db.close_db()
        return result

    def _attach_session_variable(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        result: Any,
        runtime_ctx: Dict[str, Any],
        thread_id: Optional[int],
        task_id: Optional[int],
        turn_id: Optional[int],
    ) -> None:
        if not isinstance(result, dict):
            return
        ctx = dict(runtime_ctx or {})
        if thread_id and not ctx.get("thread_id"):
            ctx["thread_id"] = thread_id
        if task_id and not ctx.get("task_id"):
            ctx["task_id"] = task_id
        if turn_id and not ctx.get("turn_id"):
            ctx["turn_id"] = turn_id
        session_id = self._session_id(ctx)
        if not session_id:
            return
        try:
            store = self.session_variable_store
            if ctx.get("data_root"):
                store = SessionVariableStoreService(data_root=ctx.get("data_root"))
            variable = store.register_tool_result(
                session_id=session_id,
                tool_name=tool_name,
                result=result,
                task=self._result_task(args=args, runtime_ctx=ctx),
                runtime_ctx=ctx,
            )
        except Exception as exc:
            meta = result.setdefault("meta", {})
            if isinstance(meta, dict):
                meta["session_variable_error"] = str(exc)
            return
        if not variable:
            return
        meta = result.setdefault("meta", {})
        if isinstance(meta, dict):
            meta["session_variable"] = variable

    def _session_id(self, runtime_ctx: Dict[str, Any]) -> str:
        for key in ("conversation_id", "session_id", "thread_id"):
            value = self._trim(runtime_ctx.get(key))
            if value:
                return value
        return ""

    def _result_task(self, *, args: Dict[str, Any], runtime_ctx: Dict[str, Any]) -> str:
        for value in (
            runtime_ctx.get("goal"),
            runtime_ctx.get("task"),
            args.get("request"),
            args.get("query"),
            args.get("task"),
            args.get("file_path"),
            args.get("file_id"),
        ):
            text = self._trim(value)
            if text:
                return text
        return ""

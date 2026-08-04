import json
from typing import Any, Dict, Optional

import pymysql

from src.utils.system_db_utils import SystemDbUtils
from src.services.user_session_service import USER_TABLE


THREAD_TABLE = "aiia_runtime_thread"
TURN_TABLE = "aiia_runtime_turn"


class RuntimeConversationAccessError(PermissionError):
    """Raised when a caller tries to reuse a thread it does not own."""


class RuntimeConversationService:
    HISTORY_OUTPUT_KEYS = (
        "mode",
        "message",
        "surface_blocks",
        "render_blocks",
        "render_payload",
        "surface",
        "items",
        "workspace",
        "task_state",
        "report_export",
    )
    HISTORY_OMIT_KEYS = {
        "events",
        "raw_events",
        "coding_events",
        "raw",
        "raw_stdout",
        "raw_stderr",
        "last_message",
    }

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _json_text(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _safe_json_dict(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str) and str(value).strip():
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    @classmethod
    def _compact_history_payload(cls, value: Any, *, depth: int = 0) -> Any:
        if depth > 8:
            return None
        if isinstance(value, dict):
            compact: Dict[str, Any] = {}
            for key, item in value.items():
                if str(key) in cls.HISTORY_OMIT_KEYS:
                    continue
                compact[str(key)] = cls._compact_history_payload(item, depth=depth + 1)
            return compact
        if isinstance(value, list):
            return [cls._compact_history_payload(item, depth=depth + 1) for item in value]
        return value

    @staticmethod
    def _normalize_token_usage(value: Any) -> Dict[str, int]:
        source = value if isinstance(value, dict) else {}
        normalized: Dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            try:
                normalized[key] = int(source.get(key, 0) or 0)
            except Exception:
                normalized[key] = 0
        return normalized

    @staticmethod
    def _table_exists(cursor: pymysql.cursors.Cursor, table_name: str) -> bool:
        cursor.execute("SHOW TABLES LIKE %s", (str(table_name),))
        return bool(cursor.fetchone())

    def ensure_thread(
        self,
        *,
        thread_id: Optional[int] = None,
        title: str = "",
        owner_type: str = "system",
        owner_id: str = "conversation_workbench",
        context_summary: str = "",
    ) -> int:
        if thread_id:
            normalized_owner_type = self._trim(owner_type) or "system"
            normalized_owner_id = self._trim(owner_id) or "conversation_workbench"
            db = SystemDbUtils()
            try:
                with db.conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT owner_type, owner_id
                        FROM {THREAD_TABLE}
                        WHERE thread_id = %s
                        """,
                        (int(thread_id),),
                    )
                    row = cursor.fetchone()
                if (
                    not row
                    or self._trim(row[0]) != normalized_owner_type
                    or self._trim(row[1]) != normalized_owner_id
                ):
                    # Use one response for missing and foreign IDs so this
                    # check does not become a thread-enumeration oracle.
                    raise RuntimeConversationAccessError("无权访问该 thread")
                return int(thread_id)
            finally:
                db.close_db()
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {THREAD_TABLE} (
                      thread_type, title, owner_type, owner_id, status,
                      context_summary, metadata_json, last_event_at
                    ) VALUES (%s, %s, %s, %s, 'active', %s, %s, NOW())
                    """,
                    (
                        "chat",
                        self._trim(title) or "conversation_workbench",
                        self._trim(owner_type) or "system",
                        self._trim(owner_id) or "conversation_workbench",
                        self._trim(context_summary) or "对话工作台会话",
                        self._json_text({"source": "conversation_workbench"}),
                    ),
                )
                new_thread_id = int(cursor.lastrowid)
            db.conn.commit()
            return new_thread_id
        finally:
            db.close_db()

    def list_threads(
        self,
        *,
        owner_type: str,
        owner_id: str,
        limit: int = 12,
    ) -> list[Dict[str, Any]]:
        normalized_owner_type = self._trim(owner_type) or "user"
        normalized_owner_id = self._trim(owner_id)
        if not normalized_owner_id:
            return []
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                      t.thread_id,
                      t.title,
                      t.status,
                      t.created_at,
                      t.updated_at,
                      t.last_event_at,
                      r.user_input_text,
                      r.assistant_output_text,
                      r.finished_at
                    FROM {THREAD_TABLE} t
                    LEFT JOIN {TURN_TABLE} r ON r.turn_id = t.latest_turn_id
                    WHERE t.owner_type = %s
                      AND t.owner_id = %s
                    ORDER BY COALESCE(t.last_event_at, t.updated_at, t.created_at) DESC, t.thread_id DESC
                    LIMIT %s
                    """,
                    (
                        normalized_owner_type,
                        normalized_owner_id,
                        max(1, int(limit or 12)),
                    ),
                )
                rows = cursor.fetchall() or []
            items: list[Dict[str, Any]] = []
            for row in rows:
                (
                    thread_id,
                    title,
                    status,
                    created_at,
                    updated_at,
                    last_event_at,
                    latest_user_input,
                    latest_assistant_output,
                    latest_turn_finished_at,
                ) = row
                items.append(
                    {
                        "thread_id": int(thread_id),
                        "title": self._trim(title) or f"会话 {thread_id}",
                        "status": self._trim(status) or "active",
                        "created_at": str(created_at or ""),
                        "updated_at": str(updated_at or ""),
                        "last_event_at": str(last_event_at or updated_at or created_at or ""),
                        "latest_user_input": self._trim(latest_user_input),
                        "latest_assistant_output": self._trim(latest_assistant_output),
                        "latest_turn_finished_at": str(latest_turn_finished_at or ""),
                    }
                )
            return items
        finally:
            db.close_db()

    def get_thread(self, *, thread_id: int, include_context: bool = False) -> Dict[str, Any] | None:
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                context_select = ", metadata_json" if include_context else ""
                cursor.execute(
                    f"""
                    SELECT thread_id, title, owner_type, owner_id, status, created_at, updated_at, last_event_at{context_select}
                    FROM {THREAD_TABLE}
                    WHERE thread_id = %s
                    """,
                    (int(thread_id),),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                (
                    row_thread_id,
                    title,
                    owner_type,
                    owner_id,
                    status,
                    created_at,
                    updated_at,
                    last_event_at,
                ) = row[:8]
                result = {
                    "thread_id": int(row_thread_id),
                    "title": self._trim(title) or f"会话 {row_thread_id}",
                    "owner_type": self._trim(owner_type),
                    "owner_id": self._trim(owner_id),
                    "status": self._trim(status) or "active",
                    "created_at": str(created_at or ""),
                    "updated_at": str(updated_at or ""),
                    "last_event_at": str(last_event_at or updated_at or created_at or ""),
                }
                if include_context:
                    metadata = self._safe_json_dict(row[8] if len(row) > 8 else None)
                    assistant_context = metadata.get("assistant_context")
                    result["_thread_context"] = dict(assistant_context) if isinstance(assistant_context, dict) else {}
                return result
        finally:
            db.close_db()

    def update_thread_title(
        self,
        *,
        thread_id: int,
        title: str,
        expected_title: str = "",
    ) -> bool:
        normalized_title = self._trim(title)[:80]
        if not normalized_title:
            return False
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                if self._trim(expected_title):
                    cursor.execute(
                        f"""
                        UPDATE {THREAD_TABLE}
                        SET title = %s, updated_at = NOW()
                        WHERE thread_id = %s AND title = %s
                        """,
                        (normalized_title, int(thread_id), self._trim(expected_title)),
                    )
                else:
                    cursor.execute(
                        f"UPDATE {THREAD_TABLE} SET title = %s, updated_at = NOW() WHERE thread_id = %s",
                        (normalized_title, int(thread_id)),
                    )
                updated = int(cursor.rowcount or 0) > 0
            db.conn.commit()
            return updated
        finally:
            db.close_db()

    def list_turns(
        self,
        *,
        thread_id: int,
        limit: int = 80,
        include_output_payload: bool = True,
        history_payload_only: bool = False,
    ) -> list[Dict[str, Any]]:
        db = SystemDbUtils()
        if include_output_payload and history_payload_only:
            fields = ",\n".join(
                f"'{key}', JSON_EXTRACT(output_structured_json, '$.{key}')"
                for key in self.HISTORY_OUTPUT_KEYS
            )
            output_payload_select = f"JSON_OBJECT({fields}) AS output_structured_json"
        else:
            output_payload_select = "output_structured_json" if include_output_payload else "NULL AS output_structured_json"
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                      turn_id,
                      turn_no,
                      user_input_text,
                      input_structured_json,
                      assistant_output_text,
                      {output_payload_select},
                      status,
                      started_at,
                      finished_at,
                      model_name,
                      token_usage_json
                    FROM {TURN_TABLE}
                    WHERE thread_id = %s
                    ORDER BY turn_no ASC
                    LIMIT %s
                    """,
                    (int(thread_id), max(1, int(limit or 80))),
                )
                rows = cursor.fetchall() or []
            items: list[Dict[str, Any]] = []
            for row in rows:
                (
                    turn_id,
                    turn_no,
                    user_input_text,
                    input_structured_json,
                    assistant_output_text,
                    output_structured_json,
                    status,
                    started_at,
                    finished_at,
                    model_name,
                    token_usage_json,
                ) = row
                output_payload = self._compact_history_payload(self._safe_json_dict(output_structured_json))
                if history_payload_only and isinstance(output_payload, dict):
                    output_payload = {key: value for key, value in output_payload.items() if value is not None}
                items.append(
                    {
                        "turn_id": int(turn_id),
                        "turn_no": int(turn_no),
                        "user_input_text": self._trim(user_input_text),
                        "input_payload": self._safe_json_dict(input_structured_json),
                        "assistant_output_text": self._trim(assistant_output_text),
                        "output_payload": output_payload,
                        "status": self._trim(status) or "",
                        "started_at": str(started_at or ""),
                        "finished_at": str(finished_at or ""),
                        "model_name": self._trim(model_name),
                        "token_usage": self._normalize_token_usage(
                            self._safe_json_dict(token_usage_json)
                        ),
                    }
                )
            return items
        finally:
            db.close_db()

    def get_turn(
        self,
        *,
        thread_id: int,
        turn_id: int,
        include_output_payload: bool = True,
    ) -> Optional[Dict[str, Any]]:
        db = SystemDbUtils()
        output_payload_select = (
            "output_structured_json"
            if include_output_payload
            else "NULL AS output_structured_json"
        )
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                      turn_id,
                      turn_no,
                      user_input_text,
                      input_structured_json,
                      assistant_output_text,
                      {output_payload_select},
                      status,
                      started_at,
                      finished_at,
                      model_name,
                      token_usage_json
                    FROM {TURN_TABLE}
                    WHERE thread_id = %s AND turn_id = %s
                    LIMIT 1
                    """,
                    (int(thread_id), int(turn_id)),
                )
                row = cursor.fetchone()
            if not row:
                return None
            (
                resolved_turn_id,
                turn_no,
                user_input_text,
                input_structured_json,
                assistant_output_text,
                output_structured_json,
                status,
                started_at,
                finished_at,
                model_name,
                token_usage_json,
            ) = row
            return {
                "turn_id": int(resolved_turn_id),
                "turn_no": int(turn_no),
                "user_input_text": self._trim(user_input_text),
                "input_payload": self._safe_json_dict(input_structured_json),
                "assistant_output_text": self._trim(assistant_output_text),
                "output_payload": self._compact_history_payload(
                    self._safe_json_dict(output_structured_json)
                ),
                "status": self._trim(status) or "",
                "started_at": str(started_at or ""),
                "finished_at": str(finished_at or ""),
                "model_name": self._trim(model_name),
                "token_usage": self._normalize_token_usage(
                    self._safe_json_dict(token_usage_json)
                ),
            }
        finally:
            db.close_db()

    def get_thread_context(self, *, thread_id: int) -> Dict[str, Any]:
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT metadata_json FROM {THREAD_TABLE} WHERE thread_id = %s",
                    (int(thread_id),),
                )
                row = cursor.fetchone()
                if not row:
                    return {}
                metadata = self._safe_json_dict((row or [None])[0])
                assistant_context = metadata.get("assistant_context")
                return dict(assistant_context) if isinstance(assistant_context, dict) else {}
        finally:
            db.close_db()

    def get_context_window(self, *, thread_id: int, max_rounds: int = 5) -> list[Dict[str, Any]]:
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                      turn_no,
                      user_input_text,
                      input_structured_json,
                      assistant_output_text,
                      output_structured_json
                    FROM {TURN_TABLE}
                    WHERE thread_id = %s
                    ORDER BY turn_no DESC
                    LIMIT %s
                    """,
                    (int(thread_id), max(1, int(max_rounds or 5))),
                )
                rows = cursor.fetchall() or []
            rows = list(reversed(rows))
            window: list[Dict[str, Any]] = []
            for row in rows:
                turn_no, user_text, input_structured_json, assistant_text, output_structured_json = row
                input_payload = self._safe_json_dict(input_structured_json)
                output_payload = self._safe_json_dict(output_structured_json)
                input_attachments = input_payload.get("attachments") if isinstance(input_payload.get("attachments"), list) else []
                window.append(
                    {
                        "round": int(turn_no),
                        "role": "user",
                        "text": self._trim(user_text),
                        "attachments": self._normalize_context_attachments(input_attachments),
                    }
                )
                assistant_summary = self._trim(output_payload.get("answer_summary"))
                assistant_message = assistant_summary or self._trim(assistant_text)
                if assistant_message:
                    assistant_message = self._context_answer_preview(assistant_message)
                    window.append(
                        {
                            "round": int(turn_no),
                            "role": "assistant",
                            "text": self._trim(assistant_message),
                            "attachments": self._extract_output_context_attachments(output_payload),
                        }
                    )
            return window
        finally:
            db.close_db()

    @staticmethod
    def _context_answer_preview(text: Any, max_chars: int = 360) -> str:
        # Context resolution needs enough semantic evidence to resolve natural
        # follow-ups such as “和五日前比呢” or “第二个呢”.  Keep a bounded
        # narrative preview instead of either the full result table or the old
        # ten-character fragment.
        normalized = " ".join(str(text or "").strip().split())
        if len(normalized) <= max_chars:
            return normalized
        return normalized[:max_chars] + "…"

    def _normalize_context_attachments(self, attachments: Any) -> list[Dict[str, str]]:
        rows: list[Dict[str, str]] = []
        for item in attachments or []:
            if not isinstance(item, dict):
                continue
            attachment_id = self._trim(item.get("attachment_id"))
            if not attachment_id:
                continue
            rows.append(
                {
                    "attachment_id": attachment_id,
                    "attachment_summary": self._trim(item.get("summary")) or self._trim(item.get("name")) or self._trim(item.get("kind")),
                }
            )
        return rows

    def _extract_output_context_attachments(self, output_payload: Dict[str, Any]) -> list[Dict[str, str]]:
        patch = output_payload.get("thread_context_patch") if isinstance(output_payload.get("thread_context_patch"), dict) else {}
        summary = self._trim(patch.get("last_image_summary"))
        image_ids = patch.get("last_image_attachment_ids") if isinstance(patch.get("last_image_attachment_ids"), list) else []
        rows: list[Dict[str, str]] = []
        for attachment_id in image_ids[:3]:
            normalized = self._trim(attachment_id)
            if not normalized:
                continue
            rows.append(
                {
                    "attachment_id": normalized,
                    "attachment_summary": summary,
                }
            )
        return rows

    def update_thread_context(
        self,
        *,
        thread_id: int,
        patch: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized_patch = patch if isinstance(patch, dict) else {}
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT metadata_json FROM {THREAD_TABLE} WHERE thread_id = %s",
                    (int(thread_id),),
                )
                row = cursor.fetchone()
                metadata = self._safe_json_dict((row or [None])[0] if row else None)
                current_ctx = metadata.get("assistant_context")
                merged = dict(current_ctx) if isinstance(current_ctx, dict) else {}
                for key, value in normalized_patch.items():
                    if value is None:
                        merged.pop(str(key), None)
                    else:
                        merged[str(key)] = value
                metadata["assistant_context"] = merged
                cursor.execute(
                    f"""
                    UPDATE {THREAD_TABLE}
                    SET metadata_json = %s, updated_at = NOW(), last_event_at = NOW()
                    WHERE thread_id = %s
                    """,
                    (
                        self._json_text(metadata),
                        int(thread_id),
                    ),
                )
            db.conn.commit()
            return merged
        finally:
            db.close_db()

    def create_turn(
        self,
        *,
        thread_id: int,
        user_input_text: str,
        input_payload: Dict[str, Any],
        started_at: Any = None,
    ) -> int:
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT COALESCE(MAX(turn_no), 0) + 1 FROM {TURN_TABLE} WHERE thread_id = %s",
                    (int(thread_id),),
                )
                row = cursor.fetchone()
                turn_no = int((row or [1])[0] or 1)
                cursor.execute(
                    f"""
                    INSERT INTO {TURN_TABLE} (
                      thread_id, turn_no, user_input_text, input_structured_json,
                      status, started_at
                    ) VALUES (%s, %s, %s, %s, 'running', COALESCE(%s, NOW()))
                    """,
                    (
                        int(thread_id),
                        turn_no,
                        self._trim(user_input_text),
                        self._json_text(input_payload),
                        self._trim(started_at) or None,
                    ),
                )
                turn_id = int(cursor.lastrowid)
                cursor.execute(
                    f"""
                    UPDATE {THREAD_TABLE}
                    SET latest_turn_id = %s, updated_at = NOW(), last_event_at = NOW()
                    WHERE thread_id = %s
                    """,
                    (turn_id, int(thread_id)),
                )
            db.conn.commit()
            return turn_id
        finally:
            db.close_db()

    def complete_turn(
        self,
        *,
        thread_id: int,
        turn_id: int,
        assistant_output_text: str,
        output_payload: Dict[str, Any],
        status: str = "completed",
        model_name: str = "",
        token_usage: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        normalized_usage = self._normalize_token_usage(token_usage)
        turn_meta: Dict[str, Any] = {}
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {TURN_TABLE}
                    SET assistant_output_text = %s,
                        output_structured_json = %s,
                        status = %s,
                        model_name = %s,
                        token_usage_json = %s,
                        finished_at = NOW(),
                        updated_at = NOW()
                    WHERE turn_id = %s
                    """,
                    (
                        self._trim(assistant_output_text),
                        self._json_text(output_payload),
                        self._trim(status) or "completed",
                        self._trim(model_name),
                        self._json_text(normalized_usage),
                        int(turn_id),
                    ),
                )
                cursor.execute(
                    f"SELECT started_at, finished_at FROM {TURN_TABLE} WHERE turn_id = %s",
                    (int(turn_id),),
                )
                time_row = cursor.fetchone()
                if time_row:
                    started_at, finished_at = time_row
                    duration_ms = None
                    if (
                        hasattr(started_at, "__sub__")
                        and finished_at is not None
                    ):
                        try:
                            duration_ms = max(
                                0,
                                round(
                                    (finished_at - started_at).total_seconds()
                                    * 1_000
                                ),
                            )
                        except (AttributeError, TypeError, ValueError):
                            duration_ms = None
                    turn_meta = {
                        "started_at": (
                            started_at.isoformat()
                            if hasattr(started_at, "isoformat")
                            else str(started_at or "")
                        ),
                        "finished_at": (
                            finished_at.isoformat()
                            if hasattr(finished_at, "isoformat")
                            else str(finished_at or "")
                        ),
                        **(
                            {"duration_ms": duration_ms}
                            if duration_ms is not None
                            else {}
                        ),
                    }
                cursor.execute(
                    f"SELECT owner_type, owner_id, metadata_json FROM {THREAD_TABLE} WHERE thread_id = %s",
                    (int(thread_id),),
                )
                row = cursor.fetchone()
                owner_type = ""
                owner_id = ""
                metadata = {}
                if row:
                    owner_type = self._trim((row or [None, None, None])[0])
                    owner_id = self._trim((row or [None, None, None])[1])
                    metadata = self._safe_json_dict((row or [None, None, None])[2])
                usage_summary = metadata.get("token_usage_summary")
                if not isinstance(usage_summary, dict):
                    usage_summary = {
                        "turn_count": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    }
                usage_summary["turn_count"] = int(usage_summary.get("turn_count", 0) or 0) + 1
                usage_summary["prompt_tokens"] = int(usage_summary.get("prompt_tokens", 0) or 0) + normalized_usage["prompt_tokens"]
                usage_summary["completion_tokens"] = int(usage_summary.get("completion_tokens", 0) or 0) + normalized_usage["completion_tokens"]
                usage_summary["total_tokens"] = int(usage_summary.get("total_tokens", 0) or 0) + normalized_usage["total_tokens"]
                metadata["token_usage_summary"] = usage_summary
                cursor.execute(
                    f"UPDATE {THREAD_TABLE} SET latest_turn_id = %s, metadata_json = %s, updated_at = NOW(), last_event_at = NOW() WHERE thread_id = %s",
                    (int(turn_id), self._json_text(metadata), int(thread_id)),
                )
                if owner_type == "user" and owner_id and self._table_exists(cursor, USER_TABLE):
                    cursor.execute(
                        f"SELECT profile_json FROM {USER_TABLE} WHERE user_id = %s",
                        (owner_id,),
                    )
                    user_row = cursor.fetchone()
                    profile = self._safe_json_dict((user_row or [None])[0] if user_row else None)
                    usage = profile.get("token_usage_summary")
                    if not isinstance(usage, dict):
                        usage = {
                            "turn_count": 0,
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                        }
                    usage["turn_count"] = int(usage.get("turn_count", 0) or 0) + 1
                    usage["prompt_tokens"] = int(usage.get("prompt_tokens", 0) or 0) + normalized_usage["prompt_tokens"]
                    usage["completion_tokens"] = int(usage.get("completion_tokens", 0) or 0) + normalized_usage["completion_tokens"]
                    usage["total_tokens"] = int(usage.get("total_tokens", 0) or 0) + normalized_usage["total_tokens"]
                    profile["token_usage_summary"] = usage
                    cursor.execute(
                        f"UPDATE {USER_TABLE} SET profile_json = %s, updated_at = NOW() WHERE user_id = %s",
                        (self._json_text(profile), owner_id),
                    )
            db.conn.commit()
            return turn_meta
        finally:
            db.close_db()

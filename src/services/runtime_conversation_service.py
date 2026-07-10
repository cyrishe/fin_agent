import json
from typing import Any, Dict, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils
from src.services.user_session_service import USER_TABLE


THREAD_TABLE = "aiia_runtime_thread"
TURN_TABLE = "aiia_runtime_turn"


class RuntimeConversationService:
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
            return int(thread_id)
        db = StockInfoDbUtils()
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
        db = StockInfoDbUtils()
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

    def get_thread(self, *, thread_id: int) -> Dict[str, Any] | None:
        db = StockInfoDbUtils()
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT thread_id, title, owner_type, owner_id, status, created_at, updated_at, last_event_at
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
                ) = row
                return {
                    "thread_id": int(row_thread_id),
                    "title": self._trim(title) or f"会话 {row_thread_id}",
                    "owner_type": self._trim(owner_type),
                    "owner_id": self._trim(owner_id),
                    "status": self._trim(status) or "active",
                    "created_at": str(created_at or ""),
                    "updated_at": str(updated_at or ""),
                    "last_event_at": str(last_event_at or updated_at or created_at or ""),
                }
        finally:
            db.close_db()

    def list_turns(self, *, thread_id: int, limit: int = 80, include_output_payload: bool = True) -> list[Dict[str, Any]]:
        db = StockInfoDbUtils()
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
                      finished_at
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
                ) = row
                items.append(
                    {
                        "turn_id": int(turn_id),
                        "turn_no": int(turn_no),
                        "user_input_text": self._trim(user_input_text),
                        "input_payload": self._safe_json_dict(input_structured_json),
                        "assistant_output_text": self._trim(assistant_output_text),
                        "output_payload": self._compact_history_payload(self._safe_json_dict(output_structured_json)),
                        "status": self._trim(status) or "",
                        "started_at": str(started_at or ""),
                        "finished_at": str(finished_at or ""),
                    }
                )
            return items
        finally:
            db.close_db()

    def get_thread_context(self, *, thread_id: int) -> Dict[str, Any]:
        db = StockInfoDbUtils()
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
        db = StockInfoDbUtils()
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
        db = StockInfoDbUtils()
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
    ) -> int:
        db = StockInfoDbUtils()
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
                    ) VALUES (%s, %s, %s, %s, 'running', NOW())
                    """,
                    (
                        int(thread_id),
                        turn_no,
                        self._trim(user_input_text),
                        self._json_text(input_payload),
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
    ) -> None:
        normalized_usage = self._normalize_token_usage(token_usage)
        db = StockInfoDbUtils()
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
        finally:
            db.close_db()

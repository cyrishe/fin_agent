import datetime as dt
import hashlib
import secrets
from typing import Any, Dict, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


USER_TABLE = "aiia_user"
SESSION_TABLE = "aiia_user_session"


class UserSessionService:
    GUEST_COOKIE_NAME = "aiia_guest_user_id"
    THREAD_COOKIE_NAME = "aiia_assistant_thread_id"

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        text = str(value or "").strip()
        if not text.isdigit():
            return None
        return int(text)

    @staticmethod
    def _table_exists(cursor: pymysql.cursors.Cursor, table_name: str) -> bool:
        cursor.execute("SHOW TABLES LIKE %s", (str(table_name),))
        return bool(cursor.fetchone())

    @staticmethod
    def build_guest_user_id(*, user_agent: str = "", remote_addr: str = "") -> str:
        seed = "|".join(
            [
                UserSessionService._trim(user_agent)[:180],
                UserSessionService._trim(remote_addr)[:64],
            ]
        )
        if not seed.strip("|"):
            return f"guest_{secrets.token_hex(12)}"
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]
        return f"guest_{digest}"

    def ensure_guest_user(self, *, user_id: str, user_agent: str = "", remote_addr: str = "") -> Dict[str, Any]:
        normalized_user_id = self._trim(user_id)
        if not normalized_user_id:
            raise ValueError("user_id 不能为空")
        payload = {
            "user_id": normalized_user_id,
            "user_type": "guest",
            "display_name": "Guest",
        }
        db = StockInfoDbUtils()
        try:
            with db.conn.cursor() as cursor:
                if not self._table_exists(cursor, USER_TABLE):
                    return payload
                cursor.execute(
                    f"""
                    INSERT INTO {USER_TABLE} (
                      user_id, user_type, display_name, status, profile_json
                    ) VALUES (%s, 'guest', %s, 'active', JSON_OBJECT('remote_addr', %s, 'user_agent', %s))
                    ON DUPLICATE KEY UPDATE
                      status = 'active',
                      updated_at = NOW()
                    """,
                    (
                        normalized_user_id,
                        "Guest",
                        self._trim(remote_addr),
                        self._trim(user_agent)[:255],
                    ),
                )
            db.conn.commit()
            return payload
        finally:
            db.close_db()

    def ensure_guest_session(
        self,
        *,
        session_token: str,
        user_id: str,
        user_agent: str = "",
        remote_addr: str = "",
        ttl_days: int = 30,
    ) -> Dict[str, Any]:
        normalized_token = self._trim(session_token)
        normalized_user_id = self._trim(user_id)
        if not normalized_token or not normalized_user_id:
            return {"session_token": normalized_token, "user_id": normalized_user_id}
        expires_at = dt.datetime.now() + dt.timedelta(days=max(1, int(ttl_days or 30)))
        db = StockInfoDbUtils()
        try:
            with db.conn.cursor() as cursor:
                if not self._table_exists(cursor, SESSION_TABLE):
                    return {"session_token": normalized_token, "user_id": normalized_user_id}
                cursor.execute(
                    f"""
                    INSERT INTO {SESSION_TABLE} (
                      session_token, user_id, session_type, status,
                      user_agent, ip_address, expires_at, last_seen_at
                    ) VALUES (%s, %s, 'guest', 'active', %s, %s, %s, NOW())
                    ON DUPLICATE KEY UPDATE
                      user_id = VALUES(user_id),
                      status = 'active',
                      user_agent = VALUES(user_agent),
                      ip_address = VALUES(ip_address),
                      expires_at = VALUES(expires_at),
                      last_seen_at = NOW(),
                      updated_at = NOW()
                    """,
                    (
                        normalized_token,
                        normalized_user_id,
                        self._trim(user_agent)[:255],
                        self._trim(remote_addr)[:64],
                        expires_at.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
            db.conn.commit()
            return {"session_token": normalized_token, "user_id": normalized_user_id}
        finally:
            db.close_db()

    def resolve_or_create_guest(
        self,
        *,
        cookie_user_id: str = "",
        cookie_session_token: str = "",
        user_agent: str = "",
        remote_addr: str = "",
    ) -> Dict[str, Any]:
        user_id = self._trim(cookie_user_id) or self.build_guest_user_id(
            user_agent=user_agent,
            remote_addr=remote_addr,
        )
        session_token = self._trim(cookie_session_token) or f"gs_{secrets.token_hex(16)}"
        self.ensure_guest_user(user_id=user_id, user_agent=user_agent, remote_addr=remote_addr)
        self.ensure_guest_session(
            session_token=session_token,
            user_id=user_id,
            user_agent=user_agent,
            remote_addr=remote_addr,
        )
        return {
            "user_id": user_id,
            "user_type": "guest",
            "session_token": session_token,
        }

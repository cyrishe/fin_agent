import datetime as dt
import hashlib
import json
import secrets
from typing import Any, Dict, Optional

import pymysql

from src.utils.system_db_utils import SystemDbUtils


USER_TABLE = "aiia_user"
IDENTITY_TABLE = "aiia_user_identity"
CREDENTIAL_TABLE = "aiia_user_credential"
SESSION_TABLE = "aiia_user_session"
PHONE_CHALLENGE_TABLE = "aiia_phone_verification_challenge"


class UserIdentityConflictError(ValueError):
    """Raised when a globally unique external identity is already registered."""


class UserSessionStorageError(RuntimeError):
    """Raised when the system identity schema is unavailable or incomplete."""


class PhoneChallengeConsumptionError(ValueError):
    """Raised when a verified phone challenge cannot be consumed."""


class UserSessionService:
    GUEST_COOKIE_NAME = "aiia_guest_user_id"
    GUEST_SESSION_COOKIE_NAME = "aiia_guest_session_token"
    MEMBER_SESSION_COOKIE_NAME = "aiia_member_session_token"
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
        # A browser fingerprint is neither unique nor an authentication
        # credential. Guest identities must be opaque and unguessable; the
        # session token is then validated independently on every request.
        del user_agent, remote_addr
        return f"guest_{secrets.token_hex(16)}"

    @staticmethod
    def _member_session_storage_token(session_token: str) -> str:
        digest = hashlib.sha256(str(session_token or "").encode("utf-8")).hexdigest()
        return f"mh_{digest}"

    @staticmethod
    def mask_mobile(mobile: str) -> str:
        value = UserSessionService._trim(mobile)
        return f"{value[:3]}****{value[-4:]}" if len(value) == 11 else "***"

    @classmethod
    def _require_tables(cls, cursor: pymysql.cursors.Cursor, *table_names: str) -> None:
        missing = [table_name for table_name in table_names if not cls._table_exists(cursor, table_name)]
        if missing:
            raise UserSessionStorageError(f"system identity schema is incomplete: {', '.join(missing)}")

    def ensure_guest_user(self, *, user_id: str, user_agent: str = "", remote_addr: str = "") -> Dict[str, Any]:
        normalized_user_id = self._trim(user_id)
        if not normalized_user_id:
            raise ValueError("user_id 不能为空")
        payload = {
            "user_id": normalized_user_id,
            "user_type": "guest",
            "display_name": "Guest",
        }
        db = SystemDbUtils()
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
        db = SystemDbUtils()
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

    def resolve_guest_session(self, *, session_token: str, user_id: str) -> Optional[Dict[str, Any]]:
        normalized_token = self._trim(session_token)
        normalized_user_id = self._trim(user_id)
        if not normalized_token or not normalized_user_id:
            return None
        db = SystemDbUtils()
        try:
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                if not self._table_exists(cursor, SESSION_TABLE):
                    return None
                cursor.execute(
                    f"""
                    SELECT session_id, user_id
                    FROM {SESSION_TABLE}
                    WHERE session_token = %s
                      AND user_id = %s
                      AND session_type = 'guest'
                      AND status = 'active'
                      AND (expires_at IS NULL OR expires_at > NOW())
                    LIMIT 1
                    """,
                    (normalized_token, normalized_user_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                cursor.execute(
                    f"UPDATE {SESSION_TABLE} SET last_seen_at = NOW(), updated_at = NOW() WHERE session_id = %s",
                    (int(row["session_id"]),),
                )
            db.conn.commit()
            return {
                "user_id": normalized_user_id,
                "user_type": "guest",
                "session_token": normalized_token,
            }
        finally:
            db.close_db()

    def phone_identity_exists(self, *, mobile: str) -> bool:
        normalized_mobile = self._trim(mobile)
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                self._require_tables(cursor, IDENTITY_TABLE)
                cursor.execute(
                    f"""
                    SELECT 1
                    FROM {IDENTITY_TABLE}
                    WHERE identity_type = 'phone' AND identity_value = %s
                    LIMIT 1
                    """,
                    (normalized_mobile,),
                )
                return bool(cursor.fetchone())
        finally:
            db.close_db()

    def account_schema_ready(self) -> bool:
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                return all(
                    self._table_exists(cursor, table_name)
                    for table_name in (
                        USER_TABLE,
                        IDENTITY_TABLE,
                        CREDENTIAL_TABLE,
                        SESSION_TABLE,
                    )
                )
        finally:
            db.close_db()

    def registration_schema_ready(self) -> bool:
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                return all(
                    self._table_exists(cursor, table_name)
                    for table_name in (
                        USER_TABLE,
                        IDENTITY_TABLE,
                        CREDENTIAL_TABLE,
                        SESSION_TABLE,
                        PHONE_CHALLENGE_TABLE,
                    )
                )
        finally:
            db.close_db()

    def create_phone_member_with_session(
        self,
        *,
        mobile: str,
        password_hash: str,
        verification_provider: str,
        challenge_id: str,
        challenge_mobile_hash: str,
        verification_request_id: str = "",
        verified_at: str = "",
        user_agent: str = "",
        remote_addr: str = "",
        ttl_days: int = 30,
    ) -> Dict[str, Any]:
        """Atomically consume a verified phone challenge and create the account.

        The phone identity, password credential, initial login session, and
        one-time challenge consumption are one database transaction. A caller
        therefore never receives a registration failure after the account has
        already been committed.
        """

        normalized_mobile = self._trim(mobile)
        normalized_hash = self._trim(password_hash)
        normalized_challenge_id = self._trim(challenge_id)
        normalized_mobile_hash = self._trim(challenge_mobile_hash)
        if not all(
            (
                normalized_mobile,
                normalized_hash,
                normalized_challenge_id,
                normalized_mobile_hash,
            )
        ):
            raise ValueError(
                "mobile、password_hash、challenge_id 和 challenge_mobile_hash 不能为空"
            )

        user_id = f"user_{secrets.token_hex(16)}"
        display_name = f"用户 {self.mask_mobile(normalized_mobile)}"
        metadata = json.dumps(
            {
                "phone_possession_challenge_id": normalized_challenge_id,
                "verification_provider": self._trim(verification_provider),
                "verification_request_id": self._trim(verification_request_id),
                "verified_at": self._trim(verified_at),
            },
            ensure_ascii=False,
        )
        session_token = f"ms_{secrets.token_urlsafe(32)}"
        stored_token = self._member_session_storage_token(session_token)
        expires_at = dt.datetime.now() + dt.timedelta(days=max(1, int(ttl_days or 30)))

        db = SystemDbUtils()
        try:
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                self._require_tables(
                    cursor,
                    USER_TABLE,
                    IDENTITY_TABLE,
                    CREDENTIAL_TABLE,
                    SESSION_TABLE,
                    PHONE_CHALLENGE_TABLE,
                )
                cursor.execute(
                    f"""
                    SELECT challenge_id
                    FROM {PHONE_CHALLENGE_TABLE}
                    WHERE challenge_id = %s
                      AND mobile_hash = %s
                      AND purpose = 'registration'
                      AND send_succeeded_at IS NOT NULL
                      AND verified_at IS NOT NULL
                      AND consumed_at IS NULL
                      AND expires_at > UTC_TIMESTAMP(6)
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (normalized_challenge_id, normalized_mobile_hash),
                )
                if not cursor.fetchone():
                    raise PhoneChallengeConsumptionError(
                        "短信验证码已失效或已使用，请重新获取。"
                    )

                cursor.execute(
                    f"""
                    INSERT INTO {USER_TABLE} (
                      user_id, user_type, display_name, status, profile_json
                    ) VALUES (%s, 'member', %s, 'active', JSON_OBJECT('registration_channel', 'phone'))
                    """,
                    (user_id, display_name),
                )
                cursor.execute(
                    f"""
                    INSERT INTO {IDENTITY_TABLE} (
                      user_id, identity_type, identity_value, is_primary, metadata_json
                    ) VALUES (%s, 'phone', %s, 1, %s)
                    """,
                    (user_id, normalized_mobile, metadata),
                )
                cursor.execute(
                    f"""
                    INSERT INTO {CREDENTIAL_TABLE} (
                      user_id, credential_type, credential_hash
                    ) VALUES (%s, 'password', %s)
                    """,
                    (user_id, normalized_hash),
                )
                cursor.execute(
                    f"""
                    INSERT INTO {SESSION_TABLE} (
                      session_token, user_id, session_type, status,
                      user_agent, ip_address, expires_at, last_seen_at,
                      metadata_json
                    ) VALUES (%s, %s, 'login', 'active', %s, %s, %s, NOW(),
                      JSON_OBJECT('token_storage', 'sha256'))
                    """,
                    (
                        stored_token,
                        user_id,
                        self._trim(user_agent)[:255],
                        self._trim(remote_addr)[:64],
                        expires_at.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
                cursor.execute(
                    f"""
                    UPDATE {PHONE_CHALLENGE_TABLE}
                    SET consumed_at = UTC_TIMESTAMP(6),
                        updated_at = UTC_TIMESTAMP(6)
                    WHERE challenge_id = %s
                      AND consumed_at IS NULL
                    """,
                    (normalized_challenge_id,),
                )
                if int(cursor.rowcount or 0) != 1:
                    raise PhoneChallengeConsumptionError(
                        "短信验证码已失效或已使用，请重新获取。"
                    )
            db.conn.commit()
            return {
                "user_id": user_id,
                "user_type": "member",
                "display_name": display_name,
                "mobile_masked": self.mask_mobile(normalized_mobile),
                "session_token": session_token,
                "expires_at": expires_at.isoformat(timespec="seconds"),
            }
        except PhoneChallengeConsumptionError:
            db.conn.rollback()
            raise
        except pymysql.IntegrityError as exc:
            db.conn.rollback()
            if int(exc.args[0] or 0) == 1062:
                raise UserIdentityConflictError("该手机号已经注册") from exc
            raise UserSessionStorageError("无法创建手机号账户") from None
        except UserSessionStorageError:
            if db is not None:
                db.conn.rollback()
            raise
        except Exception:
            db.conn.rollback()
            raise UserSessionStorageError("无法创建手机号账户") from None
        finally:
            db.close_db()

    def get_phone_member(self, *, mobile: str) -> Optional[Dict[str, Any]]:
        normalized_mobile = self._trim(mobile)
        if not normalized_mobile:
            return None
        db = SystemDbUtils()
        try:
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                self._require_tables(cursor, USER_TABLE, IDENTITY_TABLE, CREDENTIAL_TABLE)
                cursor.execute(
                    f"""
                    SELECT
                      u.user_id,
                      u.user_type,
                      u.display_name,
                      i.identity_value AS mobile,
                      c.credential_hash
                    FROM {IDENTITY_TABLE} i
                    JOIN {USER_TABLE} u ON u.user_id = i.user_id
                    JOIN {CREDENTIAL_TABLE} c
                      ON c.user_id = u.user_id AND c.credential_type = 'password'
                    WHERE i.identity_type = 'phone'
                      AND i.identity_value = %s
                      AND u.status = 'active'
                    LIMIT 1
                    """,
                    (normalized_mobile,),
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        finally:
            db.close_db()

    def create_member_session(
        self,
        *,
        user_id: str,
        user_agent: str = "",
        remote_addr: str = "",
        ttl_days: int = 30,
    ) -> Dict[str, Any]:
        normalized_user_id = self._trim(user_id)
        if not normalized_user_id:
            raise ValueError("user_id 不能为空")
        session_token = f"ms_{secrets.token_urlsafe(32)}"
        stored_token = self._member_session_storage_token(session_token)
        expires_at = dt.datetime.now() + dt.timedelta(days=max(1, int(ttl_days or 30)))
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                self._require_tables(cursor, USER_TABLE, SESSION_TABLE)
                cursor.execute(
                    f"""
                    INSERT INTO {SESSION_TABLE} (
                      session_token, user_id, session_type, status,
                      user_agent, ip_address, expires_at, last_seen_at,
                      metadata_json
                    ) VALUES (%s, %s, 'login', 'active', %s, %s, %s, NOW(),
                      JSON_OBJECT('token_storage', 'sha256'))
                    """,
                    (
                        stored_token,
                        normalized_user_id,
                        self._trim(user_agent)[:255],
                        self._trim(remote_addr)[:64],
                        expires_at.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
            db.conn.commit()
            return {
                "session_token": session_token,
                "user_id": normalized_user_id,
                "expires_at": expires_at.isoformat(timespec="seconds"),
            }
        except Exception:
            db.conn.rollback()
            raise
        finally:
            db.close_db()

    def resolve_member_session(self, *, session_token: str) -> Optional[Dict[str, Any]]:
        normalized_token = self._trim(session_token)
        if not normalized_token:
            return None
        stored_token = self._member_session_storage_token(normalized_token)
        db = None
        try:
            db = SystemDbUtils()
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                self._require_tables(cursor, USER_TABLE, IDENTITY_TABLE, SESSION_TABLE)
                cursor.execute(
                    f"""
                    SELECT
                      s.session_id,
                      s.user_id,
                      u.user_type,
                      u.display_name,
                      i.identity_value AS mobile
                    FROM {SESSION_TABLE} s
                    JOIN {USER_TABLE} u ON u.user_id = s.user_id
                    LEFT JOIN {IDENTITY_TABLE} i
                      ON i.user_id = u.user_id
                     AND i.identity_type = 'phone'
                     AND i.is_primary = 1
                    WHERE s.session_token = %s
                      AND s.session_type = 'login'
                      AND s.status = 'active'
                      AND u.status = 'active'
                      AND (s.expires_at IS NULL OR s.expires_at > NOW())
                    LIMIT 1
                    """,
                    (stored_token,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                cursor.execute(
                    f"UPDATE {SESSION_TABLE} SET last_seen_at = NOW(), updated_at = NOW() WHERE session_id = %s",
                    (int(row["session_id"]),),
                )
            db.conn.commit()
            mobile = self._trim(row.get("mobile"))
            return {
                "user_id": self._trim(row.get("user_id")),
                "user_type": self._trim(row.get("user_type")) or "member",
                "display_name": self._trim(row.get("display_name")),
                "mobile_masked": self.mask_mobile(mobile) if mobile else "",
                "session_token": normalized_token,
            }
        except UserSessionStorageError:
            if db is not None:
                db.conn.rollback()
            raise
        except Exception:
            if db is not None:
                db.conn.rollback()
            raise UserSessionStorageError("会员会话存储暂时不可用") from None
        finally:
            if db is not None:
                db.close_db()

    def revoke_member_session(self, *, session_token: str) -> None:
        normalized_token = self._trim(session_token)
        if not normalized_token:
            return
        stored_token = self._member_session_storage_token(normalized_token)
        db = None
        try:
            db = SystemDbUtils()
            with db.conn.cursor() as cursor:
                self._require_tables(cursor, SESSION_TABLE)
                cursor.execute(
                    f"""
                    UPDATE {SESSION_TABLE}
                    SET status = 'revoked', updated_at = NOW()
                    WHERE session_token = %s AND session_type = 'login'
                    """,
                    (stored_token,),
                )
            db.conn.commit()
        except UserSessionStorageError:
            db.conn.rollback()
            raise
        except Exception:
            if db is not None:
                db.conn.rollback()
            raise UserSessionStorageError("会员会话存储暂时不可用") from None
        finally:
            if db is not None:
                db.close_db()

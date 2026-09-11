from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.services.auth_rate_limit_service import (
    AuthRateLimitError,
    AuthRateLimitService,
)
from src.services.phone_possession_verification_service import (
    PhonePossessionVerificationError,
    PhonePossessionVerificationService,
)
from src.services.phone_identity_verification_service import (
    PhoneIdentityVerificationService,
    PhoneVerificationError,
)
from src.services.user_session_service import (
    PhoneChallengeConsumptionError,
    UserIdentityConflictError,
    UserSessionService,
    UserSessionStorageError,
)


class PhoneAccountError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = str(code or "phone_account_error")
        self.public_message = str(message or "手机号账户操作失败")
        self.status_code = int(status_code)


def normalize_mainland_mobile(value: Any) -> str:
    raw = str(value or "").strip()
    compact = re.sub(r"[\s()-]", "", raw)
    if compact.startswith("+86"):
        compact = compact[3:]
    elif compact.startswith("0086"):
        compact = compact[4:]
    if not re.fullmatch(r"1[3-9][0-9]{9}", compact):
        raise PhoneAccountError(
            "invalid_mobile",
            "请输入有效的 11 位中国大陆手机号。",
            status_code=400,
        )
    return compact


def _normalize_real_name(value: Any) -> str:
    real_name = re.sub(r"\s+", " ", str(value or "").strip())
    if len(real_name) < 2 or len(real_name) > 50 or any(ord(char) < 32 for char in real_name):
        raise PhoneAccountError(
            "invalid_real_name",
            "请输入与手机号实名信息一致的姓名。",
            status_code=400,
        )
    return real_name


def _validate_password(value: Any) -> str:
    password = str(value or "")
    if len(password) < 8:
        raise PhoneAccountError("weak_password", "密码至少需要 8 个字符。", status_code=400)
    if len(password) > 128:
        raise PhoneAccountError("invalid_password", "密码不能超过 128 个字符。", status_code=400)
    return password


def _hash_password(password: str) -> str:
    rounds = 310_000
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return (
        f"pbkdf2_sha256${rounds}$"
        f"{base64.b64encode(salt).decode('ascii')}$"
        f"{base64.b64encode(digest).decode('ascii')}"
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds_text, salt_text, digest_text = str(encoded or "").split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        rounds = int(rounds_text)
        if rounds < 100_000 or rounds > 2_000_000:
            return False
        salt = base64.b64decode(salt_text, validate=True)
        expected = base64.b64decode(digest_text, validate=True)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


_DUMMY_PASSWORD_HASH = _hash_password(secrets.token_urlsafe(24))


class PhoneAccountService:
    def __init__(
        self,
        *,
        user_sessions: Optional[UserSessionService] = None,
        possession_verifier: Optional[PhonePossessionVerificationService] = None,
        verifier: Optional[PhoneIdentityVerificationService] = None,
        auth_rate_limiter: Optional[AuthRateLimitService] = None,
        identity_match_required: Optional[bool] = None,
    ) -> None:
        self.user_sessions = user_sessions or UserSessionService()
        self.possession_verifier = (
            possession_verifier or PhonePossessionVerificationService.from_env()
        )
        self.verifier = verifier or PhoneIdentityVerificationService.from_env()
        self.auth_rate_limiter = (
            auth_rate_limiter or AuthRateLimitService.from_env()
        )
        if identity_match_required is None:
            identity_match_required = str(
                os.environ.get("FIN_AGENT_PHONE_IDENTITY_MATCH_REQUIRED") or ""
            ).strip().lower() in {"1", "true", "yes", "on"}
        self.identity_match_required = bool(identity_match_required)

    def verification_status(self) -> Dict[str, Any]:
        possession_status = dict(self.possession_verifier.status())
        identity_status = dict(self.verifier.status())
        auth_rate_status = dict(self.auth_rate_limiter.status())
        try:
            storage_ready = bool(self.user_sessions.registration_schema_ready())
        except Exception:
            storage_ready = False
        try:
            login_protection_available = bool(
                auth_rate_status.get("enabled")
            ) and bool(self.auth_rate_limiter.schema_ready())
        except Exception:
            login_protection_available = False
        possession_available = bool(possession_status.get("enabled"))
        identity_available = bool(identity_status.get("enabled"))
        return {
            "available": (
                possession_available
                and storage_ready
                and login_protection_available
                and (not self.identity_match_required or identity_available)
            ),
            "provider": str(possession_status.get("provider") or "disabled"),
            "method": "sms_otp",
            "possession_available": possession_available,
            "identity_match_required": self.identity_match_required,
            "identity_match_available": identity_available,
            "identity_provider": str(identity_status.get("provider") or "disabled"),
            "storage_ready": storage_ready,
            "login_protection_available": login_protection_available,
            "required_registration_fields": [
                "mobile",
                "challenge_id",
                "verification_code",
                "password",
                "confirm_password",
            ] + (["real_name"] if self.identity_match_required else []),
        }

    @staticmethod
    def _seconds_until(value: Any) -> int:
        text = str(value or "").strip()
        if not text:
            return 0
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(
                0,
                int(
                    (
                        parsed.astimezone(timezone.utc)
                        - datetime.now(timezone.utc)
                    ).total_seconds()
                ),
            )
        except ValueError:
            return 0

    @staticmethod
    def _map_possession_error(
        exc: PhonePossessionVerificationError,
    ) -> PhoneAccountError:
        code = str(getattr(exc, "code", "") or "phone_verification_unavailable")
        retry_after = getattr(exc, "retry_after_seconds", None)
        if code in {
            "invalid_mobile",
            "phone_code_invalid",
            "phone_code_consumed",
            "phone_code_expired",
            "phone_code_attempts_exceeded",
            "phone_code_not_ready",
        }:
            status_code = 400
        elif code in {
            "phone_code_rate_limited",
            "phone_code_resend_too_soon",
            "phone_code_request_busy",
            "phone_code_request_in_progress",
            "phone_code_budget_exhausted",
        }:
            status_code = 429
        else:
            status_code = 503
        error = PhoneAccountError(
            code,
            str(
                getattr(exc, "public_message", "")
                or "手机号验证服务暂时不可用，请稍后再试。"
            ),
            status_code=status_code,
        )
        if retry_after is not None:
            error.retry_after_seconds = max(1, int(retry_after))
        return error

    @staticmethod
    def _map_auth_rate_error(exc: AuthRateLimitError) -> PhoneAccountError:
        status_code = 429 if exc.code in {
            "auth_rate_limited",
            "auth_rate_limit_busy",
        } else 503
        error = PhoneAccountError(
            exc.code,
            exc.public_message,
            status_code=status_code,
        )
        if exc.retry_after is not None:
            error.retry_after_seconds = int(exc.retry_after)
        return error

    def request_registration_code(
        self,
        *,
        mobile: Any,
        remote_addr: str = "",
    ) -> Dict[str, Any]:
        normalized_mobile = normalize_mainland_mobile(mobile)
        try:
            storage_ready = bool(self.user_sessions.registration_schema_ready())
        except Exception:
            storage_ready = False
        if not storage_ready:
            raise PhoneAccountError(
                "account_storage_unavailable",
                "账户存储尚未完成初始化，请稍后再试。",
                status_code=503,
            )
        try:
            login_protection_ready = bool(
                self.auth_rate_limiter.status().get("enabled")
            ) and bool(self.auth_rate_limiter.schema_ready())
        except Exception:
            login_protection_ready = False
        if not login_protection_ready:
            raise PhoneAccountError(
                "login_protection_unavailable",
                "登录保护服务尚未完成配置，请稍后再试。",
                status_code=503,
            )
        if self.user_sessions.phone_identity_exists(mobile=normalized_mobile):
            raise PhoneAccountError(
                "phone_already_registered",
                "该手机号已经注册，请直接登录。",
                status_code=409,
            )
        try:
            challenge = self.possession_verifier.request_code(
                normalized_mobile,
                remote_addr,
            )
        except PhonePossessionVerificationError as exc:
            raise self._map_possession_error(exc) from exc
        result = {
            "challenge_id": str(challenge.get("challenge_id") or ""),
            "mobile_masked": self.user_sessions.mask_mobile(normalized_mobile),
            "expires_in_seconds": self._seconds_until(challenge.get("expires_at")),
            "resend_after_seconds": self._seconds_until(
                challenge.get("resend_after")
            ),
        }
        if (
            str(challenge.get("provider") or "") == "mock"
            and challenge.get("debug_code")
        ):
            result["debug_code"] = str(challenge.get("debug_code"))
        return result

    def register(
        self,
        *,
        mobile: Any,
        challenge_id: Any,
        verification_code: Any,
        password: Any,
        confirm_password: Any,
        real_name: Any = "",
        user_agent: str = "",
        remote_addr: str = "",
    ) -> Dict[str, Any]:
        normalized_mobile = normalize_mainland_mobile(mobile)
        normalized_password = _validate_password(password)
        if normalized_password != str(confirm_password or ""):
            raise PhoneAccountError(
                "password_mismatch",
                "两次输入的密码不一致。",
                status_code=400,
            )
        try:
            storage_ready = bool(self.user_sessions.registration_schema_ready())
        except Exception:
            storage_ready = False
        if not storage_ready:
            raise PhoneAccountError(
                "account_storage_unavailable",
                "账户存储尚未完成初始化，请稍后再试。",
                status_code=503,
            )
        try:
            login_protection_ready = bool(
                self.auth_rate_limiter.status().get("enabled")
            ) and bool(self.auth_rate_limiter.schema_ready())
        except Exception:
            login_protection_ready = False
        if not login_protection_ready:
            raise PhoneAccountError(
                "login_protection_unavailable",
                "登录保护服务尚未完成配置，请稍后再试。",
                status_code=503,
            )
        if self.user_sessions.phone_identity_exists(mobile=normalized_mobile):
            raise PhoneAccountError(
                "phone_already_registered",
                "该手机号已经注册，请直接登录。",
                status_code=409,
            )

        try:
            possession_proof = self.possession_verifier.verify_code(
                str(challenge_id or "").strip(),
                mobile=normalized_mobile,
                code=str(verification_code or "").strip(),
            )
        except PhonePossessionVerificationError as exc:
            raise self._map_possession_error(exc) from exc

        identity_provider = ""
        identity_request_id = ""
        identity_verified_at = ""
        if self.identity_match_required:
            normalized_real_name = _normalize_real_name(real_name)
            try:
                identity_attempt_id = self.auth_rate_limiter.begin_attempt(
                    "identity_match",
                    normalized_mobile,
                    remote_addr,
                )
            except AuthRateLimitError as exc:
                raise self._map_auth_rate_error(exc) from exc
            try:
                identity_verification = self.verifier.verify(
                    real_name=normalized_real_name,
                    mobile=normalized_mobile,
                )
            except PhoneVerificationError as exc:
                code = str(
                    getattr(exc, "code", "") or "verification_unavailable"
                )
                message = str(
                    getattr(exc, "public_message", "")
                    or "手机号实名核验服务暂时不可用，请稍后再试。"
                )
                status_code = 422 if code in {
                    "identity_mismatch",
                    "identity_not_found",
                    "phone_identity_mismatch",
                    "phone_identity_not_found",
                } else 503
                if status_code == 503:
                    try:
                        self.auth_rate_limiter.mark_succeeded(
                            identity_attempt_id
                        )
                    except AuthRateLimitError as rate_exc:
                        raise self._map_auth_rate_error(rate_exc) from rate_exc
                raise PhoneAccountError(
                    code,
                    message,
                    status_code=status_code,
                ) from exc
            if not bool(identity_verification.passed):
                raise PhoneAccountError(
                    "identity_mismatch",
                    str(
                        identity_verification.message
                        or "姓名与手机号实名信息不一致。"
                    ),
                    status_code=422,
                )
            try:
                self.auth_rate_limiter.mark_succeeded(identity_attempt_id)
            except AuthRateLimitError as exc:
                raise self._map_auth_rate_error(exc) from exc
            identity_provider = str(identity_verification.provider or "")
            identity_request_id = str(identity_verification.request_id or "")
            identity_verified_at = str(identity_verification.verified_at or "")

        verification_provider = f"sms:{possession_proof.provider or 'unknown'}"
        if identity_provider:
            verification_provider = (
                f"{verification_provider}+identity:{identity_provider}"
            )
        verification_request_id = (
            identity_request_id
            or str(possession_proof.provider_request_id or "")
        )
        verified_at = identity_verified_at or str(
            possession_proof.verified_at or ""
        )

        try:
            member_with_session = self.user_sessions.create_phone_member_with_session(
                mobile=normalized_mobile,
                password_hash=_hash_password(normalized_password),
                verification_provider=verification_provider,
                verification_request_id=verification_request_id,
                verified_at=verified_at,
                challenge_id=str(possession_proof.challenge_id or ""),
                challenge_mobile_hash=str(possession_proof.mobile_hash or ""),
                user_agent=user_agent,
                remote_addr=remote_addr,
            )
        except PhoneChallengeConsumptionError as exc:
            raise PhoneAccountError(
                "phone_code_consumed",
                "短信验证码已失效或已使用，请重新获取。",
                status_code=400,
            ) from exc
        except UserSessionStorageError as exc:
            raise PhoneAccountError(
                "account_storage_unavailable",
                "账户服务暂时不可用，请稍后重试。",
                status_code=503,
            ) from exc
        except UserIdentityConflictError as exc:
            raise PhoneAccountError(
                "phone_already_registered",
                "该手机号已经注册，请直接登录。",
                status_code=409,
            ) from exc
        return {
            "user": {
                key: value
                for key, value in member_with_session.items()
                if key not in {"session_token", "expires_at"}
            },
            "session_token": str(
                member_with_session.get("session_token") or ""
            ),
            "expires_at": str(member_with_session.get("expires_at") or ""),
        }

    def login(
        self,
        *,
        mobile: Any,
        password: Any,
        user_agent: str = "",
        remote_addr: str = "",
    ) -> Dict[str, Any]:
        normalized_mobile = normalize_mainland_mobile(mobile)
        normalized_password = str(password or "")
        try:
            storage_ready = bool(self.user_sessions.account_schema_ready())
        except Exception:
            storage_ready = False
        if not storage_ready:
            raise PhoneAccountError(
                "account_storage_unavailable",
                "账户存储尚未完成初始化，请稍后再试。",
                status_code=503,
            )
        try:
            limiter_ready = bool(self.auth_rate_limiter.status().get("enabled"))
            limiter_ready = limiter_ready and bool(
                self.auth_rate_limiter.schema_ready()
            )
        except Exception:
            limiter_ready = False
        if not limiter_ready:
            raise PhoneAccountError(
                "login_protection_unavailable",
                "登录保护服务尚未完成配置，请稍后再试。",
                status_code=503,
            )
        try:
            attempt_id = self.auth_rate_limiter.begin_attempt(
                "login",
                normalized_mobile,
                remote_addr,
            )
        except AuthRateLimitError as exc:
            raise self._map_auth_rate_error(exc) from exc
        member = self.user_sessions.get_phone_member(mobile=normalized_mobile)
        password_matches = _verify_password(
            normalized_password,
            str(member.get("credential_hash") or "") if member else _DUMMY_PASSWORD_HASH,
        )
        if not member or not password_matches:
            raise PhoneAccountError(
                "invalid_credentials",
                "手机号或密码不正确。",
                status_code=401,
            )
        try:
            self.auth_rate_limiter.mark_succeeded(attempt_id)
        except AuthRateLimitError as exc:
            raise PhoneAccountError(
                exc.code,
                exc.public_message,
                status_code=503,
            ) from exc
        session = self.user_sessions.create_member_session(
            user_id=str(member.get("user_id") or ""),
            user_agent=user_agent,
            remote_addr=remote_addr,
        )
        return {
            "user": {
                "user_id": str(member.get("user_id") or ""),
                "user_type": str(member.get("user_type") or "member"),
                "display_name": str(member.get("display_name") or ""),
                "mobile_masked": self.user_sessions.mask_mobile(str(member.get("mobile") or "")),
            },
            "session_token": str(session.get("session_token") or ""),
            "expires_at": str(session.get("expires_at") or ""),
        }

    def logout(self, *, session_token: str) -> None:
        self.user_sessions.revoke_member_session(session_token=session_token)

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.services.phone_account_service import (
    PhoneAccountError,
    PhoneAccountService,
    normalize_mainland_mobile,
)
from src.services.phone_possession_verification_service import (
    PhonePossessionProof,
    PhonePossessionVerificationError,
)
from src.services.phone_identity_verification_service import (
    PhoneVerificationError,
    PhoneVerificationResult,
)


@dataclass
class _Verifier:
    error: PhoneVerificationError | None = None
    calls: int = 0
    last_name: str = ""
    last_mobile: str = ""

    def status(self) -> dict:
        return {"provider": "mock", "enabled": True}

    def verify(self, *, real_name: str, mobile: str) -> PhoneVerificationResult:
        self.calls += 1
        self.last_name = real_name
        self.last_mobile = mobile
        if self.error:
            raise self.error
        return PhoneVerificationResult(
            passed=True,
            provider="mock",
            code="verified",
            public_message="手机号实名核验通过。",
            verified_at="2026-07-30T10:00:00+00:00",
            request_id="request-test",
        )


@dataclass
class _Possession:
    error: PhonePossessionVerificationError | None = None
    verify_calls: int = 0
    request_calls: int = 0

    def status(self) -> dict:
        return {"provider": "mock", "enabled": True}

    @staticmethod
    def schema_ready() -> bool:
        return True

    def request_code(self, mobile: str, remote_addr: str) -> dict:
        self.request_calls += 1
        assert mobile == "13800138000"
        assert remote_addr
        return {
            "challenge_id": "pvc_challenge_123",
            "provider": "mock",
            "expires_at": "2099-01-01T00:10:00+00:00",
            "resend_after": "2099-01-01T00:01:00+00:00",
            "debug_code": "123456",
        }

    def verify_code(self, challenge_id: str, mobile: str, code: str) -> PhonePossessionProof:
        self.verify_calls += 1
        if self.error:
            raise self.error
        assert challenge_id == "pvc_challenge_123"
        assert mobile == "13800138000"
        assert code == "123456"
        return PhonePossessionProof(
            challenge_id=challenge_id,
            mobile_hash="mobile-hmac",
            provider="mock",
            provider_request_id="sms-request",
            verified_at="2026-07-30T10:00:00+00:00",
        )


class _Sessions:
    def __init__(self) -> None:
        self.members: dict[str, dict] = {}
        self.revoked: list[str] = []
        self.last_session_context: dict = {}

    @staticmethod
    def mask_mobile(mobile: str) -> str:
        return f"{mobile[:3]}****{mobile[-4:]}"

    @staticmethod
    def account_schema_ready() -> bool:
        return True

    @staticmethod
    def registration_schema_ready() -> bool:
        return True

    def phone_identity_exists(self, *, mobile: str) -> bool:
        return mobile in self.members

    def create_phone_member(self, *, mobile: str, password_hash: str, **_kwargs) -> dict:
        member = {
            "user_id": "user_test",
            "user_type": "member",
            "display_name": "用户 138****8000",
            "mobile": mobile,
            "mobile_masked": self.mask_mobile(mobile),
            "credential_hash": password_hash,
        }
        self.members[mobile] = member
        return dict(member)

    def get_phone_member(self, *, mobile: str):
        member = self.members.get(mobile)
        return dict(member) if member else None

    def create_member_session(self, *, user_id: str, user_agent: str, remote_addr: str) -> dict:
        self.last_session_context = {
            "user_id": user_id,
            "user_agent": user_agent,
            "remote_addr": remote_addr,
        }
        return {
            "session_token": "ms_secret_cookie_only",
            "expires_at": "2026-08-29T10:00:00",
        }

    def create_phone_member_with_session(
        self,
        *,
        mobile: str,
        password_hash: str,
        challenge_id: str,
        challenge_mobile_hash: str,
        user_agent: str,
        remote_addr: str,
        **kwargs,
    ) -> dict:
        assert challenge_id == "pvc_challenge_123"
        assert challenge_mobile_hash == "mobile-hmac"
        member = self.create_phone_member(
            mobile=mobile,
            password_hash=password_hash,
            **kwargs,
        )
        session = self.create_member_session(
            user_id=member["user_id"],
            user_agent=user_agent,
            remote_addr=remote_addr,
        )
        return {**member, **session}

    def revoke_member_session(self, *, session_token: str) -> None:
        self.revoked.append(session_token)


class _AuthLimiter:
    def __init__(self) -> None:
        self.started: list[tuple[str, str, str]] = []
        self.succeeded: list[str] = []

    @staticmethod
    def status() -> dict:
        return {"enabled": True}

    @staticmethod
    def schema_ready() -> bool:
        return True

    def begin_attempt(self, action: str, subject: str, remote_addr: str) -> str:
        self.started.append((action, subject, remote_addr))
        return f"ara_test_attempt_{len(self.started):04d}"

    def mark_succeeded(self, attempt_id: str) -> None:
        self.succeeded.append(attempt_id)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("13800138000", "13800138000"),
        ("+86 138-0013-8000", "13800138000"),
        ("0086(138)00138000", "13800138000"),
    ],
)
def test_mobile_is_canonicalized_before_identity_lookup(raw: str, expected: str) -> None:
    assert normalize_mainland_mobile(raw) == expected


@pytest.mark.parametrize("raw", ["", "12800138000", "138٠٠١٣٨٠٠٠", "+1 2025550123"])
def test_mobile_rejects_non_mainland_or_non_ascii_numbers(raw: str) -> None:
    with pytest.raises(PhoneAccountError) as captured:
        normalize_mainland_mobile(raw)
    assert captured.value.code == "invalid_mobile"


def test_register_verifies_once_hashes_password_and_returns_member_session() -> None:
    sessions = _Sessions()
    verifier = _Verifier()
    possession = _Possession()
    service = PhoneAccountService(
        user_sessions=sessions,
        possession_verifier=possession,
        verifier=verifier,
        auth_rate_limiter=_AuthLimiter(),
    )

    result = service.register(
        mobile="+86 138-0013-8000",
        challenge_id="pvc_challenge_123",
        verification_code="123456",
        password="correct horse",
        confirm_password="correct horse",
        user_agent="test-agent",
        remote_addr="127.0.0.1",
    )

    assert possession.verify_calls == 1
    assert verifier.calls == 0
    assert sessions.members["13800138000"]["credential_hash"].startswith("pbkdf2_sha256$")
    assert "correct horse" not in sessions.members["13800138000"]["credential_hash"]
    assert result["session_token"] == "ms_secret_cookie_only"
    assert result["user"]["mobile_masked"] == "138****8000"


def test_duplicate_phone_is_rejected_before_paid_verification() -> None:
    sessions = _Sessions()
    sessions.members["13800138000"] = {"user_id": "existing"}
    verifier = _Verifier()
    possession = _Possession()
    service = PhoneAccountService(
        user_sessions=sessions,
        possession_verifier=possession,
        verifier=verifier,
        auth_rate_limiter=_AuthLimiter(),
    )

    with pytest.raises(PhoneAccountError) as captured:
        service.register(
            mobile="13800138000",
            challenge_id="pvc_challenge_123",
            verification_code="123456",
            password="correct horse",
            confirm_password="correct horse",
            remote_addr="127.0.0.1",
        )

    assert captured.value.code == "phone_already_registered"
    assert captured.value.status_code == 409
    assert possession.verify_calls == 0
    assert verifier.calls == 0


def test_provider_mismatch_is_a_stable_user_error() -> None:
    verifier = _Verifier(
        error=PhoneVerificationError(
            "phone_identity_mismatch",
            "姓名与手机号实名信息不一致，请检查后重试。",
            False,
        )
    )
    auth_limiter = _AuthLimiter()
    service = PhoneAccountService(
        user_sessions=_Sessions(),
        possession_verifier=_Possession(),
        verifier=verifier,
        auth_rate_limiter=auth_limiter,
        identity_match_required=True,
    )

    with pytest.raises(PhoneAccountError) as captured:
        service.register(
            real_name="张三",
            mobile="13800138000",
            challenge_id="pvc_challenge_123",
            verification_code="123456",
            password="correct horse",
            confirm_password="correct horse",
            remote_addr="127.0.0.1",
        )

    assert captured.value.code == "phone_identity_mismatch"
    assert captured.value.status_code == 422
    assert auth_limiter.started == [
        ("identity_match", "13800138000", "127.0.0.1")
    ]
    assert auth_limiter.succeeded == []


def test_registration_fails_closed_without_shared_login_protection() -> None:
    class _UnavailableLimiter(_AuthLimiter):
        @staticmethod
        def status() -> dict:
            return {"enabled": False}

    possession = _Possession()
    service = PhoneAccountService(
        user_sessions=_Sessions(),
        possession_verifier=possession,
        verifier=_Verifier(),
        auth_rate_limiter=_UnavailableLimiter(),
    )

    with pytest.raises(PhoneAccountError) as captured:
        service.register(
            mobile="13800138000",
            challenge_id="pvc_challenge_123",
            verification_code="123456",
            password="correct horse",
            confirm_password="correct horse",
            remote_addr="127.0.0.1",
        )

    assert captured.value.code == "login_protection_unavailable"
    assert captured.value.status_code == 503
    assert possession.verify_calls == 0


def test_registration_code_uses_the_same_shared_protection_gate() -> None:
    possession = _Possession()
    service = PhoneAccountService(
        user_sessions=_Sessions(),
        possession_verifier=possession,
        verifier=_Verifier(),
        auth_rate_limiter=_AuthLimiter(),
    )

    result = service.request_registration_code(
        mobile="+86 138-0013-8000",
        remote_addr="127.0.0.1",
    )

    assert result["challenge_id"] == "pvc_challenge_123"
    assert result["mobile_masked"] == "138****8000"
    assert result["debug_code"] == "123456"
    assert possession.request_calls == 1


def test_registration_code_fails_before_sending_without_shared_protection() -> None:
    class _UnavailableLimiter(_AuthLimiter):
        @staticmethod
        def schema_ready() -> bool:
            return False

    possession = _Possession()
    service = PhoneAccountService(
        user_sessions=_Sessions(),
        possession_verifier=possession,
        verifier=_Verifier(),
        auth_rate_limiter=_UnavailableLimiter(),
    )

    with pytest.raises(PhoneAccountError) as captured:
        service.request_registration_code(
            mobile="13800138000",
            remote_addr="127.0.0.1",
        )

    assert captured.value.code == "login_protection_unavailable"
    assert captured.value.status_code == 503
    assert possession.request_calls == 0


def test_missing_account_schema_stops_before_paid_verification() -> None:
    sessions = _Sessions()
    sessions.registration_schema_ready = lambda: False  # type: ignore[method-assign]
    verifier = _Verifier()
    possession = _Possession()
    service = PhoneAccountService(
        user_sessions=sessions,
        possession_verifier=possession,
        verifier=verifier,
    )

    with pytest.raises(PhoneAccountError) as captured:
        service.register(
            mobile="13800138000",
            challenge_id="pvc_challenge_123",
            verification_code="123456",
            password="correct horse",
            confirm_password="correct horse",
            remote_addr="127.0.0.1",
        )

    assert captured.value.code == "account_storage_unavailable"
    assert captured.value.status_code == 503
    assert possession.verify_calls == 0
    assert verifier.calls == 0


def test_login_checks_password_and_issues_a_new_session() -> None:
    sessions = _Sessions()
    auth_limiter = _AuthLimiter()
    service = PhoneAccountService(
        user_sessions=sessions,
        possession_verifier=_Possession(),
        verifier=_Verifier(),
        auth_rate_limiter=auth_limiter,
    )
    service.register(
        mobile="13800138000",
        challenge_id="pvc_challenge_123",
        verification_code="123456",
        password="correct horse",
        confirm_password="correct horse",
        remote_addr="127.0.0.1",
    )

    logged_in = service.login(
        mobile="13800138000",
        password="correct horse",
        user_agent="browser",
        remote_addr="127.0.0.2",
    )

    assert logged_in["user"]["user_id"] == "user_test"
    assert logged_in["session_token"] == "ms_secret_cookie_only"
    assert sessions.last_session_context["remote_addr"] == "127.0.0.2"
    assert auth_limiter.succeeded == ["ara_test_attempt_0001"]

    with pytest.raises(PhoneAccountError) as captured:
        service.login(
            mobile="13800138000",
            password="wrong password",
            remote_addr="127.0.0.3",
        )
    assert captured.value.code == "invalid_credentials"
    assert captured.value.status_code == 401
    assert len(auth_limiter.started) == 2
    assert auth_limiter.succeeded == ["ara_test_attempt_0001"]

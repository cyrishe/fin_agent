from __future__ import annotations

from flask import Flask

from src.services.phone_account_service import PhoneAccountError
from src.services.user_session_service import UserSessionService, UserSessionStorageError
from src.web.auth_routes import create_auth_blueprint


class _AuthService:
    def __init__(self) -> None:
        self.revoked = ""
        self.registration_error: PhoneAccountError | None = None
        self.registration_kwargs: dict = {}
        self.code_kwargs: dict = {}

    def verification_status(self) -> dict:
        return {
            "available": True,
            "provider": "mock",
            "method": "sms_otp",
            "storage_ready": True,
            "required_registration_fields": [
                "mobile",
                "challenge_id",
                "verification_code",
                "password",
                "confirm_password",
            ],
        }

    def register(self, **kwargs) -> dict:
        self.registration_kwargs = dict(kwargs)
        if self.registration_error:
            raise self.registration_error
        return {
            "user": {
                "user_id": "user_register",
                "user_type": "member",
                "display_name": "用户 138****8000",
                "mobile_masked": "138****8000",
            },
            "session_token": "ms_registration_secret",
        }

    def request_registration_code(self, **kwargs) -> dict:
        self.code_kwargs = dict(kwargs)
        return {
            "challenge_id": "pvc_route_test",
            "mobile_masked": "138****8000",
            "expires_in_seconds": 600,
            "resend_after_seconds": 60,
        }

    def login(self, **_kwargs) -> dict:
        return {
            "user": {
                "user_id": "user_login",
                "user_type": "member",
                "display_name": "用户 139****9000",
                "mobile_masked": "139****9000",
            },
            "session_token": "ms_login_secret",
        }

    def logout(self, *, session_token: str) -> None:
        self.revoked = session_token


def _app(service: _AuthService, identity=None) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(
        create_auth_blueprint(
            service=service,
            member_identity_resolver=lambda: identity,
        )
    )
    return app


def test_register_sets_httponly_cookie_without_returning_session_token() -> None:
    service = _AuthService()
    response = _app(service).test_client().post(
        "/api/auth/register",
        json={
            "mobile": "13800138000",
            "challenge_id": "pvc_route_test",
            "verification_code": "123456",
            "password": "correct horse",
            "confirm_password": "correct horse",
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["authenticated"] is True
    assert payload["user"]["mobile_masked"] == "138****8000"
    assert "session_token" not in payload
    assert service.registration_kwargs["challenge_id"] == "pvc_route_test"
    assert service.registration_kwargs["verification_code"] == "123456"
    cookies = response.headers.getlist("Set-Cookie")
    assert any(
        cookie.startswith(f"{UserSessionService.MEMBER_SESSION_COOKIE_NAME}=ms_registration_secret")
        and "HttpOnly" in cookie
        and "SameSite=Lax" in cookie
        for cookie in cookies
    )


def test_register_maps_domain_errors_without_provider_details() -> None:
    service = _AuthService()
    service.registration_error = PhoneAccountError(
        "phone_identity_mismatch",
        "姓名与手机号实名信息不一致。",
        status_code=422,
    )
    response = _app(service).test_client().post(
        "/api/auth/register",
        json={
            "real_name": "张三",
            "mobile": "13800138000",
            "challenge_id": "pvc_route_test",
            "verification_code": "123456",
            "password": "correct horse",
            "confirm_password": "correct horse",
        },
    )

    assert response.status_code == 422
    assert response.get_json() == {
        "ok": False,
        "code": "phone_identity_mismatch",
        "error": "姓名与手机号实名信息不一致。",
    }


def test_auth_json_endpoints_reject_cross_origin_and_wrong_content_type(monkeypatch) -> None:
    monkeypatch.delenv("FIN_AGENT_ALLOWED_ORIGINS", raising=False)
    client = _app(_AuthService()).test_client()

    wrong_type = client.post("/api/auth/login", data="mobile=13800138000")
    cross_origin = client.post(
        "/api/auth/login",
        json={"mobile": "13800138000", "password": "correct horse"},
        headers={"Origin": "https://evil.example"},
    )

    assert wrong_type.status_code == 415
    assert wrong_type.get_json()["code"] == "unsupported_media_type"
    assert cross_origin.status_code == 403
    assert cross_origin.get_json()["code"] == "invalid_origin"


def test_auth_json_endpoint_accepts_configured_public_frontend_origin(monkeypatch) -> None:
    monkeypatch.setenv(
        "FIN_AGENT_ALLOWED_ORIGINS",
        "http://127.0.0.1:22054, http://localhost:22054",
    )
    service = _AuthService()

    response = _app(service).test_client().post(
        "/api/auth/registration-code",
        json={"mobile": "13800138000"},
        headers={
            "Host": "127.0.0.1:22053",
            "Origin": "http://127.0.0.1:22054",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["challenge_id"] == "pvc_route_test"
    assert service.code_kwargs["mobile"] == "13800138000"


def test_auth_json_endpoint_rejects_malformed_origin(monkeypatch) -> None:
    monkeypatch.setenv("FIN_AGENT_ALLOWED_ORIGINS", "http://127.0.0.1:22054")

    response = _app(_AuthService()).test_client().post(
        "/api/auth/login",
        json={"mobile": "13800138000", "password": "correct horse"},
        headers={"Origin": "null"},
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "invalid_origin"


def test_registration_code_endpoint_returns_only_public_challenge_data() -> None:
    service = _AuthService()

    response = _app(service).test_client().post(
        "/api/auth/registration-code",
        json={"mobile": "+86 138-0013-8000"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "challenge_id": "pvc_route_test",
        "mobile_masked": "138****8000",
        "expires_in_seconds": 600,
        "resend_after_seconds": 60,
    }
    assert service.code_kwargs["mobile"] == "+86 138-0013-8000"


def test_session_exposes_only_masked_identity_and_logout_is_idempotent() -> None:
    service = _AuthService()
    identity = {
        "user_id": "user_member",
        "user_type": "member",
        "display_name": "用户 138****8000",
        "mobile_masked": "138****8000",
    }
    client = _app(service, identity=identity).test_client()
    client.set_cookie(UserSessionService.MEMBER_SESSION_COOKIE_NAME, "ms_logout_secret")

    session = client.get("/api/auth/session")
    logout = client.post("/api/auth/logout", json={})

    assert session.get_json()["user"] == {
        "user_id": "user_member",
        "display_name": "用户 138****8000",
        "mobile_masked": "138****8000",
    }
    assert service.revoked == "ms_logout_secret"
    assert logout.status_code == 200
    assert any(
        cookie.startswith(f"{UserSessionService.MEMBER_SESSION_COOKIE_NAME}=;")
        for cookie in logout.headers.getlist("Set-Cookie")
    )


def test_session_store_failure_is_503_and_does_not_clear_member_cookie() -> None:
    service = _AuthService()

    def unavailable_identity():
        raise UserSessionStorageError("database unavailable")

    app = Flask(__name__)
    app.register_blueprint(
        create_auth_blueprint(
            service=service,
            member_identity_resolver=unavailable_identity,
        )
    )
    client = app.test_client()
    client.set_cookie(UserSessionService.MEMBER_SESSION_COOKIE_NAME, "ms_still_valid")

    response = client.get("/api/auth/session")

    assert response.status_code == 503
    assert response.get_json()["code"] == "identity_storage_unavailable"
    assert not any(
        cookie.startswith(f"{UserSessionService.MEMBER_SESSION_COOKIE_NAME}=;")
        for cookie in response.headers.getlist("Set-Cookie")
    )


def test_logout_store_failure_does_not_report_success_or_clear_cookie() -> None:
    service = _AuthService()

    def unavailable_logout(*, session_token: str) -> None:
        del session_token
        raise UserSessionStorageError("database unavailable")

    service.logout = unavailable_logout  # type: ignore[method-assign]
    client = _app(service).test_client()
    client.set_cookie(UserSessionService.MEMBER_SESSION_COOKIE_NAME, "ms_still_valid")

    response = client.post("/api/auth/logout", json={})

    assert response.status_code == 503
    assert response.get_json()["code"] == "logout_unavailable"
    assert not any(
        cookie.startswith(f"{UserSessionService.MEMBER_SESSION_COOKIE_NAME}=;")
        for cookie in response.headers.getlist("Set-Cookie")
    )


def test_auth_responses_are_private_and_small_json_limit_is_enforced() -> None:
    client = _app(_AuthService()).test_client()

    session = client.get("/api/auth/session")
    oversized = client.post(
        "/api/auth/login",
        data=b"x" * (32 * 1024 + 1),
        content_type="application/json",
    )

    assert session.headers["Cache-Control"] == "no-store, private"
    assert session.headers["Vary"] == "Cookie"
    assert oversized.status_code == 413
    assert oversized.get_json()["code"] == "request_too_large"

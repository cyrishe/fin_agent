from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request

from src.services.phone_account_service import PhoneAccountError, PhoneAccountService
from src.services.user_session_service import UserSessionService, UserSessionStorageError


_MAX_AUTH_JSON_BYTES = 32 * 1024


def _normalized_origin(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _origin_is_allowed(origin: str) -> bool:
    normalized = _normalized_origin(origin)
    if not normalized:
        return False

    parsed = urlsplit(normalized)
    if parsed.netloc == str(request.host or "").strip().lower():
        return True

    configured = {
        candidate
        for candidate in (
            _normalized_origin(item)
            for item in str(os.environ.get("FIN_AGENT_ALLOWED_ORIGINS") or "").split(",")
        )
        if candidate
    }
    return normalized in configured


def _secure_cookie() -> bool:
    configured = str(os.environ.get("FIN_AGENT_COOKIE_SECURE") or "").strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    return bool(request.is_secure)


def _remote_addr() -> str:
    trust_proxy = str(os.environ.get("FIN_AGENT_TRUST_PROXY_HEADERS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    forwarded = (
        str(request.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
        if trust_proxy
        else ""
    )
    return (forwarded or str(request.remote_addr or "")).strip()[:64]


def _json_payload() -> Dict[str, Any]:
    if request.content_length is not None and request.content_length > _MAX_AUTH_JSON_BYTES:
        raise PhoneAccountError(
            "request_too_large",
            "请求内容过大。",
            status_code=413,
        )
    if not request.is_json:
        raise PhoneAccountError(
            "unsupported_media_type",
            "请求需要使用 application/json。",
            status_code=415,
        )
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise PhoneAccountError("invalid_request", "请求内容格式不正确。", status_code=400)
    origin = str(request.headers.get("Origin") or "").strip()
    if origin and not _origin_is_allowed(origin):
        raise PhoneAccountError("invalid_origin", "请求来源无效。", status_code=403)
    return payload


def _public_user(identity: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    if not identity or str(identity.get("user_type") or "") != "member":
        return None
    return {
        "user_id": str(identity.get("user_id") or ""),
        "display_name": str(identity.get("display_name") or ""),
        "mobile_masked": str(identity.get("mobile_masked") or ""),
    }


def create_auth_blueprint(
    *,
    service: PhoneAccountService,
    member_identity_resolver: Callable[[], Optional[Dict[str, Any]]],
) -> Blueprint:
    blueprint = Blueprint("phone_auth", __name__)

    @blueprint.after_request
    def disable_auth_response_caching(response):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Vary"] = "Cookie"
        return response

    def error_response(exc: PhoneAccountError):
        response = jsonify(
            {
                "ok": False,
                "code": exc.code,
                "error": exc.public_message,
            }
        )
        response.status_code = exc.status_code
        retry_after = getattr(exc, "retry_after_seconds", None)
        if retry_after is not None:
            response.headers["Retry-After"] = str(max(1, int(retry_after)))
        return response

    def login_response(result: Dict[str, Any], *, status_code: int = 200):
        response = jsonify(
            {
                "ok": True,
                "authenticated": True,
                "user": _public_user(
                    {
                        **dict(result.get("user") or {}),
                        "user_type": "member",
                    }
                ),
                "redirect_to": "/assistant",
            }
        )
        response.status_code = int(status_code)
        response.set_cookie(
            UserSessionService.MEMBER_SESSION_COOKIE_NAME,
            str(result.get("session_token") or ""),
            max_age=60 * 60 * 24 * 30,
            secure=_secure_cookie(),
            httponly=True,
            samesite="Lax",
            path="/",
        )
        response.delete_cookie(UserSessionService.THREAD_COOKIE_NAME, path="/")
        return response

    @blueprint.get("/api/auth/config")
    def auth_config():
        return jsonify({"ok": True, **service.verification_status()})

    @blueprint.get("/api/auth/session")
    def auth_session():
        try:
            identity = member_identity_resolver()
        except UserSessionStorageError:
            return error_response(
                PhoneAccountError(
                    "identity_storage_unavailable",
                    "账户服务暂时不可用，请稍后重试。",
                    status_code=503,
                )
            )
        user = _public_user(identity)
        response = jsonify(
            {
                "ok": True,
                "authenticated": bool(user),
                "user": user,
            }
        )
        if not user and request.cookies.get(UserSessionService.MEMBER_SESSION_COOKIE_NAME):
            response.delete_cookie(UserSessionService.MEMBER_SESSION_COOKIE_NAME, path="/")
        return response

    @blueprint.post("/api/auth/register")
    def auth_register():
        try:
            payload = _json_payload()
            result = service.register(
                real_name=payload.get("real_name"),
                mobile=payload.get("mobile"),
                challenge_id=payload.get("challenge_id"),
                verification_code=payload.get("verification_code"),
                password=payload.get("password"),
                confirm_password=payload.get("confirm_password"),
                user_agent=str(request.headers.get("User-Agent") or ""),
                remote_addr=_remote_addr(),
            )
            return login_response(result, status_code=201)
        except PhoneAccountError as exc:
            return error_response(exc)
        except Exception:
            return error_response(
                PhoneAccountError(
                    "registration_unavailable",
                    "注册服务暂时不可用，请稍后再试。",
                    status_code=503,
                )
            )

    @blueprint.post("/api/auth/registration-code")
    def auth_registration_code():
        try:
            payload = _json_payload()
            result = service.request_registration_code(
                mobile=payload.get("mobile"),
                remote_addr=_remote_addr(),
            )
            return jsonify({"ok": True, **result})
        except PhoneAccountError as exc:
            return error_response(exc)
        except Exception:
            return error_response(
                PhoneAccountError(
                    "phone_code_send_unavailable",
                    "验证码发送服务暂时不可用，请稍后重试。",
                    status_code=503,
                )
            )

    @blueprint.post("/api/auth/login")
    def auth_login():
        try:
            payload = _json_payload()
            result = service.login(
                mobile=payload.get("mobile"),
                password=payload.get("password"),
                user_agent=str(request.headers.get("User-Agent") or ""),
                remote_addr=_remote_addr(),
            )
            return login_response(result)
        except PhoneAccountError as exc:
            return error_response(exc)
        except Exception:
            return error_response(
                PhoneAccountError(
                    "login_unavailable",
                    "登录服务暂时不可用，请稍后再试。",
                    status_code=503,
                )
            )

    @blueprint.post("/api/auth/logout")
    def auth_logout():
        try:
            _json_payload()
        except PhoneAccountError as exc:
            return error_response(exc)
        token = str(
            request.cookies.get(UserSessionService.MEMBER_SESSION_COOKIE_NAME, "")
            or ""
        ).strip()
        try:
            service.logout(session_token=token)
        except Exception:
            # Do not report success or clear the browser credential while the
            # server-side token may still be active.
            return error_response(
                PhoneAccountError(
                    "logout_unavailable",
                    "退出登录暂时失败，请稍后重试。",
                    status_code=503,
                )
            )
        response = jsonify({"ok": True, "authenticated": False})
        response.delete_cookie(UserSessionService.MEMBER_SESSION_COOKIE_NAME, path="/")
        response.delete_cookie(UserSessionService.THREAD_COOKIE_NAME, path="/")
        return response

    return blueprint

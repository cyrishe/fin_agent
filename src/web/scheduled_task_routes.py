from __future__ import annotations

import os
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request

from src.services.scheduled_task_compiler import ScheduledTaskCompileError
from src.services.scheduled_task_service import (
    ScheduledTaskNotFoundError,
    ScheduledTaskService,
)
from src.services.user_session_service import UserSessionStorageError


_MAX_SCHEDULE_JSON_BYTES = 128 * 1024
_MAX_INSTRUCTION_CHARS = 4000
_MAX_IDEMPOTENCY_KEY_CHARS = 128


class ScheduledTaskRequestError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


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


def _validate_origin() -> None:
    origin = str(request.headers.get("Origin") or "").strip()
    if not origin:
        return
    normalized = _normalized_origin(origin)
    host_origin = _normalized_origin(f"{request.scheme}://{request.host}")
    configured = {
        candidate
        for candidate in (
            _normalized_origin(item)
            for item in str(os.environ.get("FIN_AGENT_ALLOWED_ORIGINS") or "").split(",")
        )
        if candidate
    }
    if not normalized or (normalized != host_origin and normalized not in configured):
        raise ScheduledTaskRequestError(
            "invalid_origin",
            "请求来源无效。",
            status_code=403,
        )


def _json_payload(*, allow_empty: bool = False) -> dict[str, Any]:
    _validate_origin()
    if request.content_length is not None and request.content_length > _MAX_SCHEDULE_JSON_BYTES:
        raise ScheduledTaskRequestError(
            "request_too_large",
            "请求内容过大。",
            status_code=413,
        )
    if allow_empty and not request.data:
        return {}
    if not request.is_json:
        raise ScheduledTaskRequestError(
            "unsupported_media_type",
            "请求需要使用 application/json。",
            status_code=415,
        )
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ScheduledTaskRequestError("invalid_request", "请求内容格式不正确。")
    return payload


def _instruction(payload: Mapping[str, Any]) -> str:
    value = str(payload.get("instruction") or "").strip()
    if len(value) > _MAX_INSTRUCTION_CHARS:
        raise ScheduledTaskRequestError(
            "instruction_too_long",
            f"定时任务说明不能超过 {_MAX_INSTRUCTION_CHARS} 个字符。",
        )
    return value


def create_scheduled_task_blueprint(
    *,
    service: ScheduledTaskService,
    identity_resolver: Callable[[], Mapping[str, Any]],
) -> Blueprint:
    blueprint = Blueprint("scheduled_tasks", __name__)

    @blueprint.after_request
    def disable_schedule_response_caching(response):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Vary"] = "Cookie"
        return response

    @blueprint.errorhandler(ScheduledTaskRequestError)
    def handle_request_error(exc: ScheduledTaskRequestError):
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), exc.status_code

    def owner_user_id() -> str:
        identity = identity_resolver()
        owner = str(identity.get("user_id") or "").strip()
        if not owner:
            raise ValueError("缺少当前用户身份")
        return owner

    @blueprint.post("/api/schedules/preview")
    def preview_schedule():
        payload = _json_payload()
        return _respond(
            lambda: {
                "ok": True,
                "preview": service.preview(
                    owner_user_id=owner_user_id(),
                    instruction=_instruction(payload),
                    draft=payload.get("draft") if isinstance(payload.get("draft"), Mapping) else None,
                ),
            }
        )

    @blueprint.post("/api/schedules")
    def create_schedule():
        payload = _json_payload()
        key = str(
            request.headers.get("Idempotency-Key")
            or payload.get("idempotency_key")
            or ""
        ).strip()
        if len(key) > _MAX_IDEMPOTENCY_KEY_CHARS:
            raise ScheduledTaskRequestError(
                "idempotency_key_too_long",
                f"Idempotency-Key 不能超过 {_MAX_IDEMPOTENCY_KEY_CHARS} 个字符。",
            )
        return _respond(
            lambda: {
                "ok": True,
                "schedule": service.create(
                    owner_user_id=owner_user_id(),
                    instruction=_instruction(payload),
                    draft=payload.get("draft") if isinstance(payload.get("draft"), Mapping) else None,
                    idempotency_key=key,
                ),
            },
            success_status=201,
        )

    @blueprint.get("/api/schedules")
    def list_schedules():
        return _respond(
            lambda: {
                "ok": True,
                "schedules": service.list(owner_user_id=owner_user_id()),
            }
        )

    @blueprint.get("/api/schedules/<schedule_id>")
    def get_schedule(schedule_id: str):
        return _respond(
            lambda: {
                "ok": True,
                "schedule": service.get(
                    owner_user_id=owner_user_id(),
                    schedule_id=schedule_id,
                ),
            }
        )

    @blueprint.patch("/api/schedules/<schedule_id>")
    def update_schedule(schedule_id: str):
        payload = _json_payload()
        enabled = payload.get("enabled") if isinstance(payload.get("enabled"), bool) else None
        return _respond(
            lambda: {
                "ok": True,
                "schedule": service.update(
                    owner_user_id=owner_user_id(),
                    schedule_id=schedule_id,
                    instruction=_instruction(payload),
                    draft=payload.get("draft") if isinstance(payload.get("draft"), Mapping) else None,
                    enabled=enabled,
                ),
            }
        )

    @blueprint.post("/api/schedules/<schedule_id>/run")
    def run_schedule(schedule_id: str):
        _json_payload(allow_empty=True)
        return _respond(
            lambda: {
                "ok": True,
                "run": service.run_now(
                    owner_user_id=owner_user_id(),
                    schedule_id=schedule_id,
                ),
            },
            success_status=202,
        )

    @blueprint.get("/api/schedules/<schedule_id>/runs")
    def list_schedule_runs(schedule_id: str):
        raw_limit = str(request.args.get("limit") or "50").strip()
        limit = int(raw_limit) if raw_limit.isdigit() else 50
        return _respond(
            lambda: {
                "ok": True,
                "runs": service.list_runs(
                    owner_user_id=owner_user_id(),
                    schedule_id=schedule_id,
                    limit=limit,
                ),
            }
        )

    @blueprint.get("/api/schedule-runs/<run_id>")
    def get_schedule_run(run_id: str):
        return _respond(
            lambda: {
                "ok": True,
                "run": service.get_run(
                    owner_user_id=owner_user_id(),
                    run_id=run_id,
                ),
            }
        )

    return blueprint


def _respond(action: Callable[[], dict], *, success_status: int = 200):
    try:
        return jsonify(action()), success_status
    except ScheduledTaskRequestError as exc:
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), exc.status_code
    except ScheduledTaskNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": "schedule_not_found"}), 404
    except ScheduledTaskCompileError as exc:
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 400
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": "invalid_request"}), 400
    except UserSessionStorageError:
        raise
    except Exception:
        return jsonify({"ok": False, "error": "定时任务服务暂时不可用", "code": "schedule_service_error"}), 500

"""Authenticated, read-only strategy execution dashboard."""
from __future__ import annotations

from typing import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, jsonify, render_template, request

from src.services.strategy_execution_view_service import StrategyExecutionViewService


def create_strategy_execution_blueprint(
    *, identity_resolver: Callable[[], dict | None],
    service: StrategyExecutionViewService | None = None,
) -> Blueprint:
    blueprint = Blueprint("strategy_execution_dashboard", __name__)
    view_service = service or StrategyExecutionViewService()

    @blueprint.get("/strategies")
    @blueprint.get("/strategies/")
    def page():
        response = current_app.make_response(render_template("strategy_executions.html"))
        response.headers["Cache-Control"] = "no-store, private"
        return response

    @blueprint.get("/api/strategies/executions")
    def recent_executions():
        owner, is_admin, error = resolve_owner()
        if error:
            return error
        try:
            result = view_service.load(owner_user_id=owner,
                                       include_owners=is_admin)
        except Exception:
            current_app.logger.exception("strategy execution view unavailable")
            return jsonify({"ok": False, "error": "策略记录暂时不可用，请稍后重试。"}), 503
        response = jsonify({"ok": True, **result})
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Vary"] = "Cookie"
        return response

    def resolve_owner():
        identity = identity_resolver()
        if not identity or identity.get("user_type") not in {"member", "admin"}:
            return None, False, (jsonify({"ok": False, "error": "请登录后查看策略记录。"}), 403)
        own_id = str(identity.get("user_id") or "").strip()
        if not own_id:
            return None, False, (jsonify({"ok": False, "error": "账户身份不可用。"}), 403)
        is_admin = identity.get("user_type") == "admin"
        requested_owner = str(request.args.get("user_id") or "").strip()
        owner_id = requested_owner if is_admin and requested_owner else own_id
        if len(owner_id) > 128:
            return None, False, (jsonify({"ok": False, "error": "用户标识过长。"}), 400)
        return owner_id, is_admin, None

    @blueprint.get("/api/strategies/market")
    @blueprint.get("/api/strategies/today-market")
    def today_market():
        owner, _is_admin, error = resolve_owner()
        if error:
            return error
        args = {"owner_user_id": owner}
        requested_date = request.args.get("date")
        if requested_date:
            try:
                day = date.fromisoformat(requested_date)
                if day > datetime.now(ZoneInfo("Asia/Shanghai")).date():
                    raise ValueError()
                args["signal_date"] = day.isoformat()
            except ValueError:
                return jsonify({"ok": False, "error": "选股日期无效。"}), 400
        try:
            if "minute_offset" in request.args:
                offset = int(request.args["minute_offset"])
                if offset not in range(-2, 3):
                    raise ValueError()
                args["minute_offset"] = offset
            if "stock_code" in request.args:
                code = request.args["stock_code"]
                if len(code) != 6 or not code.isascii() or not code.isdigit():
                    raise ValueError()
                args["stock_code"] = code
        except ValueError:
            return jsonify({"ok": False, "error": "分钟图翻日参数无效。"}), 400
        try:
            result = view_service.load_today_market(**args)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception:
            current_app.logger.exception("strategy today market unavailable")
            return jsonify({"ok": False, "error": "行情暂时不可用，请稍后重试。"}), 503
        response = jsonify({"ok": True, **result})
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Vary"] = "Cookie"
        return response

    return blueprint

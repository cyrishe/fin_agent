from __future__ import annotations

from typing import Any, Mapping

from flask import Blueprint, current_app, jsonify, request

from src.backtest import BacktestError
from src.services.buy_and_hold_backtest_service import BuyAndHoldBacktestService


def create_backtest_blueprint(
    *,
    service: BuyAndHoldBacktestService | None = None,
) -> Blueprint:
    runtime_service = service or BuyAndHoldBacktestService()
    blueprint = Blueprint("backtest_api", __name__)

    @blueprint.post("/api/backtests/buy-and-hold")
    def run_buy_and_hold():
        payload: Any = request.get_json(silent=True)
        if not isinstance(payload, Mapping):
            return jsonify(
                {
                    "ok": False,
                    "code": "invalid_request",
                    "error": "请求正文必须是 JSON 对象。",
                }
            ), 400
        try:
            result = runtime_service.run(payload)
            return jsonify({"ok": True, "result": result})
        except BacktestError as exc:
            return jsonify(
                {
                    "ok": False,
                    "code": exc.code,
                    "error": exc.message,
                    "details": exc.details,
                }
            ), 400
        except Exception:
            current_app.logger.exception("buy-and-hold backtest failed")
            return jsonify(
                {
                    "ok": False,
                    "code": "backtest_unavailable",
                    "error": "回测服务暂时不可用，请稍后重试。",
                }
            ), 500

    return blueprint

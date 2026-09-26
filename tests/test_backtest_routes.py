from __future__ import annotations

from flask import Flask

from src.backtest import BacktestError
from src.web.backtest_routes import create_backtest_blueprint


class _SuccessfulService:
    def run(self, payload):
        return {"strategy": {"name": "买入并持有"}, "received": dict(payload)}


class _RejectedService:
    def run(self, _payload):
        raise BacktestError(
            "invalid_date_range",
            "开始日期不能晚于结束日期。",
            details={"field": "start_date"},
        )


class _BrokenService:
    def run(self, _payload):
        raise RuntimeError("database password must never leak")


def _client(service):
    app = Flask(__name__)
    app.register_blueprint(create_backtest_blueprint(service=service))
    return app.test_client()


def test_buy_and_hold_route_returns_simple_result_contract():
    response = _client(_SuccessfulService()).post(
        "/api/backtests/buy-and-hold",
        json={
            "stocks": ["贵州茅台", "五粮液"],
            "start_date": "2026-01-01",
            "end_date": "2026-07-31",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert response.get_json()["result"]["strategy"]["name"] == "买入并持有"


def test_buy_and_hold_route_preserves_caller_visible_backtest_error():
    response = _client(_RejectedService()).post(
        "/api/backtests/buy-and-hold",
        json={"stocks": ["贵州茅台"]},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "ok": False,
        "code": "invalid_date_range",
        "error": "开始日期不能晚于结束日期。",
        "details": {"field": "start_date"},
    }


def test_buy_and_hold_route_rejects_non_object_json():
    response = _client(_SuccessfulService()).post(
        "/api/backtests/buy-and-hold",
        json=["not", "an", "object"],
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_request"


def test_buy_and_hold_route_does_not_leak_internal_errors():
    response = _client(_BrokenService()).post(
        "/api/backtests/buy-and-hold",
        json={"stocks": ["贵州茅台"]},
    )

    assert response.status_code == 500
    assert response.get_json()["code"] == "backtest_unavailable"
    assert "password" not in response.get_data(as_text=True)

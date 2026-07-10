import pytest

pytest.importorskip("sentence_transformers")

from src.web.flask_app import app


def test_api_stock_deep_dive_requires_code():
    client = app.test_client()
    response = client.get("/api/stock_deep_dive")
    data = response.get_json()

    assert response.status_code == 400
    assert data["ok"] is False
    assert "code" in data["error"]


def test_api_stock_deep_dive_returns_result(monkeypatch):
    def fake_run(args):
        return {
            "ok": True,
            "skill_name": "stock_deep_dive",
            "final_output": {
                "render_payload": {
                    "version": "1.0",
                    "page_id": "demo",
                    "page_type": "stock_deep_dive",
                    "title": "贵州茅台深度分析",
                    "subtitle": "demo",
                    "summary": {"market_phase": "rebound", "tags": ["白酒"]},
                    "sections": [],
                }
            },
            "error": "",
        }

    monkeypatch.setattr("src.web.flask_app._run_stock_deep_dive_from_request", fake_run)
    client = app.test_client()
    response = client.get("/api/stock_deep_dive?code=600519")
    data = response.get_json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["result"]["render_payload"]["page_type"] == "stock_deep_dive"


def test_api_stock_deep_dive_page_payload_returns_render_payload(monkeypatch):
    def fake_run(args):
        return {
            "ok": True,
            "render_payload": {
                "version": "1.0",
                "page_id": "demo",
                "page_type": "stock_deep_dive",
                "title": "贵州茅台深度分析",
                "subtitle": "demo",
                "summary": {"market_phase": "rebound", "tags": ["白酒"]},
                "sections": [],
            },
        }

    monkeypatch.setattr("src.web.flask_app._run_stock_deep_dive_from_request", fake_run)
    client = app.test_client()
    response = client.get("/api/stock_deep_dive_page_payload?code=600519")
    data = response.get_json()

    assert response.status_code == 200
    assert data["page_type"] == "stock_deep_dive"
    assert data["title"] == "贵州茅台深度分析"

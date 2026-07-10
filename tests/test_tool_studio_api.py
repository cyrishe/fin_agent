import pytest

pytest.importorskip("sentence_transformers")

from src.web.flask_app import app


def test_tool_catalog_api_hides_theme_leaders():
    client = app.test_client()
    response = client.get("/api/tools/catalog")
    data = response.get_json()

    assert response.status_code == 200
    assert data["ok"] is True
    names = [item["tool_name"] for item in data["items"]]
    assert "theme_leaders" not in names


def test_tool_bundle_api_returns_theme_leaders_bundle():
    client = app.test_client()
    response = client.get("/api/tools/theme_leaders/bundle")
    data = response.get_json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["bundle"]["tool_name"] == "theme_leaders"
    assert data["bundle"]["files"]["definition"]["name"] == "theme_leaders"
    assert data["bundle"]["files"]["definition"]["status"] == "disabled"
    assert data["bundle"]["files"]["tool_hub"] == {}


def test_finance_data_catalog_api_returns_structured_dataview():
    client = app.test_client()
    response = client.get("/api/tools/finance-data/catalog?subject=stock&dataview=margin")
    data = response.get_json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["catalog"]["mode"] == "dataview"
    assert data["catalog"]["dataview"]["name"] == "margin"

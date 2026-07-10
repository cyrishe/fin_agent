import json
import os
from pathlib import Path

import pytest

from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService
from src.services.active_tool_registry_service import ActiveToolRegistryService
from src.tools.registry import run_tool


requires_kingdomai = pytest.mark.skipif(
    not os.getenv("KINGDOMAI_DB_PASSWORD"),
    reason="KINGDOMAI_DB_PASSWORD is required for the live provider integration test",
)


def test_finance_data_catalog_builds_subject_dataview_function_tree() -> None:
    catalog = FinanceDataToolCatalogService().build_tree()

    subjects = {item["name"]: item for item in catalog["subjects"]}
    assert {"stock", "industry", "plate", "hot_event"} <= set(subjects)

    stock_views = {item["name"]: item for item in subjects["stock"]["dataviews"]}
    assert "margin" in stock_views
    assert "quote" in stock_views
    assert any(item["api_name"] == "stock.margin" for item in stock_views["margin"]["functions"])
    assert any(item["api_class"] == "kday_margin_metric" for item in stock_views["margin"]["functions"])

    hot_event_views = {item["name"]: item for item in subjects["hot_event"]["dataviews"]}
    assert {"base_info", "state", "member"} <= set(hot_event_views)


def test_finance_data_catalog_can_load_precise_dataview_payload() -> None:
    payload = FinanceDataToolCatalogService().build_tool_studio_payload(subject="stock", dataview="quote")

    assert payload["mode"] == "dataview"
    assert payload["subject"] == "stock"
    assert payload["dataview"]["name"] == "quote"
    assert any(item["name"] == "close" for item in payload["dataview"]["fields"])
    assert any(item["api_name"] == "stock.quote.dynamic_cal" for item in payload["dataview"]["functions"])


def test_finance_data_catalog_saves_single_dataview_node(tmp_path: Path) -> None:
    source = Path(FinanceDataToolCatalogService.DEFAULT_CATALOG_PATH)
    catalog_path = tmp_path / "api_view_catalog.json"
    catalog_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    service = FinanceDataToolCatalogService(catalog_path=str(catalog_path))

    saved = service.save_dataview_node(
        subject="stock",
        dataview="margin",
        node={
            "desc": "updated margin desc",
            "fields": [
                {"name": "code", "aliases": ["股票代码"], "desc": ""},
                {"name": "name", "aliases": ["股票名称"], "desc": ""},
            ],
            "functions": [
                {"api_name": "stock.margin", "api_function": "updated function", "api_class": "basic_query"}
            ],
            "kd": {},
            "computed": {},
        },
    )

    assert saved["desc"] == "updated margin desc"
    assert [item["name"] for item in saved["fields"]] == ["code", "name"]
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert raw["subjects"]["stock"]["margin"]["desc"] == "updated margin desc"


@requires_kingdomai
def test_finance_data_runtime_executes_one_valid_protocol_request() -> None:
    result = FinanceDataToolRuntimeService().execute_request(
        request='r1 = stock.basic_info(filter = "name = 贵州茅台") -> code, name'
    )

    assert result["protocol"] == "finance_data_tool.v1"
    assert result["validation"]["ok"] is True
    assert result["call"]["api"] == "stock.basic_info"
    assert result["result"]["name"] == "r1"
    assert result["result"]["columns"] == ["code", "name"]


def test_finance_data_runtime_returns_validation_error_without_execution() -> None:
    result = FinanceDataToolRuntimeService().execute_request(
        request='r1 = stock.quote(filter = "name = 贵州茅台") -> value'
    )

    assert result["validation"]["ok"] is False
    assert result["result"] is None
    assert any("OUTPUT_ERROR" in item for item in result["validation"]["errors"])


@requires_kingdomai
def test_finance_data_query_tool_is_top_level_and_executable() -> None:
    result = run_tool(
        "finance_data_query",
        {
            "request": 'r1 = stock.basic_info(filter = "name = 贵州茅台") -> code, name',
        },
    )

    assert result["tool"] == "finance_data_query"
    assert result["ok"] is True
    assert result["data"]["validation"]["ok"] is True
    active_names = {item["tool_name"] for item in ActiveToolRegistryService().list_active_tools()}
    assert "finance_data_query" in active_names

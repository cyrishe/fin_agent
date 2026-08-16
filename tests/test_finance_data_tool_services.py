import json
import os
from pathlib import Path

import pytest

from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService
from src.services.active_tool_registry_service import ActiveToolRegistryService
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.models import ResultHandle
from src.tools.registry import run_tool


requires_kingdomai = pytest.mark.skipif(
    not os.getenv("KINGDOMAI_DB_PASSWORD"),
    reason="KINGDOMAI_DB_PASSWORD is required for the live provider integration test",
)


def test_finance_request_accepts_semantic_result_name_for_python_coding() -> None:
    call = parse_api_call(
        'daily_quotes = stock.quote(filter = "code = 600519.SH", realtime = 0) '
        '-> code, tradedate, close'
    )

    assert call.result_id == "daily_quotes"
    assert call.api == "stock.quote"


def test_finance_runtime_resolves_large_target_list_from_structured_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.finance_data_tool_runtime_service as runtime_module

    captured = {}

    def fake_execute(call, previous_results):
        captured["args"] = call.args
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=["code", "close"],
            data={"status": "ok", "rows": [], "row_count": 0},
        )

    monkeypatch.setattr(runtime_module, "execute_api_call", fake_execute)
    stock_codes = [f"{index:06d}.SZ" for index in range(5_000)]

    result = FinanceDataToolRuntimeService().execute_request(
        request=(
            "quotes = stock.quote(codes = $stock_codes, mode = 0, count = 3) "
            "-> code, close"
        ),
        bindings={"stock_codes": stock_codes},
    )

    assert result["ok"] is True
    assert captured["args"]["codes"] == stock_codes
    assert captured["args"]["count"] == 3
    assert len(result["request"]) < 200


def test_finance_runtime_rejects_missing_or_unused_binding() -> None:
    service = FinanceDataToolRuntimeService()

    with pytest.raises(ValueError, match="missing finance query binding"):
        service.execute_request(
            request="r1 = stock.quote(codes = $stock_codes, mode = 0) -> code"
        )

    with pytest.raises(ValueError, match="unused finance query bindings"):
        service.execute_request(
            request="r1 = stock.quote(mode = 0) -> code",
            bindings={"stock_codes": ["600519.SH"]},
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
    assert result["ok"] is False
    assert result["result"] is None
    assert any("OUTPUT_ERROR" in item for item in result["validation"]["errors"])


def test_finance_data_runtime_exposes_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.finance_data_tool_runtime_service as runtime_module

    monkeypatch.setattr(
        runtime_module,
        "execute_api_call",
        lambda call, previous_results: ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=["code", "close"],
            data={
                "status": "provider_error",
                "reason": "database unavailable",
                "rows": [],
            },
        ),
    )

    result = FinanceDataToolRuntimeService().execute_request(
        request='r1 = stock.quote(filter = "code = 600519.SH", realtime = 0) -> code, close'
    )

    assert result["validation"]["ok"] is True
    assert result["ok"] is False
    assert result["execution"] == {
        "ok": False,
        "status": "provider_error",
        "reason": "database unavailable",
    }


def test_finance_data_runtime_hides_provider_sql_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.finance_data_tool_runtime_service as runtime_module

    monkeypatch.setattr(
        runtime_module,
        "execute_api_call",
        lambda call, previous_results: ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=["code", "close"],
            data={
                "status": "ok",
                "rows": [{"code": "600519.SH", "close": 1800.0}],
                "row_count": 1,
                "sql_shape": {"where": "q.trade_date < %s"},
            },
        ),
    )

    result = FinanceDataToolRuntimeService().execute_request(
        request='r1 = stock.quote(filter = "code = 600519.SH", mode = 0) -> code, close'
    )

    assert result["ok"] is True
    assert "sql_shape" not in result["result"]["data"]


def test_finance_data_runtime_structures_provider_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.finance_data_tool_runtime_service as runtime_module

    def raise_provider_exception(call, previous_results):
        raise RuntimeError("connection setup failed")

    monkeypatch.setattr(
        runtime_module,
        "execute_api_call",
        raise_provider_exception,
    )

    result = FinanceDataToolRuntimeService().execute_request(
        request='r1 = stock.quote(filter = "code = 600519.SH", realtime = 0) -> code, close'
    )

    assert result["ok"] is False
    assert result["execution"] == {
        "ok": False,
        "status": "provider_exception",
        "reason": "connection setup failed",
    }
    assert result["result"] is None


def test_finance_data_runtime_rejects_bare_filter_result_reference() -> None:
    previous = {
        "r1": ResultHandle(
            name="r1",
            api="stock.quote",
            columns=["code"],
            data={"status": "ok", "rows": [{"code": "600519.SH"}]},
        )
    }

    result = FinanceDataToolRuntimeService().execute_request(
        request="r2 = stock.quote(filter = r1.code, realtime = 0) -> code, close",
        previous_results=previous,
    )

    assert result["validation"]["ok"] is False
    assert result["ok"] is False
    assert any("bare filter" in item for item in result["validation"]["errors"])


def test_filter_reference_validation_checks_each_occurrence() -> None:
    previous = {
        "r1": ResultHandle(
            name="r1",
            api="stock.quote",
            columns=["code"],
            data={"status": "ok", "rows": [{"code": "600519.SH"}]},
        )
    }

    result = FinanceDataToolRuntimeService().execute_request(
        request=(
            'r2 = stock.quote(filter = "code in r1.code and name = r1.code", '
            "realtime = 0) -> code, close"
        ),
        previous_results=previous,
    )

    assert result["validation"]["ok"] is False
    assert any("bare filter" in item for item in result["validation"]["errors"])


def test_finance_data_runtime_short_circuits_empty_upstream_reference() -> None:
    previous = {
        "r1": ResultHandle(
            name="r1",
            api="stock.quote",
            columns=["code"],
            data={"status": "ok", "rows": [], "row_count": 0},
        )
    }

    result = FinanceDataToolRuntimeService().execute_request(
        request='r2 = stock.quote(filter = "code in r1.code", realtime = 0) -> code, close',
        previous_results=previous,
    )

    assert result["validation"]["ok"] is True
    assert result["ok"] is True
    assert result["execution"]["ok"] is True
    assert result["result"]["data"]["row_count"] == 0
    assert result["result"]["data"]["empty_references"] == ["r1.code"]


def test_aggregate_result_reference_is_not_materialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.experiments.staged_data_protocol.phase2.api_runner as runner

    captured = {}

    def fake_aggregate(*, subject, args, outputs, previous_results):
        captured.update(
            {
                "subject": subject,
                "args": dict(args),
                "outputs": list(outputs),
                "previous": sorted(previous_results),
            }
        )
        return {
            "status": "ok",
            "columns": ["plate_code", "plate_name", "average_metric"],
            "rows": [],
            "row_count": 0,
        }

    monkeypatch.setattr(
        runner,
        "execute_constitution_agg_api",
        fake_aggregate,
    )
    previous = {
        "r1": ResultHandle(
            name="r1",
            api="stock.quote",
            columns=["code", "plate_code", "metric"],
            data={
                "status": "ok",
                "rows": [
                    {
                        "code": "600519.SH",
                        "plate_code": "885001",
                        "metric": 2.5,
                    }
                ],
            },
        )
    }
    call = parse_api_call(
        'r2 = plate.constitution.agg(filter = "plate_code in r1.plate_code", '
        'agg = avg(r1.metric), group_by = "plate_code, plate_name", realtime = 0) '
        "-> plate_code, plate_name, average_metric"
    )

    runner.execute_api_call(call, previous)

    assert captured["args"]["filter"] == "plate_code in [885001]"
    assert captured["args"]["agg"] == "avg(r1.metric)"


def test_empty_reference_in_or_filter_fails_closed_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.experiments.staged_data_protocol.phase2.api_runner as runner

    provider_called = False

    def fake_quote(*, subject, args, outputs):
        nonlocal provider_called
        provider_called = True
        return {
            "status": "ok",
            "columns": ["code", "close"],
            "rows": [{"code": "600519.SH", "close": 1500.0}],
            "row_count": 1,
        }

    monkeypatch.setattr(runner, "execute_quote_api", fake_quote)
    previous = {
        "r1": ResultHandle(
            name="r1",
            api="stock.quote",
            columns=["code"],
            data={"status": "ok", "rows": [], "row_count": 0},
        ),
        "r2": ResultHandle(
            name="r2",
            api="stock.quote",
            columns=["code"],
            data={"status": "ok", "rows": [{"code": "600519.SH"}]},
        ),
    }
    call = parse_api_call(
        'r3 = stock.quote(filter = "(code in r1.code or code in r2.code) and pct > 0", '
        "realtime = 0) -> code, close"
    )

    result = runner.execute_api_call(call, previous)

    assert provider_called is False
    assert result.data["status"] == "filter_reference_resolution_error"
    assert "alternative branch" in result.data["reason"]
    assert result.data["empty_references"] == ["r1.code"]


def test_required_empty_reference_short_circuits_before_nested_or() -> None:
    previous = {
        "r1": ResultHandle(
            name="r1",
            api="stock.quote",
            columns=["code"],
            data={"status": "ok", "rows": [], "row_count": 0},
        )
    }

    result = FinanceDataToolRuntimeService().execute_request(
        request=(
            'r2 = stock.quote(filter = "code in r1.code and '
            '(pct > 1 or amount > 5000000000)", realtime = 0) '
            "-> code, close"
        ),
        previous_results=previous,
    )

    assert result["ok"] is True
    assert result["result"]["data"]["row_count"] == 0
    assert result["result"]["data"]["empty_references"] == ["r1.code"]


def test_unparseable_empty_reference_filter_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.experiments.staged_data_protocol.phase2.api_runner as runner

    provider_called = False

    def fake_quote(*, subject, args, outputs):
        nonlocal provider_called
        provider_called = True
        return {
            "status": "ok",
            "columns": ["code", "close"],
            "rows": [{"code": "600519.SH", "close": 1500.0}],
            "row_count": 1,
        }

    monkeypatch.setattr(runner, "execute_quote_api", fake_quote)
    previous = {
        "r1": ResultHandle(
            name="r1",
            api="stock.quote",
            columns=["code"],
            data={"status": "ok", "rows": [], "row_count": 0},
        )
    }

    result = FinanceDataToolRuntimeService().execute_request(
        request=(
            'r2 = stock.quote(filter = "code in r1.code '
            'AND(pct > 1 OR amount > 5000000000)", realtime = 0) '
            "-> code, close"
        ),
        previous_results=previous,
    )

    assert provider_called is False
    assert result["ok"] is False
    assert result["execution"] == {
        "ok": False,
        "status": "filter_reference_resolution_error",
        "reason": (
            "filter boolean structure could not be resolved safely after "
            "an upstream reference returned no values"
        ),
    }


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

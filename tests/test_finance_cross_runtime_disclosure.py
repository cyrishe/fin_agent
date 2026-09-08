"""Offline CC/DSH compatibility through their real tool adapters.

Catalogs cross the CC SDK handler and the DSH MCP wire handler. Query tests keep
the real parser, validator, execution router and result registry; only the final
data providers are deterministic fakes. These tests do not evaluate an LLM,
financial formula accuracy, live data, or an external MCP transport.
"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
from mcp import types

from src.experiments.staged_data_protocol.phase2 import api_runner
from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
from src.experiments.staged_data_protocol.phase2.python_filter import predicates
from src.scenarios.financial_qa.dsh_mcp_server import FinanceDshMcpBridge, create_server
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService
from src.services.session_variable_store_service import SessionVariableStoreService


_CATALOG = FinanceDataToolCatalogService()
_SUBJECTS = _CATALOG.list_subjects()
_VIEWS = [(row["name"], view) for row in _SUBJECTS for view in row["dataviews"]]
_OPERATIONS = [
    (subject, view, function["operation"])
    for subject, view in _VIEWS
    for function in _CATALOG.get_model_dataview(subject, view)["functions"]
]
_RELATIONS = [(subject, view) for subject, view in _VIEWS if view in {"constitution", "member"}]
_OPERATION_TYPES = {
    "query": "base", "window": "kd",
    "aggregate": "agg", "compute": "dynamic_cal",
}
_METADATA = {"kd", "computed", "aggregate_fields", "value_domains"}
_METADATA_BY_OPERATION = {
    "query": {"computed", "value_domains"},
    "aggregate": {"aggregate_fields", "value_domains"},
    "window": {"kd", "value_domains"},
    "compute": {"computed", "value_domains"},
}


class _Adapters:
    def __init__(self, root: Path):
        context = root / "context.json"
        context.write_text(json.dumps({
            "revision": "offline-disclosure",
            "owner_ids": ["offline-test"],
            "tool_context": {"_agent_runtime_scope": "dsh:offline-disclosure"},
        }), encoding="utf-8")
        self.service = FinanceDataQueryCcTools(
            finance_runtime=FinanceDataToolRuntimeService(),
            result_store=SessionVariableStoreService(data_root=root / "results"),
        )
        definitions, _, self.cc_tracker = self.service.build_tools(
            owner_ids=["offline-test"],
            tool_context={"_agent_runtime_scope": "cc:offline-disclosure"},
        )
        self.cc = {definition.name: definition for definition in definitions}
        self.bridge = FinanceDshMcpBridge(
            context_path=context, trace_path=root / "trace.json", system_tools=self.service,
        )
        self.server = create_server(self.bridge)

    def call(self, runtime: str, name: str, arguments: dict) -> dict:
        # Supply independent input objects: neither adapter gets the other's
        # mutated arguments, cached return value, or runtime result registry.
        arguments = deepcopy(arguments)
        if runtime == "cc":
            sdk = asyncio.run(self.cc[name].handler(arguments))
            return json.loads(sdk["content"][0]["text"])
        wire = asyncio.run(self.server.request_handlers[types.CallToolRequest](
            types.CallToolRequest(method="tools/call", params=types.CallToolRequestParams(
                name=name, arguments=arguments,
            )),
        )).root
        assert wire.isError is False
        assert json.loads(wire.content[0].text) == wire.structuredContent
        assert wire.content[0].text == json.dumps(
            wire.structuredContent, ensure_ascii=False, separators=(",", ":"),
        )
        return wire.structuredContent

    def catalog(self, arguments: dict) -> dict:
        cc = self.call("cc", "read_finance_catalog", arguments)
        dsh = self.call("dsh", "read_finance_catalog", arguments)
        assert dsh == cc
        assert "error" not in cc
        return cc


@pytest.fixture(scope="module")
def catalog_adapters(tmp_path_factory):
    return _Adapters(tmp_path_factory.mktemp("cross-runtime-catalog"))


@pytest.fixture
def query_adapters(tmp_path):
    return _Adapters(tmp_path)


@pytest.fixture
def provider_calls(monkeypatch):
    calls = []
    values = {
        "code": "600519.SH", "name": "贵州茅台", "stock_code": "600519.SH",
        "stock_name": "贵州茅台", "plate_code": "886001", "plate_name": "测试板块",
        "close": 100.0, "value": 100.0, "avg_close": 100.0,
        "close_gt_open_days": 15, "total_count": 1,
    }

    def fake(provider):
        def execute(**kwargs):
            calls.append({"provider": provider, **deepcopy(kwargs)})
            columns = [api_runner._output_column(output) for output in kwargs["outputs"]]
            return {
                "status": "ok", "columns": columns,
                "rows": [{column: values.get(column, 1) for column in columns}], "row_count": 1,
            }
        return execute

    # Mock every execution provider at the router's boundary, so an unexpected
    # dispatch is observable and cannot accidentally query a live data source.
    for name in dir(api_runner):
        if name.startswith("execute_") and name.endswith("_api"):
            monkeypatch.setattr(api_runner, name, fake(name))
    return calls


def _without_scope_ref(value):
    """Only the opaque, independently scoped result reference may differ."""
    if isinstance(value, dict):
        return {key: _without_scope_ref(item) for key, item in value.items() if key != "result_ref"}
    if isinstance(value, list):
        return [_without_scope_ref(item) for item in value]
    return value


def _flow(goal: str, request: str) -> dict:
    return {"steps": [{"goal": goal, "request": request}]}


def test_full_inventory_and_shared_tool_schemas(catalog_adapters):
    assert len(_SUBJECTS) == 7
    assert len(_VIEWS) == 31
    assert len(_OPERATIONS) == 47
    assert set(op for _, _, op in _OPERATIONS) == set(_OPERATION_TYPES)
    dsh_tools = {item.name: item for item in catalog_adapters.bridge.list_tools()}
    assert set(dsh_tools) == {"read_finance_catalog", "finance_query", "load_finance_result",
                              "read_finance_skill", "read_finance_skill_reference"}
    for name, definition in dsh_tools.items():
        assert definition.description == catalog_adapters.cc[name].description
        assert definition.inputSchema == catalog_adapters.cc[name].input_schema
    operation_schema = dsh_tools["read_finance_catalog"].inputSchema["properties"]["operation"]
    assert set(operation_schema["enum"]) == set(_OPERATION_TYPES)


def test_index_is_navigation_not_all_method_contracts(catalog_adapters):
    index = catalog_adapters.catalog({})
    assert index["mode"] == "index"
    assert len(index["subjects"]) == 7
    for subject in index["subjects"]:
        assert set(subject) == {"name", "desc", "dataviews"}
        assert all(set(view) == {"name", "desc", "operations"} for view in subject["dataviews"])
        for view in subject["dataviews"]:
            assert view["operations"] == [
                op for s, v, op in _OPERATIONS if s == subject["name"] and v == view["name"]
            ]


@pytest.mark.parametrize("subject", [row["name"] for row in _SUBJECTS])
def test_subject_discloses_only_its_view_navigation(catalog_adapters, subject):
    payload = catalog_adapters.catalog({"subject": subject})
    assert payload["mode"] == "subject"
    assert payload["subject"]["name"] == subject
    assert set(payload["subject"]) == {"name", "desc", "dataviews"}
    expected = {view for s, view in _VIEWS if s == subject}
    assert {view["name"] for view in payload["subject"]["dataviews"]} == expected
    assert all(set(view) == {"name", "desc", "operations"} for view in payload["subject"]["dataviews"])


@pytest.mark.parametrize("subject,view", _VIEWS, ids=[".".join(row) for row in _VIEWS])
def test_all_views_are_identical_complete_contracts(catalog_adapters, subject, view):
    payload = catalog_adapters.catalog({"subject": subject, "dataview": view})
    assert payload["mode"] == "dataview" and payload["subject"] == subject
    selected = payload["dataview"]
    assert selected["name"] == view
    assert selected["fields"] and selected["functions"]
    assert "api_classes" not in selected
    assert "selected_operation" not in selected
    for function in selected["functions"]:
        assert "api_class" not in function
        assert function["api_name"] and function["request_pattern"] and function["args"]
        assert function["output_rule"] and function["operation"] in _OPERATION_TYPES


@pytest.mark.parametrize("subject,view,operation", _OPERATIONS, ids=[".".join(row) for row in _OPERATIONS])
def test_all_operation_packs_keep_only_relevant_metadata_and_real_api_bindings(
    catalog_adapters, subject, view, operation,
):
    full = catalog_adapters.catalog({"subject": subject, "dataview": view})["dataview"]
    selected = catalog_adapters.catalog({
        "subject": subject, "dataview": view, "operation": operation,
    })["dataview"]
    assert selected["selected_operation"] == operation
    assert len(selected["functions"]) == 1
    function = selected["functions"][0]
    assert function == next(fn for fn in full["functions"] if fn["operation"] == operation)
    assert selected["available_operations"] == {
        fn["operation"]: fn["api_name"] for fn in full["functions"]
    }
    assert selected.keys() & _METADATA == full.keys() & _METADATA_BY_OPERATION[operation]
    for key in selected.keys() & _METADATA:
        assert selected[key] == full[key]
    assert set(selected["fields"]) == set(full["fields"])
    if operation != "query":
        assert all("modes" not in field for field in selected["fields"].values())

    api = function["api_name"]
    if operation == "window":
        field, methods = next(iter(selected["kd"].items()))
        api = api.replace("<field>", field).replace("<method>", methods[0])
    resolved = resolve_api(api)
    assert resolved is not None
    # The public basic_info spelling already aliases the provider's base_info
    # binding; the catalog refactor must preserve both, not rename the API.
    runtime_view = "base_info" if view == "basic_info" else view
    assert resolved["subject"] == subject and resolved["dataview"] == runtime_view
    assert function["api_name"].startswith(f"{subject}.{view}")
    assert resolved["operation"] == operation
    assert resolved["type"] == _OPERATION_TYPES[operation]


@pytest.mark.parametrize("subject,view", _RELATIONS)
def test_relation_view_uses_the_same_query_selection(catalog_adapters, subject, view):
    full = catalog_adapters.catalog({"subject": subject, "dataview": view})
    current = catalog_adapters.catalog({"subject": subject, "dataview": view, "operation": "query"})
    assert current["dataview"]["functions"][0] in full["dataview"]["functions"]
    assert current["dataview"]["selected_operation"] == "query"
    api = current["dataview"]["functions"][0]["api_name"]
    assert resolve_api(api)["type"] == "base"
    assert api == f"{subject}.{view}.query"


@pytest.mark.parametrize("arguments", [
    {"dataview": "quote"}, {"subject": "stock", "operation": "query"},
    {"subject": "stock", "dataview": "basic_info", "operation": "compute"},
])
def test_structurally_incomplete_or_unavailable_selection_has_same_error(catalog_adapters, arguments):
    cc = catalog_adapters.call("cc", "read_finance_catalog", arguments)
    dsh = catalog_adapters.call("dsh", "read_finance_catalog", arguments)
    assert dsh == cc and cc["error"]


@pytest.mark.parametrize("operation,api,provider,request_text", [
    ("query", "stock.basic_info", "execute_base_info_api",
     'result = stock.basic_info(filter="name == \'贵州茅台\'", limit=1) -> code, name'),
    ("query", "stock.basic_info.query", "execute_base_info_api",
     'result = stock.basic_info.query(filter="name == \'贵州茅台\'", limit=1) -> code, name'),
    ("window", "stock.quote.kd_close_avg", "execute_kd_quote_api",
     'result = stock.quote.kd_close_avg(k=5, filter="code == \'600519.SH\'", mode=0) -> code, name, value as avg_close'),
    ("query", "plate.constitution.query", "execute_constitution_api",
     'result = plate.constitution.query(filter="plate_name == \'测试板块\'", limit=-1) -> plate_code, plate_name, stock_code, stock_name'),
    ("aggregate", "stock.quote.agg", "execute_quote_agg_api",
     'result = stock.quote.agg(filter="pct > 5", agg=count(stock.quote.code), mode=0) -> total_count'),
    ("compute", "stock.quote.dynamic_cal", "execute_dynamic_quote_api",
     'result = stock.quote.dynamic_cal(k=20, filter="code == \'600519.SH\'", fields="code, name, tradedate, open, close", '
     'task="统计每只股票近20个交易日收盘价高于开盘价的天数", mode=0) -> code, name, close_gt_open_days'),
])
def test_four_operations_validate_route_execute_and_load_equally(
    query_adapters, provider_calls, operation, api, provider, request_text,
):
    subject, view = api.split(".")[:2]
    contract = query_adapters.catalog({"subject": subject, "dataview": view, "operation": operation})
    assert contract["dataview"]["functions"][0]["operation"] == operation
    results = {}
    for runtime in ("cc", "dsh"):
        payload = query_adapters.call(runtime, "finance_query", _flow("读取计算所需的数据", request_text))
        assert payload.get("ok") is True, payload
        assert payload["api"] == api and payload["result_name"] == "r1"
        assert payload["row_count"] == 1 and payload["sample_complete"] is True
        assert payload["next_result_name"] == "r2"
        page = query_adapters.call(runtime, "load_finance_result", {"result_ref": payload["result_ref"]})
        assert "error" not in page and len(page["rows"]) == 1
        results[runtime] = (payload, page)
    assert _without_scope_ref(results["cc"][0]) == _without_scope_ref(results["dsh"][0])
    assert _without_scope_ref(results["cc"][1]) == _without_scope_ref(results["dsh"][1])
    assert len(provider_calls) == 2
    assert all(call["provider"] == provider for call in provider_calls)
    assert provider_calls[0] == provider_calls[1]
    assert resolve_api(api)["type"] == _OPERATION_TYPES[operation]


def test_intra_flow_reference_materialization_matches_between_runtimes(query_adapters, provider_calls):
    arguments = {"steps": [
        {"goal": "确认股票身份", "request": 'result = stock.basic_info.query(filter="name == \'贵州茅台\'") -> code, name'},
        {"goal": "读取该股票行情", "request": 'result = stock.quote.query(filter="code in step1.code", count=1, mode=0) -> code, close'},
    ]}
    results = [query_adapters.call(runtime, "finance_query", arguments) for runtime in ("cc", "dsh")]
    assert all(result.get("ok") is True for result in results), results
    assert _without_scope_ref(results[0]) == _without_scope_ref(results[1])
    for result in results:
        assert [step["result_name"] for step in result["steps"]] == ["r1", "r2"]
        assert result["steps"][1]["depends_on"] == ["r1"]
        assert result["next_result_name"] == "r3"
    for tracker in (query_adapters.cc_tracker, query_adapters.bridge.tracker):
        # Exact rewritten requests live in trace, not repeated successful
        # model-facing summaries; both runtimes retain them for diagnostics.
        assert "r1.code" in tracker["result_refs"][1]["request"]
    assert [call["provider"] for call in provider_calls] == [
        "execute_base_info_api", "execute_quote_api", "execute_base_info_api", "execute_quote_api",
    ]
    for call in (provider_calls[1], provider_calls[3]):
        assert next(predicates(call["args"]["_filter_expression"]))["value"] == ["600519.SH"]


def test_failed_step_retains_success_and_repair_contract_without_replaying_it(query_adapters, provider_calls):
    failed_flow = {"steps": [
        {"goal": "确认股票身份", "request": 'result = stock.basic_info(filter="name == \'贵州茅台\'") -> code, name'},
        {"goal": "读取该股票行情", "request": 'result = stock.quote(filter="code in step1.code", count=1, mode=0) -> code, missing_output_field'},
    ]}
    failed = [query_adapters.call(runtime, "finance_query", failed_flow) for runtime in ("cc", "dsh")]
    assert _without_scope_ref(failed[0]) == _without_scope_ref(failed[1])
    for payload in failed:
        assert payload["failed_step"] == 2 and payload["next_result_name"] == "r2"
        assert payload["validation"]["ok"] is False
        assert "missing_output_field" in str(payload["validation"]["errors"])
        assert payload["completed_steps"][0]["result_name"] == "r1"
        assert payload["recovery"]["category"] == "request_invalid"
        assert payload["recovery"]["retryable"] is True
    assert len(provider_calls) == 2
    assert all(call["provider"] == "execute_base_info_api" for call in provider_calls)

    # Read the exact operation contract after failure, then repair just the
    # failed request using the authoritative persisted result name.
    contract = query_adapters.catalog({"subject": "stock", "dataview": "quote", "operation": "query"})["dataview"]
    assert "close" in contract["fields"] and "filter" in contract["functions"][0]["args"]["optional"]
    repair = _flow("读取该股票行情", 'result = stock.quote(filter="code in r1.code", count=1, mode=0) -> code, close')
    repaired = []
    for runtime, previous in zip(("cc", "dsh"), failed):
        result = query_adapters.call(runtime, "finance_query", repair)
        assert result.get("ok") is True, result
        assert result["result_name"] == "r2" and result["next_result_name"] == "r3"
        assert result["depends_on"] == ["r1"]
        stored = query_adapters.call(runtime, "load_finance_result", {
            "result_ref": previous["completed_steps"][0]["result_ref"],
        })
        assert stored["rows"] == [{"code": "600519.SH", "name": "贵州茅台"}]
        repaired.append(result)
    assert _without_scope_ref(repaired[0]) == _without_scope_ref(repaired[1])
    assert [call["provider"] for call in provider_calls] == [
        "execute_base_info_api", "execute_base_info_api", "execute_quote_api", "execute_quote_api",
    ]

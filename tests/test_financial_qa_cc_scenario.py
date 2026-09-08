import asyncio
import json
from pathlib import Path
import pytest

from src.experiments.staged_data_protocol.phase2.catalog import OPERATION_DESCRIPTIONS
from src.scenarios.financial_qa.result_registry import FinanceResultRegistry
from src.scenarios.financial_qa.service import FinancialQaCcService
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.services.conversation_preprocess_service import ConversationPreprocessService
from src.services.execution_plan_service import AgentRuntimePlanner
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.services.session_variable_store_service import SessionVariableStoreService
from src.skill_runtime.models import ToolSpec


def _payload(tool_result: dict) -> dict:
    return json.loads(tool_result["content"][0]["text"])


def test_zero_row_guidance_treats_matching_empty_result_as_complete() -> None:
    evidence = FinanceResultRegistry.step_evidence(
        {
            "result_name": "r1",
            "row_count": 0,
            "columns": [],
            "sample_complete": True,
        },
        call_args={"filter": "name = 贵州茅台"},
    )

    assert evidence["execution_completed"] is True
    assert "本步按空结果完成" in evidence["guidance"]
    assert "保留已执行的对象、时间和筛选条件" in evidence["guidance"]
    assert evidence["selection_applied"] == {"filter": "name = 贵州茅台"}
    assert evidence["populated_columns"] == []
    assert evidence["sample_complete"] is True


class _Catalog:
    def build_tree(self):
        return {
            "subjects": [
                {
                    "name": "stock",
                    "desc": "股票",
                    "rules": ["股票名称与代码随结果返回"],
                    "dataviews": [
                        {
                            "name": "quote",
                            "desc": "行情",
                            "route_summary": "行情",
                            "rules": ["实时与历史模式按参数区分"],
                            "fields": [{"name": "close"}],
                        },
                        {
                            "name": "margin",
                            "desc": "融资融券",
                            "route_summary": "融资融券",
                            "rules": [],
                            "fields": [{"name": "financing_balance"}],
                        },
                    ],
                }
            ]
        }

    def get_subject(self, subject):
        if subject != "stock":
            raise KeyError(subject)
        return self.build_tree()["subjects"][0]

    def get_dataview(self, subject, dataview):
        for item in self.get_subject(subject)["dataviews"]:
            if item["name"] == dataview:
                return item
        raise KeyError(f"{subject}.{dataview}")


class _Runtime:
    def __init__(self):
        self.calls = []

    def execute_request(self, *, request, previous_results=None):
        previous = dict(previous_results or {})
        self.calls.append({"request": request, "previous": sorted(previous)})
        if "invalid" in request:
            return {
                "protocol": "finance_data_tool.v1",
                "request": request,
                "validation": {
                    "ok": False,
                    "errors": ["unknown output: made_up_field"],
                    "warnings": [],
                },
                "result": None,
            }
        result_name = "r2" if "r2 =" in request else "r1"
        if result_name == "r2":
            assert "r1" in previous
        return {
            "protocol": "finance_data_tool.v1",
            "request": request,
            "validation": {"ok": True, "errors": [], "warnings": []},
            "result": {
                "name": result_name,
                "api": "stock.quote",
                "columns": ["stock_code", "stock_name", "close"],
                "data": {
                    "rows": [
                        {
                            "stock_code": "600519.SH",
                            "stock_name": "贵州茅台",
                            "close": 1500.0,
                        }
                    ],
                    "row_count": 1,
                },
            },
        }


def _tools(tmp_path: Path):
    runtime = _Runtime()
    result_store = SessionVariableStoreService(data_root=tmp_path / "data")
    service = FinanceDataQueryCcTools(
        finance_runtime=runtime,
        finance_catalog=_Catalog(),
        result_store=result_store,
    )
    tool_runtime = service.create_runtime()
    tools, names, tracker = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/thread-7"},
        runtime=tool_runtime,
    )
    return (
        service,
        runtime,
        tool_runtime,
        {item.name: item for item in tools},
        names,
        tracker,
    )


def test_financial_qa_exposes_only_read_only_data_tools(tmp_path: Path) -> None:
    _, _, _, tools, names, _ = _tools(tmp_path)

    assert set(tools) == {
        "read_finance_catalog",
        "finance_query",
        "load_finance_result",
        "run_backtest",
        "read_finance_skill",
        "read_finance_skill_reference",
    }
    assert set(names) == {
        "mcp__finance__read_finance_catalog",
        "mcp__finance__finance_query",
        "mcp__finance__load_finance_result",
        "mcp__finance__run_backtest",
        "mcp__finance__read_finance_skill",
        "mcp__finance__read_finance_skill_reference",
    }
    assert all("implement" not in name and "codex" not in name for name in names)
    assert tools["finance_query"].input_schema["required"] == ["steps"]
    assert "data_request_complete" in tools["finance_query"].input_schema["properties"]
    operation_schema = tools["read_finance_catalog"].input_schema[
        "properties"
    ]["operation"]
    assert operation_schema["enum"] == list(OPERATION_DESCRIPTIONS)
    assert set(operation_schema["enum"]) == {
        "query", "window", "aggregate", "compute",
    }
    for operation, description in OPERATION_DESCRIPTIONS.items():
        assert f"{operation}: {description}" in operation_schema["description"]
    relation = FinanceDataToolCatalogService().get_model_dataview(
        "plate", "constitution", "constitution"
    )
    assert relation["selected_operation"] == "query"
    assert relation["functions"][0]["api_name"] == "plate.constitution.query"
    assert relation["functions"][0]["operation"] == "query"
    assert tools["finance_query"].input_schema["properties"]["steps"]["minItems"] == 1
    load_schema = tools["load_finance_result"].input_schema["properties"]
    assert load_schema["limit"]["maximum"] == 50
    assert load_schema["columns"]["maxItems"] == 20
    backtest_schema = tools["run_backtest"].input_schema["properties"]
    assert "benchmark" in backtest_schema
    assert backtest_schema["context_benchmarks"]["maxItems"] == 2


def test_run_backtest_tool_registers_compact_result_for_direct_holdings(
    tmp_path: Path,
) -> None:
    class _Backtest:
        def __init__(self):
            self.calls = []

        def run(self, payload):
            self.calls.append(payload)
            return {
                "ok": True,
                "backtest_type": "fixed_basket",
                "strategy": {"name": "买入并持有", "allocation": "equal_weight"},
                "period": {"actual_start": "2025-01-02", "actual_end": "2025-06-30"},
                "stocks": [{"code": "600519.SH"}, {"code": "000858.SZ"}],
                "summary": {"total_return": 0.12, "max_drawdown": -0.08},
                "comparison": {
                    "primary_benchmark": {
                        "subject": "沪深300",
                        "code": "000300.SH",
                        "name": "沪深300",
                        "source": "default",
                        "period": {
                            "actual_start": "2025-01-02",
                            "actual_end": "2025-06-30",
                        },
                    },
                    "summary": {
                        "portfolio_total_return": 0.12,
                        "benchmark_total_return": 0.08,
                        "excess_return": 0.04,
                    },
                    "contextual_benchmarks": [],
                    "warnings": [],
                },
                "warnings": [],
            }

    backtest = _Backtest()
    result_store = SessionVariableStoreService(data_root=tmp_path / "data")
    service = FinanceDataQueryCcTools(
        finance_runtime=_Runtime(),
        finance_catalog=_Catalog(),
        result_store=result_store,
        backtest_service=backtest,
    )
    tools, _, tracker = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/thread-backtest"},
    )
    tool_by_name = {item.name: item for item in tools}

    result = _payload(
        asyncio.run(
            tool_by_name["run_backtest"].handler(
                {
                    "holdings": [{"stock": "贵州茅台"}, {"stock": "五粮液"}],
                    "start_date": "2025-01-01",
                    "end_date": "2025-06-30",
                }
            )
        )
    )

    assert result["ok"] is True
    assert result["stock_count"] == 2
    assert result["benchmark_comparison"]["primary_benchmark"]["name"] == "沪深300"
    assert result["benchmark_comparison"]["summary"]["excess_return"] == 0.04
    assert result["result_ref"].startswith("session://")
    assert backtest.calls[0]["holdings"] == [
        {"stock": "贵州茅台"},
        {"stock": "五粮液"},
    ]
    assert tracker["result_refs"][0]["semantic"] == "finance.backtest"


def test_run_backtest_tool_reads_only_server_supplied_attachment_rows(
    tmp_path: Path,
) -> None:
    class _Backtest:
        def __init__(self):
            self.calls = []

        def run(self, payload):
            self.calls.append(payload)
            return {
                "ok": True,
                "backtest_type": "fixed_basket",
                "strategy": {"name": "买入并持有"},
                "period": {},
                "stocks": [{"code": "600519.SH"}],
                "summary": {},
                "warnings": [],
            }

    backtest = _Backtest()
    service = FinanceDataQueryCcTools(
        finance_runtime=_Runtime(),
        finance_catalog=_Catalog(),
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
        backtest_service=backtest,
    )
    tools, _, _ = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={
            "_agent_runtime_scope": "financial_qa:owner-a/thread-attachment",
            "_backtest_attachments": [
                {
                    "attachment_id": "att-owned",
                    "parsed": {
                        "header": ["股票", "权重"],
                        "rows": [["贵州茅台", 0.6]],
                    },
                }
            ],
        },
    )
    result = _payload(
        asyncio.run(
            {item.name: item for item in tools}["run_backtest"].handler(
                {
                    "attachment_source": {
                        "attachment_id": "att-owned",
                        "stock_column": "股票",
                        "weight_column": "权重",
                    },
                    "start_date": "2025-01-01",
                    "end_date": "2025-06-30",
                }
            )
        )
    )

    assert result["ok"] is True
    assert backtest.calls[0]["holdings"] == [{"stock": "贵州茅台", "weight": 0.6}]

    rejected = _payload(
        asyncio.run(
            {item.name: item for item in tools}["run_backtest"].handler(
                {
                    "attachment_source": {
                        "attachment_id": "att-foreign",
                        "stock_column": "股票",
                    },
                    "start_date": "2025-01-01",
                    "end_date": "2025-06-30",
                }
            )
        )
    )
    assert rejected["ok"] is False
    assert "附件来源不存在" in rejected["error"]


def test_skill_references_are_loaded_from_the_bound_snapshot_only_after_skill_activation(
    tmp_path: Path,
) -> None:
    class _BusinessSkillCatalog:
        def __init__(self):
            self.calls = []

        def method_snapshot(self):
            self.calls.append("snapshot")
            return {
                "revision": "revision-1",
                "skills": {"stock-research": {
                    "method": "stock method", "description": "Stock research",
                    "content_hash": "method-hash", "references": {
                        "references/industry-lenses.md": {
                            "content": "consumer lens", "content_hash": "abc123",
                        },
                    },
                }},
            }

    catalog = _BusinessSkillCatalog()
    service = FinanceDataQueryCcTools(
        finance_runtime=_Runtime(),
        finance_catalog=_Catalog(),
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
        business_skill_catalog=catalog,
    )
    runtime = service.create_runtime()
    tools, names, tracker = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={
            "_agent_runtime_scope": "financial_qa:owner-a/thread-skill-ref",
            "allowed_finance_skills": ["stock-research"],
            "_finance_skill_catalog_revision": "revision-1",
        },
        runtime=runtime,
    )
    tool_map = {item.name: item for item in tools}

    blocked = _payload(
        asyncio.run(
            tool_map["read_finance_skill_reference"].handler(
                {
                    "skill_id": "fin-agent-finance-business:stock-research",
                    "reference": "references/industry-lenses.md",
                }
            )
        )
    )
    runtime.activate_skill("stock-research")
    loaded = _payload(
        asyncio.run(
            tool_map["read_finance_skill_reference"].handler(
                {
                    "skill_id": "fin-agent-finance-business:stock-research",
                    "reference": "references/industry-lenses.md",
                }
            )
        )
    )
    assert "mcp__finance__read_finance_skill_reference" in names
    assert "先加载对应" in blocked["error"]
    assert loaded["content"] == "consumer lens"
    assert catalog.calls == ["snapshot"]
    assert loaded["revision"] == "revision-1"
    assert loaded["content_hash"] == "abc123"
    assert [item["tool"] for item in tracker["calls"]] == [
        "read_finance_skill_reference",
        "read_finance_skill_reference",
    ]
    assert tracker["calls"][-1]["requested_skill_id"] == (
        "fin-agent-finance-business:stock-research"
    )
    assert tracker["calls"][-1]["content_hash"] == "abc123"
    assert "content" not in tracker["calls"][-1]


def test_configured_agent_tools_are_registered_in_the_same_cc_harness(
    tmp_path: Path,
) -> None:
    class _Adapter:
        def __init__(self):
            self.calls = []

        def list_tool_specs(self, allowed_tools):
            assert allowed_tools == ["financial_news_search"]
            return [
                ToolSpec(
                    name="financial_news_search",
                    description="查询金融新闻",
                    schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                )
            ]

        def execute(self, name, arguments):
            self.calls.append((name, arguments))
            return {
                "ok": True,
                "data": {
                    "rows": [
                        {
                            "title": "贵州茅台发布公告",
                            "published_at": "2026-07-27",
                        }
                    ]
                },
            }

    adapter = _Adapter()
    result_store = SessionVariableStoreService(data_root=tmp_path / "data")
    service = FinanceDataQueryCcTools(
        finance_runtime=_Runtime(),
        finance_catalog=_Catalog(),
        result_store=result_store,
        tool_adapter=adapter,
    )
    runtime = service.create_runtime()
    tools, names, tracker = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={
            "_agent_runtime_scope": "financial_qa:owner-a/thread-7",
            "allowed_agent_tools": ["financial_news_search"],
        },
        runtime=runtime,
    )
    tool_map = {item.name: item for item in tools}

    result = _payload(
        asyncio.run(
            tool_map["financial_news_search"].handler(
                {"query": "贵州茅台"}
            )
        )
    )

    assert adapter.calls == [
        ("financial_news_search", {"query": "贵州茅台"})
    ]
    assert "mcp__finance__financial_news_search" in names
    assert "复用本轮已取得的结果" in tool_map[
        "financial_news_search"
    ].description
    assert result["sample"]["rows"][0]["title"] == "贵州茅台发布公告"
    assert tracker["result_refs"][0]["tool"] == "financial_news_search"

    blocked_context = {
        "_agent_runtime_scope": "financial_qa:owner-a/thread-7",
        "allowed_agent_tools": [],
        "skill_tool_access": {"earnings-analysis": ["financial_news_search"]},
    }
    runtime.begin_turn(owner_ids=["owner-a"], tool_context=blocked_context)
    runtime.activate_skill("earnings-analysis")
    blocked = _payload(
        asyncio.run(
            tool_map["financial_news_search"].handler(
                {"query": "贵州茅台"}
            )
        )
    )
    assert blocked["ok"] is False
    assert blocked["error"] == "当前用户或运行策略未授权该补充工具。"
    assert adapter.calls == [
        ("financial_news_search", {"query": "贵州茅台"})
    ]

    allowed_context = {
        **blocked_context,
        "allowed_agent_tools": ["financial_news_search"],
        "skill_tool_access": {"stock-research": []},
    }
    runtime.begin_turn(owner_ids=["owner-a"], tool_context=allowed_context)
    runtime.activate_skill("stock-research")
    allowed = _payload(
        asyncio.run(
            tool_map["financial_news_search"].handler(
                {"query": "贵州茅台"}
            )
        )
    )
    assert allowed["sample"]["rows"][0]["title"] == "贵州茅台发布公告"
    assert len(adapter.calls) == 2


def test_catalog_is_loaded_progressively(tmp_path: Path) -> None:
    _, _, _, tools, _, tracker = _tools(tmp_path)

    index = _payload(asyncio.run(tools["read_finance_catalog"].handler({})))
    subject = _payload(
        asyncio.run(
            tools["read_finance_catalog"].handler({"subject": "stock"})
        )
    )
    dataview = _payload(
        asyncio.run(
            tools["read_finance_catalog"].handler(
                {"subject": "stock", "dataview": "quote"}
            )
        )
    )

    assert index["mode"] == "index"
    assert index["subjects"][0]["dataviews"] == [
        {"name": "quote", "desc": "行情", "operations": []},
        {"name": "margin", "desc": "融资融券", "operations": []},
    ]
    assert "fields" not in index["subjects"][0]["dataviews"][0]
    assert subject["mode"] == "subject"
    assert dataview["dataview"]["fields"] == [{"name": "close"}]
    assert [item["mode"] for item in tracker["catalog_reads"]] == [
        "index",
        "subject",
        "dataview",
    ]


def test_catalog_routing_index_exposes_direct_subject_dataview_choices(
    tmp_path: Path,
) -> None:
    service, _, _, _, _, _ = _tools(tmp_path)

    assert service._catalog_routing_index() == (
        "- stock（股票）\n  - quote：行情\n  - margin：融资融券"
    )


def test_catalog_routing_uses_description_as_the_single_source(
    tmp_path: Path,
) -> None:
    class _CatalogWithStaleLegacySummary(_Catalog):
        def build_tree(self):
            tree = super().build_tree()
            view = tree["subjects"][0]["dataviews"][0]
            view["desc"] = "个股历史、分钟与当前行情，以及价格和成交数据。"
            view["route_summary"] = "不应使用的旧摘要"
            return tree

    service = FinanceDataQueryCcTools(
        finance_runtime=_Runtime(),
        finance_catalog=_CatalogWithStaleLegacySummary(),
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
    )

    routing = service._catalog_routing_index()
    assert "quote：个股历史、分钟与当前行情，以及价格和成交数据。" in routing
    assert "不应使用的旧摘要" not in routing


def test_catalog_routing_hides_subject_execution_guidance_until_view_selected() -> None:
    service = FinanceDataQueryCcTools()

    routing = service._catalog_routing_index()
    selected = service.finance_catalog.get_model_dataview(
        "plate", "basic_info", "query"
    )

    assert "plate_name = 名称" not in routing
    assert "LIKE" not in routing
    raw_subject = service.finance_catalog.load_raw_catalog()["subjects"]["plate"]
    assert raw_subject["_meta"]["rules"] == []
    assert not selected.get("subject_guidance")
    assert selected["functions"][0]["api_name"] == "plate.basic_info.query"
    assert selected["rules"] == raw_subject["basic_info"]["rules"]


def test_catalog_revision_change_is_rejected_inside_an_active_tool_set(
    tmp_path: Path,
) -> None:
    class _RevisionCatalog(_Catalog):
        revision = "catalog-a"

        def catalog_revision(self) -> str:
            return self.revision

    catalog = _RevisionCatalog()
    service = FinanceDataQueryCcTools(
        finance_runtime=_Runtime(),
        finance_catalog=catalog,
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
    )
    built, _, _ = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "cc:catalog-revision"},
    )
    read_catalog = {item.name: item for item in built}["read_finance_catalog"]

    catalog.revision = "catalog-b"
    payload = _payload(
        asyncio.run(
            read_catalog.handler(
                {"subject": "stock", "dataview": "quote"}
            )
        )
    )

    assert "changed during the active agent turn" in payload["error"]


def test_catalog_tool_uses_compact_view_without_changing_full_catalog_api(
    tmp_path: Path,
) -> None:
    catalog = FinanceDataToolCatalogService()
    service = FinanceDataQueryCcTools(
        finance_runtime=_Runtime(),
        finance_catalog=catalog,
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
    )
    runtime = service.create_runtime()
    built, _, _ = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/catalog-compact"},
        runtime=runtime,
    )
    read_catalog = next(item for item in built if item.name == "read_finance_catalog")

    assert "operation" not in read_catalog.input_schema.get("required", [])

    payload = _payload(
        asyncio.run(
            read_catalog.handler({"subject": "stock", "dataview": "quote"})
        )
    )

    model_view = payload["dataview"]
    full_view = catalog.get_dataview("stock", "quote")
    assert payload["mode"] == "dataview"
    assert "selected_operation" not in model_view
    assert {method["operation"] for method in model_view["functions"]} == {
        "query", "window", "aggregate", "compute",
    }
    assert isinstance(model_view["fields"], dict)
    assert set(model_view["fields"]) == {
        field["name"] for field in full_view["fields"]
    }
    assert "api_classes" not in model_view
    assert model_view["functions"]
    for method in model_view["functions"]:
        assert {"api_name", "operation", "request_pattern", "args"} <= set(method)
        assert "api_class" not in method
    assert model_view["functions"][0]["api_name"] == "stock.quote.query"
    assert "field_count" not in model_view
    assert isinstance(full_view["fields"], list)


def test_financial_qa_loads_global_api_protocol_before_dataview_guidance() -> None:
    service = FinancialQaCcService(enabled=True)

    paths = list(service.session_service.system_context_paths)
    assert paths == [
        Path("src/scenarios/financial_qa/finance_api_protocol.md"),
        Path("src/scenarios/financial_qa/data_query.md"),
    ]
    protocol = paths[0].read_text(encoding="utf-8")
    assert "## 分层读取" in protocol
    assert "具体 API 路径、字段、参数、方法、规则和示例的唯一依据" in protocol
    assert "subject + dataview + operation" in protocol
    assert "身份范围" in protocol
    assert "rN.column" in protocol
    assert "stock." not in protocol
    assert "mode=" not in protocol


def test_financial_qa_runtime_accepts_an_authorized_skill_subset() -> None:
    service = FinancialQaCcService(enabled=True)
    try:
        context = service._runtime_context(
            application_context={
                "default_agent": {
                    "runtime_profile": {
                        "skills": ["stock-research", "earnings-analysis"],
                    }
                }
            }
        )

        options = service.session_service._runtime_options(context)

        assert options["effective_skill_names"] == (
            "fin-agent-finance-business:stock-research",
            "fin-agent-finance-business:earnings-analysis",
        )
        assert options["skill_revision"] == service.business_skill_catalog.revision
    finally:
        service.close()


def test_queries_keep_dependency_handles_and_results_are_pageable(tmp_path: Path) -> None:
    service, runtime, tool_runtime, tools, _, tracker = _tools(tmp_path)

    flow = _payload(
        asyncio.run(
            tools["finance_query"].handler(
                {
                    "steps": [
                        {
                            "goal": "取得贵州茅台的名称和收盘价",
                            "request": "result = stock.quote(filter='贵州茅台') -> stock_code, stock_name, close",
                        },
                        {
                            "goal": "使用第一步对象范围再次取得收盘价",
                            "request": "result = stock.quote(filter=\"code in step1.stock_code\") -> stock_code, close",
                        },
                    ]
                }
            )
        )
    )
    first, second = flow["steps"]
    loaded = _payload(
        asyncio.run(
            tools["load_finance_result"].handler(
                {
                    "result_ref": first["result_ref"],
                    "columns": ["stock_name"],
                }
            )
        )
    )
    loaded_by_result_name = _payload(
        asyncio.run(
            tools["load_finance_result"].handler(
                {
                    "result_ref": "r1",
                    "columns": ["stock_name"],
                }
            )
        )
    )

    assert runtime.calls[0]["previous"] == []
    assert runtime.calls[1]["previous"] == ["r1"]
    assert runtime.calls[0]["request"].startswith("r1 =")
    assert runtime.calls[1]["request"].startswith("r2 =")
    assert "code in r1.stock_code" in runtime.calls[1]["request"]
    assert set(tool_runtime.result_handles) == {"r1", "r2"}
    assert first["row_count"] == 1
    assert second["result_name"] == "r2"
    assert first["goal"] == "取得贵州茅台的名称和收盘价"
    assert flow["next_result_name"] == "r3"
    assert "working_set" not in flow
    assert first["step_evidence"]["selection_applied"] == {
        "filter": "贵州茅台",
    }
    assert first["step_evidence"]["populated_columns"] == [
        "stock_code",
        "stock_name",
        "close",
    ]
    assert second["depends_on"] == ["r1"]
    assert loaded["rows"] == [{"stock_name": "贵州茅台"}]
    assert loaded["columns"] == ["stock_name"]
    assert loaded_by_result_name["rows"] == [{"stock_name": "贵州茅台"}]
    assert "manifest" not in loaded
    assert len(tracker["result_refs"]) == 2
    legacy_model_payload = {
        "ok": True,
        "steps": tracker["result_refs"],
        "next_result_name": flow["next_result_name"],
        "working_set": tool_runtime.working_set(),
    }
    current_size = len(
        json.dumps(flow, ensure_ascii=False, separators=(",", ":"))
    )
    legacy_size = len(
        json.dumps(
            legacy_model_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    assert current_size <= legacy_size * 0.65

    restored = service.create_runtime()
    restored.begin_turn(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/thread-7"},
    )
    assert set(restored.result_handles) == {"r1", "r2"}
    assert restored.result_handles["r1"].task == "取得贵州茅台的名称和收盘价"
    assert restored.working_set()[0]["selection_applied"] == {
        "filter": "贵州茅台",
    }
    assert restored.working_set()[1]["depends_on"] == ["r1"]
    assert '"result_name": "r1"' in restored.current_context_prompt()

    restored_tools, _, _ = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/thread-7"},
        runtime=restored,
    )
    restored_query_tool = next(
        item for item in restored_tools if item.name == "finance_query"
    )
    assert "复用成功步骤" in restored_query_tool.description
    assert "响应提供本次新增结果与执行证据" in restored_query_tool.description
    assert '"result_name": "r1"' not in restored_query_tool.description
    assert "取得贵州茅台的名称和收盘价" not in restored_query_tool.description


def test_data_only_query_marks_server_owned_result_as_terminal(
    tmp_path: Path,
) -> None:
    service, _, runtime, _, _, _ = _tools(tmp_path)
    tools, _, tracker = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={
            "_agent_runtime_scope": "financial_qa:owner-a/data-only",
            "_finance_data_only": True,
        },
        runtime=runtime,
    )
    finance_query = next(item for item in tools if item.name == "finance_query")

    payload = _payload(
        asyncio.run(
            finance_query.handler(
                {
                    "steps": [
                        {
                            "goal": "取得贵州茅台收盘价",
                            "request": (
                                "result = stock.quote(filter='贵州茅台') "
                                "-> stock_code, stock_name, close"
                            ),
                        }
                    ],
                    "data_request_complete": True,
                }
            )
        )
    )

    assert payload["ok"] is True
    assert payload["data_only_mode"] is True
    assert payload["data_only_complete"] is True
    assert tracker["result_refs"][0]["result_ref"].startswith("session://")
    assert tracker["data_only_complete"] is True
    # A failed later flow must not inherit completion from the earlier one.
    failed = _payload(asyncio.run(finance_query.handler({"steps": [], "data_request_complete": True})))
    assert "error" in failed
    assert tracker["data_only_complete"] is False


def test_query_result_names_are_system_assigned_and_progress_is_observable(
    tmp_path: Path,
) -> None:
    runtime = _Runtime()
    result_store = SessionVariableStoreService(data_root=tmp_path / "data")
    service = FinanceDataQueryCcTools(
        finance_runtime=runtime,
        finance_catalog=_Catalog(),
        result_store=result_store,
    )
    events = []
    tools, _, tracker = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/thread-8"},
        event_sink=events.append,
    )
    tool_map = {item.name: item for item in tools}

    first = _payload(
        asyncio.run(
            tool_map["finance_query"].handler(
                {
                    "goal": "取得第一组行情",
                    "request": "r1 = stock.quote(filter='贵州茅台') -> stock_code, stock_name, close",
                }
            )
        )
    )
    second = _payload(
        asyncio.run(
            tool_map["finance_query"].handler(
                {
                    "goal": "取得第二组行情",
                    "request": "r1 = stock.quote(filter=\"code in r1.stock_code\") -> stock_code, close",
                }
            )
        )
    )

    assert first["result_name"] == "r1"
    assert second["result_name"] == "r2"
    assert "steps" not in first
    assert "working_set" not in first
    assert "request" not in first
    assert first["step_evidence"]["execution_completed"] is True
    assert "本步已完成" in first["step_evidence"]["guidance"]
    assert tracker["result_refs"][0]["request"].startswith("r1 =")
    assert "guidance" in tracker["result_refs"][0]["step_evidence"]
    assert runtime.calls[1]["request"].startswith("r2 =")
    assert tracker["calls"][1]["submitted_request"].startswith("r1 =")
    assert tracker["calls"][1]["assigned_result_name"] == "r2"
    assert any(
        event["content"] == "正在查询：取得第一组行情"
        and event["metadata"]["progress_id"] == "finance_query_step_1"
        and event["metadata"]["status"] == "running"
        for event in events
    )
    assert any(
        event["content"] == "已完成：取得第一组行情，取得 1 条记录。"
        and event["metadata"]["progress_id"] == "finance_query_step_1"
        and event["metadata"]["status"] == "completed"
        for event in events
    )
    visible_progress = "\n".join(str(event.get("content") or "") for event in events)
    assert "selection_applied" not in visible_progress
    assert "sample_complete" not in visible_progress
    assert "闭环判断" not in visible_progress


@pytest.mark.parametrize("count,budget,expected", [(5, 6000, 5), (31, 6000, 31), (31, 150, 3)])
def test_dsh_model_sample_can_cover_a_small_complete_result(tmp_path: Path, count, budget, expected) -> None:
    class _FiveRowRuntime(_Runtime):
        def execute_request(self, *, request, previous_results=None):
            result = super().execute_request(
                request=request,
                previous_results=previous_results,
            )
            result["result"]["data"] = {
                "rows": [
                    {
                        "stock_code": "600519.SH",
                        "stock_name": "贵州茅台",
                        "close": 1500.0 + index,
                    }
                    for index in range(count)
                ],
                "row_count": count,
            }
            return result

    service = FinanceDataQueryCcTools(
        finance_runtime=_FiveRowRuntime(),
        finance_catalog=_Catalog(),
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
    )
    tools, _, tracker = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={
            "_agent_runtime_scope": "financial_qa_dsh:owner-a/thread-five",
            "_finance_model_sample_rows": 50,
            "_finance_model_sample_max_chars": budget,
        },
    )
    result = _payload(
        asyncio.run(
            {item.name: item for item in tools}["finance_query"].handler(
                {
                    "goal": "取得最近五日行情",
                    "request": (
                        "r1 = stock.quote(filter='贵州茅台', count=5) "
                        "-> stock_code, stock_name, close"
                    ),
                }
            )
        )
    )

    assert len(result["sample"]["rows"]) == expected
    assert result["sample_complete"] is (expected == count)
    assert result["step_evidence"]["sample_complete"] is (expected == count)
    # The turn tracker preserves the same evidence used for this answer.
    assert len(tracker["result_refs"][0]["sample"]["rows"]) == expected


def test_dsh_detail_default_can_cover_a_small_multi_period_result(
    tmp_path: Path,
) -> None:
    class _ElevenRowRuntime(_Runtime):
        def execute_request(self, *, request, previous_results=None):
            result = super().execute_request(
                request=request,
                previous_results=previous_results,
            )
            result["result"]["data"] = {
                "rows": [
                    {
                        "stock_code": "600519.SH",
                        "stock_name": "贵州茅台",
                        "close": 1500.0 + index,
                    }
                    for index in range(11)
                ],
                "row_count": 11,
            }
            return result

    service = FinanceDataQueryCcTools(
        finance_runtime=_ElevenRowRuntime(),
        finance_catalog=_Catalog(),
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
    )
    tools, _, _ = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={
            "_agent_runtime_scope": "financial_qa_dsh:owner-a/thread-eleven",
            "_finance_detail_default_limit": 20,
        },
    )
    tool_map = {item.name: item for item in tools}
    result = _payload(
        asyncio.run(
            tool_map["finance_query"].handler(
                {
                    "goal": "取得多期行情",
                    "request": (
                        "r1 = stock.quote(filter='贵州茅台', count=11) "
                        "-> stock_code, stock_name, close"
                    ),
                }
            )
        )
    )
    loaded = _payload(
        asyncio.run(
            tool_map["load_finance_result"].handler(
                {"result_ref": result["result_name"]}
            )
        )
    )

    assert len(loaded["rows"]) == 11
    assert loaded["page"]["limit"] == 20
    assert loaded["page"]["has_more"] is False


def test_working_set_distinguishes_available_identity_from_null_metric(
    tmp_path: Path,
) -> None:
    class _NullMetricRuntime(_Runtime):
        def execute_request(self, *, request, previous_results=None):
            result = super().execute_request(
                request=request,
                previous_results=previous_results,
            )
            result["result"]["data"]["rows"][0]["close"] = None
            return result

    runtime = _NullMetricRuntime()
    service = FinanceDataQueryCcTools(
        finance_runtime=runtime,
        finance_catalog=_Catalog(),
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
    )
    tool_runtime = service.create_runtime()
    tools, _, _ = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/thread-null"},
        runtime=tool_runtime,
    )
    tool_map = {item.name: item for item in tools}

    result = _payload(
        asyncio.run(
            tool_map["finance_query"].handler(
                {
                    "steps": [
                        {
                            "goal": "取得目标股票身份和收盘价",
                            "request": "result = stock.quote(filter='贵州茅台') -> stock_code, stock_name, close",
                        },
                        {
                            "goal": "继续使用第一步的股票范围查询",
                            "request": "result = stock.quote(filter=\"code in step1.stock_code\") -> stock_code, stock_name, close",
                        },
                    ]
                }
            )
        )
    )
    columns = {
        item["name"]: item["populated_count"]
        for item in tool_runtime.working_set()[0]["columns"]
    }

    assert columns == {
        "stock_code": 1,
        "stock_name": 1,
        "close": 0,
    }
    assert "working_set" not in result
    assert result["steps"][0]["sample_complete"] is True
    assert result["steps"][0]["step_evidence"]["available_refs"] == [
        "r1.stock_code",
        "r1.stock_name",
    ]
    assert result["steps"][0]["step_evidence"]["execution_completed"] is True
    assert result["steps"][0]["step_evidence"]["populated_columns"] == [
        "stock_code",
        "stock_name",
    ]
    assert result["steps"][0]["step_evidence"]["unavailable_columns"] == ["close"]
    assert "在本次结果中缺值" in result["steps"][0]["step_evidence"]["guidance"]
    assert "保留可用身份范围与实际缺口" in result["steps"][0]["step_evidence"]["guidance"]
    assert len(runtime.calls) == 2
    assert "code in r1.stock_code" in runtime.calls[1]["request"]


def test_finance_flow_stops_at_invalid_forward_reference_and_keeps_prior_result(
    tmp_path: Path,
) -> None:
    service, runtime, _, tools, _, tracker = _tools(tmp_path)

    result = _payload(
        asyncio.run(
            tools["finance_query"].handler(
                {
                    "steps": [
                        {
                            "goal": "取得第一步行情",
                            "request": "result = stock.quote(filter='贵州茅台') -> stock_code, close",
                        },
                        {
                            "goal": "错误引用尚未执行的第三步",
                            "request": "result = stock.quote(filter=step3.stock_code) -> stock_code, close",
                        },
                        {
                            "goal": "本步不应执行",
                            "request": "result = stock.quote(filter=\"code in step1.stock_code\") -> stock_code, close",
                        },
                    ]
                }
            )
        )
    )

    assert result["failed_step"] == 2
    assert "FLOW_REF_ERROR" in result["error"]
    assert len(runtime.calls) == 1
    assert [item["result_name"] for item in tracker["result_refs"]] == ["r1"]
    assert "working_set" not in result
    assert result["completed_steps"][0]["goal"] == "取得第一步行情"


def test_first_step_reference_error_returns_original_error_without_progress_crash(tmp_path: Path) -> None:
    _, runtime, _, tools, _, tracker = _tools(tmp_path)
    failed = _payload(asyncio.run(tools["finance_query"].handler({
        "steps": [{
            "goal": "取得已有公司行情",
            "request": 'result = stock.quote(filter="code in step2.code") -> code, close',
        }],
    })))
    assert failed["failed_step"] == 1
    assert "FLOW_REF_ERROR" in failed["error"]
    assert "progress_title" not in failed["error"]
    assert runtime.calls == []
    assert tracker["result_refs"] == []


def test_validation_failure_is_returned_for_cc_repair_and_not_registered(
    tmp_path: Path,
) -> None:
    service, _, _, tools, _, tracker = _tools(tmp_path)

    failed = _payload(
        asyncio.run(
            tools["finance_query"].handler(
                    {
                        "goal": "取得一个无效字段",
                        "request": "result = stock.quote(filter='贵州茅台') -> invalid",
                    }
            )
        )
    )

    assert failed["validation"]["ok"] is False
    assert "made_up_field" in failed["validation"]["errors"][0]
    assert failed["recovery"]["category"] == "request_invalid"
    assert failed["recovery"]["retryable"] is True
    assert failed["recovery"]["owner"] == "cc"
    assert failed["recovery"]["max_retries"] == 1
    assert tracker["result_refs"] == []
    assert service.result_store.list_variables(
        session_id="financial_qa:owner-a/thread-7"
    ) == []


def test_provider_failure_stops_flow_and_is_not_registered(tmp_path: Path) -> None:
    class _ProviderFailureRuntime(_Runtime):
        def execute_request(self, *, request, previous_results=None):
            self.calls.append(
                {
                    "request": request,
                    "previous": sorted(dict(previous_results or {})),
                }
            )
            return {
                "protocol": "finance_data_tool.v1",
                "request": request,
                "validation": {"ok": True, "errors": [], "warnings": []},
                "execution": {
                    "ok": False,
                    "status": "provider_error",
                    "reason": "database unavailable",
                },
                "result": {
                    "name": "r1",
                    "api": "stock.quote",
                    "columns": ["stock_code", "close"],
                    "data": {
                        "status": "provider_error",
                        "reason": "database unavailable",
                        "rows": [],
                    },
                },
            }

    runtime = _ProviderFailureRuntime()
    service = FinanceDataQueryCcTools(
        finance_runtime=runtime,
        finance_catalog=_Catalog(),
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
    )
    tool_runtime = service.create_runtime()
    tools, _, tracker = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/thread-provider-error"},
        runtime=tool_runtime,
    )
    result = _payload(
        asyncio.run(
            next(item for item in tools if item.name == "finance_query").handler(
                {
                    "steps": [
                        {
                            "goal": "取得行情",
                            "request": "result = stock.quote(filter='贵州茅台') -> stock_code, close",
                        }
                    ]
                }
            )
        )
    )

    assert result["failed_step"] == 1
    assert result["execution"]["status"] == "provider_error"
    assert result["recovery"]["category"] == "provider_failure"
    assert result["recovery"]["retryable"] is False
    assert result["recovery"]["automatic_retries_used"] == 1
    assert len(runtime.calls) == 2
    assert tracker["result_refs"] == []
    assert tool_runtime.result_handles == {}
    assert service.result_store.list_variables(
        session_id="financial_qa:owner-a/thread-provider-error"
    ) == []


def test_provider_failure_is_retried_by_harness_without_another_cc_turn(
    tmp_path: Path,
) -> None:
    class _TransientProviderRuntime(_Runtime):
        def execute_request(self, *, request, previous_results=None):
            if not self.calls:
                self.calls.append(
                    {
                        "request": request,
                        "previous": sorted(dict(previous_results or {})),
                    }
                )
                return {
                    "protocol": "finance_data_tool.v1",
                    "request": request,
                    "validation": {"ok": True, "errors": [], "warnings": []},
                    "execution": {
                        "ok": False,
                        "status": "provider_exception",
                        "reason": "temporary connection reset",
                    },
                    "result": None,
                }
            return super().execute_request(
                request=request,
                previous_results=previous_results,
            )

    runtime = _TransientProviderRuntime()
    service = FinanceDataQueryCcTools(
        finance_runtime=runtime,
        finance_catalog=_Catalog(),
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
    )
    tools, _, tracker = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/transient"},
    )

    result = _payload(
        asyncio.run(
            next(item for item in tools if item.name == "finance_query").handler(
                {
                    "steps": [
                        {
                            "goal": "取得行情",
                            "request": "result = stock.quote(filter='贵州茅台') -> stock_code, close",
                        }
                    ]
                }
            )
        )
    )

    assert result["ok"] is True
    assert result["provider_retry_count"] == 1
    assert tracker["calls"][0]["provider_retry_count"] == 1
    assert len(tracker["calls"][0]["attempts"]) == 2
    assert all(item["duration_ms"] >= 0 for item in tracker["calls"][0]["attempts"])
    assert len(runtime.calls) == 2


def test_runtime_exception_is_retried_once_before_becoming_a_cc_result(
    tmp_path: Path,
) -> None:
    class _RaisedOnceRuntime(_Runtime):
        def execute_request(self, *, request, previous_results=None):
            if not self.calls:
                self.calls.append({"request": request, "raised": True})
                raise ConnectionError("temporary transport failure")
            return super().execute_request(
                request=request,
                previous_results=previous_results,
            )

    runtime = _RaisedOnceRuntime()
    service = FinanceDataQueryCcTools(
        finance_runtime=runtime,
        finance_catalog=_Catalog(),
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
    )
    tools, _, tracker = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/raised-once"},
    )

    result = _payload(
        asyncio.run(
            next(item for item in tools if item.name == "finance_query").handler(
                {
                    "steps": [
                        {
                            "goal": "取得行情",
                            "request": "result = stock.quote(filter='贵州茅台') -> stock_code, close",
                        }
                    ]
                }
            )
        )
    )

    assert result["ok"] is True
    assert result["provider_retry_count"] == 1
    assert tracker["calls"][0]["provider_retry_count"] == 1
    assert len(runtime.calls) == 2


@pytest.mark.parametrize("raised", [False, True])
def test_fast_mode_does_not_retry_provider_failures(tmp_path, raised):
    class FailingRuntime(_Runtime):
        def execute_request(self, *, request, previous_results=None):
            self.calls.append({"request": request})
            if raised:
                raise ConnectionError("temporary transport failure")
            return {
                "validation": {"ok": True},
                "execution": {"ok": False, "status": "provider_exception", "reason": "temporary failure"},
                "result": None,
            }

    runtime = FailingRuntime()
    service = FinanceDataQueryCcTools(
        finance_runtime=runtime, finance_catalog=_Catalog(),
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
    )
    tools, _, tracker = service.build_tools(owner_ids=["owner-a"], tool_context={
        "_agent_runtime_scope": "financial_qa:owner-a/fast-failure", "_finance_execution_mode": "fast",
    })
    result = _payload(asyncio.run(next(t for t in tools if t.name == "finance_query").handler({
        "goal": "取得行情", "request": "result = stock.quote(filter='贵州茅台') -> stock_code, close",
    })))
    assert result.get("ok") is not True
    assert result["failed_step"] == 1
    assert len(runtime.calls) == 1
    assert tracker["result_refs"] == []


def test_restore_ignores_legacy_provider_failure_marked_as_ok(
    tmp_path: Path,
) -> None:
    result_store = SessionVariableStoreService(data_root=tmp_path / "data")
    scope = "financial_qa:owner-a/legacy-provider-error"
    variable = result_store.register_tool_result(
        session_id=scope,
        tool_name="finance_data_query",
        task="旧失败查询",
        local_alias="r1",
        result={
            "protocol": "finance_data_tool.v1",
            "validation": {"ok": True, "errors": [], "warnings": []},
            "result": {
                "name": "r1",
                "api": "stock.quote",
                "columns": ["stock_code", "close"],
                "data": {
                    "status": "provider_error",
                    "reason": "legacy database error",
                    "rows": [],
                },
            },
        },
    )
    assert variable is not None
    assert variable["status"] == "ok"

    service = FinanceDataQueryCcTools(
        finance_runtime=_Runtime(),
        finance_catalog=_Catalog(),
        result_store=result_store,
    )
    restored = service.create_runtime()
    restored.begin_turn(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": scope},
    )

    assert restored.result_handles == {}
    assert restored.working_set() == []


def test_registration_failure_does_not_leave_in_memory_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, _, tool_runtime, tools, _, tracker = _tools(tmp_path)
    monkeypatch.setattr(
        service.result_store,
        "register_tool_result",
        lambda **kwargs: None,
    )

    result = _payload(
        asyncio.run(
            tools["finance_query"].handler(
                {
                    "steps": [
                        {
                            "goal": "取得行情",
                            "request": "result = stock.quote(filter='贵州茅台') -> stock_code, close",
                        }
                    ]
                }
            )
        )
    )

    assert result["error"] == "query result could not be registered"
    assert tracker["result_refs"] == []
    assert tool_runtime.result_handles == {}


def test_long_lived_query_tools_record_calls_on_the_current_turn(
    tmp_path: Path,
) -> None:
    _, _, tool_runtime, tools, _, first_tracker = _tools(tmp_path)
    asyncio.run(
        tools["finance_query"].handler(
            {
                "goal": "取得第一步行情",
                "request": "result = stock.quote(filter='贵州茅台') -> stock_code, close",
            }
        )
    )
    second_tracker = tool_runtime.begin_turn(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/thread-7"},
    )
    asyncio.run(
        tools["finance_query"].handler(
            {
                "goal": "取得第二步行情",
                "request": "result = stock.quote(filter=\"code in r1.stock_code\") -> stock_code, close",
            }
        )
    )

    assert [item["result_name"] for item in first_tracker["result_refs"]] == ["r1"]
    assert [item["result_name"] for item in second_tracker["result_refs"]] == ["r2"]


class _Session:
    def __init__(self):
        self.calls = []

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "session_id": "cc-session",
            "resumed": False,
            "duration_ms": 12,
            "result": "贵州茅台最近交易日收盘价为 1500 元。",
            "error": "",
            "tool_calls": [{"tool": "finance_query"}],
            "result_refs": [{"result_ref": "session://x/vars/v1"}],
        }

    def close(self):
        return None


def test_stock_research_result_declares_pdf_export_without_an_extra_model_step() -> None:
    class _StockResearchSession(_Session):
        def run_turn(self, **kwargs):
            result = super().run_turn(**kwargs)
            result["skill_entries"] = [{
                "skill_id": "stock-research",
                "qualified_skill": "fin-agent-finance-business:stock-research",
            }]
            return result

    session = _StockResearchSession()
    service = FinancialQaCcService(enabled=True, session_service=session)
    result = service.answer(
        thread_id=7,
        turn_id=8,
        owner_id="owner-a",
        user_text="深度分析贵州茅台",
        dispatch_plan={
            "selected_agent": "investment_analyst",
            "turn_mode": "normal_qa",
            "entry": "agent_route",
            "semantic_turn": {"resolved_question": "深度分析贵州茅台"},
        },
    )

    assert result["report_export"] == {
        "pdf": True,
        "label": "下载 PDF",
        "title": "个股深度研究报告",
    }
    assert len(session.calls) == 1


def test_non_stock_research_result_does_not_declare_pdf_export() -> None:
    session = _Session()
    service = FinancialQaCcService(enabled=True, session_service=session)
    result = service.answer(
        thread_id=7,
        turn_id=8,
        owner_id="owner-a",
        user_text="贵州茅台收盘价",
        dispatch_plan={
            "selected_agent": "investment_analyst",
            "turn_mode": "normal_qa",
            "entry": "agent_route",
            "semantic_turn": {"resolved_question": "贵州茅台收盘价"},
        },
    )

    assert "report_export" not in result


def test_financial_qa_service_uses_resolved_question_and_normal_qa_only() -> None:
    session = _Session()
    service = FinancialQaCcService(
        enabled=True,
        session_service=session,
    )
    plan = {
        "selected_agent": "investment_analyst",
        "turn_mode": "normal_qa",
        "entry": "agent_route",
        "semantic_turn": {
            "ori_question": "那它呢？",
            "resolved_question": "贵州茅台最近交易日的收盘价是多少？",
        },
    }

    assert service.accepts(dispatch_plan=plan)
    assert not service.accepts(
        dispatch_plan={**plan, "turn_mode": "tool_development"}
    )
    assert not service.accepts(
        dispatch_plan={**plan, "entry": "vision_intake"}
    )
    assert not service.accepts(
        dispatch_plan={**plan, "entry": "skill_run"}
    )
    assert not service.accepts(
        dispatch_plan={**plan, "entry": "planned_run"}
    )
    assert service.accepts(
        dispatch_plan=plan,
        attachments=[{"attachment_id": "att-table", "kind": "table"}],
    )
    assert not service.accepts(
        dispatch_plan=plan,
        attachments=[{"attachment_id": "att-image", "kind": "image"}],
    )

    result = service.answer(
        thread_id=7,
        turn_id=8,
        owner_id="owner-a",
        user_text="那它呢？",
        dispatch_plan=plan,
        application_context={
            "default_agent": {
                "tools": [
                    "financial_news_search",
                    "general_search",
                    "stock_realtime_quote",
                ],
                "runtime_profile": {
                    "tools": [
                        "financial_news_search",
                        "general_search",
                        "stock_realtime_quote",
                    ],
                    "skills": [
                        "stock-research",
                        "earnings-analysis",
                    ],
                    "sections": [
                        {
                            "section": "soul",
                            "content": "Investment Analyst 原有角色提示",
                        },
                        {
                            "section": "tool_policy",
                            "content": {
                                "allowed_tools": [
                                    "不存在于当前 harness 的旧工具"
                                ]
                            },
                        },
                    ],
                },
            }
        },
    )

    assert session.calls[0]["user_text"] == "贵州茅台最近交易日的收盘价是多少？"
    assert "context_window" not in session.calls[0]["context"]
    assert session.calls[0]["context"]["allowed_agent_tools"] == []
    assert session.calls[0]["context"]["allowed_finance_skills"] == [
        "stock-research",
        "earnings-analysis",
    ]
    assert session.calls[0]["context"]["skill_tool_access"] == {
        "stock-research": [],
        "earnings-analysis": [],
    }
    assert session.calls[0]["context"]["_resolved_question"] == (
        "贵州茅台最近交易日的收盘价是多少？"
    )
    assert "stock-research" in session.calls[0]["context"][
        "_finance_skill_catalog_prompt"
    ]
    assert "factor-analysis" not in session.calls[0]["context"][
        "_finance_skill_catalog_prompt"
    ]
    assert len(
        session.calls[0]["context"]["_finance_skill_catalog_revision"]
    ) == 64
    pinned_binding = session.calls[0]["context"][
        "_finance_skill_runtime_binding"
    ]
    assert pinned_binding["revision"] == session.calls[0]["context"][
        "_finance_skill_catalog_revision"
    ]
    assert Path(pinned_binding["runtime_root"]).is_dir()
    assert pinned_binding["skill_names"] == [
        "fin-agent-finance-business:stock-research",
        "fin-agent-finance-business:earnings-analysis",
    ]
    assert session.calls[0]["context"]["_agent_system_prompt"] == (
        "Investment Analyst 原有角色提示"
    )
    assert "不存在于当前 harness 的旧工具" not in session.calls[0]["context"][
        "_agent_system_prompt"
    ]
    assert result["mode"] == "financial_qa_cc"
    assert result["financial_qa"]["tool_calls"] == [{"tool": "finance_query"}]
    assert result["surface_blocks"][0]["block_id"] == "financial_qa_answer"
    assert result["surface_blocks"][0]["content"] == result["message"]


def test_financial_qa_passes_authenticated_table_data_to_backtest_tool_context() -> None:
    class _InputResolver:
        def inspect(self, attachments):
            assert attachments == [{"attachment_id": "att-owned", "kind": "table"}]
            return {
                "attachments": [
                    {
                        "attachment_id": "att-owned",
                        "kind": "table",
                        "parsed": {"header": ["股票"], "rows": [["贵州茅台"]]},
                    }
                ],
                "prompt_attachments": [
                    {
                        "attachment_id": "att-owned",
                        "kind": "table",
                        "parsed": {"header": ["股票"], "rows": [["贵州茅台"]]},
                    }
                ],
            }

    session = _Session()
    service = FinancialQaCcService(
        enabled=True,
        session_service=session,
        input_resolver=_InputResolver(),
    )
    plan = {
        "selected_agent": "investment_analyst",
        "turn_mode": "normal_qa",
        "entry": "agent_route",
        "semantic_turn": {"resolved_question": "回测附件里的股票"},
    }

    service.answer(
        thread_id=7,
        turn_id=9,
        owner_id="owner-a",
        user_text="回测附件里的股票",
        dispatch_plan=plan,
        attachments=[{"attachment_id": "att-owned", "kind": "table"}],
    )

    call = session.calls[0]
    assert call["context"]["_backtest_attachments"][0]["attachment_id"] == "att-owned"
    assert "不可信的用户数据" in call["user_text"]
    assert "贵州茅台" in call["user_text"]


class _ExplodingPlanner:
    def build_plan(self, **kwargs):
        raise AssertionError("legacy agent-local planner must not run")


def test_agent_owned_normal_qa_skips_legacy_llm_planner() -> None:
    service = ConversationPreprocessService(
        agent_runtime_planner=AgentRuntimePlanner(),
        agent_runtime_llm_planner_service=_ExplodingPlanner(),
        agent_owned_runtime_names={"investment_analyst"},
    )

    plan = service._build_execution_plan_preview(
        normalized_request={
            "round_task_desc": "贵州茅台最近交易日的开盘价是多少？",
            "raw_user_text": "贵州茅台最近交易日的开盘价是多少？",
            "task_splitd": [],
        },
        work_context={"default_agent": "investment_analyst"},
        application_context={},
        task_domain="business_dialog",
        capability_family="business_analysis",
        selected_agent="investment_analyst",
        enable_llm=True,
    )

    assert plan["selected_path"]["type"] == "agent_route"
    assert plan["selected_path"]["target"]["name"] == "investment_analyst"


def test_financial_qa_prompt_keeps_business_rules_and_manual_stays_generic() -> None:
    root = Path("src/scenarios/financial_qa")
    prompt = (root / "system.md").read_text(encoding="utf-8")
    manual = (root / "data_query.md").read_text(encoding="utf-8")
    protocol = (root / "finance_api_protocol.md").read_text(encoding="utf-8")
    business_root = Path("src/skills/finance-business/skills")
    business_skills = {
        path.parent.name: path.read_text(encoding="utf-8")
        for path in business_root.glob("*/SKILL.md")
    }

    assert "具体金融事实通过金融数据工具取得" in prompt
    assert "对象、指标、时间与结果粒度" in prompt
    assert "有匹配方法时优先加载" in prompt
    assert "只覆盖部分问题" in prompt
    assert "通用分析能力补足" in prompt
    assert "general_search" not in prompt
    assert "financial_news_search" not in prompt
    assert "服务端默认沪深300" in prompt
    assert "依据回测前已知的持仓特征" in prompt
    assert "展示感知的数据组织" in prompt
    assert "可组织一份直接相关的补充数据" in prompt
    assert "仅取数和极简回答按用户指定范围交付" in prompt
    assert "真实可比的观测或正式窗口指标" in prompt
    assert "系统根据实际结果生成卡片、图表与明细表" in prompt
    assert "rN.column" in protocol
    assert "stepN.column" in protocol
    assert "data_request_complete=true" in protocol
    assert "方法的用途由 `read_finance_catalog` 统一说明" in protocol
    assert "金融公式、窗口与数据源适配由工具实现" in protocol
    assert "sample_complete=true" in manual
    assert "本步按空结果完成" in manual
    assert "实际状态" in prompt
    assert "恢复以工具返回的执行证据为准" in prompt
    assert "stock." not in prompt + manual + protocol
    assert "mode=" not in prompt + manual + protocol
    assert set(business_skills) == {
        "market-overview",
        "sector-theme-analysis",
        "stock-research",
        "stock-screening",
        "equity-report-analysis",
        "earnings-analysis",
        "factor-analysis",
        "valuation-analysis",
        "financial-quality-analysis",
        "stock-comparison",
        "technical-structure-analysis",
        "dividend-analysis",
    }
    assert all(f"name: {name}" in text for name, text in business_skills.items())
    assert all("finance_query" not in text for text in business_skills.values())
    assert "Codex" not in prompt + manual + protocol + "".join(business_skills.values())


def test_chat_dispatch_hands_investment_normal_qa_directly_to_financial_cc(
    monkeypatch,
) -> None:
    from src.web import flask_app as web

    calls = []

    class _Primary:
        enabled = True

        def accepts(self, *, dispatch_plan, attachments=None):
            return (
                dispatch_plan.get("selected_agent") == "investment_analyst"
                and dispatch_plan.get("turn_mode") == "normal_qa"
            )

        def answer(self, **kwargs):
            calls.append(kwargs)
            return {
                "mode": "financial_qa_cc",
                "message": "真实查询结果",
                "items": [],
            }

    class _OldRuntime:
        def execute_for_assistant(self, **kwargs):
            raise AssertionError("old tool plan runtime must not run")

    monkeypatch.setattr(web, "financial_qa_cc_service", _Primary())
    monkeypatch.setattr(web, "tool_plan_runtime_service", _OldRuntime())
    plan = {
        "selected_agent": "investment_analyst",
        "turn_mode": "normal_qa",
        "entry": "agent_route",
        "semantic_turn": {
            "ori_question": "茅台收盘价",
            "resolved_question": "贵州茅台最近交易日收盘价",
        },
    }

    result = web._build_chat_dispatch_payload(
        "茅台收盘价",
        application_context={},
        thread_context={},
        thread_id=7,
        turn_id=8,
        owner_id="owner-a",
        precomputed_plan=plan,
        research_mode="deep",
        data_only=True,
    )

    assert result["mode"] == "financial_qa_cc"
    assert calls[0]["owner_id"] == "owner-a"
    assert calls[0]["dispatch_plan"] == plan
    assert calls[0]["research_mode"] == "deep"
    assert calls[0]["data_only"] is True


def test_disabled_financial_cc_fails_closed_without_generic_tool_fallback(
    monkeypatch,
) -> None:
    from src.web import flask_app as web

    class _DisabledPrimary:
        enabled = False

        @staticmethod
        def accepts(**_kwargs):
            return False

    class _GenericRuntime:
        @staticmethod
        def execute_for_assistant(**_kwargs):
            raise AssertionError("financial QA must not fall back to generic tools")

    monkeypatch.setattr(web, "financial_qa_cc_service", _DisabledPrimary())
    monkeypatch.setattr(web, "tool_plan_runtime_service", _GenericRuntime())
    plan = {
        "selected_agent": "investment_analyst",
        "turn_mode": "normal_qa",
        "entry": "agent_route",
    }

    result = web._build_chat_dispatch_payload(
        "查询贵州茅台财务数据",
        application_context={},
        thread_context={},
        precomputed_plan=plan,
    )

    assert result["mode"] == "financial_qa_unavailable"
    assert "不会回落到通用搜索工具" in result["message"]


def test_financial_raw_rows_are_not_duplicated_in_persisted_chat_payload() -> None:
    from src.web import flask_app as web

    result = {
        "mode": "financial_qa_dsh",
        "summary": "简短回答",
        "data": {
            "format": "row-dict",
            "results": [
                {
                    "result_ref": "session://scope/vars/v1",
                    "row_count": 2,
                    "rows_complete": True,
                    "rows": [{"code": "600519.SH"}, {"code": "300750.SZ"}],
                }
            ],
        },
    }

    persisted = web._persistable_output_payload(result)

    assert result["data"]["results"][0]["rows"] == [
        {"code": "600519.SH"},
        {"code": "300750.SZ"},
    ]
    assert "rows" not in persisted["data"]["results"][0]
    assert persisted["data"]["results"][0]["result_ref"] == (
        "session://scope/vars/v1"
    )


def test_chat_dispatch_keeps_an_existing_planned_run_outside_financial_cc(
    monkeypatch,
) -> None:
    from src.web import flask_app as web

    class _Primary:
        enabled = True

        def accepts(self, *, dispatch_plan, attachments=None):
            return dispatch_plan.get("entry") == "agent_route"

        def answer(self, **kwargs):
            raise AssertionError("planned_run must not enter financial CC")

    class _OldRuntime:
        def execute_for_assistant(self, **kwargs):
            return {
                "mode": "tool_plan_result",
                "message": "既有计划执行完成",
                "items": [],
            }

    monkeypatch.setattr(web, "financial_qa_cc_service", _Primary())
    monkeypatch.setattr(web, "tool_plan_runtime_service", _OldRuntime())
    plan = {
        "selected_agent": "investment_analyst",
        "turn_mode": "normal_qa",
        "entry": "planned_run",
        "execution_plan": {
            "plan_type": "planned_run",
            "work_items": [],
        },
    }

    result = web._build_chat_dispatch_payload(
        "执行已有计划",
        application_context={},
        thread_context={},
        precomputed_plan=plan,
    )

    assert result["mode"] == "tool_plan_result"
    assert result["message"] == "既有计划执行完成"

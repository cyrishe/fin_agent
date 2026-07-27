import asyncio
import json
from pathlib import Path

from src.scenarios.financial_qa.service import FinancialQaCcService
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.services.conversation_preprocess_service import ConversationPreprocessService
from src.services.execution_plan_service import AgentRuntimePlanner
from src.services.session_variable_store_service import SessionVariableStoreService
from src.skill_runtime.models import ToolSpec


def _payload(tool_result: dict) -> dict:
    return json.loads(tool_result["content"][0]["text"])


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
                            "rules": ["实时与历史模式按参数区分"],
                            "fields": [{"name": "close"}],
                        },
                        {
                            "name": "margin",
                            "desc": "融资融券",
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
    }
    assert set(names) == {
        "mcp__finance__read_finance_catalog",
        "mcp__finance__finance_query",
        "mcp__finance__load_finance_result",
    }
    assert all("implement" not in name and "codex" not in name for name in names)


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
    tools, names, tracker = service.build_tools(
        owner_ids=["owner-a"],
        tool_context={
            "_agent_runtime_scope": "financial_qa:owner-a/thread-7",
            "allowed_agent_tools": ["financial_news_search"],
        },
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
    assert result["sample"]["rows"][0]["title"] == "贵州茅台发布公告"
    assert tracker["result_refs"][0]["tool"] == "financial_news_search"


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
        {"name": "quote", "desc": "行情"},
        {"name": "margin", "desc": "融资融券"},
    ]
    assert "fields" not in index["subjects"][0]["dataviews"][0]
    assert subject["mode"] == "subject"
    assert dataview["dataview"]["fields"] == [{"name": "close"}]
    assert [item["mode"] for item in tracker["catalog_reads"]] == [
        "index",
        "subject",
        "dataview",
    ]


def test_queries_keep_dependency_handles_and_results_are_pageable(tmp_path: Path) -> None:
    service, runtime, tool_runtime, tools, _, tracker = _tools(tmp_path)

    first = _payload(
        asyncio.run(
            tools["finance_query"].handler(
                {"request": "r1 = stock.quote(filter='贵州茅台') -> stock_code, stock_name, close"}
            )
        )
    )
    second = _payload(
        asyncio.run(
            tools["finance_query"].handler(
                {"request": "r2 = stock.quote(filter=r1.stock_code) -> stock_code, close"}
            )
        )
    )
    loaded = _payload(
        asyncio.run(
            tools["load_finance_result"].handler(
                {"result_ref": first["result_ref"]}
            )
        )
    )

    assert runtime.calls[0]["previous"] == []
    assert runtime.calls[1]["previous"] == ["r1"]
    assert set(tool_runtime.result_handles) == {"r1", "r2"}
    assert first["row_count"] == 1
    assert second["result_name"] == "r2"
    assert loaded["rows"][0]["stock_name"] == "贵州茅台"
    assert len(tracker["result_refs"]) == 2

    restored = service.create_runtime()
    restored.begin_turn(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/thread-7"},
    )
    assert set(restored.result_handles) == {"r1", "r2"}


def test_validation_failure_is_returned_for_cc_repair_and_not_registered(
    tmp_path: Path,
) -> None:
    service, _, _, tools, _, tracker = _tools(tmp_path)

    failed = _payload(
        asyncio.run(
            tools["finance_query"].handler(
                {"request": "r1 = stock.quote(...) -> invalid"}
            )
        )
    )

    assert failed["validation"]["ok"] is False
    assert "made_up_field" in failed["validation"]["errors"][0]
    assert tracker["result_refs"] == []
    assert service.result_store.list_variables(
        session_id="financial_qa:owner-a/thread-7"
    ) == []


def test_long_lived_query_tools_record_calls_on_the_current_turn(
    tmp_path: Path,
) -> None:
    _, _, tool_runtime, tools, _, first_tracker = _tools(tmp_path)
    asyncio.run(
        tools["finance_query"].handler(
            {"request": "r1 = stock.quote(...) -> stock_code, close"}
        )
    )
    second_tracker = tool_runtime.begin_turn(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "financial_qa:owner-a/thread-7"},
    )
    asyncio.run(
        tools["finance_query"].handler(
            {"request": "r2 = stock.quote(filter=r1.stock_code) -> stock_code, close"}
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

    result = service.answer(
        thread_id=7,
        turn_id=8,
        owner_id="owner-a",
        user_text="那它呢？",
        dispatch_plan=plan,
        application_context={
            "default_agent": {
                "tools": ["financial_news_search"],
                "runtime_profile": {
                    "tools": ["financial_news_search"],
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
    assert session.calls[0]["context"]["allowed_agent_tools"] == [
        "financial_news_search"
    ]
    assert session.calls[0]["context"]["_agent_system_prompt"] == (
        "Investment Analyst 原有角色提示"
    )
    assert "不存在于当前 harness 的旧工具" not in session.calls[0]["context"][
        "_agent_system_prompt"
    ]
    assert result["mode"] == "financial_qa_cc"
    assert result["financial_qa"]["tool_calls"] == [{"tool": "finance_query"}]


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


def test_financial_qa_prompt_and_manual_keep_business_specific_rules() -> None:
    root = Path("src/scenarios/financial_qa")
    prompt = (root / "system.md").read_text(encoding="utf-8")
    manual = (root / "data_query.md").read_text(encoding="utf-8")
    deep_dive = Path("src/skills/stock_deep_dive/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "没有查询结果时不编造数值" in prompt
    assert "constitution" in manual
    assert "margin" in manual
    assert "rN.column" in manual
    assert "name: stock-deep-dive" in deep_dive
    assert "finance_query" in deep_dive
    assert "Codex" not in prompt + manual + deep_dive


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
    )

    assert result["mode"] == "financial_qa_cc"
    assert calls[0]["owner_id"] == "owner-a"
    assert calls[0]["dispatch_plan"] == plan


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

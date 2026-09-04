from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.scenarios.financial_qa.dsh_mcp_server import FinanceDshMcpBridge
from src.scenarios.financial_qa.dsh_service import (
    FinanceDeepSeekHarnessSessionService,
    _loop_policy_observability,
)
from src.scenarios.financial_qa.runtime import normalize_financial_qa_runtime
from src.scenarios.financial_qa.service import FinancialQaCcService
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.services.session_variable_store_service import SessionVariableStoreService


class _Session:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[dict] = []

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "session_id": f"{self.label}-session",
            "resumed": False,
            "duration_ms": 10,
            "result": f"{self.label} answer",
            "error": "",
            "tool_calls": [{"tool": "finance_query"}],
            "result_refs": [],
            "model_name": "deepseek-v4-flash",
            "loop_policy": {"enabled": True, "request_count": 3},
            "prompt_assets": {
                "global_system": {"sha256": "global-revision"},
                "stage_policy": {"sha256": "stage-revision"},
            },
        }

    def close(self) -> None:
        return None


def _plan() -> dict:
    return {
        "selected_agent": "investment_analyst",
        "turn_mode": "normal_qa",
        "entry": "agent_route",
        "semantic_turn": {"resolved_question": "贵州茅台最近收盘价"},
    }


def _cc_payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def test_financial_runtime_is_explicit_and_defaults_to_cc() -> None:
    assert normalize_financial_qa_runtime(None) == "cc"
    assert normalize_financial_qa_runtime("DSH") == "dsh"
    with pytest.raises(ValueError, match="cc 或 dsh"):
        normalize_financial_qa_runtime("auto")


def test_dsh_reasoning_effort_defaults_low_and_validates_env(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FINANCE_DSH_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("FINANCE_DSH_WORKERS", raising=False)
    default_service = FinanceDeepSeekHarnessSessionService(
        enabled=False,
        root_dir=tmp_path / "default",
    )
    assert default_service.reasoning_effort == "low"
    assert default_service.worker_count == 10

    monkeypatch.setenv("FINANCE_DSH_REASONING_EFFORT", "high")
    high_service = FinanceDeepSeekHarnessSessionService(
        enabled=False,
        root_dir=tmp_path / "high",
    )
    assert high_service.reasoning_effort == "high"

    monkeypatch.setenv("FINANCE_DSH_REASONING_EFFORT", "medium")
    with pytest.raises(ValueError, match="off、low、high 或 max"):
        FinanceDeepSeekHarnessSessionService(
            enabled=False,
            root_dir=tmp_path / "invalid",
        )


def test_dsh_uses_server_specific_key_and_endpoint_before_legacy_env(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[dict] = []

    class _Harness:
        def __init__(self, **kwargs) -> None:
            captured.append(kwargs)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "personal-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://personal.example/v1")
    monkeypatch.setenv("FINANCE_DSH_API_KEY", "server-key")
    monkeypatch.setenv(
        "FINANCE_DSH_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    service = FinanceDeepSeekHarnessSessionService(
        enabled=False,
        root_dir=tmp_path / "runtime",
        harness_factory=_Harness,
        worker_count=1,
    )

    service._create_harness(service._workers[0])

    assert captured[0]["api_key"] == "server-key"
    assert captured[0]["base_url"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )


def test_dsh_loop_observability_reads_stable_message_prompt_and_token_cap() -> None:
    marker = (
        "[FINANCE_LOOP stage=query reason=dataview_ready]\n"
        "目录字段与口径已经就绪。"
    )
    events = [
        {
            "type": "user/message",
            "data": {
                "role": "user",
                "content": [{"type": "text", "text": marker}],
                "source": {
                    "kind": "plugin",
                    "plugin": "fin-agent-finance-loop-policy",
                },
            },
        },
        {
            "type": "request/header",
            "data": {
                "header": {
                    "system": "stable system",
                    "tools": [{"name": "mcp__finance__finance_query"}],
                    "config": {
                        "reasoningEffort": "low",
                        "maxTokens": 3072,
                    },
                }
            },
        },
        {
            "type": "tool/call",
            "data": {
                "turn": 1,
                "step": 2,
                "callId": "query-1",
                "name": "mcp__finance__finance_query",
            },
        },
        {
            "type": "assistant/message",
            "data": {
                "turn": 1,
                "step": 2,
                "usage": {
                    "inputTokens": 100,
                    "cacheReadTokens": 20,
                    "outputTokens": 3072,
                    "reasoningTokens": 3060,
                },
            },
        },
    ]

    observed = _loop_policy_observability(events, config={"enabled": True})
    request = observed["requests"][0]

    assert request["stage"] == "query"
    assert request["stage_reason"] == "dataview_ready"
    assert request["prompt_injected"] is True
    assert request["prompt_surface"] == "message"
    assert len(request["prompt_sha256"]) == 64
    assert request["stage_inferred_from_calls"] is False
    assert request["visible_output_tokens"] == 12
    assert request["reasoning_share"] == pytest.approx(0.9961)
    assert request["max_token_hit"] is True


def test_financial_qa_service_delegates_only_the_selected_runtime() -> None:
    cc = _Session("cc")
    dsh = _Session("dsh")
    service = FinancialQaCcService(
        enabled=True,
        session_service=cc,
        dsh_session_service=dsh,
    )

    result = service.answer(
        thread_id=7,
        turn_id=8,
        owner_id="owner-a",
        user_text="茅台收盘价",
        dispatch_plan=_plan(),
        runtime="dsh",
    )

    assert not cc.calls
    assert len(dsh.calls) == 1
    assert result["mode"] == "financial_qa_dsh"
    assert result["financial_qa"]["runtime"] == "dsh"
    assert result["financial_qa"]["loop_policy"]["request_count"] == 3
    assert result["financial_qa"]["prompt_assets"]["stage_policy"]["sha256"] == (
        "stage-revision"
    )
    assert result["message"] == "dsh answer"


def test_financial_qa_returns_full_row_dict_data_and_can_suppress_summary(
    tmp_path: Path,
) -> None:
    store = SessionVariableStoreService(data_root=tmp_path / "data")
    rows = [
        {"institution": f"机构{index}", "metric_value": 90 + index}
        for index in range(1, 6)
    ]
    variable = store.register_tool_result(
        session_id="dsh-data",
        tool_name="finance_data_query",
        task="取得逐机构预测值",
        local_alias="r1",
        result={
            "ok": True,
            "result": {
                "name": "r1",
                "api": "stock.report_metric",
                "columns": ["institution", "metric_value"],
                "data": {"rows": rows, "row_count": len(rows)},
            },
        },
    )
    assert variable is not None

    class _DataSession(_Session):
        def run_turn(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "session_id": "dsh-session",
                "resumed": False,
                "duration_ms": 10,
                "result": "机构预测值有所变化。",
                "error": "",
                "tool_calls": [{"tool": "finance_query"}],
                "result_refs": [
                    {
                        "result_name": "r1",
                        "goal": "取得逐机构预测值",
                        "api": "stock.report_metric",
                        "result_ref": variable["data_ref"],
                        "data_type": "table",
                        "row_count": len(rows),
                        "schema": variable["schema"],
                        "sample": variable["sample"],
                        "sample_complete": False,
                    }
                ],
                "model_name": "deepseek-v4-flash",
                "loop_policy": {"enabled": True, "request_count": 2},
            }

    session = _DataSession("dsh")
    tools = FinanceDataQueryCcTools(result_store=store)
    service = FinancialQaCcService(
        enabled=True,
        session_service=_Session("cc"),
        dsh_session_service=session,
        system_tools=tools,
    )

    regular = service.answer(
        thread_id=7,
        turn_id=8,
        owner_id="owner-a",
        user_text="对比机构预测",
        dispatch_plan=_plan(),
        runtime="dsh",
    )
    assert regular["summary"] == regular["message"] == "机构预测值有所变化。"
    assert regular["data"]["format"] == "row-dict"
    assert regular["data"]["results"][0]["rows"] == rows
    assert regular["data"]["results"][0]["rows_complete"] is True

    data_only = service.answer(
        thread_id=7,
        turn_id=9,
        owner_id="owner-a",
        user_text="对比机构预测",
        dispatch_plan=_plan(),
        runtime="dsh",
        data_only=True,
    )
    assert data_only["data_only"] is True
    assert data_only["message"] == ""
    assert data_only["summary"] == ""
    assert data_only["data"]["results"][0]["rows"] == rows
    assert all(
        block.get("semantic") != "finance.answer"
        for block in data_only["surface_blocks"]
    )
    assert session.calls[-1]["context"]["_finance_data_only"] is True


def test_dsh_mcp_bridge_exposes_only_financial_data_query_tools(tmp_path: Path) -> None:
    context_path = tmp_path / "context.json"
    trace_path = tmp_path / "trace.json"
    context_path.write_text(
        json.dumps(
            {
                "revision": "turn-1",
                "owner_ids": ["owner-a"],
                "tool_context": {"_agent_runtime_scope": "dsh:test"},
            }
        ),
        encoding="utf-8",
    )
    bridge = FinanceDshMcpBridge(
        context_path=context_path,
        trace_path=trace_path,
    )

    assert {item.name for item in bridge.list_tools()} == {
        "read_finance_catalog",
        "finance_query",
        "load_finance_result",
    }
    result = asyncio.run(
        bridge.call_tool(
            "read_finance_catalog",
            {"subject": "stock", "dataview": "quote"},
        )
    )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))

    assert result["mode"] == "dataview"
    assert result["dataview"]["name"] == "quote"
    assert trace["revision"] == "turn-1"
    assert trace["tracker"]["calls"][0]["tool"] == "read_finance_catalog"


def test_cc_and_dsh_share_the_exact_finance_catalog_contract(tmp_path: Path) -> None:
    context_path = tmp_path / "context.json"
    trace_path = tmp_path / "trace.json"
    context_path.write_text(
        json.dumps(
            {
                "revision": "turn-parity",
                "owner_ids": ["owner-a"],
                "tool_context": {"_agent_runtime_scope": "dsh:parity"},
            }
        ),
        encoding="utf-8",
    )
    system_tools = FinanceDataQueryCcTools()
    cc_tools, _, _ = system_tools.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "cc:parity"},
    )
    cc_by_name = {item.name: item for item in cc_tools}
    bridge = FinanceDshMcpBridge(
        context_path=context_path,
        trace_path=trace_path,
        system_tools=system_tools,
    )
    dsh_by_name = {item.name: item for item in bridge.list_tools()}

    for name in {"read_finance_catalog", "finance_query", "load_finance_result"}:
        assert dsh_by_name[name].description == cc_by_name[name].description
        assert dsh_by_name[name].inputSchema == cc_by_name[name].input_schema

    catalog_reads = [
        {},
        {"subject": "stock"},
        {"subject": "stock", "dataview": "quote"},
        {
            "subject": "stock",
            "dataview": "quote",
            "operation": "aggregate",
        },
        {"subject": "unknown"},
    ]
    for arguments in catalog_reads:
        cc_result = _cc_payload(
            asyncio.run(
                cc_by_name["read_finance_catalog"].handler(arguments)
            )
        )
        dsh_result = asyncio.run(
            bridge.call_tool("read_finance_catalog", arguments)
        )
        assert dsh_result == cc_result


def test_cc_and_dsh_share_finance_query_and_result_load_payloads(
    tmp_path: Path,
) -> None:
    class _FinanceRuntime:
        @staticmethod
        def execute_request(*, request, previous_results=None):
            result_name = "r2" if request.startswith("r2 =") else "r1"
            return {
                "protocol": "finance_data_tool.v1",
                "request": request,
                "validation": {"ok": True, "errors": [], "warnings": []},
                "result": {
                    "name": result_name,
                    "api": "stock.quote",
                    "columns": ["code", "name", "close"],
                    "data": {
                        "status": "ok",
                        "rows": [
                            {
                                "code": "600519.SH",
                                "name": "贵州茅台",
                                "close": 1500.0,
                            }
                        ],
                        "row_count": 1,
                    },
                },
            }

    context_path = tmp_path / "context.json"
    trace_path = tmp_path / "trace.json"
    context_path.write_text(
        json.dumps(
            {
                "revision": "turn-query-parity",
                "owner_ids": ["owner-a"],
                "tool_context": {"_agent_runtime_scope": "dsh:query-parity"},
            }
        ),
        encoding="utf-8",
    )
    system_tools = FinanceDataQueryCcTools(
        finance_runtime=_FinanceRuntime(),
        result_store=SessionVariableStoreService(data_root=tmp_path / "data"),
    )
    cc_tools, _, _ = system_tools.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "cc:query-parity"},
    )
    cc_by_name = {item.name: item for item in cc_tools}
    bridge = FinanceDshMcpBridge(
        context_path=context_path,
        trace_path=trace_path,
        system_tools=system_tools,
    )
    arguments = {
        "steps": [
            {
                "goal": "查询贵州茅台收盘价",
                "request": (
                    'result = stock.quote(filter = "code = 600519.SH", '
                    "mode = 0, count = 1) -> code, name, close"
                ),
            }
        ]
    }

    cc_query = _cc_payload(
        asyncio.run(cc_by_name["finance_query"].handler(arguments))
    )
    dsh_query = asyncio.run(bridge.call_tool("finance_query", arguments))
    cc_ref = cc_query.pop("result_ref")
    dsh_ref = dsh_query.pop("result_ref")
    assert dsh_query == cc_query

    cc_page = _cc_payload(
        asyncio.run(
            cc_by_name["load_finance_result"].handler(
                {"result_ref": cc_ref, "columns": ["code", "close"]}
            )
        )
    )
    dsh_page = asyncio.run(
        bridge.call_tool(
            "load_finance_result",
            {"result_ref": dsh_ref, "columns": ["code", "close"]},
        )
    )
    cc_page.pop("result_ref")
    dsh_page.pop("result_ref")
    assert dsh_page == cc_page


def test_dsh_trace_uses_the_revision_pinned_when_tools_were_built(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context_path = tmp_path / "context.json"
    trace_path = tmp_path / "trace.json"
    context_path.write_text(
        json.dumps(
            {
                "revision": "turn-race",
                "finance_catalog_revision": "catalog-a",
                "owner_ids": ["owner-a"],
                "tool_context": {"_agent_runtime_scope": "dsh:race"},
            }
        ),
        encoding="utf-8",
    )
    system_tools = FinanceDataQueryCcTools()
    revisions = iter(["catalog-a", "catalog-a", "catalog-b"])
    last_revision = "catalog-b"

    def scripted_revision() -> str:
        nonlocal last_revision
        try:
            last_revision = next(revisions)
        except StopIteration:
            pass
        return last_revision

    monkeypatch.setattr(
        system_tools.finance_catalog,
        "catalog_revision",
        scripted_revision,
    )

    bridge = FinanceDshMcpBridge(
        context_path=context_path,
        trace_path=trace_path,
        system_tools=system_tools,
    )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))

    assert trace["finance_catalog_revision"] == "catalog-a"
    with pytest.raises(RuntimeError, match="active agent turn"):
        bridge.list_tools()
    assert asyncio.run(
        bridge.call_tool(
            "read_finance_catalog",
            {"subject": "stock", "dataview": "quote"},
        )
    ) == {"error": "finance catalog changed during the active agent turn"}


def test_dsh_session_reuses_worker_and_projects_trace(tmp_path: Path) -> None:
    created: list[object] = []

    class _Harness:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.closed = False
            created.append(self)

        def run(self, prompt, *, session_id, on_notification):
            context_path = Path(self.kwargs["env"]["FIN_AGENT_DSH_CONTEXT_PATH"])
            trace_path = Path(self.kwargs["env"]["FIN_AGENT_DSH_TRACE_PATH"])
            context = json.loads(context_path.read_text(encoding="utf-8"))
            trace_path.write_text(
                json.dumps(
                    {
                        "revision": context["revision"],
                        "tracker": {
                            "calls": [
                                {
                                    "tool": "finance_query",
                                    "api": "stock.quote",
                                    "api_execution_ms": 12,
                                }
                            ],
                            "result_refs": [
                                {"result_ref": "session://dsh/vars/v1"}
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(
                final_response="真实数据回答",
                finish_reason="completed",
                events=[
                    {
                        "type": "assistant/message",
                        "data": {
                            "message": {
                                "source": {
                                    "kind": "model",
                                    "model": "deepseek-v4-flash",
                                }
                            },
                            "usage": {
                                "inputTokens": 100,
                                "cacheReadTokens": 20,
                                "outputTokens": 30,
                                "reasoningTokens": 10,
                            },
                        },
                    },
                    {"type": "tool/result", "data": {}},
                ],
            )

        def close(self) -> None:
            self.closed = True

    service = FinanceDeepSeekHarnessSessionService(
        enabled=True,
        root_dir=tmp_path / "runtime",
        log_path=tmp_path / "events.jsonl",
        worker_count=1,
        harness_factory=_Harness,
    )
    first = service.run_turn(
        thread_id=7,
        turn_id=1,
        owner_id="owner-a",
        user_text="贵州茅台最近收盘价",
    )
    second = service.run_turn(
        thread_id=7,
        turn_id=2,
        owner_id="owner-a",
        user_text="那成交额呢",
    )

    assert len(created) == 1
    assert first["resumed"] is False
    assert second["resumed"] is True
    assert first["tool_calls"][0]["api"] == "stock.quote"
    assert first["result_refs"][0]["result_ref"] == "session://dsh/vars/v1"
    assert first["llm_usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 30,
        "total_tokens": 130,
        "cache_read_tokens": 20,
        "cumulative_context_tokens": 120,
        "mean_context_tokens_per_call": 120.0,
        "max_context_tokens_per_call": 120,
        "final_context_tokens": 120,
        "reasoning_tokens": 10,
        "non_reasoning_completion_tokens": 20,
        "call_count": 1,
    }
    assert first["llm_step_usages"] == [
        {
            "request_index": 1,
            "turn": 0,
            "step": 0,
            "input_tokens": 100,
            "cache_read_tokens": 20,
            "context_tokens": 120,
            "output_tokens": 30,
            "reasoning_tokens": 10,
            "non_reasoning_output_tokens": 20,
        }
    ]
    assert first["reasoning_effort"] == "low"
    assert created[0].kwargs["reasoning_effort"] == "low"
    service.close()
    assert created[0].closed is True


def test_dsh_runs_ten_independent_sessions_concurrently_without_context_leakage(
    tmp_path: Path,
) -> None:
    state_lock = threading.Lock()
    active = 0
    max_active = 0
    observed: list[tuple[str, str, str]] = []

    class _Catalog:
        @staticmethod
        def catalog_revision() -> str:
            return "catalog-a"

    class _Runtime:
        def begin_turn(self, **kwargs):
            return {}

        def current_context_prompt(self) -> str:
            return ""

    class _SystemTools:
        finance_catalog = _Catalog()

        @staticmethod
        def create_runtime():
            return _Runtime()

    class _Harness:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, prompt, *, session_id, on_notification):
            nonlocal active, max_active
            context = json.loads(
                Path(
                    self.kwargs["env"]["FIN_AGENT_DSH_CONTEXT_PATH"]
                ).read_text(encoding="utf-8")
            )
            with state_lock:
                active += 1
                max_active = max(max_active, active)
                observed.append(
                    (
                        prompt,
                        session_id,
                        context["tool_context"]["_agent_runtime_scope"],
                    )
                )
            time.sleep(0.08)
            with state_lock:
                active -= 1
            return SimpleNamespace(
                final_response=f"answer:{prompt}",
                finish_reason="completed",
                events=[],
            )

        def close(self) -> None:
            return None

    service = FinanceDeepSeekHarnessSessionService(
        enabled=True,
        system_tools=_SystemTools(),
        root_dir=tmp_path / "runtime",
        log_path=tmp_path / "events.jsonl",
        worker_count=10,
        harness_factory=_Harness,
    )

    with ThreadPoolExecutor(max_workers=10) as executor:
        records = list(
            executor.map(
                lambda index: service.run_turn(
                    thread_id=f"request-{index}",
                    turn_id=index,
                    owner_id="api-client",
                    user_text=f"query-{index}",
                    context={"_finance_isolated_request": True},
                ),
                range(10),
            )
        )

    assert max_active == 10
    assert len({item["session_id"] for item in records}) == 10
    assert len({item["worker_index"] for item in records}) == 10
    assert len({item[2] for item in observed}) == 10
    assert service._session_workers == {}
    assert all(item["resumed"] is False for item in records)
    assert all(item["isolated_request"] is True for item in records)
    assert all(
        session_id.removeprefix("financial-qa-")
        == scope.removeprefix("financial_qa_dsh:")
        for _prompt, session_id, scope in observed
    )
    assert {item["result"] for item in records} == {
        f"answer:query-{index}" for index in range(10)
    }
    service.close()


def test_dsh_prewarms_each_configured_worker(tmp_path: Path, monkeypatch) -> None:
    started: list[str] = []

    class _Harness:
        def __init__(self, **kwargs) -> None:
            self.home = kwargs["dsh_home"]

        def start(self) -> None:
            started.append(self.home)

        def close(self) -> None:
            return None

    monkeypatch.delenv("FINANCE_DSH_PREWARM_WORKERS", raising=False)
    service = FinanceDeepSeekHarnessSessionService(
        enabled=True,
        root_dir=tmp_path / "runtime",
        log_path=tmp_path / "events.jsonl",
        worker_count=3,
        harness_factory=_Harness,
    )

    status = service.prewarm()

    assert status["workers_ready"] == 3
    assert status["worker_indexes"] == [0, 1, 2]
    assert len(started) == 3
    service.close()


def test_dsh_reconnects_worker_when_finance_catalog_revision_changes(
    tmp_path: Path,
) -> None:
    created: list[object] = []

    class _CatalogRevision:
        value = "catalog-a"

        def catalog_revision(self) -> str:
            return self.value

    class _Runtime:
        def begin_turn(self, **kwargs):
            return {}

        def current_context_prompt(self) -> str:
            return ""

    class _SystemTools:
        def __init__(self) -> None:
            self.finance_catalog = _CatalogRevision()

        def create_runtime(self):
            return _Runtime()

    class _Harness:
        def __init__(self, **kwargs) -> None:
            self.closed = False
            created.append(self)

        def run(self, prompt, *, session_id, on_notification):
            return SimpleNamespace(
                final_response="ok",
                finish_reason="completed",
                events=[],
            )

        def close(self) -> None:
            self.closed = True

    system_tools = _SystemTools()
    service = FinanceDeepSeekHarnessSessionService(
        enabled=True,
        system_tools=system_tools,
        root_dir=tmp_path / "runtime",
        log_path=tmp_path / "events.jsonl",
        worker_count=1,
        harness_factory=_Harness,
    )

    service.run_turn(
        thread_id=7,
        turn_id=1,
        owner_id="owner-a",
        user_text="first",
    )
    system_tools.finance_catalog.value = "catalog-b"
    service.run_turn(
        thread_id=7,
        turn_id=2,
        owner_id="owner-a",
        user_text="second",
    )

    assert len(created) == 2
    assert created[0].closed is True
    context = json.loads(
        service._workers[0].context_path.read_text(encoding="utf-8")
    )
    assert context["finance_catalog_revision"] == "catalog-b"
    service.close()


def test_chat_runtime_parameter_is_forwarded_only_for_dsh(monkeypatch) -> None:
    from src.web import flask_app as web

    calls: list[tuple[str, dict]] = []

    class _Primary:
        enabled = True

        def accepts(self, **kwargs):
            calls.append(("accepts", kwargs))
            return True

        def answer(self, **kwargs):
            calls.append(("answer", kwargs))
            return {"mode": "financial_qa_dsh", "message": "完成", "items": []}

    monkeypatch.setattr(web, "financial_qa_cc_service", _Primary())
    result = web._build_chat_dispatch_payload(
        "茅台收盘价",
        application_context={},
        thread_context={},
        thread_id=7,
        turn_id=8,
        owner_id="owner-a",
        precomputed_plan=_plan(),
        financial_qa_runtime="dsh",
    )

    assert result["mode"] == "financial_qa_dsh"
    assert calls[0][1]["runtime"] == "dsh"
    assert calls[1][1]["runtime"] == "dsh"


def test_chat_api_rejects_unknown_financial_runtime_before_work() -> None:
    from src.web import flask_app as web

    client = web.app.test_client()
    dispatch = client.post(
        "/api/chat/dispatch",
        json={"text": "分析贵州茅台", "financial_qa_runtime": "other"},
    )
    stream = client.post(
        "/api/chat/stream/start",
        json={"text": "分析贵州茅台", "financial_qa_runtime": "other"},
    )

    assert dispatch.status_code == 400
    assert stream.status_code == 400
    assert "cc 或 dsh" in dispatch.get_json()["error"]
    assert "cc 或 dsh" in stream.get_json()["error"]

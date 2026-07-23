import asyncio

from src.services.finance_cc_system_tools import FinanceCcSystemTools
from src.services.session_variable_store_service import SessionVariableStoreService


class FakeFinanceRuntime:
    def execute_request(self, *, request):
        return {"protocol": "finance_data_tool.v1", "request": request, "result": {"data": [{"close": 10.0}]}}


class FakeFinanceCatalog:
    def build_tree(self):
        return {"version": "v1", "subjects": [{"name": "stock", "desc": "股票", "dataviews": [{"name": "quote"}]}]}

    def get_subject(self, subject):
        return {"name": subject, "dataviews": [{"name": "quote"}]}

    def get_dataview(self, subject, dataview):
        return {"subject": subject, "name": dataview, "fields": [{"name": "close"}]}


class FakeStore:
    def load_for_runtime(self, tool_name, *, owner_ids=None, allow_inactive=False):
        return {
            "manifest": {"tool_name": tool_name, "last_test": {"ok": True}},
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "sample_input": {},
            "modules": [{"name": "main"}],
            "code": "def run(inputs): return {}",
        }


class FakeDynamicRuntime:
    def run(self, tool_name, arguments, *, owner_ids=None, allow_inactive=False):
        return {"tool": tool_name, "ok": True, "data": {"arguments": arguments}, "error": "", "meta": {}}


def _tools(*, implementation_runner=None, runtime_context_adapter=None, runtime_scope="", requirement_only=False):
    service = FinanceCcSystemTools(
        custom_tool_store=FakeStore(),
        custom_tool_runtime=FakeDynamicRuntime(),
        finance_runtime=FakeFinanceRuntime(),
        finance_catalog=FakeFinanceCatalog(),
        implementation_runner=implementation_runner,
        runtime_context_adapter=runtime_context_adapter,
    )
    tools, names, tracker = service.build_tools(
        owner_ids=["owner-1"],
        tool_context={
            "_agent_runtime_scope": runtime_scope,
            "_finance_cc_requirement_only": requirement_only,
            "custom_tool_state": {
                "tool_name": "demo_tool",
                "requirement_text": "筛选股票",
                "design_contract": {"tool_name": "demo_tool", "flow": {"steps": []}},
            }
        },
    )
    return {item.name: item for item in tools}, names, tracker


def test_requirement_only_mode_exposes_only_requirement_tools() -> None:
    tools, names, _ = _tools(requirement_only=True)

    assert set(tools) == {"request_user_interaction", "save_finance_artifact"}
    assert names == [
        "mcp__finance__request_user_interaction",
        "mcp__finance__save_finance_artifact",
    ]


def test_finance_cc_repeated_implementation_uses_adapter_owned_session(tmp_path) -> None:
    from src.services.agent_providers.runtime_context import AgentRuntimeContextAdapter

    received_sessions = []

    def implementation_runner(**kwargs):
        runtime = dict(kwargs["state"].get("agent_runtime") or {})
        received_sessions.append(runtime)
        return {
            "message": "技术测试完成。",
            "state": {
                "tool_name": "demo_tool",
                "agent_runtime": {
                    **runtime,
                    "provider_session_id": "codex-provider-session",
                },
            },
            "test_result": {"execution_ok": True, "contract_ok": True},
            "tool": {"manifest": {"tool_name": "demo_tool", "current_revision": 1}},
            "implementation_meta": {"provider_session_id": "codex-provider-session"},
        }

    adapter = AgentRuntimeContextAdapter(tmp_path)
    tools, _, tracker = _tools(
        implementation_runner=implementation_runner,
        runtime_context_adapter=adapter,
        runtime_scope="owner-a/thread-7",
    )
    asyncio.run(tools["implement_dynamic_tool"].handler({"instruction": "首次实现"}))
    asyncio.run(tools["implement_dynamic_tool"].handler({"instruction": "同一回合重复请求"}))
    assert len(received_sessions) == 1

    next_turn_tools, _, _ = _tools(
        implementation_runner=implementation_runner,
        runtime_context_adapter=adapter,
        runtime_scope="owner-a/thread-7",
    )
    asyncio.run(next_turn_tools["implement_dynamic_tool"].handler({"instruction": "下一回合新增反馈"}))

    assert received_sessions[0]["session_id"] == received_sessions[1]["session_id"]
    assert received_sessions[1]["provider_session_id"] == "codex-provider-session"
    assert "agent_runtime" not in tracker["implementation_runs"][-1]["state"]


def test_finance_cc_system_tools_execute_existing_services() -> None:
    tools, names, tracker = _tools()

    catalog = asyncio.run(tools["read_finance_asset"].handler({"asset_type": "api_catalog"}))
    query = asyncio.run(tools["finance_query"].handler({"request": "r1 = stock.quote(...)"}))
    run = asyncio.run(tools["run_dynamic_tool"].handler({"tool_name": "demo_tool", "arguments": {"code": "600519.SH"}}))

    assert "mcp__finance__finance_query" in names
    assert "mcp__finance__load_result" in names
    assert catalog.get("isError") is not True
    assert "stock" in catalog["content"][0]["text"]
    assert query.get("isError") is not True
    assert "close" in query["content"][0]["text"]
    assert run.get("isError") is not True
    assert [item["tool"] for item in tracker["calls"]] == [
        "read_finance_asset",
        "finance_query",
        "run_dynamic_tool",
    ]


def test_finance_cc_user_interaction_is_recorded_without_guessing_answer() -> None:
    tools, _, tracker = _tools()

    result = asyncio.run(
        tools["request_user_interaction"].handler(
            {
                "questions": [{"question": "使用30日还是60日？", "candidate": ["30日", "60日"]}],
            }
        )
    )

    assert result.get("isError") is not True
    assert tracker["interaction_requests"][0]["questions"][0]["candidate"][0] == "30日"


def test_finance_cc_artifact_save_validates_existing_protocol() -> None:
    tools, _, tracker = _tools()
    payload = {
        "summary": "做一个选股工具。",
        "requirement_brief": "筛选满足用户条件的股票，并返回候选股票列表。",
        "notice": ["未指定市场时按A股处理。"],
        "questions": [],
    }

    valid = asyncio.run(
        tools["save_finance_artifact"].handler({"artifact_type": "requirement", "payload": payload})
    )
    invalid = asyncio.run(
        tools["save_finance_artifact"].handler({"artifact_type": "requirement", "payload": {"summary": "missing"}})
    )

    assert valid.get("isError") is not True
    assert invalid.get("isError") is True
    assert tracker["artifact_updates"] == [{"artifact_type": "requirement", "payload": payload}]


def test_finance_cc_asset_access_uses_current_tool_without_requiring_name() -> None:
    tools, _, _ = _tools()

    design = asyncio.run(tools["read_finance_asset"].handler({"asset_type": "design"}))
    code = asyncio.run(tools["read_finance_asset"].handler({"asset_type": "code"}))

    assert "demo_tool" in design["content"][0]["text"]
    assert "def run" in code["content"][0]["text"]


def test_requirement_asset_exposes_only_the_merged_brief() -> None:
    tools, _, _ = _tools()
    result = asyncio.run(tools["read_finance_asset"].handler({"asset_type": "requirement"}))
    payload = result["content"][0]["text"]

    assert "requirement_brief" in payload
    assert "requirement_text" not in payload
    assert "feedback_ledger" not in payload
    assert '"notice"' not in payload


def test_finance_cc_implementation_tool_delegates_saved_state_to_codex_runner() -> None:
    calls = []

    def implementation_runner(**kwargs):
        calls.append(kwargs)
        return {
            "message": "代码和样例技术测试已完成。",
            "state": {"tool_name": "demo_tool", "implementation_revision": 1},
            "test_result": {
                "execution_ok": True,
                "contract_ok": True,
                "business_ok": False,
                "gate_passed": False,
                "summary": "1 / 1 项技术运行成功",
            },
            "tool": {"manifest": {"tool_name": "demo_tool", "current_revision": 1}, "code": "hidden"},
        }

    tools, names, tracker = _tools(implementation_runner=implementation_runner)
    result = asyncio.run(
        tools["implement_dynamic_tool"].handler({"instruction": "按已确认设计实现工具"})
    )

    assert "mcp__finance__implement_dynamic_tool" in names
    assert result.get("isError") is not True
    assert calls[0]["owner_id"] == "owner-1"
    assert calls[0]["state"]["design_contract"]["tool_name"] == "demo_tool"
    assert tracker["implementation_runs"][0]["test_result"]["result_ref"]
    assert result.get("isError") is not True
    assert "business_ok" not in result["content"][0]["text"]
    assert "gate_passed" not in result["content"][0]["text"]
    assert '"tool_name": "demo_tool"' in result["content"][0]["text"]
    assert '"revision": 1' in result["content"][0]["text"]
    assert "代码和样例技术测试已完成" in result["content"][0]["text"]
    assert "code" not in tracker["implementation_runs"][0]["tool"]


def test_finance_cc_saved_design_is_visible_to_later_tools_in_same_turn() -> None:
    calls = []

    def implementation_runner(**kwargs):
        calls.append(kwargs)
        return {
            "message": "代码和样例技术测试已完成。",
            "state": {**kwargs["state"], "implementation_revision": 1},
            "test_result": {"execution_ok": True},
            "tool": {"manifest": {"tool_name": "new_tool", "current_revision": 1}},
        }

    tools, _, _ = _tools(implementation_runner=implementation_runner)
    design_payload = {
        "summary": "形成新工具方案。",
        "design": "## 新工具\n读取所需数据，计算结果并返回。",
    }

    saved = asyncio.run(
        tools["save_finance_artifact"].handler(
            {"artifact_type": "design", "payload": design_payload}
        )
    )
    viewed = asyncio.run(tools["read_finance_asset"].handler({"asset_type": "design"}))
    implemented = asyncio.run(
        tools["implement_dynamic_tool"].handler({"instruction": "按刚保存的设计实现"})
    )

    assert saved.get("isError") is not True
    assert "读取所需数据" in viewed["content"][0]["text"]
    assert implemented.get("isError") is not True
    assert "读取所需数据" in calls[0]["state"]["design_contract"]["document"]


def test_finance_cc_implementation_does_not_report_success_without_an_implementation() -> None:
    service = FinanceCcSystemTools(
        custom_tool_store=FakeStore(),
        custom_tool_runtime=FakeDynamicRuntime(),
        finance_runtime=FakeFinanceRuntime(),
        finance_catalog=FakeFinanceCatalog(),
        implementation_runner=lambda **_: {
            "message": "当前设计稿为空，请先形成设计。",
            "state": {},
        },
    )
    tools, _, tracker = service.build_tools(
        owner_ids=["owner-1"],
        tool_context={"custom_tool_state": {}},
    )

    result = asyncio.run(
        {item.name: item for item in tools}["implement_dynamic_tool"].handler(
            {"instruction": "继续实现"}
        )
    )

    assert result.get("isError") is True
    assert tracker["implementation_runs"][0]["ok"] is False


def test_finance_cc_execution_results_are_compact_and_loadable_by_conversation(tmp_path) -> None:
    class TableFinanceRuntime:
        def execute_request(self, *, request):
            rows = [{"code": f"00000{index}.SZ", "score": index} for index in range(1, 6)]
            return {
                "protocol": "finance_data_tool.v1",
                "request": request,
                "result": {
                    "name": "r1",
                    "api": "stock.quote",
                    "columns": ["code", "score"],
                    "data": {"rows": rows, "row_count": len(rows)},
                },
            }

    result_store = SessionVariableStoreService(data_root=tmp_path / "data")
    service = FinanceCcSystemTools(
        custom_tool_store=FakeStore(),
        custom_tool_runtime=FakeDynamicRuntime(),
        finance_runtime=TableFinanceRuntime(),
        finance_catalog=FakeFinanceCatalog(),
        result_store=result_store,
    )
    tools, _, tracker = service.build_tools(
        owner_ids=["owner-1"],
        tool_context={"_agent_runtime_scope": "owner-a/thread-7", "custom_tool_state": {}},
    )
    tools = {item.name: item for item in tools}

    query = asyncio.run(tools["finance_query"].handler({"request": "r1 = stock.quote(...)"}))
    payload = query["content"][0]["text"]
    assert '"row_count": 5' in payload
    assert "000005.SZ" not in payload
    result_ref = tracker["result_refs"][0]["result_ref"]

    loaded = asyncio.run(
        tools["load_result"].handler({"result_ref": result_ref, "offset": 3, "limit": 2})
    )
    assert "000004.SZ" in loaded["content"][0]["text"]
    assert "000005.SZ" in loaded["content"][0]["text"]

    other_tools, _, _ = service.build_tools(
        owner_ids=["owner-1"],
        tool_context={"_agent_runtime_scope": "owner-a/thread-8", "custom_tool_state": {}},
    )
    denied = asyncio.run(
        {item.name: item for item in other_tools}["load_result"].handler(
            {"result_ref": result_ref}
        )
    )
    assert denied.get("isError") is True


def test_finance_cc_saved_implementation_is_success_even_when_smoke_test_failed() -> None:
    def implementation_runner(**kwargs):
        return {
            "message": "实现已保存，样例运行失败。",
            "state": {"tool_name": "demo_tool", "implementation_revision": 2},
            "coding_status": "implemented",
            "implementation_review": {
                "status": "complete",
                "conclusion": "实现与设计一致，保留真实运行错误。",
                "verified": ["公开输入输出与设计一致"],
                "unresolved": ["样例数据不足"],
            },
            "test_result": {
                "execution_ok": False,
                "contract_ok": False,
                "summary": "0 / 1 项技术运行成功",
                "error": "sample failed",
            },
            "tool": {"manifest": {"tool_name": "demo_tool", "current_revision": 2}},
        }

    tools, _, tracker = _tools(implementation_runner=implementation_runner)
    result = asyncio.run(
        tools["implement_dynamic_tool"].handler({"instruction": "实现并保留真实验证结果"})
    )

    assert result.get("isError") is not True
    assert tracker["implementation_runs"][0]["ok"] is True
    assert '"execution_ok": false' in result["content"][0]["text"]
    assert '"tool_name": "demo_tool"' in result["content"][0]["text"]


def test_finance_cc_has_no_tool_capability_after_codex_implementation() -> None:
    def implementation_runner(**kwargs):
        return {
            "message": "实现、验证和静态检查已完成。",
            "state": {"tool_name": "demo_tool", "implementation_revision": 3},
            "coding_status": "implemented",
            "implementation_explanation": {
                "summary": "按设计实现筛选流程。",
                "core_flow": ["查询", "计算", "输出"],
                "key_modules": ["run"],
            },
            "implementation_review": {
                "conclusion": "matches",
                "requirement_alignment": ["目标一致"],
                "design_alignment": ["流程一致"],
                "deviations": [],
            },
            "test_result": {"execution_ok": True, "contract_ok": True},
            "tool": {"manifest": {"tool_name": "demo_tool", "current_revision": 3}},
        }

    tools, _, tracker = _tools(implementation_runner=implementation_runner)
    implemented = asyncio.run(
        tools["implement_dynamic_tool"].handler({"instruction": "实现当前设计"})
    )
    assert implemented.get("isError") is not True
    assert "implementation_review" in implemented["content"][0]["text"]
    assert "implementation_explanation" in implemented["content"][0]["text"]

    blocked_calls = [
        asyncio.run(tools["implement_dynamic_tool"].handler({"instruction": "再检查一次"})),
        asyncio.run(tools["read_finance_asset"].handler({"asset_type": "code"})),
        asyncio.run(tools["run_dynamic_tool"].handler({"tool_name": "demo_tool", "arguments": {}})),
        asyncio.run(tools["finance_query"].handler({"request": "r1 = stock.quote(...)"})),
    ]

    assert all(item.get("isError") is True for item in blocked_calls)
    assert all("implementation_turn_complete" in item["content"][0]["text"] for item in blocked_calls)
    assert len(tracker["implementation_runs"]) == 1
    assert not tracker["dynamic_runs"]

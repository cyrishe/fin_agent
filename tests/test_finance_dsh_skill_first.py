from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from src.scenarios.financial_qa.dsh_mcp_server import FinanceDshMcpBridge
from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService


def _context() -> dict:
    return {
        "allowed_finance_skills": ["equity-report-analysis"],
        "_finance_skill_catalog_prompt": "equity-report-analysis: 个股研报证据解读",
        "_finance_skill_catalog_revision": "skills-v1",
        "_finance_explicit_skill_ids": ["equity-report-analysis"],
        "_finance_explicit_skill_prompt": "已加载：依据来源区分事实、机构预测与推断。",
        "_finance_skill_snapshot": {
            "revision": "skills-v1",
            "skills": {
                "equity-report-analysis": {
                    "description": "个股研报证据解读",
                    "method": "依据来源区分事实、机构预测与推断。",
                    "content_hash": "method-v1",
                    "references": {
                        "references/consensus.md": {
                            "content": "LAZY_REFERENCE_CONTENT",
                            "content_hash": "reference-v1",
                        }
                    },
                }
            },
        },
    }


def test_skill_progress_uses_successful_loads_not_generic_zero_row_fallback(tmp_path):
    notifications = []
    class Harness:
        def __init__(self, **kwargs):
            self.env = kwargs["env"]

        def run(self, prompt, *, session_id, on_notification):
            def notify(event):
                on_notification(SimpleNamespace(method="session.event", payload={"event": event}))
            for call_id, name, payload in [
                ("s1", "read_finance_skill", {"skills": [{"skill_id": "equity-report-analysis", "method": "正文"}]}),
                ("s2", "read_finance_skill", {"skills": []}),
                ("s3", "read_finance_skill", {"error": "unavailable"}),
                ("ref", "read_finance_skill_reference", {"content": "专业参考"}),
                ("identity", "resolve_security", {"results": []}),
            ]:
                notify({"type": "tool/call", "data": {"callId": call_id, "name": "mcp__finance__" + name, "arguments": {"skill_ids": ["equity-report-analysis"]}}})
                notify({"type": "tool/result", "data": {"message": {"source": {"callId": call_id}, "content": [{"type": "tool-result", "content": [{"type": "text", "text": json.dumps(payload)}]}]}}})
            return SimpleNamespace(final_response="有来源的回答", finish_reason="completed", events=[])

        def close(self):
            pass

    service = FinanceDeepSeekHarnessSessionService(enabled=True, system_tools=_SystemTools(), harness_factory=Harness,
        root_dir=tmp_path / "runtime", log_path=tmp_path / "trace.jsonl", worker_count=1)
    context = _context()
    context["_finance_explicit_skill_ids"] = []
    context["_finance_explicit_skill_prompt"] = ""
    context["_finance_skill_snapshot"]["skills"]["equity-report-analysis"]["display_name"] = "研报分析"
    try:
        service.run_turn(thread_id=1, owner_id="test", user_text="分析", context=context, event_sink=notifications.append)
    finally:
        service.close()
    loaded = [event for event in notifications if event["metadata"].get("skill_id")]
    assert len(loaded) == 1
    assert loaded[0]["metadata"]["display_name"] == "研报分析"
    assert loaded[0]["metadata"]["status"] == "completed"
    assert not any("0 条必要明细" in event["content"] for event in notifications)
    assert any(event["metadata"].get("progress_id") == "s3" and event["metadata"]["status"] == "error" for event in notifications)


class _Runtime:
    runtime_scope = "test-runtime"

    def begin_turn(self, *, owner_ids, tool_context):
        self.tool_context = tool_context
        return {"calls": [], "result_refs": []}

    def current_context_prompt(self):
        return ""


class _SystemTools:
    finance_catalog = SimpleNamespace(catalog_revision=lambda: "catalog-v1")

    @staticmethod
    def create_runtime():
        return _Runtime()

    def build_tools(self, *, owner_ids, tool_context, runtime):
        tracker = runtime.begin_turn(owner_ids=owner_ids, tool_context=tool_context)
        tracker["finance_catalog_revision"] = "catalog-v1"

        async def read_method(args):
            snapshot = runtime.tool_context.get("_finance_skill_snapshot") or {}
            method = (snapshot.get("skills") or {}).get(args.get("skill_id"))
            payload = {"method": method} if method else {"error": "unavailable"}
            return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        return [
            SimpleNamespace(
                name=name,
                description=name,
                input_schema={"type": "object"},
                handler=read_method,
            )
            for name in (
                "read_finance_catalog", "finance_query", "load_finance_result",
                "read_finance_skill", "read_finance_skill_reference", "run_backtest",
            )
        ], [], tracker


def test_dsh_prompt_discloses_catalog_and_selected_method_but_keeps_references_lazy(tmp_path):
    service = FinanceDeepSeekHarnessSessionService(
        enabled=False, system_tools=_SystemTools(), root_dir=tmp_path, worker_count=1,
    )
    context = _context()
    prompt = service._prompt("机构如何评价？", runtime_context=context, working_set="")
    assert context["_finance_skill_catalog_prompt"] in prompt
    assert context["_finance_explicit_skill_prompt"] in prompt
    assert "LAZY_REFERENCE_CONTENT" not in prompt
    assert "先用 read_finance_skill 读取这些 Skill" not in prompt
    assert "平台权限与本轮用户目标保持不变" in prompt
    assert "选择合适的方法与数据" in service.system_prompt
    assert "按实际缺口补充方法或数据" in service.system_prompt
    assert "方法选择" in service.system_prompt
    for name in ("read_finance_catalog", "finance_query", "load_finance_result", "resolve_security"):
        assert name not in service.system_prompt

    context["_finance_data_only"] = True
    context["_finance_research_mode_prompt"] = "不应进入仅数据模式的分析指令"
    data_prompt = service._prompt("取得预测值", runtime_context=context, working_set="")
    assert "仅取数" in data_prompt
    assert context["_finance_explicit_skill_prompt"] in data_prompt
    assert context["_finance_research_mode_prompt"] not in data_prompt
    assert "finance_query" not in data_prompt


def test_dsh_mcp_reads_current_turn_snapshot_after_worker_reuse(tmp_path):
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps({
        "revision": "turn-1", "owner_ids": ["owner-1"], "tool_context": _context(),
    }))
    bridge = FinanceDshMcpBridge(
        context_path=context_path, trace_path=tmp_path / "trace.json", system_tools=_SystemTools(),
    )
    assert {tool.name for tool in bridge.list_tools()} == {
        "read_finance_catalog", "finance_query", "load_finance_result",
        "read_finance_skill", "read_finance_skill_reference",
    }
    result = asyncio.run(bridge.call_tool("read_finance_skill", {"skill_id": "equity-report-analysis"}))
    assert result["method"]["content_hash"] == "method-v1"
    context_path.write_text(json.dumps({
        "revision": "turn-2", "owner_ids": ["owner-2"],
        "tool_context": {"_finance_skill_snapshot": {"revision": "skills-v2", "skills": {}}},
    }))
    result = asyncio.run(bridge.call_tool("read_finance_skill", {"skill_id": "equity-report-analysis"}))
    assert result == {"error": "unavailable"}
    assert bridge._context_revision == "turn-2"


def test_real_skill_bridge_keeps_unselected_method_and_references_out_of_trace(tmp_path):
    context_path = tmp_path / "context.json"
    trace_path = tmp_path / "trace.json"
    context = _context()
    context["_finance_skill_snapshot"]["skills"]["unselected-skill"] = {
        "description": "另一个可用方法", "method": "UNSELECTED_METHOD_BODY", "content_hash": "other-v1",
    }
    context["allowed_finance_skills"].append("unselected-skill")
    context_path.write_text(json.dumps({
        "revision": "real-turn-1", "owner_ids": ["owner-1"], "tool_context": context,
    }))
    bridge = FinanceDshMcpBridge(context_path=context_path, trace_path=trace_path)
    names = {tool.name for tool in bridge.list_tools()}
    assert {"read_finance_skill", "read_finance_skill_reference"} <= names
    before = trace_path.read_text()
    assert "UNSELECTED_METHOD_BODY" not in before
    assert "LAZY_REFERENCE_CONTENT" not in before
    # Explicit methods are already loaded by the trusted turn snapshot.
    reference = asyncio.run(bridge.call_tool("read_finance_skill_reference", {
        "skill_id": "equity-report-analysis", "reference": "references/consensus.md",
    }))
    assert reference["content"] == "LAZY_REFERENCE_CONTENT"
    assert "UNSELECTED_METHOD_BODY" not in trace_path.read_text()
    context_path.write_text(json.dumps({
        "revision": "real-turn-2", "owner_ids": ["owner-2"],
        "tool_context": {
            "allowed_finance_skills": [],
            "_finance_skill_snapshot": {"revision": "skills-v2", "skills": {}},
        },
    }))
    denied = asyncio.run(bridge.call_tool("read_finance_skill", {"skill_id": "equity-report-analysis"}))
    assert denied.get("error")
    assert json.loads(trace_path.read_text())["tracker"]["active_skill_ids"] == []


def test_dsh_host_forwards_frozen_skills_and_keeps_actual_skill_trace(tmp_path):
    observed = []

    class Harness:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run(self, prompt, *, session_id, on_notification):
            context = json.loads(Path(self.kwargs["env"]["FIN_AGENT_DSH_CONTEXT_PATH"]).read_text())
            observed.append((prompt, context))
            Path(self.kwargs["env"]["FIN_AGENT_DSH_TRACE_PATH"]).write_text(json.dumps({
                "revision": context["revision"],
                "tracker": {
                    "calls": [{"tool": "read_finance_skill", "skill_id": "equity-report-analysis"}],
                    "skill_entries": [{"skill_id": "equity-report-analysis", "content_hash": "method-v1"}],
                    "skill_results": ["已加载的专业方法"],
                },
            }))
            return SimpleNamespace(final_response="来源支持的解释", finish_reason="completed", events=[])

        def close(self):
            return None

    service = FinanceDeepSeekHarnessSessionService(
        enabled=True, system_tools=_SystemTools(), harness_factory=Harness,
        root_dir=tmp_path / "runtime", log_path=tmp_path / "trace.jsonl", worker_count=1,
    )
    try:
        context = _context()
        result = service.run_turn(thread_id=1, owner_id="owner-1", user_text="请解读", context=context)
        assert result["error"] == ""
        for key, value in context.items():
            assert observed[0][1]["tool_context"][key] == value
        assert "LAZY_REFERENCE_CONTENT" not in observed[0][0]
        assert result["skill_entries"] == [{"skill_id": "equity-report-analysis", "content_hash": "method-v1"}]
        assert result["skill_results"] == ["已加载的专业方法"]
        service.run_turn(thread_id=1, owner_id="owner-1", user_text="下一题", context={})
        assert "_finance_skill_snapshot" not in observed[1][1]["tool_context"]
        assert "equity-report-analysis" not in observed[1][0]
    finally:
        service.close()

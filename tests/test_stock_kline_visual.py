import asyncio
import base64
import hashlib
import json
from pathlib import Path

import pytest

from src.services import stock_kline_visual as visual
from src.services.request_usage_service import total_tokens, with_tool_model_usage
from src.services.session_variable_store_service import SessionVariableStoreService
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.scenarios.financial_qa.presentation import FinancialQaPresentationService
from src.scenarios.financial_qa.dsh_mcp_server import _EXPOSED_TOOLS
from test_stock_technical_history import bars, connection_factory


def test_basic_visual_is_independent_same_source_and_no_nested_synthesis(monkeypatch):
    monkeypatch.setenv("LLM_VISION_MODEL", "fixture-vision")
    seen = []
    def calls(records, **kwargs):
        def call(stage, prompt, images, structured):
            assert stage == "vlm_basic" and len(images) == 1 and not structured
            assert "秘密用户结论" not in prompt and "程序统计" not in prompt
            seen.append(hashlib.sha256(Path(images[0]).read_bytes()).hexdigest())
            result = {"stage": stage, "finish_reason": "stop", "response": "图中量价逐步上行"}
            records.append(result)
            return result
        return call
    result = visual.inspect_daily_chart(code="000001.SZ", question="秘密用户结论",
        as_of="2025-08-01", review_patterns=False,
        connection_factory=connection_factory(bars(), []), call_factory=calls)
    assert result["ok"] and len(result["charts"]) == 1
    chart = result["charts"][0]
    assert chart["sha256"] == seen[0]
    assert base64.b64decode(chart["url"].split(",")[1]).startswith(b"\x89PNG")
    assert chart["metadata"]["display_end"] == "2025-08-01"
    assert chart["metadata"]["display_bars"] == 10
    assert chart["metadata"]["panels"] == ["price", "volume"]
    assert result["provider_evidence"]["data_as_of"] == "2025-08-01"
    assert "图中量价" in result["data"] and "未运行形态扫描" in result["data"]


def test_missing_configuration_does_not_claim_vision_or_read_market(monkeypatch):
    monkeypatch.delenv("LLM_VISION_MODEL", raising=False)
    result = visual.inspect_daily_chart(code="000001.SZ",
        connection_factory=lambda: pytest.fail("must not query without visual configuration"))
    assert result["ok"] is False and "未执行看图" in result["error"]


@pytest.mark.parametrize("params", [
    {"code": "file:///etc/passwd"}, {"code": "000001"},
    {"code": "000001.SZ", "window": 61}, {"code": "000001.SZ", "window": True},
    {"code": "000001.SZ", "review_patterns": "true"},
    {"code": "000001.SZ", "as_of": "not-a-date"},
])
def test_invalid_machine_inputs_rejected_before_io(params):
    with pytest.raises((ValueError, TypeError)):
        visual.inspect_daily_chart(**params, connection_factory=lambda: pytest.fail("unexpected IO"))


def test_visual_failure_preserves_calculated_facts(monkeypatch):
    monkeypatch.setenv("LLM_VISION_MODEL", "fixture-vision")
    def calls(records, **kwargs):
        def call(*args):
            result = {"finish_reason": "error", "response": ""}
            records.append(result)
            return result
        return call
    result = visual.inspect_daily_chart(code="000001.SZ", as_of="2025-08-01", review_patterns=False,
        connection_factory=connection_factory(bars(), []), call_factory=calls)
    assert result["ok"] and "基础视觉解读未完成" in result["data"]
    assert "最近两日" in result["data"] and result["llm_calls"]


def test_chat_tool_registered_owned_text_complete_images_not_in_model_context(tmp_path, monkeypatch):
    name = "stock_kline_visual_analysis"
    assert name in _EXPOSED_TOOLS
    store = SessionVariableStoreService(data_root=tmp_path)
    system = FinanceDataQueryCcTools(result_store=store)
    calls = [{"stage": "vlm_basic", "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}]
    text = "量价背景。"*500
    monkeypatch.setattr(system.tool_adapter, "execute", lambda *args: {"ok": True, "data": text,
        "provider_evidence": {"code": "000001.SZ", "data_as_of": "2025-08-01"},
        "charts": [{"name": "current.png", "url": "data:image/png;base64,aGVsbG8=", "sha256": "a" * 64}],
        "llm_calls": calls})
    runtime = system.create_runtime()
    tools, _, tracker = system.build_tools(owner_ids=["a"], runtime=runtime,
        tool_context={"_agent_runtime_scope": "a/thread-1", "allowed_agent_tools": [name]})
    tool = next(item for item in tools if item.name == name)
    result = asyncio.run(tool.handler({"code": "000001.SZ"}))
    payload = json.loads(result["content"][0]["text"])
    assert payload["analysis"] == text and "data:image" not in json.dumps(payload)
    assert payload["figures"][0]["id"] == "fig_" + "a" * 16
    assert tracker["calls"][0]["llm_calls"] == calls
    loaded = store.load_data_ref(session_id="a/thread-1", data_ref=payload["result_ref"], limit=5000)
    assert "data:image" not in json.dumps(loaded) and loaded["text"] == text
    with pytest.raises(ValueError, match="current conversation"):
        store.load_data_ref(session_id="b/thread-2", data_ref=payload["result_ref"])
    blocks = FinancialQaPresentationService().build("结论", tracker["result_refs"])
    assert blocks[1]["semantic"] == "finance.kline_visual"
    assert blocks[1]["payload"]["resources"][0]["mime_type"] == "image/png"
    runtime.tool_context["allowed_agent_tools"] = []
    denied = json.loads(asyncio.run(tool.handler({"code": "000001.SZ"}))["content"][0]["text"])
    assert denied["ok"] is False


def test_auxiliary_usage_counts_cache_once_and_missing_is_unknown():
    primary = {"cumulative_context_tokens": 200, "completion_tokens": 20, "total_tokens": 70}
    calls = [{"llm_calls": [{"usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105,
        "prompt_tokens_details": {"cached_tokens": 70}}}]}]
    combined = with_tool_model_usage(primary, calls)
    assert total_tokens(combined) == 325
    assert primary == {"cumulative_context_tokens": 200, "completion_tokens": 20, "total_tokens": 70}
    calls[0]["llm_calls"].append({"finish_reason": "error"})
    incomplete = with_tool_model_usage(primary, calls)
    assert total_tokens(incomplete) is None
    assert incomplete["auxiliary_usage"]["reported_total_tokens"] == 105


def test_chat_usage_merge_does_not_hide_an_unreported_visual_call():
    from src.web.flask_app import _merge_llm_usage
    known = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    partial = {"total_tokens": 100, "accounting_total_tokens": None}
    assert total_tokens(_merge_llm_usage(known, partial)) is None
    assert total_tokens(_merge_llm_usage(known, {"accounting_total_tokens": 100})) == 112
    assert total_tokens(_merge_llm_usage(known, None)) == 12


def test_public_detail_exposes_visual_usage_without_prompts():
    from src.finance_api.service import FinanceApiGateway
    usage = {"accounting_total_tokens": 325, "auxiliary_usage": {"call_count": 1, "complete": True}}
    calls = [{"tool": "stock_kline_visual_analysis", "llm_calls": [{
        "stage": "vlm_basic", "model": "vision", "duration_ms": 25,
        "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105}}]}]
    detail = FinanceApiGateway._detail({"llm_usage": usage}, {"tool_calls": calls})
    assert detail["usage"]["total_tokens"] == 325
    assert detail["usage"]["auxiliary_usage"]["call_count"] == 1
    assert detail["tool_calls"][0]["llm_calls"][0]["duration_ms"] == 25

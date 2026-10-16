import asyncio
import json
from pathlib import Path

import pytest

from src.services.stock_news_index_service import read_stock_news_index, sina_stock_index_url
from src.tools.general_search_tool import run
from src.scenarios.financial_qa.dsh_mcp_server import FinanceDshMcpBridge


def test_index_candidates_preserve_dates_and_skip_navigation(monkeypatch):
    monkeypatch.setattr("src.services.stock_news_index_service.fetch_finance_html", lambda url: '''
        <a href="https://finance.sina.com.cn/nav">导航</a><div class="datelist"><ul>
        2026-09-13 15:24 <a href="https://finance.sina.com.cn/old">行业变化</a><br/>
        2026-09-14 10:00 <a href="https://cj.sina.cn/new">涨价影响</a><br/>
        2026-09-14 10:01 <a href="http://127.0.0.1/private">外链</a>
        </ul></div>''')
    result = run({"query": "涨价影响", "stock_code": "300408.SZ", "limit": 1})
    assert result["ok"] and result["total"] == 2
    assert result["data"][0]["title"] == "涨价影响"
    assert result["data"][0]["publish_time"] == "2026-09-14 10:00"
    assert "content_markdown" not in result["data"][0]
    assert result["index_url"].endswith("sz300408.phtml")
    from jsonschema import validate
    validate(result, json.loads(Path("src/tools/schemas/general_search.schema.json").read_text()))
    assert sina_stock_index_url("sz300408") == result["index_url"]
    assert read_stock_news_index("300408.SZ", start_time="2026-09-14")["total"] == 1
    assert not run({"query": "新闻", "stock_code": "../../etc/passwd"})["ok"]


def test_selected_read_only_fetches_selected_urls_and_marks_failures(monkeypatch):
    calls = []
    def fetch(url):
        calls.append(url)
        return "# 正文标题\n\n发布日期：2026-09-14 10:00\n\n正文证据" if url.endswith("good") else ""
    monkeypatch.setattr("src.tools.general_search_tool.fetch_article_markdown", fetch)
    result = run({"query": "事件影响", "urls": ["https://finance.sina.com.cn/good", "https://finance.sina.com.cn/bad"]})
    assert result["ok"] and len(calls) == 2
    assert result["data"][0]["publish_time"] == "2026-09-14 10:00"
    assert result["data"][1]["content_markdown"] == ""
    assert "未取得" in result["data"][1]["snippet"]


@pytest.mark.parametrize("authorized", [False, True])
def test_dsh_search_exposure_and_selected_read_respect_authorization(tmp_path, monkeypatch, authorized):
    path = tmp_path / "context.json"
    path.write_text(json.dumps({"revision": "test", "owner_ids": ["test"],
        "tool_context": {"_agent_runtime_scope": "news-test", "allowed_agent_tools": ["general_search"] if authorized else []}}))
    bridge = FinanceDshMcpBridge(context_path=path, trace_path=tmp_path / "trace.json")
    assert ("general_search" in {item.name for item in bridge.list_tools()}) == authorized
    if authorized:
        monkeypatch.setattr("src.tools.general_search_tool.fetch_article_markdown", lambda url: "# 新闻\n\n已读取正文证据。")
        result = asyncio.run(bridge.call_tool("general_search", {"query": "新闻", "urls": ["https://finance.sina.com.cn/article"]}))
        assert "error" not in result
        trace = json.loads((tmp_path / "trace.json").read_text())
        assert trace["tracker"]["calls"][-1]["tool"] == "general_search"
    path.write_text(json.dumps({"revision": "next", "owner_ids": ["test"],
        "tool_context": {"_agent_runtime_scope": "news-test", "allowed_agent_tools": [] if authorized else ["general_search"]}}))
    assert ("general_search" in {item.name for item in bridge.list_tools()}) != authorized

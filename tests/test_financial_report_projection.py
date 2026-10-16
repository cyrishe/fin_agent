import asyncio

from src.finance_api.models import FinanceTaskRequest
from src.finance_api.service import FinanceApiGateway
from src.scenarios.financial_qa.presentation import FinancialQaPresentationService
from src.scenarios.financial_qa.report import build_report, public_report


FIGURE_ID = "fig_" + "a" * 16
REFS = [{"provider_evidence": {"code": "000001.SZ", "data_as_of": "2026-09-29"},
         "charts": [{"name": "current.png", "sha256": "a" * 64, "url": "data:image/png;base64,aGVsbG8="}]}]
ANSWER = f"# 个股分析\n\n核心结论。\n\n## 估值 {{#valuation}}\n| 项目 | 数值 |\n|---|---|\n| PE | 10 |\n\n## 技术面 {{#technical}}\n均线下方整理。\n\n![近期量价](finance-figure:{FIGURE_ID})"


def test_one_answer_is_projected_without_business_rewriting():
    markdown, report = build_report(ANSWER, REFS)
    assert "{#" not in markdown
    assert report["title"] == "个股分析"
    assert report["introduction"] == "核心结论。"
    assert [s["id"] for s in report["sections"]] == ["valuation", "technical"]
    assert "| PE | 10 |" in report["sections"][0]["content"]
    assert report["sections"][1]["figure_ids"] == [FIGURE_ID]
    assert report["figures"][0]["as_of"] == "2026-09-29"
    block = FinancialQaPresentationService().build(ANSWER, REFS)[0]
    assert block["payload"]["report"] == report
    assert block["content"] == markdown


def test_legacy_and_compatible_headings_do_not_block_answer():
    markdown, report = build_report("一个简短回答。", [])
    assert report["introduction"] == markdown == "一个简短回答。"
    assert report["sections"] == []
    markdown, report = build_report("## 一 {#same}\n证据\n## 二 {#same}\n更多\n## 三\n```md\n## 示例标题\n```", [])
    assert [s["id"] for s in report["sections"]] == ["same", "same-2", "section-3"]
    assert "## 示例标题" in report["sections"][-1]["content"]
    markdown, report = build_report("## 形态{#patterns}\n**内包线 {#detail}**\n### 背景 {#background}\n```md\n## 示例 {#literal}\n```", [])
    assert report["sections"][0]["id"] == "patterns"
    assert "**内包线**" in markdown and "### 背景\n" in markdown
    assert "{#literal}" in report["sections"][0]["content"]


def test_unowned_figures_and_non_png_urls_are_not_delivered():
    refs = [{"charts": [{"sha256": "b" * 64, "url": "file:///etc/passwd"},
                        {"sha256": "c" * 64, "url": "https://untrusted.test/a.png"}]}]
    markdown, report = build_report("![不存在](finance-figure:foreign)", refs)
    assert markdown == "不存在" and report["figures"] == []
    assert "image_url" not in public_report(build_report(ANSWER, REFS)[1])["figures"][0]


def test_mcp_and_rest_gateway_report_is_optional_images_opt_in_data_unchanged():
    markdown, report = build_report(ANSWER, REFS)
    class Engine:
        def answer(self, **kwargs):
            return {"summary": markdown, "report": report, "financial_qa": {}}
    gateway = FinanceApiGateway(engine=Engine())
    async def run():
        plain = await gateway.execute(FinanceTaskRequest(query="分析个股"), principal_id="a")
        rich = await gateway.execute(FinanceTaskRequest(query="分析个股", include_images=True), principal_id="a")
        data = await gateway.execute(FinanceTaskRequest(query="行情", response_mode="data", include_images=True), principal_id="a")
        assert plain.report["sections"] == report["sections"]
        assert "image_url" not in plain.report["figures"][0]
        assert rich.report["figures"][0]["image_url"].startswith("data:image/png;")
        assert data.summary is None and "report" not in data.model_dump()
        assert plain.summary == rich.summary == markdown
    asyncio.run(run())


def test_authenticated_mcp_transport_delivers_sections_and_opt_in_images(monkeypatch):
    from fastapi.testclient import TestClient
    from src.finance_api.app import create_app
    from src.finance_api.auth import FinanceApiKeyAuth
    monkeypatch.setenv("FINANCE_API_ALLOWED_HOSTS", "testserver")
    markdown, report = build_report(ANSWER, REFS)
    class Engine:
        def answer(self, **kwargs):
            return {"summary": markdown, "report": report, "financial_qa": {}}
    gateway = FinanceApiGateway(engine=Engine())
    key = "local-report-contract-test-key"
    with TestClient(create_app(auth=FinanceApiKeyAuth({"test": key}), gateway=gateway)) as client:
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream"}
        for include_images in (False, True):
            response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "finance_task", "arguments": {"query": "分析个股", "include_images": include_images}}})
            result = response.json()["result"]
            assert result["isError"] is False
            delivered = result["structuredContent"]["report"]
            assert delivered["sections"][1]["id"] == "technical"
            assert ("image_url" in delivered["figures"][0]) == include_images
        denied = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert denied.status_code == 401

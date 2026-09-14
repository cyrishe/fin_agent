from __future__ import annotations

from dataclasses import replace

from src.experiments.staged_data_protocol.phase2 import news_provider
from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.models import ApiCall
from src.services.search_gateway_service import (
    BraveWebSearchProvider,
    ElasticsearchSearchProvider,
    SearchGatewayConfig,
    SearchGatewayService,
)
from src.services.site_browser_search_provider import SiteBrowserSearchProvider
from src.tools.general_search_tool import run as run_general_search


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, *, json, timeout):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.response


def _config(provider="elasticsearch"):
    return SearchGatewayConfig(
        provider=provider,
        coverage="internal_news",
        elasticsearch_url="http://search.invalid",
        elasticsearch_index="articles",
        elasticsearch_timeout_seconds=3,
    )


def test_brave_key_supports_local_env_alias(monkeypatch):
    monkeypatch.delenv("FIN_AGENT_SEARCH_BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("BRAVE_API_KEY", "local-test-key")
    assert SearchGatewayConfig.from_env().brave_api_key == "local-test-key"
    monkeypatch.setenv("FIN_AGENT_SEARCH_BRAVE_API_KEY", "canonical-test-key")
    assert SearchGatewayConfig.from_env().brave_api_key == "canonical-test-key"


def test_elasticsearch_provider_normalizes_results_and_filters():
    session = _Session(
        _Response(
            {
                "hits": {
                    "total": {"value": 1},
                    "hits": [
                        {
                            "_id": "doc-1",
                            "_score": 8.5,
                            "_source": {
                                "article_id": "article-1",
                                "title": "贵州茅台发布经营数据",
                                "summary": "经营保持稳定。",
                                "url": "https://example.test/1",
                                "source_id": "example",
                                "category": "finance",
                                "published_at": "2026-08-03T10:00:00+08:00",
                            },
                        }
                    ],
                }
            }
        )
    )
    provider = ElasticsearchSearchProvider(
        url="http://search.invalid",
        index="articles",
        timeout_seconds=3,
        session=session,
    )

    result = provider.search(
        query="贵州茅台",
        limit=10,
        start_time="2026-08-01",
        category_scope=["finance"],
        sort="date_desc",
    )

    assert result["status"] == "ok"
    assert result["total"] == 1
    assert result["items"][0]["document_id"] == "article-1"
    assert result["items"][0]["source"] == "example"
    body = session.calls[0]["json"]
    assert {"range": {"published_at": {"gte": "2026-08-01"}}} in body["query"]["bool"]["filter"]
    assert body["sort"] == [{"published_at": {"order": "desc", "missing": "_last"}}]


def test_elasticsearch_provider_distinguishes_zero_results_from_failure():
    empty = ElasticsearchSearchProvider(
        url="http://search.invalid",
        index="articles",
        session=_Session(_Response({"hits": {"total": {"value": 0}, "hits": []}})),
    ).search(query="没有结果", limit=5)
    failed = ElasticsearchSearchProvider(
        url="http://search.invalid",
        index="articles",
        session=_Session(_Response({}, status_code=502)),
    ).search(query="服务失败", limit=5)

    assert empty == {
        "status": "ok",
        "provider": "elasticsearch",
        "items": [],
        "count": 0,
        "total": 0,
        "reason": "",
    }
    assert failed["status"] == "provider_error"
    assert failed["items"] == []
    assert "status=502" in failed["reason"]


def test_public_opinion_profile_uses_nested_stock_identity_and_pub_time():
    session = _Session(_Response({"hits": {"total": {"value": 0}, "hits": []}}))
    provider = ElasticsearchSearchProvider(
        url="http://search.invalid",
        index="article_news_*",
        profile="public_opinion",
        session=session,
    )

    provider.search(
        query="贵州茅台",
        limit=10,
        start_time="2026-08-01",
        end_time="2026-08-04",
        sort="date_desc",
        entity={"code": "600519.SH", "name": "贵州茅台"},
    )

    body = session.calls[0]["json"]
    filters = body["query"]["bool"]["filter"]
    assert {
        "nested": {
            "path": "related_cp_score",
            "query": {"term": {"related_cp_score.code": "600519.SH"}},
        }
    } in filters
    assert {"range": {"pub_time": {"gte": "2026-08-01", "lt": "2026-08-05"}}} in filters
    assert body["sort"] == [{"pub_time": {"order": "desc", "missing": "_last"}}]


def test_gateway_provider_is_selected_by_config_without_changing_call_contract():
    class _GoogleLikeProvider:
        name = "google"

        def search(self, **kwargs):
            return {"status": "ok", "provider": self.name, "items": [], "count": 0, "total": 0, "reason": ""}

    gateway = SearchGatewayService(
        config=replace(_config(), provider="google", coverage="web"),
        providers={"google": _GoogleLikeProvider()},
    )
    result = gateway.search(query="same contract", limit=3)

    assert result["status"] == "ok"
    assert result["provider"] == "google"
    assert result["coverage"] == "web"


def test_brave_web_provider_uses_seed_domains_and_keeps_only_matching_sources():
    class _WebSession:
        def __init__(self):
            self.calls = []

        def get(self, url, *, params, headers, timeout):
            self.calls.append((url, params, headers, timeout))
            return _Response({"web": {"results": [
                {"title": "基金公告", "url": "https://www.sse.com.cn/disclosure/1", "description": "公告摘要", "page_age": "2026-08-03T10:00:00"},
                {"title": "无关页面", "url": "https://example.com/2", "description": "其他"},
            ]}})

    session = _WebSession()
    provider = BraveWebSearchProvider(api_key="test-key", seed_domains=["sse.com.cn"], session=session)
    result = provider.search(query="基金公告", limit=5, start_time="2026-01-01", end_time="2026-09-14")
    assert result["status"] == "ok"
    assert [item["source"] for item in result["items"]] == ["www.sse.com.cn"]
    url, params, headers, _ = session.calls[0]
    assert url.endswith("/res/v1/web/search")
    assert params == {
        "q": "基金公告 (site:sse.com.cn)", "count": 5,
        "country": "CN", "search_lang": "zh-hans", "ui_lang": "zh-CN",
        "freshness": "2026-01-01to2026-09-14",
    }
    assert headers["X-Subscription-Token"] == "test-key"
    assert provider.search(query="基金公告", limit=5, source_scope=["cninfo.com.cn"])["items"] == []
    assert provider.search(query="基金公告", limit=5, source_scope=["https://example.com"])["status"] == "invalid_request"
    assert len(session.calls) == 1


def test_brave_provider_without_key_fails_closed():
    provider = BraveWebSearchProvider(api_key="")
    assert provider.search(query="基金公告", limit=5)["status"] == "provider_error"
    gateway = SearchGatewayService(config=replace(_config(), provider="brave"))
    result = gateway.search(query="基金公告")
    assert result["coverage"] == "public_web"
    assert result["status"] == "provider_error"


def test_brave_finance_news_scope_expands_to_curated_financial_media():
    class _WebSession:
        def __init__(self):
            self.params = None

        def get(self, url, *, params, headers, timeout):
            self.params = params
            return _Response({"web": {"results": [
                {"title": "宏观新闻", "url": "https://finance.sina.com.cn/a", "description": "摘要"},
                {"title": "无关新闻", "url": "https://example.com/a", "description": "摘要"},
            ]}})

    session = _WebSession()
    provider = BraveWebSearchProvider(api_key="test-key", session=session)
    result = provider.search(query="宏观政策", limit=2, source_scope=["finance_news"])
    assert "site:finance.sina.com.cn" in session.params["q"]
    assert "site:cnstock.com" in session.params["q"]
    assert "site:stcn.com" in session.params["q"]
    assert len(result["items"]) == 1


def test_brave_date_desc_uses_article_publication_not_page_modification(monkeypatch):
    class _WebSession:
        def get(self, url, *, params, headers, timeout):
            assert params["count"] == 20
            assert params["freshness"] == "2026-09-01to2026-09-14"
            return _Response({"web": {"results": [
                {"title": "三环集团旧闻被修改", "url": "https://www.stcn.com/article/detail/old.html", "page_age": "2026-09-14T12:00:00"},
                {"title": "三环集团较新文章", "url": "https://www.stcn.com/article/detail/newer.html", "page_age": "2026-09-13T11:00:00"},
                {"title": "三环集团最新文章", "url": "https://finance.sina.com.cn/news/2026-09-14/doc-newest.shtml", "page_age": "2026-09-14T08:00:00"},
                {"title": "三环集团站点首页", "url": "https://www.stcn.com/", "page_age": "2026-09-14T13:00:00"},
                {"title": "无关的新文章", "url": "https://www.stcn.com/article/detail/unrelated.html", "page_age": "2026-09-14T14:00:00"},
            ]}})

    published = {
        "old.html": "2026-05-21 09:43",
        "newer.html": "2026-09-13 11:00",
        "doc-newest.shtml": "2026-09-14T10:03:00+08:00",
    }
    monkeypatch.setattr(
        "src.services.search_gateway_service._article_publish_time",
        lambda url: next((value for suffix, value in published.items() if url.endswith(suffix)), ""),
    )
    provider = BraveWebSearchProvider(api_key="test-key", session=_WebSession())
    result = provider.search(
        query="三环集团近期上涨原因", limit=5, sort="date_desc",
        source_scope=["finance_news"], start_time="2026-09-01", end_time="2026-09-14",
    )
    assert [item["title"] for item in result["items"]] == ["三环集团最新文章", "三环集团较新文章"]
    assert result["items"][0]["publish_time"] == "2026-09-14T10:03:00+08:00"


def test_brave_unrestricted_search_sorts_page_time_without_claiming_publish_time(monkeypatch):
    class _WebSession:
        def get(self, url, *, params, headers, timeout):
            return _Response({"web": {"results": [
                {"title": "三环集团旧报道", "url": "https://www.stcn.com/article/detail/1.html", "page_age": "2026-09-13T10:00:00"},
                {"title": "三环集团今日异动", "url": "https://example.com/news/2", "page_age": "2026-09-14T09:00:00"},
                {"title": "三环集团另一篇报道", "url": "https://example.com/news/3", "page_age": "2026-09-14T12:00:00"},
            ]}})

    monkeypatch.setattr(
        "src.services.search_gateway_service._article_publish_time",
        lambda url: "2026-09-13 10:00" if url.endswith("1.html") else "",
    )
    result = BraveWebSearchProvider(api_key="test-key", session=_WebSession()).search(
        query="三环集团近期上涨原因", limit=5, sort="date_desc",
    )
    assert [item["title"] for item in result["items"]] == ["三环集团另一篇报道", "三环集团今日异动", "三环集团旧报道"]
    assert result["items"][0]["publish_time"] == ""


def test_brave_tls_fallback_keeps_key_out_of_process_arguments(monkeypatch):
    import requests

    class _FailingSession:
        def get(self, *args, **kwargs):
            raise requests.exceptions.SSLError("handshake failed")

    seen = {}

    def _curl(args, **kwargs):
        seen["args"] = args
        seen["input"] = kwargs["input"]
        return type("Result", (), {"returncode": 0, "stdout": '{"web":{"results":[]}}'})()

    monkeypatch.setattr("src.services.search_gateway_service.subprocess.run", _curl)
    provider = BraveWebSearchProvider(api_key="secret-test-key", session=_FailingSession())
    assert provider.search(query="三环集团", limit=5)["status"] == "ok"
    assert "secret-test-key" not in " ".join(seen["args"])
    assert "secret-test-key" in seen["input"]


def test_brave_english_query_keeps_default_locale():
    class _WebSession:
        def __init__(self):
            self.params = None

        def get(self, url, *, params, headers, timeout):
            self.params = params
            return _Response({"web": {"results": []}})

    session = _WebSession()
    BraveWebSearchProvider(api_key="test-key", session=session).search(query="US Treasury yields", limit=3)
    assert "country" not in session.params
    assert "search_lang" not in session.params


def test_keyless_site_browser_provider_filters_scope_and_dates(monkeypatch):
    class _Crawler:
        last_search_errors = {}

        def __init__(self, *, sites, **kwargs):
            self.sites = sites

        def search(self, query, **kwargs):
            assert query == "三环集团"
            return {site.name: [
                "https://finance.sina.com.cn/stock/2026-09-14/doc-a.shtml"
                if site.name == "sina_finance_search"
                else "https://stcn.com/article/detail/123.html"
            ] for site in self.sites}

    monkeypatch.setattr(
        "src.services.site_browser_search_provider.fetch_article_markdown",
        lambda url: (
            "# 三环集团上涨\n\n发布日期：2026-09-14T09:00:00+08:00\n\n最新报道。"
            if "sina" in url else "# 三环集团业绩\n\n发布日期：2026-08-01\n\n过往报道。"
        ),
    )
    provider = SiteBrowserSearchProvider(crawler_factory=_Crawler)
    result = provider.search(query="三环集团", limit=5, start_time="2026-09-01", source_scope=["finance_news"])
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["items"][0]["title"] == "三环集团上涨"
    assert provider.search(query="三环集团", limit=5, source_scope=["cnstock.com"])["status"] == "invalid_request"


def test_site_browser_gateway_reports_curated_coverage_without_api_key():
    class _Provider:
        name = "site_browser"

        def search(self, **kwargs):
            return {"status": "ok", "items": [], "count": 0, "total": 0, "reason": ""}

    gateway = SearchGatewayService(
        config=replace(_config(), provider="site_browser"),
        providers={"site_browser": _Provider()},
    )
    assert gateway.search(query="三环集团")["coverage"] == "curated_public_web"


def test_general_search_only_reads_two_articles_when_requested(monkeypatch):
    class _Gateway:
        def search(self, **kwargs):
            assert kwargs["sort"] == "date_desc"
            return {
                "status": "ok", "provider": "brave", "coverage": "public_web",
                "query": kwargs["query"], "total": 3,
                "items": [
                    {"document_id": str(i), "title": f"标题{i}", "url": f"https://www.stcn.com/{i}",
                     "source": "stcn.com", "publish_time": "", "snippet": "摘要", "category": "web", "score": 0.0}
                    for i in range(3)
                ],
            }

    fetched = []
    monkeypatch.setattr("src.tools.general_search_tool.SearchGatewayService", _Gateway)
    monkeypatch.setattr(
        "src.tools.general_search_tool.fetch_article_markdown",
        lambda url: fetched.append(url) or "# 正文\n\n段落",
    )
    brief = run_general_search({"query": "宏观政策"})
    assert not fetched
    assert all("content_markdown" not in item for item in brief["data"])
    full = run_general_search({"query": "宏观政策", "include_content": True})
    assert len(fetched) == 2
    assert [item.get("content_markdown") for item in full["data"]] == ["# 正文\n\n段落", "# 正文\n\n段落", None]


def test_legacy_stock_news_provider_preserves_identity(monkeypatch):
    class _Gateway:
        def search(self, **kwargs):
            assert kwargs["query"] == "贵州茅台 600519.SH"
            assert kwargs["start_time"] == "2026-07-01"
            assert kwargs["entity"] == {"code": "600519.SH", "name": "贵州茅台"}
            return {
                "status": "ok",
                "provider": "elasticsearch",
                "coverage": "internal_news",
                "total": 1,
                "items": [
                    {
                        "document_id": "a1",
                        "title": "贵州茅台经营更新",
                        "url": "https://example.test/a1",
                        "source": "example",
                        "publish_time": "2026-08-03",
                        "snippet": "摘要",
                        "category": "finance",
                        "score": 2.0,
                    }
                ],
            }

    monkeypatch.setattr(news_provider, "SearchGatewayService", _Gateway)
    result = news_provider.execute_stock_news_api(
        args={
            "filter": "code = 600519.SH and publish_time >= 2026-07-01",
            "order": "publish_time desc",
            "limit": 10,
        },
        outputs=["code", "name", "publish_time", "source", "title", "url"],
    )

    assert result["status"] == "ok"
    assert result["coverage"] == "internal_news"
    assert result["rows"] == [
        {
            "code": "600519.SH",
            "name": "贵州茅台",
            "publish_time": "2026-08-03",
            "source": "example",
            "title": "贵州茅台经营更新",
            "url": "https://example.test/a1",
        }
    ]


def test_stock_news_is_not_a_public_finance_catalog_api():
    call = ApiCall(
        result_id="r1",
        api="stock.news",
        args={"filter": "code = 600519.SH", "limit": 10},
        outputs=["title", "url"],
        raw="",
    )

    result = execute_api_call(call)

    assert result.data["status"] == "prepared"


def test_stock_news_requires_a_search_subject():
    result = news_provider.execute_stock_news_api(
        args={"filter": "publish_time >= 2026-07-01"},
        outputs=["title", "url"],
    )
    assert result["status"] == "invalid_request"
    assert result["rows"] == []


def test_general_search_tool_uses_gateway_envelope(monkeypatch):
    class _Gateway:
        def search(self, **kwargs):
            return {
                "status": "ok",
                "provider": "elasticsearch",
                "coverage": "internal_news",
                "query": kwargs["query"],
                "items": [],
                "total": 0,
            }

    monkeypatch.setattr("src.tools.general_search_tool.SearchGatewayService", _Gateway)
    result = run_general_search({"query": "白酒政策", "limit": 10})
    assert result["ok"] is True
    assert result["coverage"] == "internal_news"
    assert result["data"] == []

from __future__ import annotations

from dataclasses import replace

from src.experiments.staged_data_protocol.phase2 import news_provider
from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.models import ApiCall
from src.services.search_gateway_service import (
    ElasticsearchSearchProvider,
    SearchGatewayConfig,
    SearchGatewayService,
)
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


def test_stock_news_runs_through_finance_api_and_preserves_identity(monkeypatch):
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
    call = ApiCall(
        result_id="r1",
        api="stock.news",
        args={
            "filter": "code = 600519.SH and publish_time >= 2026-07-01",
            "order": "publish_time desc",
            "limit": 10,
        },
        outputs=["code", "name", "publish_time", "source", "title", "url"],
        raw="",
    )
    result = execute_api_call(call)

    assert result.data["status"] == "ok"
    assert result.data["coverage"] == "internal_news"
    assert result.data["rows"] == [
        {
            "code": "600519.SH",
            "name": "贵州茅台",
            "publish_time": "2026-08-03",
            "source": "example",
            "title": "贵州茅台经营更新",
            "url": "https://example.test/a1",
        }
    ]


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

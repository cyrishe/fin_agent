from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Iterable, Mapping, Optional, Protocol, Sequence

import requests


DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _bounded_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_SEARCH_LIMIT
    return min(MAX_SEARCH_LIMIT, max(1, parsed))


def _string_list(values: Optional[Iterable[Any]]) -> list[str]:
    return [_trim(item) for item in (values or []) if _trim(item)]


@dataclass(frozen=True)
class SearchGatewayConfig:
    provider: str
    coverage: str
    elasticsearch_url: str
    elasticsearch_index: str
    elasticsearch_timeout_seconds: float
    elasticsearch_profile: str = "personal_news"
    elasticsearch_username: str = ""
    elasticsearch_password: str = ""

    @classmethod
    def from_env(cls) -> "SearchGatewayConfig":
        provider = _trim(
            os.environ.get("FIN_AGENT_SEARCH_PROVIDER")
            or os.environ.get("PERSONAL_NEWS_SEARCH_BACKEND")
            or "elasticsearch"
        ).lower()
        try:
            timeout = float(
                os.environ.get("FIN_AGENT_SEARCH_ES_TIMEOUT_SECONDS")
                or os.environ.get("PERSONAL_NEWS_ES_TIMEOUT_SECONDS")
                or 8
            )
        except (TypeError, ValueError):
            timeout = 8.0
        return cls(
            provider=provider,
            coverage=_trim(os.environ.get("FIN_AGENT_SEARCH_COVERAGE") or "internal_news"),
            elasticsearch_url=_trim(
                os.environ.get("FIN_AGENT_SEARCH_ES_URL")
                or os.environ.get("ELASTICSEARCH_URL")
            ).rstrip("/"),
            elasticsearch_index=_trim(
                os.environ.get("FIN_AGENT_SEARCH_ES_INDEX")
                or os.environ.get("PERSONAL_NEWS_ES_INDEX")
                or "personal_news_articles"
            ),
            elasticsearch_timeout_seconds=max(0.5, timeout),
            elasticsearch_profile=_trim(
                os.environ.get("FIN_AGENT_SEARCH_ES_PROFILE") or "personal_news"
            ).lower(),
            elasticsearch_username=_trim(os.environ.get("FIN_AGENT_SEARCH_ES_USERNAME")),
            elasticsearch_password=_trim(os.environ.get("FIN_AGENT_SEARCH_ES_PASSWORD")),
        )


class SearchProvider(Protocol):
    name: str

    def search(
        self,
        *,
        query: str,
        limit: int,
        start_time: str = "",
        end_time: str = "",
        category_scope: Optional[Sequence[str]] = None,
        source_scope: Optional[Sequence[str]] = None,
        sort: str = "relevance",
        entity: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]: ...


class ElasticsearchSearchProvider:
    """Internal-news ES adapter behind a provider-neutral search contract."""

    name = "elasticsearch"

    def __init__(
        self,
        *,
        url: str,
        index: str,
        timeout_seconds: float = 8.0,
        profile: str = "personal_news",
        username: str = "",
        password: str = "",
        session: Optional[requests.Session] = None,
    ) -> None:
        self.url = _trim(url).rstrip("/")
        self.index = _trim(index)
        self.timeout_seconds = max(0.5, float(timeout_seconds))
        self.profile = _trim(profile).lower() or "personal_news"
        self.session = session or requests.Session()
        if _trim(username):
            self.session.auth = (_trim(username), str(password or ""))

    def search(
        self,
        *,
        query: str,
        limit: int,
        start_time: str = "",
        end_time: str = "",
        category_scope: Optional[Sequence[str]] = None,
        source_scope: Optional[Sequence[str]] = None,
        sort: str = "relevance",
        entity: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        if not self.url or not self.index:
            return self._error("elasticsearch provider is not configured")

        fields = self._physical_fields()
        filters: list[dict[str, Any]] = []
        categories = _string_list(category_scope)
        sources = _string_list(source_scope)
        if categories:
            filters.append({"terms": {fields["category"]: categories}})
        if sources:
            filters.append({"terms": {fields["source"]: sources}})
        identity = dict(entity or {})
        stock_code = _trim(identity.get("code"))
        if stock_code and self.profile == "public_opinion":
            filters.append(
                {
                    "nested": {
                        "path": "related_cp_score",
                        "query": {"term": {"related_cp_score.code": stock_code}},
                    }
                }
            )
        date_range: Dict[str, str] = {}
        if _trim(start_time):
            date_range["gte"] = _trim(start_time)
        if _trim(end_time):
            end_value = _trim(end_time)
            exclusive_end = self._next_day(end_value)
            date_range["lt" if exclusive_end else "lte"] = exclusive_end or end_value
        if date_range:
            filters.append({"range": {fields["date"]: date_range}})

        body = {
            "size": _bounded_limit(limit),
            "track_total_hits": True,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": _trim(query),
                                "fields": fields["search"],
                            }
                        }
                    ],
                    "filter": filters,
                }
            },
            "sort": (
                [{fields["date"]: {"order": "asc" if sort == "date_asc" else "desc", "missing": "_last"}}]
                if sort in {"date_asc", "date_desc"}
                else [
                    {"_score": "desc"},
                    {fields["date"]: {"order": "desc", "missing": "_last"}},
                ]
            ),
        }
        try:
            response = self.session.post(
                f"{self.url}/{self.index}/_search",
                json=body,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None)
            suffix = f" (status={status_code})" if status_code else ""
            return self._error(f"elasticsearch request failed{suffix}")
        except (requests.RequestException, ValueError):
            return self._error("elasticsearch request failed")

        hits_block = payload.get("hits") if isinstance(payload, Mapping) else {}
        raw_hits = hits_block.get("hits") if isinstance(hits_block, Mapping) else []
        total_block = hits_block.get("total") if isinstance(hits_block, Mapping) else 0
        total = total_block.get("value", 0) if isinstance(total_block, Mapping) else total_block
        items = [self._normalize_hit(item, fields) for item in raw_hits or [] if isinstance(item, Mapping)]
        items = [item for item in items if item.get("title") or item.get("url")]
        return {
            "status": "ok",
            "provider": self.name,
            "items": items,
            "count": len(items),
            "total": int(total or 0),
            "reason": "",
        }

    @staticmethod
    def _normalize_hit(hit: Mapping[str, Any], fields: Mapping[str, Any]) -> Dict[str, Any]:
        source = hit.get("_source") if isinstance(hit.get("_source"), Mapping) else {}
        summary = _trim(source.get("summary"))
        if not summary:
            summary = _trim(source.get("content"))[:300]
        return {
            "document_id": _trim(source.get("article_id") or source.get("id") or hit.get("_id")),
            "title": _trim(source.get("title")),
            "url": _trim(source.get("url")),
            "source": _trim(source.get(str(fields["source"]))),
            "publish_time": _trim(source.get(str(fields["date"]))),
            "snippet": summary[:300],
            "category": _trim(source.get(str(fields["category"]))),
            "score": float(hit.get("_score") or 0.0),
        }

    def _physical_fields(self) -> Dict[str, Any]:
        if self.profile == "public_opinion":
            return {
                "date": "pub_time",
                "source": "source",
                "category": "article_type",
                "search": ["title^4", "summary^2", "content", "keyword^3"],
            }
        return {
            "date": "published_at",
            "source": "source_id",
            "category": "category",
            "search": ["title^4", "summary^2", "content", "keywords^3", "entities^2"],
        }

    @staticmethod
    def _next_day(value: str) -> str:
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return ""
        return (parsed + timedelta(days=1)).isoformat()

    def _error(self, reason: str) -> Dict[str, Any]:
        return {
            "status": "provider_error",
            "provider": self.name,
            "items": [],
            "count": 0,
            "total": 0,
            "reason": reason,
        }


class SearchGatewayService:
    """Stable search boundary; provider selection belongs to deployment config."""

    def __init__(
        self,
        *,
        config: Optional[SearchGatewayConfig] = None,
        providers: Optional[Mapping[str, SearchProvider]] = None,
    ) -> None:
        self.config = config or SearchGatewayConfig.from_env()
        configured_providers: Dict[str, SearchProvider] = {
            "elasticsearch": ElasticsearchSearchProvider(
                url=self.config.elasticsearch_url,
                index=self.config.elasticsearch_index,
                timeout_seconds=self.config.elasticsearch_timeout_seconds,
                profile=self.config.elasticsearch_profile,
                username=self.config.elasticsearch_username,
                password=self.config.elasticsearch_password,
            )
        }
        configured_providers.update(dict(providers or {}))
        self.providers = configured_providers

    def search(
        self,
        *,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        start_time: str = "",
        end_time: str = "",
        category_scope: Optional[Sequence[str]] = None,
        source_scope: Optional[Sequence[str]] = None,
        sort: str = "relevance",
        entity: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        normalized_query = _trim(query)
        if not normalized_query:
            return {
                "status": "invalid_request",
                "provider": self.config.provider,
                "coverage": self.config.coverage,
                "items": [],
                "count": 0,
                "total": 0,
                "reason": "query is required",
            }
        provider = self.providers.get(self.config.provider)
        if provider is None:
            return {
                "status": "provider_error",
                "provider": self.config.provider,
                "coverage": self.config.coverage,
                "items": [],
                "count": 0,
                "total": 0,
                "reason": f"search provider is not available: {self.config.provider}",
            }
        result = provider.search(
            query=normalized_query,
            limit=_bounded_limit(limit),
            start_time=_trim(start_time),
            end_time=_trim(end_time),
            category_scope=category_scope,
            source_scope=source_scope,
            sort=_trim(sort).lower() or "relevance",
            entity=dict(entity or {}),
        )
        return {
            **dict(result),
            "provider": _trim(result.get("provider")) or self.config.provider,
            "coverage": self.config.coverage,
            "query": normalized_query,
        }

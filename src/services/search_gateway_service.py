from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Protocol, Sequence
from urllib.parse import urlparse

import requests
from src.services.web_article_text_service import NEWS_DOMAINS, fetch_article_markdown


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
    brave_api_key: str = ""
    seed_domains: tuple[str, ...] = ()

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
            coverage=_trim(os.environ.get("FIN_AGENT_SEARCH_COVERAGE") or (
                "curated_public_web" if provider == "site_browser"
                else "public_web" if provider == "brave" else "internal_news"
            )),
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
            brave_api_key=_trim(
                os.environ.get("FIN_AGENT_SEARCH_BRAVE_API_KEY")
                or os.environ.get("BRAVE_API_KEY")
            ),
            seed_domains=tuple(
                domain for raw in os.environ.get("FIN_AGENT_SEARCH_SEED_DOMAINS", "").split(",")
                if (domain := _valid_domain(raw))
            ),
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


def _valid_domain(value: str) -> str:
    domain = _trim(value).lower()
    return domain if re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", domain) else ""


def _search_datetime(value: Any) -> Optional[datetime]:
    raw = _trim(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _article_publish_time(url: str) -> str:
    markdown = fetch_article_markdown(url)
    match = re.search(r"(?:^|\n)发布日期：([^\n]+)", markdown)
    if match and _search_datetime(match.group(1)):
        return match.group(1).strip()
    # Some articles expose the date in the URL even when the body cannot be read.
    match = re.search(r"/(20\d{2}-\d{2}-\d{2})/", url)
    return match.group(1) if match else ""


class BraveWebSearchProvider:
    """Public web results; source_scope limits a query to configured domains."""

    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, *, api_key: str, seed_domains: Sequence[str] = (), session: Optional[requests.Session] = None) -> None:
        self.api_key = api_key
        self.seed_domains = tuple(domain for value in seed_domains if (domain := _valid_domain(value)))
        self.session = session or requests.Session()

    def search(
        self, *, query: str, limit: int, start_time: str = "", end_time: str = "",
        category_scope: Optional[Sequence[str]] = None,
        source_scope: Optional[Sequence[str]] = None,
        sort: str = "relevance", entity: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        if not self.api_key:
            return self._error("brave provider is not configured")
        if source_scope and any(value != "finance_news" and not _valid_domain(value) for value in source_scope):
            return {
                "status": "invalid_request", "provider": self.name, "items": [],
                "count": 0, "total": 0, "reason": "source_scope must contain domains",
            }
        requested = tuple(dict.fromkeys(
            domain
            for value in source_scope or ()
            for domain in (NEWS_DOMAINS if value == "finance_news" else (_valid_domain(value),))
        ))
        if requested and self.seed_domains:
            domains = tuple(domain for domain in requested if domain in self.seed_domains)
            if not domains:
                return {"status": "ok", "provider": self.name, "items": [], "count": 0, "total": 0, "reason": ""}
        else:
            domains = requested or self.seed_domains
        search_query = query
        if domains:
            search_query = f"{query} (" + " OR ".join(f"site:{domain}" for domain in domains) + ")"
        newest_first = sort == "date_desc"
        params: Dict[str, Any] = {
            "q": search_query,
            "count": min(20, max(_bounded_limit(limit), 20 if newest_first else 0)),
        }
        if re.search(r"[\u4e00-\u9fff]", query):
            params.update({"country": "CN", "search_lang": "zh-hans", "ui_lang": "zh-CN"})
        if start_time and end_time:
            try:
                params["freshness"] = f"{date.fromisoformat(start_time).isoformat()}to{date.fromisoformat(end_time).isoformat()}"
            except ValueError:
                pass
        try:
            response = self.session.get(
                self.endpoint, params=params,
                headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.SSLError:
            # Some local TLS stacks fail the Brave handshake while curl works.
            # Pass the secret through stdin, never the process argument list.
            payload = self._curl_search(params)
            if payload is None:
                return self._error("brave request failed")
        except requests.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None)
            return self._error(f"brave request failed (status={status_code})" if status_code else "brave request failed")
        except (requests.RequestException, ValueError):
            return self._error("brave request failed")
        web = payload.get("web") if isinstance(payload, Mapping) else {}
        raw = web.get("results") if isinstance(web, Mapping) else []
        items = []
        page_times: Dict[str, Optional[datetime]] = {}
        for result in raw or []:
            if not isinstance(result, Mapping):
                continue
            url = _trim(result.get("url"))
            try:
                host = (urlparse(url).hostname or "").lower()
            except ValueError:
                continue
            if not url.startswith(("https://", "http://")) or (domains and not any(host == d or host.endswith("." + d) for d in domains)):
                continue
            page_times[url] = _search_datetime(result.get("page_age"))
            items.append({
                "document_id": url, "title": _trim(result.get("title")), "url": url,
                "source": host, "publish_time": "", "snippet": _trim(result.get("description"))[:300],
                "category": "web", "score": 0.0,
            })
        if newest_first or sort == "date_asc" or start_time or end_time:
            with ThreadPoolExecutor(max_workers=min(4, len(items) or 1)) as pool:
                published = list(pool.map(_article_publish_time, [item["url"] for item in items]))
            for item, value in zip(items, published):
                item["publish_time"] = value
            if "finance_news" in (source_scope or ()):
                items = [item for item in items if item["publish_time"]]
            start = _search_datetime(start_time) if start_time else None
            end = _search_datetime(end_time) if end_time else None
            if start or end:
                items = [
                    item for item in items
                    if (moment := _search_datetime(item["publish_time"]) or page_times.get(item["url"]))
                    and (not start or moment.date() >= start.date())
                    and (not end or moment.date() <= end.date())
                ]
            if sort in {"date_desc", "date_asc"}:
                floor = datetime.min.replace(tzinfo=timezone.utc)
                items.sort(
                    key=lambda item: (
                        _search_datetime(item["publish_time"]) or page_times.get(item["url"]) or floor,
                        bool(item["publish_time"]),
                    ),
                    reverse=sort == "date_desc",
                )
        return {"status": "ok", "provider": self.name, "items": items[:limit], "count": len(items[:limit]), "total": len(items), "reason": ""}

    def _curl_search(self, params: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        if any(char in self.api_key for char in "\r\n"):
            return None
        args = [
            "curl", "--silent", "--show-error", "--fail", "--max-time", "8",
            "--header", "@-", "-H", "Accept: application/json", "--get", self.endpoint,
        ]
        for name, value in params.items():
            args.extend(["--data-urlencode", f"{name}={value}"])
        try:
            completed = subprocess.run(
                args, input=f"X-Subscription-Token: {self.api_key}\n",
                text=True, capture_output=True, timeout=10, check=False,
            )
            if completed.returncode != 0:
                return None
            payload = json.loads(completed.stdout)
            return payload if isinstance(payload, dict) else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None

    def _error(self, reason: str) -> Dict[str, Any]:
        return {"status": "provider_error", "provider": self.name, "items": [], "count": 0, "total": 0, "reason": reason}


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
            ),
            "brave": BraveWebSearchProvider(
                api_key=self.config.brave_api_key,
                seed_domains=self.config.seed_domains,
            ),
        }
        if self.config.provider == "site_browser" and "site_browser" not in (providers or {}):
            from src.services.site_browser_search_provider import SiteBrowserSearchProvider

            configured_providers["site_browser"] = SiteBrowserSearchProvider(seed_domains=self.config.seed_domains)
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
        coverage = (
            "public_web" if self.config.provider == "brave"
            else "curated_public_web" if self.config.provider == "site_browser"
            else "internal_news" if self.config.provider == "elasticsearch"
            else self.config.coverage
        )
        if not normalized_query:
            return {
                "status": "invalid_request",
                "provider": self.config.provider,
                "coverage": coverage,
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
                "coverage": coverage,
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
            "coverage": coverage,
            "query": normalized_query,
        }

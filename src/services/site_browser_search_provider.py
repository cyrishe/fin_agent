"""Keyless search of a small set of public financial-news search pages."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

from src.crawler.site_news_crawler import SiteNewsCrawler, load_site_configs
from src.services.web_article_text_service import fetch_article_markdown


_SITE_DOMAINS = {
    "sina_finance_search": "finance.sina.com.cn",
    "stcn": "stcn.com",
}
_SITE_CONFIG_PATH = Path(__file__).resolve().parents[1] / "crawler" / "finance_news_sites.json"


def _matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _article_item(url: str) -> dict[str, Any] | None:
    markdown = fetch_article_markdown(url)
    if not markdown:
        return None
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    title = next((line[2:].strip() for line in lines if line.startswith("# ")), "")
    if not title:
        return None
    published = next((line.removeprefix("发布日期：").strip() for line in lines if line.startswith("发布日期：")), "")
    if not published:
        match = re.search(r"/(20\d{2}-\d{2}-\d{2})/", url)
        published = match.group(1) if match else ""
    snippet = next((line for line in lines if not line.startswith(("#", "发布日期："))), "")[:300]
    host = (urlparse(url).hostname or "").lower()
    return {
        "document_id": url,
        "title": title,
        "url": url,
        "source": host,
        "publish_time": published,
        "snippet": snippet,
        "category": "finance_news",
        "score": 0.0,
    }


class SiteBrowserSearchProvider:
    """Search public site pages with headless Chrome, then read bounded article text."""

    name = "site_browser"

    def __init__(self, *, seed_domains: Sequence[str] = (), crawler_factory=SiteNewsCrawler) -> None:
        self.seed_domains = tuple(str(domain).strip().lower() for domain in seed_domains if str(domain).strip())
        self.crawler_factory = crawler_factory

    def search(
        self, *, query: str, limit: int, start_time: str = "", end_time: str = "",
        category_scope: Optional[Sequence[str]] = None,
        source_scope: Optional[Sequence[str]] = None,
        sort: str = "relevance", entity: Optional[Mapping[str, str]] = None,
    ) -> dict[str, Any]:
        requested = {str(value).strip().lower() for value in source_scope or () if str(value).strip()}
        allowed = {"finance_news", *_SITE_DOMAINS.values()}
        if requested - allowed:
            return self._result("invalid_request", [], "source_scope is not supported by site_browser")
        sites = [
            site for site in load_site_configs(str(_SITE_CONFIG_PATH))
            if site.name in _SITE_DOMAINS
            and (not self.seed_domains or any(_matches_domain(_SITE_DOMAINS[site.name], domain) for domain in self.seed_domains))
            and (not requested or "finance_news" in requested or _SITE_DOMAINS[site.name] in requested)
        ]
        if not sites:
            return self._result("ok", [])
        crawler = self.crawler_factory(sites=sites, site_search_workers=len(sites), page_load_timeout=12, headless=True)
        try:
            found = crawler.search(query, max_results_per_site=min(6, max(1, limit)), keep_days_override=0)
        except Exception:
            return self._result("provider_error", [], "public site search failed")
        urls = list(dict.fromkeys(
            url for site in sites for url in found.get(site.name, [])
            if url.startswith("https://") and _matches_domain((urlparse(url).hostname or "").lower(), _SITE_DOMAINS[site.name])
        ))[:12]
        if not urls and crawler.last_search_errors:
            return self._result("provider_error", [], "public site search failed")
        with ThreadPoolExecutor(max_workers=min(4, len(urls) or 1)) as pool:
            items = [item for item in pool.map(_article_item, urls) if item]
        if start_time:
            items = [item for item in items if item["publish_time"] and item["publish_time"][:10] >= start_time[:10]]
        if end_time:
            items = [item for item in items if item["publish_time"] and item["publish_time"][:10] <= end_time[:10]]
        # The site search pages can mix modules; for news, newest-first is a
        # stable default when no explicit date order was requested.
        if sort in {"date_desc", "relevance"}:
            items.sort(key=lambda item: item["publish_time"], reverse=True)
        elif sort == "date_asc":
            items.sort(key=lambda item: item["publish_time"])
        return self._result("ok", items[:limit], total=len(items))

    def _result(self, status: str, items: list[dict[str, Any]], reason: str = "", *, total: int | None = None) -> dict[str, Any]:
        return {
            "status": status, "provider": self.name, "items": items,
            "count": len(items), "total": len(items) if total is None else total,
            "reason": reason,
        }

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.crawler.site_news_crawler import SiteNewsCrawler, StockCodeResolver, load_site_configs
from src.utils.mysql_utils import MySQLUtils, PAGE_TYPE_NEWS


DEFAULT_CONFIG_PATH = "src/crawler/finance_news_sites.json"
DEFAULT_STOCK_NAME_TSV = "stock_name.tsv"


class SimpleWebTool:
    """
    Reusable wrapper for simple web crawling tools.

    This layer intentionally stays generic:
    - site selection
    - crawler execution
    - item normalization
    - dedupe
    - optional page_crawled_history persistence

    Business tools can wrap this class and add query preprocessing or result postprocessing.
    """

    def __init__(
        self,
        config_path: str = DEFAULT_CONFIG_PATH,
        stock_name_tsv: str = DEFAULT_STOCK_NAME_TSV,
    ) -> None:
        self.config_path = config_path
        self.stock_name_tsv = stock_name_tsv
        self.resolver = StockCodeResolver(stock_name_tsv)

    def _build_crawler(
        self,
        site_names: Optional[List[str]] = None,
        workers: int = 3,
        site_search_workers: int = 3,
        per_site_workers: int = 1,
        fetch_mode_override: str = "",
    ) -> SiteNewsCrawler:
        sites = load_site_configs(self.config_path)
        selected_names = set(site_names or [site.name for site in sites if site.enabled])
        selected = [site for site in sites if site.enabled and site.name in selected_names]
        if fetch_mode_override:
            normalized = str(fetch_mode_override).strip().lower()
            for site in selected:
                site.fetch_mode = normalized
        return SiteNewsCrawler(
            sites=selected,
            code_resolver=self.resolver,
            workers=max(1, int(workers)),
            site_search_workers=max(1, int(site_search_workers)),
            per_site_workers=max(1, int(per_site_workers)),
            headless=True,
        )

    @staticmethod
    def _normalize_item(item: Dict[str, Any], query: str) -> Dict[str, Any]:
        url = str(item.get("url") or "").strip()
        content = str(item.get("content") or "").strip()
        return {
            "title": str(item.get("title") or "").strip(),
            "url": url,
            "site": str(item.get("site") or "").strip(),
            "publish_time": str(item.get("publish_time") or item.get("fetched_at") or "").strip(),
            "content": content,
            "content_html": str(item.get("content_html") or "").strip(),
            "snippet": content[:180],
            "search_key": str(item.get("search_key") or query or "").strip(),
            "parent_url": str(item.get("parent_url") or "").strip(),
            "depth": int(item.get("depth") or 0),
            "host": str(item.get("host") or urlparse(url).netloc).strip(),
        }

    @staticmethod
    def _dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for item in items:
            key = str(item.get("url") or "").strip() or f"{item.get('site')}::{item.get('title')}::{item.get('publish_time')}"
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _build_history_records(search_key: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for item in items:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            records.append(
                {
                    "url": url,
                    "host": str(item.get("host") or urlparse(url).netloc),
                    "search_key": str(search_key or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "content": str(item.get("content_html") or item.get("content") or "").strip(),
                    "page_type": PAGE_TYPE_NEWS,
                    "crawled_time": str(item.get("publish_time") or "").strip(),
                }
            )
        return records

    def search(
        self,
        query: str,
        site_names: Optional[List[str]] = None,
        max_results_per_site: int = 10,
        keep_days: int = 3,
        follow_depth: int = 0,
        persist: bool = False,
        dedupe: bool = True,
        fetch_mode_override: str = "",
        db: Optional[MySQLUtils] = None,
    ) -> Dict[str, Any]:
        crawler = self._build_crawler(
            site_names=site_names,
            fetch_mode_override=fetch_mode_override,
        )
        raw_items = crawler.crawl(
            keyword=str(query or "").strip(),
            max_follow_depth=max(0, int(follow_depth)),
            max_results_per_site=max(1, int(max_results_per_site)),
            keep_days_override=max(0, int(keep_days)),
        )
        items = [self._normalize_item(item, query=str(query or "").strip()) for item in raw_items]
        if dedupe:
            items = self._dedupe_items(items)
        history_records = self._build_history_records(str(query or "").strip(), items)

        persist_stats: Dict[str, int] = {"submitted": 0, "inserted": 0, "updated": 0, "affected": 0}
        owns_db = db is None
        if persist and history_records:
            db = db or MySQLUtils()
            try:
                persist_stats = db.insert_url_crawled_history_records(history_records)
            finally:
                if owns_db and db is not None:
                    db.close_db()

        return {
            "query": str(query or "").strip(),
            "sites": list(site_names or []),
            "items": items,
            "history_records": history_records,
            "persisted": bool(persist),
            "persist_stats": persist_stats,
            "stats": {
                "total": len(raw_items),
                "deduped": len(items),
                "persisted": int(persist_stats.get("affected", 0) or 0),
            },
        }


def search_simple_web(
    query: str,
    site_names: Optional[List[str]] = None,
    max_results_per_site: int = 10,
    keep_days: int = 3,
    follow_depth: int = 0,
    persist: bool = False,
    dedupe: bool = True,
    fetch_mode_override: str = "",
    db: Optional[MySQLUtils] = None,
) -> Dict[str, Any]:
    tool = SimpleWebTool()
    return tool.search(
        query=query,
        site_names=site_names,
        max_results_per_site=max_results_per_site,
        keep_days=keep_days,
        follow_depth=follow_depth,
        persist=persist,
        dedupe=dedupe,
        fetch_mode_override=fetch_mode_override,
        db=db,
    )

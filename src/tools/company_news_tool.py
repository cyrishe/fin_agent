from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import datetime as dt

from src.crawler.site_news_crawler import SiteNewsCrawler, StockCodeResolver, load_site_configs
from src.utils.mysql_utils import MySQLUtils, PAGE_TYPE_NEWS


DEFAULT_NEWS_SITES = ["jiuyangongshe_search", "10jqka_stockpage_news", "cnstock_search"]
DEFAULT_CONFIG_PATH = "src/crawler/finance_news_sites.json"
DEFAULT_STOCK_NAME_TSV = "stock_name.tsv"
TOOL_NAME = "financial_news_search"


class CompanyNewsTool:
    def __init__(
        self,
        config_path: str = DEFAULT_CONFIG_PATH,
        stock_name_tsv: str = DEFAULT_STOCK_NAME_TSV,
    ) -> None:
        self.config_path = config_path
        self.stock_name_tsv = stock_name_tsv
        self.resolver = StockCodeResolver(stock_name_tsv)
        self._code_to_name = self._build_code_to_name()

    def _build_code_to_name(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for name, code6 in self.resolver.name_to_code6.items():
            mapping[code6] = name
        return mapping

    def _normalize_stock_identity(self, value: str) -> Optional[Dict[str, str]]:
        raw = str(value or "").strip()
        if not raw:
            return None
        code6 = self.resolver.resolve_to_6digit(raw)
        if not code6:
            return None
        name = self._code_to_name.get(code6, "")
        exchange = ""
        for token, resolved_code in self.resolver.code_tokens_to_code6.items():
            if resolved_code != code6 or "." not in token:
                continue
            exchange = token.split(".", 1)[1]
            break
        if not exchange:
            exchange = "SH" if code6.startswith(("60", "68", "69", "90")) else "BJ" if code6.startswith(("43", "82", "83", "87", "88", "92")) else "SZ"
        return {
            "entity_type": "stock",
            "query": raw,
            "code": code6,
            "stk_code": f"{code6}.{exchange}",
            "name": name,
        }

    def _resolve_query(self, query: str, entity_type: str) -> Dict[str, str]:
        normalized_type = str(entity_type or "auto").strip().lower()
        if normalized_type in {"stock", "auto"}:
            stock_identity = self._normalize_stock_identity(query)
            if stock_identity:
                return stock_identity
            if normalized_type == "stock":
                raise ValueError(f"无法识别股票代码或名称: {query}")
        return {
            "entity_type": "concept" if normalized_type == "concept" else "keyword",
            "query": str(query or "").strip(),
            "code": "",
            "stk_code": "",
            "name": "",
        }

    def _build_crawler(
        self,
        site_names: Optional[List[str]] = None,
        workers: int = 3,
        site_search_workers: int = 3,
        per_site_workers: int = 1,
    ) -> SiteNewsCrawler:
        sites = load_site_configs(self.config_path)
        selected_names = set(site_names or DEFAULT_NEWS_SITES)
        selected = [site for site in sites if site.enabled and site.name in selected_names]
        return SiteNewsCrawler(
            sites=selected,
            code_resolver=self.resolver,
            workers=max(1, int(workers)),
            site_search_workers=max(1, int(site_search_workers)),
            per_site_workers=max(1, int(per_site_workers)),
            headless=True,
        )

    @staticmethod
    def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
        content = str(item.get("content") or "").strip()
        return {
            "title": str(item.get("title") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "site": str(item.get("site") or "").strip(),
            "publish_time": str(item.get("publish_time") or item.get("fetched_at") or "").strip(),
            "snippet": content[:180],
        }

    @staticmethod
    def _dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for item in items:
            key = str(item.get("url") or "").strip() or f"{item.get('site')}::{item.get('title')}"
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
                    "crawled_time": str(item.get("publish_time") or item.get("fetched_at") or "").strip(),
                }
            )
        return records

    @staticmethod
    def _history_row_to_item(row: Dict[str, Any]) -> Dict[str, Any]:
        content = str(row.get("content") or "").strip()
        return {
            "title": str(row.get("title") or "").strip(),
            "url": str(row.get("url") or "").strip(),
            "site": str(row.get("host") or "").strip(),
            "publish_time": str(row.get("crawled_time") or "").strip(),
            "snippet": content[:180],
        }

    def _load_cached_items(
        self,
        db: MySQLUtils,
        search_key: str,
        keep_days: int,
        limit: int,
    ) -> List[Dict[str, Any]]:
        since_time = (dt.datetime.now() - dt.timedelta(days=max(0, int(keep_days)))).strftime("%Y-%m-%d %H:%M:%S")
        rows = db.list_page_history(
            page_type=PAGE_TYPE_NEWS,
            search_key=str(search_key or "").strip(),
            since_time=since_time,
            limit=max(1, int(limit)),
        )
        return [self._history_row_to_item(row) for row in rows]

    def search(
        self,
        query: str,
        entity_type: str = "auto",
        max_results_per_site: int = 6,
        keep_days: int = 3,
        site_names: Optional[List[str]] = None,
        db: Optional[MySQLUtils] = None,
    ) -> Dict[str, Any]:
        resolved = self._resolve_query(query, entity_type)
        actual_query = resolved.get("name") or resolved.get("code") or resolved.get("query") or str(query or "").strip()
        owns_db = db is None
        db = db or MySQLUtils()
        try:
            cache_limit = max(1, int(max_results_per_site)) * max(1, len(site_names or DEFAULT_NEWS_SITES))
            normalized_items = self._load_cached_items(
                db=db,
                search_key=actual_query,
                keep_days=keep_days,
                limit=cache_limit,
            )

            raw_items: List[Dict[str, Any]] = []
            if not normalized_items:
                crawler = self._build_crawler(site_names=site_names)
                raw_items = crawler.crawl(
                    keyword=actual_query,
                    max_follow_depth=0,
                    max_results_per_site=max(1, int(max_results_per_site)),
                    keep_days_override=max(0, int(keep_days)),
                )
                normalized_items = [self._normalize_item(item) for item in raw_items]

            normalized_items = self._dedupe_items(normalized_items)
            history_records = self._build_history_records(actual_query, normalized_items)

            persist_stats: Dict[str, int] = {"submitted": 0, "inserted": 0, "updated": 0, "affected": 0}
            if history_records and raw_items:
                persist_stats = db.insert_url_crawled_history_records(history_records)

            return {
                "items": normalized_items,
            }
        finally:
            if owns_db and db is not None:
                db.close_db()


def search_company_news(
    query: str,
    entity_type: str = "auto",
    max_results_per_site: int = 6,
    keep_days: int = 3,
    site_names: Optional[List[str]] = None,
    db: Optional[MySQLUtils] = None,
) -> Dict[str, Any]:
    if not str(query or "").strip():
        raise ValueError("query 不能为空")
    tool = CompanyNewsTool()
    return tool.search(
        query=query,
        entity_type=entity_type,
        max_results_per_site=max_results_per_site,
        keep_days=keep_days,
        site_names=site_names,
        db=db,
    )


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    query = str(
        params.get("query")
        or params.get("keyword")
        or params.get("company")
        or params.get("name")
        or params.get("code")
        or ""
    ).strip()
    try:
        payload = search_company_news(
            query=query,
            entity_type=str(params.get("entity_type") or "auto").strip(),
            max_results_per_site=int(params.get("max_results_per_site", 6) or 6),
            keep_days=int(params.get("keep_days", 3) or 3),
            site_names=params.get("site_names"),
        )
        return {
            "tool": TOOL_NAME,
            "ok": True,
            "data": list(payload.get("items") or []),
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": TOOL_NAME,
            "ok": False,
            "data": {},
            "error": str(exc),
        }

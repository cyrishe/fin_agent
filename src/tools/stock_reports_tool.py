from typing import Any, Dict, List, Optional
from src.crawler.site_news_crawler import StockCodeResolver
from src.services.ths_report_service import THSReportService
from src.tools.fetch_ths_stock_reports import _build_db_records, _crawl_sina_report_items
from src.utils.mysql_utils import MySQLUtils, PAGE_TYPE_REPORT


DEFAULT_STOCK_NAME_TSV = "stock_name.tsv"
TOOL_NAME = "equity_research_search"


class StockReportsTool:
    def __init__(self, stock_name_tsv: str = DEFAULT_STOCK_NAME_TSV) -> None:
        self.stock_name_tsv = stock_name_tsv
        self.resolver = StockCodeResolver(stock_name_tsv)
        self._code_to_name = self._build_code_to_name()
        self._service = THSReportService()

    def _build_code_to_name(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for name, code6 in self.resolver.name_to_code6.items():
            mapping[code6] = name
        return mapping

    def _resolve_stock_identity(self, value: str) -> Dict[str, str]:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("code 不能为空")
        code6 = self.resolver.resolve_to_6digit(raw)
        if not code6:
            raise ValueError(f"无法识别股票代码或名称: {raw}")
        exchange = ""
        for token, resolved_code in self.resolver.code_tokens_to_code6.items():
            if resolved_code != code6 or "." not in token:
                continue
            exchange = token.split(".", 1)[1]
            break
        if not exchange:
            exchange = "SH" if code6.startswith(("60", "68", "69", "90")) else "BJ" if code6.startswith(("43", "82", "83", "87", "88", "92")) else "SZ"
        return {
            "query": raw,
            "code": code6,
            "stk_code": f"{code6}.{exchange}",
            "name": self._code_to_name.get(code6, ""),
        }

    @staticmethod
    def _normalize_report_item(item: Dict[str, Any], resolved: Dict[str, str]) -> Dict[str, Any]:
        content = str(item.get("content") or item.get("content_text") or "").strip()
        return {
            "title": str(item.get("title") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "site": "sina_stock_report",
            "publish_time": str(item.get("publish_date") or item.get("crawled_time") or "").strip(),
            "institution": str(item.get("institution") or "").strip(),
            "analyst": str(item.get("analyst") or "").strip(),
            "report_type": str(item.get("report_type") or "").strip(),
            "snippet": content[:180],
            "stock_code": resolved.get("code", ""),
            "stock_name": resolved.get("name", ""),
        }

    @staticmethod
    def _dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for item in items:
            key = str(item.get("url") or "").strip() or f"{item.get('title')}::{item.get('publish_time')}"
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _load_rows_from_db(
        self,
        db: MySQLUtils,
        code6: str,
        since_time: Optional[str],
        limit: int,
        target_urls: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        if target_urls:
            return db.list_page_history_by_urls(page_type=PAGE_TYPE_REPORT, urls=target_urls)
        return db.list_page_history(
            page_type=PAGE_TYPE_REPORT,
            search_key=code6,
            since_time=since_time,
            limit=limit,
        )

    def search(
        self,
        code: str,
        since_time: Optional[str] = None,
        limit: int = 20,
        refresh: bool = True,
        persist: bool = True,
        dedupe: bool = True,
        db: Optional[MySQLUtils] = None,
    ) -> Dict[str, Any]:
        resolved = self._resolve_stock_identity(code)
        normalized_limit = max(1, min(int(limit), 100))
        target_urls: List[str] = []
        history_records: List[Dict[str, Any]] = []
        persist_stats: Dict[str, int] = {"submitted": 0, "inserted": 0, "updated": 0, "affected": 0}

        if not refresh and not persist:
            crawl_result = _crawl_sina_report_items(
                code=resolved["code"],
                max_results=normalized_limit,
                timeout=20,
            )
            items = [self._normalize_report_item(item, resolved) for item in crawl_result["items"]]
            if dedupe:
                items = self._dedupe_items(items)
            history_records = _build_db_records(crawl_result["items"], resolved["code"])
            return {
                "items": items,
            }

        owns_db = db is None
        db = db or MySQLUtils()
        assert db is not None
        try:
            if refresh:
                crawl_result = _crawl_sina_report_items(
                    code=resolved["code"],
                    max_results=normalized_limit,
                    timeout=20,
                )
                crawled_items = crawl_result["items"]
                target_urls = [str(item.get("url") or "").strip() for item in crawled_items if str(item.get("url") or "").strip()]
                history_records = _build_db_records(crawled_items, resolved["code"])
                if persist and history_records:
                    new_urls = set(db.filter_new_urls(target_urls))
                    new_items = [item for item in crawled_items if str(item.get("url") or "").strip() in new_urls]
                    if new_items:
                        persist_stats = db.insert_url_crawled_history_records(_build_db_records(new_items, resolved["code"]))
            rows = self._load_rows_from_db(
                db=db,
                code6=resolved["code"],
                since_time=since_time,
                limit=normalized_limit,
                target_urls=target_urls,
            )
        finally:
            if owns_db and db is not None:
                db.close_db()

        items = [self._normalize_report_item(self._service._normalize_record(row), resolved) for row in rows]
        if dedupe:
            items = self._dedupe_items(items)
        if not history_records:
            history_records = _build_db_records(items, resolved["code"])
        return {
            "items": items,
        }


def get_stock_reports_snapshot_tool(
    code: str,
    since_time: Optional[str] = None,
    limit: int = 10,
    refresh: bool = True,
    persist: bool = True,
    dedupe: bool = True,
    db: Optional[MySQLUtils] = None,
) -> Dict[str, Any]:
    tool = StockReportsTool()
    return tool.search(
        code=code,
        since_time=since_time,
        limit=limit,
        refresh=refresh,
        persist=persist,
        dedupe=dedupe,
        db=db,
    )


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = get_stock_reports_snapshot_tool(
            code=str(params.get("company") or params.get("query") or params.get("code") or params.get("name") or "").strip(),
            since_time=params.get("since_time"),
            limit=int(params.get("limit", 10) or 10),
            refresh=bool(params.get("refresh", False)),
            persist=bool(params.get("persist", True)),
            dedupe=bool(params.get("dedupe", True)),
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

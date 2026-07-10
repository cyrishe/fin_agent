from typing import Any, Dict, List, Optional

from src.tools.fetch_ths_stock_reports import (
    _build_db_records,
    _crawl_sina_report_items,
    _extract_report_detail_fields,
)
from src.utils.mysql_utils import MySQLUtils, PAGE_TYPE_REPORT


class THSReportService:
    """Load crawled Sina stock report records from DB and normalize them."""

    @staticmethod
    def _normalize_record(row: Dict[str, Any]) -> Dict[str, Any]:
        content = str(row.get("content") or "")
        fields = _extract_report_detail_fields(content, fallback_title=str(row.get("title") or ""))
        return {
            "id": row.get("id"),
            "url": str(row.get("url") or ""),
            "host": str(row.get("host") or ""),
            "search_key": str(row.get("search_key") or ""),
            "title": fields["title"] or str(row.get("title") or ""),
            "publish_date": fields["publish_date"],
            "institution": fields["institution"],
            "analyst": fields["analyst"],
            "content_text": fields["content_text"] or content,
            "content": fields["plain_text"] or content,
            "crawled_time": row.get("crawled_time"),
            "page_type": row.get("page_type", PAGE_TYPE_REPORT),
        }

    def get_stock_report_snapshot(
        self,
        code: str,
        since_time: Optional[str] = None,
        limit: int = 20,
        db: Optional[MySQLUtils] = None,
        refresh: bool = True,
    ) -> Dict[str, Any]:
        normalized_limit = max(1, min(int(limit), 100))
        owns_db = db is None
        if owns_db:
            db = MySQLUtils()
        assert db is not None

        try:
            target_urls: List[str] = []
            if refresh:
                crawl_result = _crawl_sina_report_items(
                    code=str(code).strip(),
                    max_results=normalized_limit,
                    timeout=20,
                )
                items = crawl_result["items"]
                target_urls = [str(item.get("url") or "").strip() for item in items if str(item.get("url") or "").strip()]
                if target_urls:
                    new_urls = set(db.filter_new_urls(target_urls))
                    new_items = [item for item in items if str(item.get("url") or "").strip() in new_urls]
                    if new_items:
                        db.insert_url_crawled_history_records(_build_db_records(new_items, str(code).strip()))

            if target_urls:
                rows = db.list_page_history_by_urls(
                    page_type=PAGE_TYPE_REPORT,
                    urls=target_urls,
                )
            else:
                rows = db.list_page_history(
                    page_type=PAGE_TYPE_REPORT,
                    search_key=str(code).strip(),
                    since_time=since_time,
                    limit=normalized_limit,
                )
        finally:
            if owns_db:
                db.close_db()

        items = [self._normalize_record(row) for row in rows]
        return {
            "source": "sina_stock_reports_db",
            "stock_code": str(code).strip(),
            "since_time": since_time or "",
            "limit": normalized_limit,
            "count": len(items),
            "items": items,
        }

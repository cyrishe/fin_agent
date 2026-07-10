from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


DbFactory = Callable[[], Any]


class KingdomaiStockAnnouncementService:
    """Read-only stock announcement provider backed by kingdomai.kcrp_news_info."""

    def __init__(self, *, db_factory: Optional[DbFactory] = None) -> None:
        self.db_factory = db_factory or StockInfoDbUtils

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _date_to_text(value: Any) -> str:
        if value in (None, ""):
            return ""
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d")
        return str(value)

    @staticmethod
    def _bounded_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = default
        return max(min_value, min(max_value, parsed))

    @staticmethod
    def _bool(value: Any) -> bool:
        text = str(value or "").strip().lower()
        if text in {"1", "true", "y", "yes", "是"}:
            return True
        if text in {"0", "false", "n", "no", "否", ""}:
            return False
        return bool(value)

    def query(
        self,
        *,
        subject: str,
        start_date: str = "",
        end_date: str = "",
        keyword: str = "",
        limit: int = 20,
        include_text: bool = False,
        max_text_chars: int = 500,
    ) -> Dict[str, Any]:
        raw_subject = self._trim(subject)
        if not raw_subject:
            raise ValueError("subject is required")
        row_limit = self._bounded_int(limit, default=20, min_value=1, max_value=200)
        text_limit = self._bounded_int(max_text_chars, default=500, min_value=80, max_value=5000)

        db = self.db_factory()
        try:
            identity = self._resolve_stock_identity(db=db, subject=raw_subject)
            stock_code = self._trim(identity.get("stock_code"))
            if not stock_code:
                stock_code = self._normalize_stock_code(raw_subject)
            if not stock_code:
                raise ValueError(f"cannot resolve stock subject: {subject}")
            rows = self._query_announcements(
                db=db,
                stock_code=stock_code,
                start_date=self._trim(start_date),
                end_date=self._trim(end_date),
                keyword=self._trim(keyword),
                limit=row_limit,
            )
            items = [
                self._normalize_announcement(row, include_text=include_text, max_text_chars=text_limit)
                for row in rows
            ]
            return {
                "source": ["kcrp_news_info", "kcrp_stock_baseinfo"],
                "stock": raw_subject,
                "code": stock_code,
                "name": self._trim(identity.get("stock_name")),
                "subject": raw_subject,
                "stock_code": stock_code,
                "stock_name": self._trim(identity.get("stock_name")),
                "resolved_subject": self._normalize_identity(identity, stock_code=stock_code),
                "filters": {
                    "start_date": self._trim(start_date),
                    "end_date": self._trim(end_date),
                    "keyword": self._trim(keyword),
                    "include_text": bool(include_text),
                },
                "date_range": self._date_range(items),
                "row_count": len(items),
                "items": items,
                "coverage": {
                    "requested_limit": row_limit,
                    "returned_rows": len(items),
                    "latest_ann_date": items[0]["ann_date"] if items else "",
                    "identity_available": bool(identity),
                    "text_included": bool(include_text),
                },
            }
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def _normalize_stock_code(self, subject: str) -> str:
        value = self._trim(subject).upper()
        if not value:
            return ""
        if value.endswith((".SH", ".SZ", ".BJ")):
            return value
        if value.isdigit() and len(value) == 6:
            if value.startswith(("60", "68", "69", "90")):
                return f"{value}.SH"
            if value.startswith(("43", "82", "83", "87", "88", "92")):
                return f"{value}.BJ"
            return f"{value}.SZ"
        return ""

    def _resolve_stock_identity(self, *, db: Any, subject: str) -> Mapping[str, Any]:
        normalized_code = self._normalize_stock_code(subject)
        sql = """
            SELECT stk_code, stk_name, market, board, list_date, delist_date
            FROM kcrp_stock_baseinfo
            WHERE stk_code = %s
               OR stk_code = %s
               OR stk_name = %s
               OR stk_name LIKE %s
            ORDER BY
              CASE
                WHEN stk_code = %s THEN 0
                WHEN stk_code = %s THEN 1
                WHEN stk_name = %s THEN 2
                ELSE 9
              END,
              list_date DESC
            LIMIT 1
        """
        like_subject = f"%{subject}%"
        row = self._fetchone(
            db,
            sql,
            (
                subject,
                normalized_code,
                subject,
                like_subject,
                subject,
                normalized_code,
                subject,
            ),
        )
        if not row:
            return {}
        return {
            "stock_code": row.get("stk_code"),
            "stock_name": row.get("stk_name"),
            "market": row.get("market"),
            "board": row.get("board"),
            "list_date": row.get("list_date"),
            "delist_date": row.get("delist_date"),
        }

    def _query_announcements(
        self,
        *,
        db: Any,
        stock_code: str,
        start_date: str,
        end_date: str,
        keyword: str,
        limit: int,
    ) -> List[Mapping[str, Any]]:
        clauses = ["stk_code = %s"]
        params: List[Any] = [stock_code]
        if start_date:
            clauses.append("ann_date >= %s")
            params.append(start_date)
        if end_date:
            clauses.append("ann_date <= %s")
            params.append(end_date)
        if keyword:
            clauses.append("(news_title LIKE %s OR news_ftxt LIKE %s)")
            like_keyword = f"%{keyword}%"
            params.extend([like_keyword, like_keyword])
        params.append(limit)
        where_sql = " AND ".join(clauses)
        sql = f"""
            SELECT news_id, data_source, stk_code, ann_date, news_title, news_ftxt, news_annlink
            FROM kcrp_news_info
            WHERE {where_sql}
            ORDER BY ann_date DESC, news_id DESC
            LIMIT %s
        """
        return self._fetchall(db, sql, tuple(params))

    def _fetchone(self, db: Any, sql: str, params: tuple[Any, ...]) -> Mapping[str, Any]:
        conn = getattr(db, "conn", db)
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        return row if isinstance(row, Mapping) else {}

    def _fetchall(self, db: Any, sql: str, params: tuple[Any, ...]) -> List[Mapping[str, Any]]:
        conn = getattr(db, "conn", db)
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [row for row in rows if isinstance(row, Mapping)]

    def _normalize_identity(self, row: Mapping[str, Any], *, stock_code: str) -> Dict[str, Any]:
        return {
            "stock_code": self._trim(row.get("stock_code")) or stock_code,
            "stock_name": self._trim(row.get("stock_name")),
            "market": self._trim(row.get("market")),
            "board": self._trim(row.get("board")),
            "list_date": self._date_to_text(row.get("list_date")),
            "delist_date": self._date_to_text(row.get("delist_date")),
        }

    def _normalize_announcement(
        self,
        row: Mapping[str, Any],
        *,
        include_text: bool,
        max_text_chars: int,
    ) -> Dict[str, Any]:
        text = self._trim(row.get("news_ftxt"))
        item = {
            "news_id": self._trim(row.get("news_id")),
            "data_source": self._trim(row.get("data_source")),
            "stock_code": self._trim(row.get("stk_code")),
            "ann_date": self._date_to_text(row.get("ann_date")),
            "title": self._trim(row.get("news_title")),
            "url": self._trim(row.get("news_annlink")),
            "snippet": text[:180],
        }
        if include_text:
            item["text"] = text[:max_text_chars]
        return item

    def _date_range(self, items: List[Mapping[str, Any]]) -> Dict[str, str]:
        dates = [self._trim(item.get("ann_date")) for item in items if self._trim(item.get("ann_date"))]
        if not dates:
            return {"start": "", "end": ""}
        return {"start": min(dates), "end": max(dates)}

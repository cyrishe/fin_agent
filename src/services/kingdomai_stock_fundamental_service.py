from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


DbFactory = Callable[[], Any]


class KingdomaiStockFundamentalService:
    """Read-only stock profile provider backed by kingdomai base tables."""

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
    def _number(value: Any) -> float:
        try:
            return float(value or 0)
        except Exception:
            return 0.0

    def snapshot(self, *, subject: str, report_period: str = "") -> Dict[str, Any]:
        raw_subject = self._trim(subject)
        if not raw_subject:
            raise ValueError("subject is required")
        target_period = self._trim(report_period)

        db = self.db_factory()
        try:
            identity = db.resolve_stock_identity(raw_subject) if hasattr(db, "resolve_stock_identity") else None
            stk_code = self._trim((identity or {}).get("stk_code")) or raw_subject
            code6 = stk_code[:6]
            if not code6:
                raise ValueError(f"cannot resolve stock subject: {subject}")

            profile = self._query_profile(db=db, stk_code=stk_code, code6=code6)
            subject_name = self._trim(profile.get("stock_name")) or self._trim((identity or {}).get("stk_name"))
            return {
                "source": [
                    "kcrp_stock_baseinfo",
                    "kcrp_stock_company",
                ],
                "subject": raw_subject,
                "code": stk_code,
                "name": subject_name,
                "profile": profile,
                "coverage": {
                    "profile_available": bool(profile),
                },
            }
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def _query_profile(self, *, db: Any, stk_code: str, code6: str) -> Dict[str, Any]:
        sql = """
            SELECT b.stk_code, b.stk_name, b.eng_short_name, b.market, b.board,
                   b.list_date, b.delist_date,
                   c.comp_name, c.comp_name_eng, c.establishment_date, c.legal_repr,
                   c.general_manager, c.secretary_bd, c.act_holder, c.reg_capital,
                   c.reg_address, c.briefIntro_text, c.business_major,
                   c.province, c.city, c.office_address, c.email, c.website
            FROM kcrp_stock_baseinfo b
            LEFT JOIN kcrp_stock_company c
              ON c.stk_code = b.stk_code
            WHERE b.delist_date = '2999-12-31'
              AND (b.stk_code = %s OR LEFT(b.stk_code, 6) = %s)
            ORDER BY b.list_date DESC
            LIMIT 1
        """
        row = self._fetchone(db, sql, (stk_code, code6))
        if not row:
            return {}
        return {
            "stock_code": self._trim(row.get("stk_code")),
            "stock_name": self._trim(row.get("stk_name")),
            "eng_short_name": self._trim(row.get("eng_short_name")),
            "market": self._trim(row.get("market")),
            "board": self._trim(row.get("board")),
            "list_date": self._date_to_text(row.get("list_date")),
            "delist_date": self._date_to_text(row.get("delist_date")),
            "company_name": self._trim(row.get("comp_name")),
            "company_name_eng": self._trim(row.get("comp_name_eng")),
            "establishment_date": self._date_to_text(row.get("establishment_date")),
            "legal_representative": self._trim(row.get("legal_repr")),
            "general_manager": self._trim(row.get("general_manager")),
            "board_secretary": self._trim(row.get("secretary_bd")),
            "actual_controller": self._trim(row.get("act_holder")),
            "registered_capital": self._number(row.get("reg_capital")),
            "registered_address": self._trim(row.get("reg_address")),
            "business_major": self._trim(row.get("business_major")),
            "brief_intro": self._trim(row.get("briefIntro_text")),
            "province": self._trim(row.get("province")),
            "city": self._trim(row.get("city")),
            "office_address": self._trim(row.get("office_address")),
            "email": self._trim(row.get("email")),
            "website": self._trim(row.get("website")),
        }

    def _fetchone(self, db: Any, sql: str, params: tuple[Any, ...]) -> Mapping[str, Any]:
        conn = getattr(db, "conn", db)
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        return row if isinstance(row, Mapping) else {}

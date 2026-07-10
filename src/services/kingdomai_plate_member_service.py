from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


DbFactory = Callable[[], Any]


class KingdomaiPlateMemberService:
    """Read-only plate membership provider backed by kingdomai tables."""

    SORT_SQL = {
        "stock_code": "pm.stk_code ASC",
        "stock_rise_fall_rate": "COALESCE(sp.rise_fall_rate, 0) DESC, COALESCE(sp.amount, 0) DESC, pm.stk_code ASC",
        "stock_amount": "COALESCE(sp.amount, 0) DESC, COALESCE(sp.rise_fall_rate, 0) DESC, pm.stk_code ASC",
        "stock_main_net_inflow": "COALESCE(sm.main_net_buy_value, 0) DESC, COALESCE(sp.rise_fall_rate, 0) DESC, pm.stk_code ASC",
        "plate_rise_fall_rate": "COALESCE(pp.rise_fall_rate, 0) DESC, COALESCE(pp.amount, 0) DESC, p.plate_code ASC",
        "plate_amount": "COALESCE(pp.amount, 0) DESC, COALESCE(pp.rise_fall_rate, 0) DESC, p.plate_code ASC",
        "plate_main_net_inflow": "COALESCE(pmf.main_btm_net, 0) DESC, COALESCE(pp.rise_fall_rate, 0) DESC, p.plate_code ASC",
    }

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

    @staticmethod
    def _bounded_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = default
        return max(min_value, min(max_value, parsed))

    @classmethod
    def _normalize_sort_by(cls, value: Any) -> str:
        raw = cls._trim(value) or "stock_rise_fall_rate"
        aliases = {
            "pct_chg": "stock_rise_fall_rate",
            "change_pct": "stock_rise_fall_rate",
            "rise_fall_rate": "stock_rise_fall_rate",
            "涨幅": "stock_rise_fall_rate",
            "涨跌幅": "stock_rise_fall_rate",
            "amount": "stock_amount",
            "成交额": "stock_amount",
            "main_net_inflow": "stock_main_net_inflow",
            "主力净流入": "stock_main_net_inflow",
        }
        return aliases.get(raw, raw)

    def query(
        self,
        *,
        subject: str,
        subject_type: str = "auto",
        trade_date: str = "",
        limit: int = 50,
        sort_by: str = "stock_rise_fall_rate",
    ) -> Dict[str, Any]:
        raw_subject = self._trim(subject)
        if not raw_subject:
            raise ValueError("subject is required")
        normalized_type = self._trim(subject_type).lower() or "auto"
        if normalized_type not in {"auto", "plate", "stock"}:
            raise ValueError(f"unsupported subject_type: {subject_type}")
        normalized_sort = self._normalize_sort_by(sort_by)
        if normalized_sort not in self.SORT_SQL:
            raise ValueError(f"unsupported sort_by: {sort_by}")
        row_limit = self._bounded_int(limit, default=50, min_value=1, max_value=500)

        db = self.db_factory()
        try:
            mode, identity = self._resolve_identity(
                db=db,
                subject=raw_subject,
                subject_type=normalized_type,
            )
            if mode == "by_plate":
                rows = self._query_by_plate(
                    db=db,
                    plate_code=self._trim(identity.get("plate_code")),
                    trade_date=self._trim(trade_date),
                    limit=row_limit,
                    sort_by=normalized_sort,
                )
            else:
                rows = self._query_by_stock(
                    db=db,
                    stock_code=self._trim(identity.get("stock_code")),
                    trade_date=self._trim(trade_date),
                    limit=row_limit,
                    sort_by=normalized_sort,
                )
            items = [self._normalize_row(row) for row in rows]
            latest_trade_date = self._latest_trade_date(items)
            return {
                "source": [
                    "kcrp_yp_plate",
                    "kcrp_yp_plate_member",
                    "kcrp_stock_baseinfo",
                    "kcrp_stock_price",
                    "kcrp_stock_moneyflow",
                    "kcrp_yp_plate_price",
                    "kcrp_yp_plate_moneyflow",
                ],
                "subject": raw_subject,
                "subject_type": normalized_type,
                "mode": mode,
                "resolved_subject": self._normalize_identity(mode=mode, row=identity),
                "trade_date": latest_trade_date,
                "sort_by": normalized_sort,
                "row_count": len(items),
                "items": items,
                "coverage": {
                    "requested_limit": row_limit,
                    "returned_rows": len(items),
                    "latest_trade_date": latest_trade_date,
                    "identity_available": bool(identity),
                    "stock_market_rows": sum(1 for item in items if item["stock_trade_date"]),
                    "plate_market_rows": sum(1 for item in items if item["plate_trade_date"]),
                },
            }
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def _resolve_identity(
        self,
        *,
        db: Any,
        subject: str,
        subject_type: str,
    ) -> tuple[str, Mapping[str, Any]]:
        if subject_type == "plate":
            plate = self._resolve_plate(db=db, subject=subject)
            if not plate:
                raise ValueError(f"cannot resolve plate subject: {subject}")
            return "by_plate", plate
        if subject_type == "stock":
            stock = self._resolve_stock(db=db, subject=subject)
            if not stock:
                raise ValueError(f"cannot resolve stock subject: {subject}")
            return "by_stock", stock

        resolvers = (
            (self._resolve_stock, "by_stock"),
            (self._resolve_plate, "by_plate"),
        )
        if not self._looks_like_stock(subject):
            resolvers = tuple(reversed(resolvers))
        for resolver, mode in resolvers:
            identity = resolver(db=db, subject=subject)
            if identity:
                return mode, identity
        raise ValueError(f"cannot resolve plate or stock subject: {subject}")

    def _looks_like_stock(self, subject: str) -> bool:
        normalized = subject.upper()
        if normalized.endswith((".SH", ".SZ", ".BJ")):
            return True
        digits = "".join(ch for ch in normalized if ch.isdigit())
        return len(digits) == 6 and len(normalized) <= 9

    def _resolve_plate(self, *, db: Any, subject: str) -> Mapping[str, Any]:
        sql = """
            SELECT plate_code, plate_name, begin_date, end_date
            FROM kcrp_yp_plate
            WHERE plate_code = %s
               OR plate_name = %s
               OR plate_name LIKE %s
            ORDER BY
              CASE
                WHEN plate_code = %s THEN 0
                WHEN plate_name = %s THEN 1
                ELSE 9
              END,
              update_time DESC
            LIMIT 1
        """
        like_subject = f"%{subject}%"
        row = self._fetchone(db, sql, (subject, subject, like_subject, subject, subject))
        if not row:
            return {}
        return {
            "plate_code": row.get("plate_code"),
            "plate_name": row.get("plate_name"),
            "begin_date": row.get("begin_date"),
            "end_date": row.get("end_date"),
        }

    def _resolve_stock(self, *, db: Any, subject: str) -> Mapping[str, Any]:
        sql = """
            SELECT stk_code, stk_name, market, board, list_date, delist_date
            FROM kcrp_stock_baseinfo
            WHERE stk_code = %s
               OR stk_name = %s
               OR stk_name LIKE %s
            ORDER BY
              CASE
                WHEN stk_code = %s THEN 0
                WHEN stk_name = %s THEN 1
                ELSE 9
              END,
              list_date DESC
            LIMIT 1
        """
        like_subject = f"%{subject}%"
        row = self._fetchone(db, sql, (subject, subject, like_subject, subject, subject))
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

    def _query_by_plate(
        self,
        *,
        db: Any,
        plate_code: str,
        trade_date: str,
        limit: int,
        sort_by: str,
    ) -> List[Mapping[str, Any]]:
        return self._query_membership_rows(
            db=db,
            where_clause="pm.plate_code = %s",
            where_params=[plate_code],
            trade_date=trade_date,
            limit=limit,
            sort_by=sort_by,
        )

    def _query_by_stock(
        self,
        *,
        db: Any,
        stock_code: str,
        trade_date: str,
        limit: int,
        sort_by: str,
    ) -> List[Mapping[str, Any]]:
        return self._query_membership_rows(
            db=db,
            where_clause="pm.stk_code = %s",
            where_params=[stock_code],
            trade_date=trade_date,
            limit=limit,
            sort_by=sort_by,
        )

    def _query_membership_rows(
        self,
        *,
        db: Any,
        where_clause: str,
        where_params: List[Any],
        trade_date: str,
        limit: int,
        sort_by: str,
    ) -> List[Mapping[str, Any]]:
        order_by = self.SORT_SQL[sort_by]
        stock_date_clause = ""
        plate_date_clause = ""
        params: List[Any] = []
        if trade_date:
            stock_date_clause = "WHERE trade_date <= %s"
            plate_date_clause = "WHERE trade_date <= %s"
            params.extend([trade_date, trade_date])
        sql = f"""
            SELECT p.plate_code, p.plate_name, p.begin_date, p.end_date,
                   pm.stk_code, bi.stk_name,
                   sp.trade_date AS stock_trade_date,
                   sp.close AS stock_close,
                   sp.rise_fall_rate AS stock_rise_fall_rate,
                   sp.amount AS stock_amount,
                   sm.main_net_buy_value AS stock_main_net_buy_value,
                   pp.trade_date AS plate_trade_date,
                   pp.close AS plate_close,
                   pp.rise_fall_rate AS plate_rise_fall_rate,
                   pp.amount AS plate_amount,
                   pmf.main_btm_net AS plate_main_btm_net
            FROM kcrp_yp_plate_member pm
            JOIN kcrp_yp_plate p
              ON p.plate_code = pm.plate_code
            LEFT JOIN kcrp_stock_baseinfo bi
              ON bi.stk_code = pm.stk_code
            LEFT JOIN kcrp_stock_price sp
              ON sp.stk_code = pm.stk_code
             AND sp.trade_date = (
                SELECT MAX(trade_date)
                FROM kcrp_stock_price
                {stock_date_clause}
             )
            LEFT JOIN kcrp_stock_moneyflow sm
              ON sm.stk_code = pm.stk_code
             AND sm.trade_date = sp.trade_date
            LEFT JOIN kcrp_yp_plate_price pp
              ON pp.plate_code = pm.plate_code
             AND pp.trade_date = (
                SELECT MAX(trade_date)
                FROM kcrp_yp_plate_price
                {plate_date_clause}
             )
            LEFT JOIN kcrp_yp_plate_moneyflow pmf
              ON pmf.plate_code = pm.plate_code
             AND pmf.trade_date = pp.trade_date
            WHERE {where_clause}
            ORDER BY {order_by}
            LIMIT %s
        """
        params.extend(where_params)
        params.append(limit)
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

    def _normalize_identity(self, *, mode: str, row: Mapping[str, Any]) -> Dict[str, Any]:
        if mode == "by_plate":
            return {
                "plate_code": self._trim(row.get("plate_code")),
                "plate_name": self._trim(row.get("plate_name")),
                "begin_date": self._date_to_text(row.get("begin_date")),
                "end_date": self._date_to_text(row.get("end_date")),
            }
        return {
            "stock_code": self._trim(row.get("stock_code")),
            "stock_name": self._trim(row.get("stock_name")),
            "market": self._trim(row.get("market")),
            "board": self._trim(row.get("board")),
            "list_date": self._date_to_text(row.get("list_date")),
            "delist_date": self._date_to_text(row.get("delist_date")),
        }

    def _normalize_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "plate_code": self._trim(row.get("plate_code")),
            "plate_name": self._trim(row.get("plate_name")),
            "plate_begin_date": self._date_to_text(row.get("begin_date")),
            "plate_end_date": self._date_to_text(row.get("end_date")),
            "stock_code": self._trim(row.get("stk_code")),
            "stock_name": self._trim(row.get("stk_name")),
            "stock_trade_date": self._date_to_text(row.get("stock_trade_date")),
            "stock_close": self._number(row.get("stock_close")),
            "stock_rise_fall_rate": self._number(row.get("stock_rise_fall_rate")),
            "stock_amount": self._number(row.get("stock_amount")),
            "stock_main_net_inflow_wan": self._number(row.get("stock_main_net_buy_value")),
            "plate_trade_date": self._date_to_text(row.get("plate_trade_date")),
            "plate_close": self._number(row.get("plate_close")),
            "plate_rise_fall_rate": self._number(row.get("plate_rise_fall_rate")),
            "plate_amount": self._number(row.get("plate_amount")),
            "plate_main_net_inflow_wan": self._number(row.get("plate_main_btm_net")),
        }

    def _latest_trade_date(self, items: List[Mapping[str, Any]]) -> str:
        dates = [
            self._trim(item.get("stock_trade_date")) or self._trim(item.get("plate_trade_date"))
            for item in items
        ]
        dates = [date for date in dates if date]
        return max(dates) if dates else ""

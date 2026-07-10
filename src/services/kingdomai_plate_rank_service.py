from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


DbFactory = Callable[[], Any]


class KingdomaiPlateRankService:
    """Read-only hot plate ranking provider backed by kingdomai tables."""

    SORT_SQL = {
        "rise_fall_rate": "COALESCE(pp.rise_fall_rate, 0) DESC, COALESCE(pp.amount, 0) DESC",
        "amount": "COALESCE(pp.amount, 0) DESC, COALESCE(pp.rise_fall_rate, 0) DESC",
        "main_net_inflow": "COALESCE(mf.main_btm_net, 0) DESC, COALESCE(pp.rise_fall_rate, 0) DESC, COALESCE(pp.amount, 0) DESC",
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

    def query(
        self,
        *,
        trade_date: str = "",
        top_k: int = 10,
        sort_by: str = "rise_fall_rate",
        query: str = "",
        include_members: bool = True,
        member_limit: int = 3,
    ) -> Dict[str, Any]:
        normalized_sort = self._trim(sort_by) or "rise_fall_rate"
        if normalized_sort not in self.SORT_SQL:
            raise ValueError(f"unsupported sort_by: {sort_by}")
        limit = self._bounded_int(top_k, default=10, min_value=1, max_value=50)
        per_plate_member_limit = self._bounded_int(member_limit, default=3, min_value=0, max_value=10)
        plate_query = self._trim(query)

        db = self.db_factory()
        try:
            plate_rows = self._query_plate_rows(
                db=db,
                trade_date=self._trim(trade_date),
                top_k=limit,
                sort_by=normalized_sort,
                query=plate_query,
            )
            members_by_plate = {}
            if include_members and per_plate_member_limit > 0 and plate_rows:
                members_by_plate = self._query_members_by_plate(
                    db=db,
                    plate_codes=[self._trim(row.get("plate_code")) for row in plate_rows],
                    trade_date=self._date_to_text(plate_rows[0].get("trade_date")),
                    member_limit=per_plate_member_limit,
                )
            items = [
                self._normalize_plate_row(row, members_by_plate.get(self._trim(row.get("plate_code")), []))
                for row in plate_rows
            ]
            latest_trade_date = self._date_to_text(plate_rows[0].get("trade_date")) if plate_rows else ""
            return {
                "source": ["kcrp_yp_plate_price", "kcrp_yp_plate_moneyflow", "kcrp_yp_plate_member"],
                "trade_date": latest_trade_date,
                "sort_by": normalized_sort,
                "query": plate_query,
                "row_count": len(items),
                "items": items,
                "coverage": {
                    "requested_top_k": limit,
                    "returned_rows": len(items),
                    "latest_trade_date": latest_trade_date,
                    "members_included": bool(include_members and per_plate_member_limit > 0),
                },
            }
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def _query_plate_rows(
        self,
        *,
        db: Any,
        trade_date: str,
        top_k: int,
        sort_by: str,
        query: str,
    ) -> List[Mapping[str, Any]]:
        query_clause = ""
        date_clause = ""
        params: List[Any] = []
        if self._trim(trade_date):
            date_clause = "WHERE trade_date <= %s"
            params.append(self._trim(trade_date))
        if query:
            query_clause = "AND (pp.plate_code = %s OR pp.plate_name LIKE %s)"
            params.extend([query, f"%{query}%"])
        params.append(top_k)
        order_by = self.SORT_SQL[sort_by]
        sql = f"""
            SELECT pp.trade_date, pp.plate_code, pp.plate_name,
                   pp.open, pp.high, pp.low, pp.close, pp.avg_price,
                   pp.differ, pp.rise_fall_rate, pp.volume, pp.amount,
                   mf.float_cap, mf.main_btm_net, mf.main_btm_net_pct,
                   mf.ret_btm_net, mf.ret_btm_net_pct
            FROM kcrp_yp_plate_price pp
            LEFT JOIN kcrp_yp_plate_moneyflow mf
              ON mf.plate_code = pp.plate_code
             AND mf.trade_date = pp.trade_date
            WHERE pp.trade_date = (
                SELECT MAX(trade_date)
                FROM kcrp_yp_plate_price
                {date_clause}
            )
              {query_clause}
            ORDER BY {order_by}, pp.plate_code ASC
            LIMIT %s
        """
        return self._fetchall(db, sql, params)

    def _query_members_by_plate(
        self,
        *,
        db: Any,
        plate_codes: List[str],
        trade_date: str,
        member_limit: int,
    ) -> Dict[str, List[Dict[str, Any]]]:
        normalized_codes = [code for code in plate_codes if code]
        if not normalized_codes:
            return {}
        placeholders = ", ".join(["%s"] * len(normalized_codes))
        params: List[Any] = [trade_date, trade_date] + normalized_codes + [max(1, len(normalized_codes) * member_limit * 5)]
        sql = f"""
            SELECT pm.plate_code, pm.stk_code, bi.stk_name,
                   sp.rise_fall_rate, sp.amount,
                   sm.main_net_buy_value
            FROM kcrp_yp_plate_member pm
            LEFT JOIN kcrp_stock_baseinfo bi
              ON bi.stk_code = pm.stk_code
            LEFT JOIN kcrp_stock_price sp
              ON sp.stk_code = pm.stk_code
             AND sp.trade_date = %s
            LEFT JOIN kcrp_stock_moneyflow sm
              ON sm.stk_code = pm.stk_code
             AND sm.trade_date = %s
            WHERE pm.plate_code IN ({placeholders})
            ORDER BY pm.plate_code ASC,
                     COALESCE(sp.rise_fall_rate, 0) DESC,
                     COALESCE(sp.amount, 0) DESC,
                     pm.stk_code ASC
            LIMIT %s
        """
        rows = self._fetchall(db, sql, params)
        grouped: Dict[str, List[Dict[str, Any]]] = {code: [] for code in normalized_codes}
        for row in rows:
            plate_code = self._trim(row.get("plate_code"))
            if plate_code not in grouped or len(grouped[plate_code]) >= member_limit:
                continue
            grouped[plate_code].append(
                {
                    "stock_code": self._trim(row.get("stk_code")),
                    "stock_name": self._trim(row.get("stk_name")),
                    "rise_fall_rate": self._number(row.get("rise_fall_rate")),
                    "amount": self._number(row.get("amount")),
                    "main_net_inflow_wan": self._number(row.get("main_net_buy_value")),
                }
            )
        return grouped

    def _fetchall(self, db: Any, sql: str, params: List[Any]) -> List[Mapping[str, Any]]:
        conn = getattr(db, "conn", db)
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return [row for row in rows if isinstance(row, Mapping)]

    def _normalize_plate_row(self, row: Mapping[str, Any], leaders: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "plate_code": self._trim(row.get("plate_code")),
            "plate_name": self._trim(row.get("plate_name")),
            "trade_date": self._date_to_text(row.get("trade_date")),
            "close": self._number(row.get("close")),
            "rise_fall_rate": self._number(row.get("rise_fall_rate")),
            "amount": self._number(row.get("amount")),
            "volume": self._number(row.get("volume")),
            "main_net_inflow_wan": self._number(row.get("main_btm_net")),
            "main_net_ratio_pct": self._number(row.get("main_btm_net_pct")),
            "retail_net_inflow_wan": self._number(row.get("ret_btm_net")),
            "retail_net_ratio_pct": self._number(row.get("ret_btm_net_pct")),
            "leaders": leaders,
        }

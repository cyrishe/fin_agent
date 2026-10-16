"""Resolve stock identifiers without asking the agent to construct a data query."""
from __future__ import annotations

import re
from typing import Any, Callable

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


class SecurityIdentityService:
    def __init__(self, *, db_factory: Callable[[], Any] | None = None) -> None:
        self.db_factory = db_factory or (lambda: StockInfoDbUtils(database="kingdomai"))

    def resolve(self, identifiers: list[str]) -> dict[str, Any]:
        if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 20
                or any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in identifiers)):
            raise ValueError("identifiers 需要 1–20 个股票名称或代码，每项 1–100 字符。")
        db = self.db_factory()
        items = []
        try:
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                for value in dict.fromkeys(v.strip() for v in identifiers):
                    code = value.upper()
                    if re.fullmatch(r"\d{6}", code):
                        condition, params = "LEFT(stk_code, 6) = %s", (code,)
                    else:
                        condition, params = "(stk_code = %s OR stk_name = %s)", (code, value)
                    cursor.execute(self._sql(condition), params)
                    rows = cursor.fetchall()
                    note = "按代码或名称精确匹配。"
                    if not rows and not re.fullmatch(r"\d{6}(?:\.[A-Z]+)?", code):
                        literal = value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
                        cursor.execute(self._sql("stk_name LIKE %s ESCAPE '!'"), (f"%{literal}%",))
                        rows = cursor.fetchall()
                        note = "名称包含匹配；候选由调用方结合原问题确认。"
                    candidates = [{"code": row["code"], "name": row["name"],
                                   "market": str(row["code"]).partition(".")[2] or None}
                                  for row in rows[:20]]
                    items.append({"input": value, "candidates": candidates,
                                  "truncated": len(rows) > 20,
                                  "note": note if rows else "当前股票身份表未匹配；不代表其他数据源没有该公司的数据。"})
            return {"ok": True, "items": items, "source": "kingdomai.kcrp_stock_baseinfo",
                    "scope": "股票名称与代码识别，包含身份表保留的历史证券。"}
        finally:
            db.close_db()

    @staticmethod
    def _sql(condition: str) -> str:
        return ("SELECT DISTINCT stk_code AS code, stk_name AS name FROM kcrp_stock_baseinfo "
                f"WHERE {condition} ORDER BY stk_code, stk_name LIMIT 21")

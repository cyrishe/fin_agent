from __future__ import annotations

from typing import Any, Dict

from src.services.kingdomai_stock_financial_statement_service import (
    KingdomaiStockFinancialStatementService,
)


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiStockFinancialStatementService().query(
            subject=str(
                params.get("stock")
                or params.get("subject")
                or params.get("stock_code")
                or params.get("name")
                or params.get("code")
                or ""
            ).strip(),
            statement_type=str(params.get("statement_type") or "all").strip(),
            report_period=str(params.get("report_period") or "").strip(),
            periods=int(params.get("periods", 4) or 4),
            accounting_statement_type=str(params.get("accounting_statement_type") or "").strip(),
        )
        return {
            "tool": "stock_financial_statement_query",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "stock_financial_statement_query",
            "ok": False,
            "data": {},
            "error": str(exc),
        }

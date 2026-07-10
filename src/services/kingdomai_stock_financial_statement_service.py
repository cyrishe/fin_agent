from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


DbFactory = Callable[[], Any]


class KingdomaiStockFinancialStatementService:
    """Read-only stock financial statement provider backed by kingdomai tables."""

    STATEMENT_TYPES = {"balance_sheet", "income", "cashflow", "all"}
    DEFAULT_ACCOUNTING_STATEMENT_TYPE = {
        "balance_sheet": "HB",
        "income": "HB",
        "cashflow": "HBTZ",
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
    def _normalize_statement_type(cls, value: Any) -> str:
        raw = cls._trim(value).lower() or "all"
        aliases = {
            "资产负债表": "balance_sheet",
            "balance": "balance_sheet",
            "balancesheet": "balance_sheet",
            "balance sheet": "balance_sheet",
            "利润表": "income",
            "损益表": "income",
            "income_statement": "income",
            "income statement": "income",
            "现金流量表": "cashflow",
            "现金流表": "cashflow",
            "cash_flow": "cashflow",
            "cash flow": "cashflow",
            "三表": "all",
            "全部": "all",
        }
        return aliases.get(raw, raw)

    def query(
        self,
        *,
        subject: str,
        statement_type: str = "all",
        report_period: str = "",
        periods: int = 4,
        accounting_statement_type: str = "",
    ) -> Dict[str, Any]:
        raw_subject = self._trim(subject)
        if not raw_subject:
            raise ValueError("subject is required")
        normalized_statement_type = self._normalize_statement_type(statement_type)
        if normalized_statement_type not in self.STATEMENT_TYPES:
            raise ValueError(f"unsupported statement_type: {statement_type}")
        period_limit = self._bounded_int(periods, default=4, min_value=1, max_value=20)
        target_period = self._trim(report_period)
        requested_statements = (
            ["balance_sheet", "income", "cashflow"]
            if normalized_statement_type == "all"
            else [normalized_statement_type]
        )

        db = self.db_factory()
        try:
            identity = db.resolve_stock_identity(raw_subject) if hasattr(db, "resolve_stock_identity") else None
            stk_code = self._trim((identity or {}).get("stk_code")) or raw_subject
            code6 = stk_code[:6]
            subject_name = self._trim((identity or {}).get("stk_name"))
            if not code6:
                raise ValueError(f"cannot resolve stock subject: {subject}")
            statements: Dict[str, List[Dict[str, Any]]] = {}
            for item in requested_statements:
                rows = self._query_statement_rows(
                    db=db,
                    statement_type=item,
                    stk_code=stk_code,
                    report_period=target_period,
                    periods=period_limit,
                    accounting_statement_type=self._trim(accounting_statement_type)
                    or self.DEFAULT_ACCOUNTING_STATEMENT_TYPE[item],
                )
                statements[item] = [self._normalize_row(item, row) for row in reversed(rows)]
            return {
                "source": [
                    "kcrp_stock_balancesheet",
                    "kcrp_stock_income",
                    "kcrp_stock_cashflow",
                ],
                "stock": raw_subject,
                "code": stk_code,
                "name": subject_name,
                "subject": raw_subject,
                "subject_code": stk_code,
                "subject_name": subject_name,
                "statement_type": normalized_statement_type,
                "report_period": target_period,
                "statements": statements,
                "coverage": {
                    "requested_statement_type": normalized_statement_type,
                    "requested_periods": period_limit,
                    "returned_statement_types": [
                        key for key, rows in statements.items() if rows
                    ],
                    "row_counts": {key: len(rows) for key, rows in statements.items()},
                    "latest_report_period_by_statement": {
                        key: (rows[-1].get("report_period") if rows else "")
                        for key, rows in statements.items()
                    },
                },
            }
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def _query_statement_rows(
        self,
        *,
        db: Any,
        statement_type: str,
        stk_code: str,
        report_period: str,
        periods: int,
        accounting_statement_type: str,
    ) -> List[Mapping[str, Any]]:
        sql_by_type = {
            "balance_sheet": """
                SELECT report_period, ann_date, statement_type, crncy_code,
                       monetary_cap, tot_cur_assets, tot_non_cur_assets, tot_assets,
                       tot_cur_liab, tot_non_cur_liab, tot_liab,
                       shares, total_quity_atsopc, minority_int,
                       total_holders_equity, tot_liab_shrhldr_eqy
                FROM kcrp_stock_balancesheet
                WHERE stk_code = %s
                  AND statement_type = %s
                  {period_clause}
                ORDER BY report_period DESC
                LIMIT %s
            """,
            "income": """
                SELECT report_period, ann_date, statement_type, crncy_code,
                       tot_oper_rev, oper_rev, tot_oper_cost, less_oper_cost,
                       oper_profit, tot_profit, inc_tax,
                       net_profit_atsopc, net_profit_after_nrgal_atsolc,
                       s_fa_eps_basic, s_fa_eps_diluted, ebit, ebitda
                FROM kcrp_stock_income
                WHERE stk_code = %s
                  AND statement_type = %s
                  {period_clause}
                ORDER BY report_period DESC
                LIMIT %s
            """,
            "cashflow": """
                SELECT report_period, ann_date, statement_type, crncy_code,
                       stot_cash_inflows_oper_act, stot_cash_outflows_oper_act,
                       net_cash_flows_oper_act,
                       stot_cash_inflows_inv_act, stot_cash_outflows_inv_act,
                       net_cash_flows_inv_act,
                       stot_cash_inflows_fnc_act, stot_cash_outflows_fnc_act,
                       net_cash_flows_fnc_act,
                       net_increase_in_cce, initial_balance_of_cce,
                       cash_cash_equ_end_period
                FROM kcrp_stock_cashflow
                WHERE stk_code = %s
                  AND statement_type = %s
                  {period_clause}
                ORDER BY report_period DESC
                LIMIT %s
            """,
        }
        params: List[Any] = [stk_code, accounting_statement_type]
        period_clause = ""
        if report_period:
            period_clause = "AND report_period <= %s"
            params.append(report_period)
        params.append(periods)
        sql = sql_by_type[statement_type].format(period_clause=period_clause)
        conn = getattr(db, "conn", db)
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return [row for row in rows if isinstance(row, Mapping)]

    def _base_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "report_period": self._trim(row.get("report_period")),
            "ann_date": self._date_to_text(row.get("ann_date")),
            "statement_type": self._trim(row.get("statement_type")),
            "currency": self._trim(row.get("crncy_code")),
        }

    def _normalize_row(self, statement_type: str, row: Mapping[str, Any]) -> Dict[str, Any]:
        base = self._base_row(row)
        if statement_type == "balance_sheet":
            base.update(
                {
                    "monetary_cap": self._number(row.get("monetary_cap")),
                    "tot_cur_assets": self._number(row.get("tot_cur_assets")),
                    "tot_non_cur_assets": self._number(row.get("tot_non_cur_assets")),
                    "tot_assets": self._number(row.get("tot_assets")),
                    "tot_cur_liab": self._number(row.get("tot_cur_liab")),
                    "tot_non_cur_liab": self._number(row.get("tot_non_cur_liab")),
                    "tot_liab": self._number(row.get("tot_liab")),
                    "shares": self._number(row.get("shares")),
                    "total_quity_atsopc": self._number(row.get("total_quity_atsopc")),
                    "minority_int": self._number(row.get("minority_int")),
                    "total_holders_equity": self._number(row.get("total_holders_equity")),
                    "tot_liab_shrhldr_eqy": self._number(row.get("tot_liab_shrhldr_eqy")),
                }
            )
            return base
        if statement_type == "income":
            base.update(
                {
                    "tot_oper_rev": self._number(row.get("tot_oper_rev")),
                    "oper_rev": self._number(row.get("oper_rev")),
                    "tot_oper_cost": self._number(row.get("tot_oper_cost")),
                    "less_oper_cost": self._number(row.get("less_oper_cost")),
                    "oper_profit": self._number(row.get("oper_profit")),
                    "tot_profit": self._number(row.get("tot_profit")),
                    "inc_tax": self._number(row.get("inc_tax")),
                    "net_profit_atsopc": self._number(row.get("net_profit_atsopc")),
                    "net_profit_after_nrgal_atsolc": self._number(row.get("net_profit_after_nrgal_atsolc")),
                    "s_fa_eps_basic": self._number(row.get("s_fa_eps_basic")),
                    "s_fa_eps_diluted": self._number(row.get("s_fa_eps_diluted")),
                    "ebit": self._number(row.get("ebit")),
                    "ebitda": self._number(row.get("ebitda")),
                }
            )
            return base
        base.update(
            {
                "stot_cash_inflows_oper_act": self._number(row.get("stot_cash_inflows_oper_act")),
                "stot_cash_outflows_oper_act": self._number(row.get("stot_cash_outflows_oper_act")),
                "net_cash_flows_oper_act": self._number(row.get("net_cash_flows_oper_act")),
                "stot_cash_inflows_inv_act": self._number(row.get("stot_cash_inflows_inv_act")),
                "stot_cash_outflows_inv_act": self._number(row.get("stot_cash_outflows_inv_act")),
                "net_cash_flows_inv_act": self._number(row.get("net_cash_flows_inv_act")),
                "stot_cash_inflows_fnc_act": self._number(row.get("stot_cash_inflows_fnc_act")),
                "stot_cash_outflows_fnc_act": self._number(row.get("stot_cash_outflows_fnc_act")),
                "net_cash_flows_fnc_act": self._number(row.get("net_cash_flows_fnc_act")),
                "net_increase_in_cce": self._number(row.get("net_increase_in_cce")),
                "initial_balance_of_cce": self._number(row.get("initial_balance_of_cce")),
                "cash_cash_equ_end_period": self._number(row.get("cash_cash_equ_end_period")),
            }
        )
        return base

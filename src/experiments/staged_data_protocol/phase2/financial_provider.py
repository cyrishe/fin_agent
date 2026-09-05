from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


DEFAULT_STATEMENT_TYPE = "HB"
COMPUTED_SUFFIXES = {"yoy", "qoq"}
COMPUTED_BASE_FIELDS = {
    "revenue",
    "total_revenue",
    "operating_revenue",
    "operating_cost",
    "total_cost",
    "sales_expense",
    "admin_expense",
    "rd_expense",
    "financial_expense",
    "operating_profit",
    "total_profit",
    "profit",
    "net_profit",
    "parent_net_profit",
    "deducted_parent_net_profit",
    "eps_basic",
    "eps_diluted",
    "total_assets",
    "total_liab",
    "total_equity",
    "parent_equity",
    "monetary_cap",
    "accounts_receivable",
    "inventory",
    "fixed_assets",
    "goodwill",
    "short_term_borrowing",
    "long_term_borrowing",
}


@dataclass(frozen=True)
class FinancialSource:
    subject: str
    fields: Mapping[str, str]


STOCK_FINANCIAL_SOURCE = FinancialSource(
    subject="stock",
    fields={
        "code": "i.stk_code",
        "name": "base.stk_name",
        "report_date": "i.report_period",
        "report_period": "i.report_period",
        "ann_date": "i.ann_date",
        "statement_type": "i.statement_type",
        "currency": "i.crncy_code",
        "revenue": "COALESCE(i.oper_rev, i.tot_oper_rev)",
        "total_revenue": "i.tot_oper_rev",
        "operating_revenue": "i.oper_rev",
        "operating_cost": "i.less_oper_cost",
        "total_cost": "i.tot_oper_cost",
        "sales_expense": "i.less_selling_dist_exp",
        "admin_expense": "i.less_gerl_admin_exp",
        "rd_expense": "i.rd_expense",
        "financial_expense": "i.less_fin_exp",
        "operating_profit": "i.oper_profit",
        "total_profit": "i.tot_profit",
        "profit": "COALESCE(i.net_profit_atsopc, i.net_profit_incl_min_int_inc)",
        "net_profit": "i.net_profit_incl_min_int_inc",
        "parent_net_profit": "i.net_profit_atsopc",
        "deducted_parent_net_profit": "i.net_profit_after_nrgal_atsolc",
        "eps_basic": "i.s_fa_eps_basic",
        "eps_diluted": "i.s_fa_eps_diluted",
        "total_assets": "bs.tot_assets",
        "total_liab": "bs.tot_liab",
        "total_equity": "bs.total_holders_equity",
        "parent_equity": "bs.total_quity_atsopc",
        "monetary_cap": "bs.monetary_cap",
        "accounts_receivable": "bs.acct_rcv",
        "inventory": "bs.inventories",
        "fixed_assets": "bs.fix_assets",
        "goodwill": "bs.goodwill",
        "short_term_borrowing": "bs.st_borrow",
        "long_term_borrowing": "bs.lt_borrow",
        "cashflow_operating": "cf.net_cash_flows_oper_act",
        "operating_cashflow": "cf.net_cash_flows_oper_act",
        "cashflow_investing": "cf.net_cash_flows_inv_act",
        "investing_cashflow": "cf.net_cash_flows_inv_act",
        "cashflow_financing": "cf.net_cash_flows_fnc_act",
        "financing_cashflow": "cf.net_cash_flows_fnc_act",
        "net_cashflow": "cf.net_increase_in_cce",
        "cash_end": "cf.cash_cash_equ_end_period",
        "roe": "fi.roe_avg",
        "roe_deducted": "fi.roe_exavg",
        "roa": "fi.roa",
        "gross_margin": "fi.gp_margin",
        "net_margin": "fi.np_margin",
        "debt_ratio": "fi.libility_to_asset",
        "current_ratio": "fi.current_ratio",
        "quick_ratio": "fi.quick_ratio",
        "asset_turnover": "fi.asset_turn_ratio",
        "revenue_growth": "NULL",
    },
)


OP_SQL = {"=": "=", "==": "=", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<="}
FILTER_RE = re.compile(
    r"(?:(?P<connector>\band\b|\bor\b)\s+)?"
    r"(?P<field>[A-Za-z_]\w*)\s*"
    r"(?P<op>in|==|=|!=|>=|<=|>|<)\s*"
    r"(?P<value>\[[^\]]+\]|\([^)]+\)|[^,;]+?)"
    r"(?=\s+(?:and|or)\s+[A-Za-z_]\w*\s*(?:in|==|=|!=|>=|<=|>|<)|[,;]|$)",
    flags=re.IGNORECASE,
)
GROUPED_OR_RE = re.compile(r"\((?P<body>[^()]+)\)")
GROUPED_OR_TERM_RE = re.compile(
    r"(?P<field>[A-Za-z_]\w*)\s*=\s*(?P<value>[^,;()]+?)",
    flags=re.IGNORECASE,
)


def execute_financial_3_table_api(*, subject: str, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    source = STOCK_FINANCIAL_SOURCE if subject == "stock" else _mock_source(subject)
    columns = _requested_columns(source=source, outputs=outputs)
    computed_columns = _computed_columns_for_request(source=source, args=args, columns=columns)
    ignored_filters = _ignored_filters(source=source, args=args)
    if subject != "stock":
        return _standard_result(
            status="ok",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            mocked_fields=columns,
            ignored_filters=ignored_filters,
        )
    if _has_unresolved_ref(args):
        return _standard_result(
            status="requires_upstream_values",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            mocked_fields=_mocked_fields(source=source, columns=columns),
            ignored_filters=ignored_filters,
            reason="filter contains result references that must be materialized by the execution engine",
        )

    limit = _bounded_limit(args.get("limit"))
    post_process = bool(_computed_filter_fields(source=source, args=args) or _computed_order_field(args))
    sql_limit = _candidate_limit(limit) if post_process else limit
    where_sql, params = _build_where(source=source, args=args)
    order_sql = _build_order(source=source, args=args)
    query_columns = _query_columns(columns=columns, computed_columns=computed_columns)
    sql = _build_sql(source=source, columns=query_columns, where_sql=where_sql, order_sql=order_sql)
    params.append(sql_limit)

    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
            if computed_columns:
                _attach_computed_fields(cursor=cursor, rows=raw_rows, args=args, computed_columns=computed_columns)
        processing_columns = _processing_columns(columns=columns, computed_columns=computed_columns)
        rows = [_normalize_row(row, processing_columns) for row in raw_rows]
        rows = _filter_post_rows(source=source, args=args, rows=rows)
        rows = _sort_post_rows(args=args, rows=rows)
        if post_process:
            rows = rows[:limit]
        rows = [_project_row(row, columns) for row in rows]
        return _standard_result(
            status="ok",
            source=source,
            args=args,
            columns=columns,
            rows=rows,
            mocked_fields=_mocked_fields(source=source, columns=columns),
            ignored_filters=ignored_filters,
            sql_shape={"where": where_sql, "order": order_sql, "limit": sql_limit, "output_limit": limit},
        )
    except Exception as exc:  # noqa: BLE001 - experiment boundary should return structured failures.
        return _standard_result(
            status="provider_error",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            mocked_fields=_mocked_fields(source=source, columns=columns),
            ignored_filters=ignored_filters,
            reason=str(exc),
        )
    finally:
        db.close_db()


def _mock_source(subject: str) -> FinancialSource:
    fields = {
        "code": "NULL",
        "name": "NULL",
        "report_date": "NULL",
        "revenue": "NULL",
        "profit": "NULL",
        "roe": "NULL",
        "debt_ratio": "NULL",
        "operating_cashflow": "NULL",
    }
    return FinancialSource(subject=subject, fields=fields)


def _requested_columns(*, source: FinancialSource, outputs: List[str]) -> List[str]:
    columns: List[str] = []
    for output in outputs:
        token = _output_token(output)
        if (token in source.fields or _computed_base(token)) and token not in columns:
            columns.append(token)
    if columns:
        return columns
    return [field for field in ["code", "name", "report_date", "revenue", "profit", "roe", "debt_ratio", "operating_cashflow"] if field in source.fields]


def _computed_base(field_name: str) -> str:
    text = str(field_name or "").strip()
    if "_" not in text:
        return ""
    base, suffix = text.rsplit("_", 1)
    if suffix in COMPUTED_SUFFIXES and base in COMPUTED_BASE_FIELDS:
        return base
    return ""


def _query_columns(*, columns: List[str], computed_columns: List[str]) -> List[str]:
    query_columns: List[str] = []
    for field_name in columns:
        base = _computed_base(field_name) or field_name
        if base in STOCK_FINANCIAL_SOURCE.fields and base not in query_columns:
            query_columns.append(base)
    for required in ["code", "report_date"]:
        if required not in query_columns:
            query_columns.append(required)
    for field_name in computed_columns:
        base = _computed_base(field_name)
        if base and base not in query_columns:
            query_columns.append(base)
    return query_columns


def _processing_columns(*, columns: List[str], computed_columns: List[str]) -> List[str]:
    result = list(columns)
    for field_name in computed_columns:
        if field_name not in result:
            result.append(field_name)
    return result


def _output_token(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text:
        text = text.split(" as ", 1)[0].strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _has_unresolved_ref(args: Mapping[str, Any]) -> bool:
    return any(isinstance(value, str) and re.search(r"\br\d+\.", value) for value in args.values())


def _bounded_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = 100
    if parsed <= 0:
        parsed = 100
    return max(1, min(parsed, 500))


def _candidate_limit(output_limit: int) -> int:
    return max(output_limit, min(max(output_limit * 20, 1000), 10000))


def _build_where(*, source: FinancialSource, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    explicit_filters = _explicit_filters(source=source, args=args)
    report_date = str(args.get("report_date") or args.get("report_period") or args.get("date") or "").strip()
    start = str(args.get("start") or args.get("start_date") or "").strip()
    end = str(args.get("end") or args.get("end_date") or "").strip()
    statement_type = str(args.get("statement_type") or DEFAULT_STATEMENT_TYPE).strip() or DEFAULT_STATEMENT_TYPE
    if report_date:
        clauses.append("i.report_period = %s")
        params.append(report_date)
    elif start and end:
        clauses.append("i.report_period BETWEEN %s AND %s")
        params.extend(sorted([start, end]))
    elif not any(field_name in {"report_date", "report_period"} for _, field_name, _, _ in explicit_filters):
        clauses.append("i.report_period = (SELECT MAX(report_period) FROM kcrp_stock_income)")
    clauses.append("i.statement_type = %s")
    params.append(statement_type)

    filter_sql, filter_params = _build_filter_clauses(
        source=source,
        args=args,
        explicit_filters=explicit_filters,
    )
    if filter_sql:
        clauses.append(filter_sql)
        params.extend(filter_params)
    return " AND ".join(clauses), params


def _build_filter_clauses(
    *,
    source: FinancialSource,
    args: Mapping[str, Any],
    explicit_filters: List[tuple[str, str, str, Any]] | None = None,
) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    filters = explicit_filters if explicit_filters is not None else _explicit_filters(source=source, args=args)
    for connector, field_name, op, value in filters:
        if _computed_base(field_name):
            continue
        expression = source.fields.get(field_name)
        if not expression or expression == "NULL":
            continue
        prefix = connector if clauses else ""
        if op == "in":
            values = _list_value(value)
            if not values:
                continue
            placeholders = ", ".join(["%s"] * len(values))
            clauses.append(f"{prefix} {expression} IN ({placeholders})".strip())
            params.extend(values)
            continue
        if op not in OP_SQL:
            continue
        clauses.append(f"{prefix} {expression} {OP_SQL[op]} %s".strip())
        params.append(value)
    if not clauses:
        return "", []
    return f"({' '.join(clauses)})", params


def _ignored_filters(*, source: FinancialSource, args: Mapping[str, Any]) -> List[str]:
    ignored: List[str] = []
    for _connector, field_name, op, value in _explicit_filters(source=source, args=args):
        if _computed_base(field_name):
            continue
        if source.fields.get(field_name) == "NULL":
            ignored.append(f"{field_name} {op} {value}")
    return ignored


def _explicit_filters(*, source: FinancialSource, args: Mapping[str, Any]) -> List[tuple[str, str, str, Any]]:
    rows: List[tuple[str, str, str, Any]] = []
    for field_name in source.fields:
        value = args.get(field_name)
        if value not in (None, ""):
            rows.append(("AND", field_name, "=", value))
    for plural, field_name in [("codes", "code"), ("names", "name")]:
        value = args.get(plural)
        if value not in (None, "") and field_name in source.fields:
            rows.append(("AND", field_name, "in", value))
    filter_text = _normalize_grouped_same_field_or(str(args.get("filter") or "").strip())
    for match in FILTER_RE.finditer(filter_text):
        rows.append(
            (
                str(match.group("connector") or "AND").upper(),
                str(match.group("field") or "").strip(),
                str(match.group("op") or "").strip().lower(),
                _clean_value(str(match.group("value") or "")),
            )
        )
    return rows


def _normalize_grouped_same_field_or(filter_text: str) -> str:
    """Collapse ``(field = a or field = b)`` to an equivalent IN predicate.

    The lightweight provider parser intentionally supports a small filter
    grammar.  Normalizing same-field OR groups preserves their boolean scope
    without teaching the provider a second, partial SQL expression parser.
    Groups containing different fields or operators are left untouched.
    """

    def replace(match: re.Match[str]) -> str:
        body = str(match.group("body") or "").strip()
        parts = re.split(r"\s+or\s+", body, flags=re.IGNORECASE)
        if len(parts) < 2:
            return match.group(0)
        terms = [GROUPED_OR_TERM_RE.fullmatch(part.strip()) for part in parts]
        if any(term is None for term in terms):
            return match.group(0)
        field_names = {str(term.group("field") or "").lower() for term in terms if term is not None}
        if len(field_names) != 1:
            return match.group(0)
        field_name = str(terms[0].group("field") or "")
        values = ", ".join(str(term.group("value") or "").strip() for term in terms if term is not None)
        return f"{field_name} in [{values}]"

    return GROUPED_OR_RE.sub(replace, str(filter_text or ""))


def _clean_value(value: str) -> Any:
    text = str(value or "").strip().strip("\"'")
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _list_value(value: Any) -> List[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    text = str(value or "").strip()
    if not text:
        return []
    if (text.startswith("[") and text.endswith("]")) or (text.startswith("(") and text.endswith(")")):
        text = text[1:-1]
    return [_clean_value(item) for item in text.split(",") if str(item).strip()]


def _build_order(*, source: FinancialSource, args: Mapping[str, Any]) -> str:
    text = str(args.get("order") or "").strip()
    if not text or text.lower() == "none":
        return "i.report_period DESC, i.ann_date DESC"
    parts = re.split(r"\s+", text, maxsplit=1)
    field_name = parts[0].strip()
    if _computed_base(field_name):
        return "i.report_period DESC, i.ann_date DESC"
    direction = parts[1].strip().lower() if len(parts) > 1 else "desc"
    expression = source.fields.get(field_name)
    if not expression or expression == "NULL":
        return "i.report_period DESC, i.ann_date DESC"
    direction_sql = "ASC" if direction.startswith("asc") else "DESC"
    return f"{expression} {direction_sql}, i.report_period DESC, i.ann_date DESC"


def _build_sql(*, source: FinancialSource, columns: List[str], where_sql: str, order_sql: str) -> str:
    select_sql = ", ".join(f"{source.fields[column]} AS `{column}`" for column in columns)
    return f"""
        SELECT {select_sql}
        FROM kcrp_stock_income i
        LEFT JOIN kcrp_stock_baseinfo base ON base.stk_code = i.stk_code
        LEFT JOIN kcrp_stock_balancesheet bs
            ON bs.stk_code = i.stk_code
           AND bs.report_period = i.report_period
           AND bs.statement_type = i.statement_type
        LEFT JOIN kcrp_stock_cashflow cf
            ON cf.stk_code = i.stk_code
           AND cf.report_period = i.report_period
           AND cf.statement_type = i.statement_type
        LEFT JOIN kcrp_stock_financial_indicator fi
            ON fi.stk_code = i.stk_code
           AND fi.report_period = i.report_period
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
    """


def _attach_computed_fields(
    *,
    cursor: pymysql.cursors.DictCursor,
    rows: List[Mapping[str, Any]],
    args: Mapping[str, Any],
    computed_columns: List[str],
) -> None:
    codes = sorted({str(row.get("code") or "").strip() for row in rows if str(row.get("code") or "").strip()})
    report_dates = sorted({_date_value(row.get("report_date")) for row in rows if _date_value(row.get("report_date"))})
    base_fields = sorted({_computed_base(column) for column in computed_columns if _computed_base(column)})
    if not codes or not report_dates or not base_fields:
        return
    start_date = min(_previous_year(item) for item in report_dates)
    end_date = max(report_dates)
    statement_type = str(args.get("statement_type") or DEFAULT_STATEMENT_TYPE).strip() or DEFAULT_STATEMENT_TYPE
    history_sql = _build_history_sql(base_fields=base_fields, code_count=len(codes))
    cursor.execute(history_sql, tuple([statement_type, start_date.isoformat(), end_date.isoformat(), *codes]))
    history_rows = cursor.fetchall()
    by_key: Dict[tuple[str, date], Mapping[str, Any]] = {}
    dates_by_code: Dict[str, List[date]] = {}
    for history_row in history_rows:
        code = str(history_row.get("code") or "").strip()
        report_date = _date_value(history_row.get("report_date"))
        if not code or not report_date:
            continue
        by_key[(code, report_date)] = history_row
        dates_by_code.setdefault(code, []).append(report_date)
    for code, dates in dates_by_code.items():
        dates_by_code[code] = sorted(set(dates), reverse=True)
    for row in rows:
        code = str(row.get("code") or "").strip()
        report_date = _date_value(row.get("report_date"))
        if not code or not report_date:
            continue
        for computed_column in computed_columns:
            base = _computed_base(computed_column)
            if not base:
                continue
            if computed_column.endswith("_yoy"):
                compare_date = _previous_year(report_date)
            else:
                compare_date = _previous_report_date(dates_by_code.get(code, []), report_date)
            compare_row = by_key.get((code, compare_date)) if compare_date else None
            row[computed_column] = _growth_pct(row.get(base), compare_row.get(base) if compare_row else None)


def _build_history_sql(*, base_fields: List[str], code_count: int) -> str:
    select_fields = ["i.stk_code AS `code`", "i.report_period AS `report_date`"]
    select_fields.extend(f"{STOCK_FINANCIAL_SOURCE.fields[field]} AS `{field}`" for field in base_fields)
    placeholders = ", ".join(["%s"] * code_count)
    return f"""
        SELECT {", ".join(select_fields)}
        FROM kcrp_stock_income i
        LEFT JOIN kcrp_stock_balancesheet bs
            ON bs.stk_code = i.stk_code
           AND bs.report_period = i.report_period
           AND bs.statement_type = i.statement_type
        WHERE i.statement_type = %s
          AND i.report_period BETWEEN %s AND %s
          AND i.stk_code IN ({placeholders})
        ORDER BY i.stk_code, i.report_period DESC
    """


def _previous_report_date(report_dates: List[date], current: date) -> date | None:
    older = [item for item in report_dates if item < current]
    return older[0] if older else None


def _previous_year(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _growth_pct(current: Any, previous: Any) -> float | None:
    current_value = _number_value(current)
    previous_value = _number_value(previous)
    if current_value is None or previous_value in (None, 0):
        return None
    return (current_value - previous_value) / previous_value * 100


def _number_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _computed_filter_fields(*, source: FinancialSource, args: Mapping[str, Any]) -> List[str]:
    return [field_name for _connector, field_name, _op, _value in _explicit_filters(source=source, args=args) if _computed_base(field_name)]


def _computed_order_field(args: Mapping[str, Any]) -> str:
    text = str(args.get("order") or "").strip()
    if not text or text.lower() == "none":
        return ""
    field_name = re.split(r"\s+", text, maxsplit=1)[0].strip()
    return field_name if _computed_base(field_name) else ""


def _computed_columns_for_request(*, source: FinancialSource, args: Mapping[str, Any], columns: List[str]) -> List[str]:
    result: List[str] = []
    for field_name in [*columns, *_computed_filter_fields(source=source, args=args), _computed_order_field(args)]:
        if _computed_base(field_name) and field_name not in result:
            result.append(field_name)
    return result


def _filter_post_rows(*, source: FinancialSource, args: Mapping[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = rows
    for _connector, field_name, op, value in _explicit_filters(source=source, args=args):
        if not _computed_base(field_name):
            continue
        if op not in OP_SQL:
            continue
        result = [row for row in result if _compare(row.get(field_name), op, value)]
    return result


def _sort_post_rows(*, args: Mapping[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    field_name = _computed_order_field(args)
    if not field_name:
        return rows
    text = str(args.get("order") or "").strip()
    parts = re.split(r"\s+", text, maxsplit=1)
    direction = parts[1].strip().lower() if len(parts) > 1 else "desc"
    reverse = not direction.startswith("asc")
    valued = [row for row in rows if row.get(field_name) is not None]
    empty = [row for row in rows if row.get(field_name) is None]
    return sorted(valued, key=lambda row: row.get(field_name) or 0, reverse=reverse) + empty


def _compare(left: Any, op: str, right: Any) -> bool:
    left_value = _number_value(left)
    right_value = _number_value(right)
    if left_value is None or right_value is None:
        return False
    if op in {"=", "=="}:
        return left_value == right_value
    if op == "!=":
        return left_value != right_value
    if op == ">":
        return left_value > right_value
    if op == ">=":
        return left_value >= right_value
    if op == "<":
        return left_value < right_value
    if op == "<=":
        return left_value <= right_value
    return False


def _normalize_row(row: Mapping[str, Any], columns: List[str]) -> Dict[str, Any]:
    return {column: _normalize_value(row.get(column)) for column in columns}


def _project_row(row: Mapping[str, Any], columns: List[str]) -> Dict[str, Any]:
    return {column: row.get(column) for column in columns}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _mocked_fields(*, source: FinancialSource, columns: List[str]) -> List[str]:
    return [column for column in columns if source.fields.get(column) == "NULL"]


def _standard_result(
    *,
    status: str,
    source: FinancialSource,
    args: Mapping[str, Any],
    columns: List[str],
    rows: List[Dict[str, Any]],
    mocked_fields: List[str] | None = None,
    ignored_filters: List[str] | None = None,
    reason: str | None = None,
    sql_shape: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": status,
        "api": f"{source.subject}.financial_3_table",
        "subject": source.subject,
        "tables": [
            "kcrp_stock_income",
            "kcrp_stock_balancesheet",
            "kcrp_stock_cashflow",
            "kcrp_stock_financial_indicator",
        ]
        if source.subject == "stock"
        else [],
        "arguments": dict(args),
        "columns": columns,
        "rows": rows,
        "mocked_fields": mocked_fields or [],
        "ignored_filters": ignored_filters or [],
    }
    if reason:
        result["reason"] = reason
    if sql_shape:
        result["sql_shape"] = dict(sql_shape)
    return result

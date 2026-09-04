from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


@dataclass(frozen=True)
class StockCorporateSource:
    source: str
    table: str
    fields: Mapping[str, str]


@dataclass(frozen=True)
class StockCorporateView:
    dataview: str
    sources: List[StockCorporateSource]
    default_columns: List[str]
    default_date_field: str
    default_order: List[tuple[str, str]]

    @property
    def fields(self) -> set[str]:
        rows: set[str] = set()
        for source in self.sources:
            rows.update(source.fields)
        return rows


OP_SQL = {"=": "=", "==": "=", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<=", "like": "LIKE"}
FILTER_RE = re.compile(
    r"(?:(?P<connector>\band\b|\bor\b)\s+)?"
    r"(?P<field>[A-Za-z_]\w*)\s*"
    r"(?P<op>like|in|==|=|!=|>=|<=|>|<)\s*"
    r"(?P<value>\[[^\]]+\]|\([^)]+\)|[^,;]+?)"
    r"(?=\s+(?:and|or)\s+[A-Za-z_]\w*\s*(?:like|in|==|=|!=|>=|<=|>|<)|[,;]|$)",
    flags=re.IGNORECASE,
)


FIELD_ALIASES = {
    "stk_code": "code",
    "stock_code": "code",
    "security_code": "code",
    "stk_name": "name",
    "stock_name": "name",
    "security_name": "name",
}


def _source(source: str, table: str, fields: Mapping[str, str]) -> StockCorporateSource:
    return StockCorporateSource(
        source=source,
        table=table,
        fields={
            "source": f"'{source}'",
            "code": "q.stk_code",
            "name": "b.stk_name",
            **fields,
        },
    )


STOCK_CORPORATE_VIEWS: Dict[str, StockCorporateView] = {
    "shareholder": StockCorporateView(
        dataview="shareholder",
        sources=[
            _source(
                "holderqty",
                "kcrp_stock_holderqty",
                {
                    "ann_date": "q.ann_date",
                    "end_date": "q.end_date",
                    "holder_num": "q.holder_num",
                    "holder_total_num": "q.holder_total_num",
                },
            ),
            _source(
                "holdertop",
                "kcrp_stock_holdertop",
                {
                    "ann_date": "q.ann_date",
                    "end_date": "q.end_date",
                    "report_period": "q.report_period",
                    "holder_name": "q.holder_name",
                    "holder_id": "q.holder_id",
                    "holder_type": "q.holder_type",
                    "holder_rank": "q.holder_rank",
                    "holder_quantity": "q.holder_quantity",
                    "holder_pct": "q.holder_pct",
                    "holder_restrictedquantity": "q.holder_restrictedquantity",
                    "ple_or_frz_shares": "q.ple_or_frz_shares",
                    "holder_accpledge": "q.holder_accpledge",
                    "holder_accfrozen": "q.holder_accfrozen",
                    "holder_pledge_ratio": "q.holder_pledge_ratio",
                    "share_type": "q.share_type",
                },
            ),
            _source(
                "holdertop1",
                "kcrp_stock_holdertop1",
                {
                    "ann_date": "q.ann_date",
                    "end_date": "q.end_date",
                    "report_period": "q.report_period",
                    "holder_name": "q.holder_name",
                    "holder_id": "q.holder_id",
                    "holder_type": "q.holder_type",
                    "holder_rank": "q.holder_rank",
                    "holder_quantity": "q.holder_quantity",
                    "holder_pct": "q.holder_pct",
                    "holder_restrictedquantity": "q.holder_restrictedquantity",
                    "ple_or_frz_shares": "q.ple_or_frz_shares",
                    "holder_accpledge": "q.holder_accpledge",
                    "holder_accfrozen": "q.holder_accfrozen",
                    "holder_pledge_ratio": "q.holder_pledge_ratio",
                    "share_type": "q.share_type",
                },
            ),
            _source(
                "holderfloat",
                "kcrp_stock_holderfloat",
                {
                    "ann_date": "q.ann_date",
                    "end_date": "q.end_date",
                    "report_period": "q.report_period",
                    "holder_name": "q.holder_name",
                    "holder_id": "q.holder_id",
                    "holder_type": "q.holder_type",
                    "holder_rank": "q.holder_rank",
                    "holder_quantity": "q.holder_quantity",
                    "holder_pct": "q.holder_pct",
                    "share_type": "q.share_type",
                },
            ),
        ],
        default_columns=["code", "name", "source", "ann_date", "end_date", "holder_num", "holder_name", "holder_rank", "holder_quantity", "holder_pct"],
        default_date_field="end_date",
        default_order=[("end_date", "desc"), ("ann_date", "desc")],
    ),
    "pledge": StockCorporateView(
        dataview="pledge",
        sources=[
            _source(
                "pledgeratio",
                "kcrp_stock_pledgeratio",
                {
                    "end_date": "q.end_date",
                    "share_unrestricted_num": "q.share_unrestricted_num",
                    "share_restricted_num": "q.share_restricted_num",
                    "pledge_num": "q.pledge_num",
                    "pledge_sharenum": "q.pledge_sharenum",
                    "pledge_ratio": "q.pledge_ratio",
                },
            ),
            _source(
                "pledgeinfo",
                "kcrp_stock_pledgeinfo",
                {
                    "ann_date": "q.ann_date",
                    "begin_date": "q.begin_date",
                    "end_date": "q.end_date",
                    "holder_name": "q.holder_name",
                    "pledge_shares": "q.pledge_shares",
                    "pledgor": "q.pledgor",
                    "discharge_date": "q.discharge_date",
                    "is_discharge": "q.is_discharge",
                    "holder_type": "q.holder_type",
                    "pledgor_type": "q.pledgor_type",
                    "share_nature": "q.share_nature",
                    "total_holding_shr": "q.total_holding_shr",
                    "total_pledge_shr": "q.total_pledge_shr",
                    "pledge_ratio_comp": "q.pledge_ratio_comp",
                    "amt_frozen_ratio": "q.amt_frozen_ratio",
                },
            ),
            _source(
                "pledge_frozen",
                "kcrp_stock_pledge_and_frozen",
                {
                    "ann_date": "q.ann_date",
                    "end_date": "q.end_date",
                    "holder_name": "q.holder_name",
                    "holder_id": "q.holder_id",
                    "pledge_total_num": "q.pledge_total_num",
                    "frozen_total_num": "q.frozen_total_num",
                    "holder_quantity": "q.holder_quantity",
                },
            ),
        ],
        default_columns=["code", "name", "source", "ann_date", "end_date", "holder_name", "pledge_ratio", "pledge_sharenum", "pledge_shares", "pledge_total_num", "frozen_total_num"],
        default_date_field="end_date",
        default_order=[("end_date", "desc"), ("ann_date", "desc")],
    ),
    "corporate_action": StockCorporateView(
        dataview="corporate_action",
        sources=[
            _source(
                "dividend",
                "kcrp_stock_dividend",
                {
                    "ann_date": "q.ann_date",
                    "end_date": "q.end_date",
                    "record_date": "q.record_date",
                    "ex_date": "q.ex_date",
                    "div_progress": "q.div_progress",
                    "per_shareqty": "q.per_shareqty",
                    "div_cash_pre_tax": "q.div_cash_pre_tax",
                    "div_cash_after_tax": "q.div_cash_after_tax",
                    "div_total_cash": "q.div_total_cash",
                    "div_payout_date": "q.div_payout_date",
                    "div_object": "q.div_object",
                    "div_conversed_rate": "q.div_conversed_rate",
                    "div_bonus_rate": "q.div_bonus_rate",
                },
            ),
            _source(
                "add_issue",
                "kcrp_stock_add_issue",
                {
                    "ann_date": "q.ann_date",
                    "record_date": "q.record_date",
                    "ex_date": "q.ex_date",
                    "list_date": "q.list_date",
                    "ai_type": "q.ai_type",
                    "ai_progress": "q.ai_progress",
                    "ai_price": "q.ai_price",
                    "ai_raised_funds": "q.ai_raised_funds",
                    "ai_amount": "q.ai_amount",
                    "ai_code": "q.ai_code",
                    "ai_name": "q.ai_name",
                },
            ),
            _source(
                "rights_issue",
                "kcrp_stock_rights_issue",
                {
                    "ann_date": "q.ann_date",
                    "record_date": "q.record_date",
                    "ex_date": "q.ex_date",
                    "list_date": "q.list_date",
                    "ri_progress": "q.ri_progress",
                    "ri_price": "q.ri_price",
                    "ri_raised_funds": "q.ri_raised_funds",
                    "ri_amount": "q.ri_amount",
                    "ri_amount_act": "q.ri_amount_act",
                    "ri_ratio": "q.ri_ratio",
                    "ri_ratio_act": "q.ri_ratio_act",
                },
            ),
            _source(
                "limit_share",
                "kcrp_stock_limitsharedetail",
                {
                    "ann_date": "q.ann_date",
                    "holder_name": "q.holder_name",
                    "free_date": "q.free_date",
                    "free_num": "q.free_num",
                    "flow_num": "q.flow_num",
                    "limited_num": "q.limited_num",
                    "limited_explain": "q.limited_explain",
                    "limited_source": "q.limited_source",
                    "issue_price": "q.issue_price",
                },
            ),
            _source(
                "ipo",
                "kcrp_stock_ipo",
                {
                    "list_date": "q.list_date",
                    "ipo_price": "q.ipo_price",
                    "online_issue_vol": "q.online_issue_vol",
                    "offline_issue_vol": "q.offline_issue_vol",
                    "ipo_collection": "q.ipo_collection",
                    "purchase_code": "q.purchase_code",
                    "purchase_name": "q.purchase_name",
                    "ipo_amount": "q.ipo_amount",
                    "online_max_apply": "q.online_max_apply",
                    "online_ratio": "q.online_ratio",
                    "intent_letter_pub_date": "q.intent_letter_pub_date",
                    "ipo_sub_date": "q.ipo_sub_date",
                    "par_value": "q.par_value",
                    "issue_vol": "q.issue_vol",
                    "issue_cost": "q.issue_cost",
                    "diluted_pe_ratio": "q.diluted_pe_ratio",
                    "ipo_type": "q.ipo_type",
                    "is_failure": "q.is_failure",
                    "listed_standard": "q.listed_standard",
                    "outstanding_shares": "q.outstanding_shares",
                    "issue_total_mv": "q.issue_total_mv",
                },
            ),
        ],
        default_columns=["code", "name", "source", "ann_date", "end_date", "record_date", "ex_date", "list_date", "div_cash_pre_tax", "div_bonus_rate", "ai_price", "ri_price", "free_date", "ipo_price"],
        default_date_field="ann_date",
        default_order=[("ann_date", "desc"), ("end_date", "desc"), ("list_date", "desc")],
    ),
    "performance_notice": StockCorporateView(
        dataview="performance_notice",
        sources=[
            _source(
                "profitnotice",
                "kcrp_stock_profitnotice",
                {
                    "report_period": "q.report_period",
                    "ann_date": "q.ann_date",
                    "currency": "q.currency_code",
                    "perf_type_code": "q.perf_type_code",
                    "performance_type": "q.performance_type",
                    "performance_content": "q.perfe_fore_content",
                    "performance_reason": "q.perf_chg_reason",
                    "net_profit_chg_ratio": "q.net_profit_chg_ratio_fore",
                    "net_profit_chg_lower": "q.fore_net_profit_chg_dl",
                    "net_profit_chg_upper": "q.fore_net_profit_chg_ul",
                    "performance_summary": "q.perf_fore_abstract",
                    "net_profit_lower": "q.fore_net_profit_dl",
                    "net_profit_upper": "q.net_profit_amt_ul_fore",
                    "last_year_net_profit": "q.net_profit_atsopc_last_year",
                    "deduct_np_lower": "q.fore_deduct_np_ll",
                    "deduct_np_upper": "q.fore_deduct_np_ul",
                    "last_year_deduct_np": "q.deduct_np_last_year",
                    "revenue_lower": "q.fore_oi_lower_limit",
                    "revenue_upper": "q.fore_oi_upper_limit",
                    "last_year_revenue": "q.oi_last_year",
                },
            )
        ],
        default_columns=["code", "name", "report_period", "ann_date", "performance_type", "performance_summary", "net_profit_chg_ratio", "net_profit_chg_lower", "net_profit_chg_upper"],
        default_date_field="ann_date",
        default_order=[("ann_date", "desc"), ("report_period", "desc")],
    ),
    "business_segment": StockCorporateView(
        dataview="business_segment",
        sources=[
            _source(
                "salessegment",
                "kcrp_stock_salessegment",
                {
                    "report_period": "q.report_period",
                    "segment_type": "q.segment_type",
                    "project_name": "q.project_name",
                    "project_speci_name": "q.project_speci_name",
                    "segment_sales": "q.segment_sales",
                    "segment_cost": "q.segment_cost",
                    "segment_profit": "q.segment_profit",
                    "gross_profit_margin": "q.gross_profit_margin",
                    "pct_segment_sales": "q.pct_segment_sales",
                    "pct_segment_profit": "q.pct_segment_profit",
                    "pct_segment_cost": "q.pct_segment_cost",
                    "inc_segment_sales": "q.inc_segment_sales",
                    "inc_segment_profit": "q.inc_segment_profit",
                    "inc_segment_cost": "q.inc_segment_cost",
                    "inc_profit_margin": "q.inc_profit_margin",
                    "project_level": "q.project_level",
                },
            ),
            _source(
                "top5opincome",
                "kcrp_stock_top5opincome",
                {
                    "report_period": "q.report_period",
                    "ann_date": "q.ann_date",
                    "info_compname": "q.info_compname",
                    "salesamount": "q.salesamount",
                    "pct": "q.pct",
                    "interchange_code": "q.interchange_code",
                },
            ),
        ],
        default_columns=["code", "name", "source", "report_period", "ann_date", "segment_type", "project_name", "segment_sales", "segment_profit", "gross_profit_margin", "info_compname", "salesamount", "pct"],
        default_date_field="report_period",
        default_order=[("report_period", "desc"), ("ann_date", "desc")],
    ),
}


def execute_stock_corporate_api(*, dataview: str, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    view = STOCK_CORPORATE_VIEWS.get(dataview)
    if not view:
        return _standard_result(status="unsupported", dataview=dataview, args=args, columns=[], rows=[], reason="unknown stock corporate dataview")

    columns = _requested_columns(view=view, outputs=outputs)
    if _has_unresolved_ref(args):
        return _standard_result(
            status="requires_upstream_values",
            dataview=dataview,
            args=args,
            columns=columns,
            rows=[],
            reason="filter contains result references that must be materialized by the execution engine",
        )

    limit = _bounded_limit(args.get("limit"))
    filter_fields = _filter_fields(view=view, args=args)
    order_fields = _order_fields(view=view, args=args)
    query_columns = _query_columns(columns=columns, filter_fields=filter_fields, order_fields=order_fields, default_order=view.default_order)
    where_sql, params = _build_where(view=view, args=args)
    order_sql = _build_order(view=view, args=args)
    sql = _build_sql(view=view, query_columns=query_columns, columns=columns, where_sql=where_sql, order_sql=order_sql)
    params.append(limit)

    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
        rows = [_normalize_row(row, columns) for row in raw_rows]
        return _standard_result(
            status="ok",
            dataview=dataview,
            args=args,
            columns=columns,
            rows=rows,
            source_tables=[source.table for source in view.sources],
            sql_shape={"where": where_sql, "order": order_sql, "limit": limit},
        )
    except Exception as exc:  # noqa: BLE001 - experiment boundary should return structured failures.
        return _standard_result(
            status="provider_error",
            dataview=dataview,
            args=args,
            columns=columns,
            rows=[],
            reason=str(exc),
            source_tables=[source.table for source in view.sources],
            sql_shape={"where": where_sql, "order": order_sql, "limit": limit},
        )
    finally:
        db.close_db()


def _requested_columns(*, view: StockCorporateView, outputs: List[str]) -> List[str]:
    columns: List[str] = []
    for output in outputs:
        token = _normalize_field(_output_token(output))
        if token in view.fields and token not in columns:
            columns.append(token)
    return columns or list(view.default_columns)


def _query_columns(
    *,
    columns: List[str],
    filter_fields: set[str],
    order_fields: set[str],
    default_order: List[tuple[str, str]],
) -> List[str]:
    rows = list(columns)
    for field_name in [*filter_fields, *order_fields, *(field for field, _direction in default_order)]:
        if field_name and field_name not in rows:
            rows.append(field_name)
    return rows


def _output_token(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text:
        text = text.split(" as ", 1)[0].strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _normalize_field(field_name: str) -> str:
    value = str(field_name or "").strip()
    return FIELD_ALIASES.get(value, value)


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


def _build_where(*, view: StockCorporateView, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    date_value = str(args.get("date") or args.get(view.default_date_field) or "").strip()
    start = str(args.get("start") or args.get("start_date") or "").strip()
    end = str(args.get("end") or args.get("end_date") or "").strip()
    if date_value and view.default_date_field in view.fields:
        clauses.append(f"u.`{view.default_date_field}` = %s")
        params.append(date_value)
    elif start and end and view.default_date_field in view.fields:
        clauses.append(f"u.`{view.default_date_field}` BETWEEN %s AND %s")
        params.extend(sorted([start, end]))

    filter_sql, filter_params = _build_filter_clauses(view=view, args=args)
    if filter_sql:
        clauses.append(filter_sql)
        params.extend(filter_params)
    return " AND ".join(clauses) or "1 = 1", params


def _build_filter_clauses(*, view: StockCorporateView, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    for connector, raw_field, op, value in _explicit_filters(args):
        field_name = _normalize_field(raw_field)
        if field_name not in view.fields:
            continue
        prefix = connector if clauses else ""
        if op == "in":
            values = _list_value(value)
            if not values:
                continue
            placeholders = ", ".join(["%s"] * len(values))
            clauses.append(f"{prefix} u.`{field_name}` IN ({placeholders})".strip())
            params.extend(values)
            continue
        if op not in OP_SQL:
            continue
        clauses.append(f"{prefix} u.`{field_name}` {OP_SQL[op]} %s".strip())
        params.append(value)
    if not clauses:
        return "", []
    return f"({' '.join(clauses)})", params


def _explicit_filters(args: Mapping[str, Any]) -> List[tuple[str, str, str, Any]]:
    rows: List[tuple[str, str, str, Any]] = []
    for field_name in ["code", "name", "source"]:
        value = args.get(field_name)
        if value not in (None, ""):
            rows.append(("AND", field_name, "=", value))
    for field_name in ["codes", "names", "sources"]:
        value = args.get(field_name)
        if value not in (None, ""):
            rows.append(("AND", field_name[:-1], "in", value))
    filter_text = str(args.get("filter") or "").strip()
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


def _filter_fields(*, view: StockCorporateView, args: Mapping[str, Any]) -> set[str]:
    fields = {_normalize_field(field_name) for _connector, field_name, _op, _value in _explicit_filters(args)}
    if args.get("date") not in (None, "") or args.get(view.default_date_field) not in (None, "") or (args.get("start") and args.get("end")):
        fields.add(view.default_date_field)
    return {field_name for field_name in fields if field_name in view.fields}


def _order_fields(*, view: StockCorporateView, args: Mapping[str, Any]) -> set[str]:
    text = str(args.get("order") or "").strip()
    if not text or text.lower() == "none":
        return {field_name for field_name, _direction in view.default_order if field_name in view.fields}
    field_name = _normalize_field(re.split(r"\s+", text, maxsplit=1)[0].strip())
    return {field_name} if field_name in view.fields else set()


def _clean_value(value: str) -> Any:
    return str(value or "").strip().strip("\"'")


def _list_value(value: Any) -> List[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    text = str(value or "").strip()
    if not text:
        return []
    if (text.startswith("[") and text.endswith("]")) or (text.startswith("(") and text.endswith(")")):
        text = text[1:-1]
    return [_clean_value(item) for item in text.split(",") if str(item).strip()]


def _build_order(*, view: StockCorporateView, args: Mapping[str, Any]) -> str:
    text = str(args.get("order") or "").strip()
    if text and text.lower() != "none":
        parts = re.split(r"\s+", text, maxsplit=1)
        field_name = _normalize_field(parts[0].strip())
        direction = parts[1].strip().lower() if len(parts) > 1 else "desc"
        if field_name in view.fields:
            direction_sql = "ASC" if direction.startswith("asc") else "DESC"
            return f"u.`{field_name}` {direction_sql}, " + _default_order_sql(view)
    return _default_order_sql(view)


def _default_order_sql(view: StockCorporateView) -> str:
    parts = []
    for field_name, direction in view.default_order:
        if field_name in view.fields:
            direction_sql = "ASC" if direction.lower().startswith("asc") else "DESC"
            parts.append(f"u.`{field_name}` {direction_sql}")
    return ", ".join(parts) or "u.`code` ASC"


def _build_sql(*, view: StockCorporateView, query_columns: List[str], columns: List[str], where_sql: str, order_sql: str) -> str:
    union_sql = "\nUNION ALL\n".join(_source_select_sql(source=source, columns=query_columns) for source in view.sources)
    select_sql = ", ".join(f"u.`{column}` AS `{column}`" for column in columns)
    return f"""
        SELECT {select_sql}
        FROM (
            {union_sql}
        ) u
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
    """


def _source_select_sql(*, source: StockCorporateSource, columns: List[str]) -> str:
    select_sql = ", ".join(f"{source.fields.get(column, 'NULL')} AS `{column}`" for column in columns)
    return f"""
        SELECT {select_sql}
        FROM {source.table} q
        LEFT JOIN kcrp_stock_baseinfo b ON b.stk_code = q.stk_code
    """


def _normalize_row(row: Mapping[str, Any], columns: List[str]) -> Dict[str, Any]:
    return {column: _normalize_value(row.get(column)) for column in columns}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _standard_result(
    *,
    status: str,
    dataview: str,
    args: Mapping[str, Any],
    columns: List[str],
    rows: List[Dict[str, Any]],
    reason: str | None = None,
    **extra: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": status,
        "api": f"stock.{dataview}",
        "subject": "stock",
        "dataview": dataview,
        "arguments": dict(args),
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "provider": "kingdomai_stock_corporate",
    }
    if reason:
        payload["reason"] = reason
    payload.update(extra)
    return payload

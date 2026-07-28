from __future__ import annotations

import os
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping
from urllib.parse import unquote, urlparse

import pymysql


REPORT_VIEW = "chatbi_report_v"
METRIC_VIEW = "chatbi_metric_v"
METRIC_CODE_NAMES = {
    "eps": "每股收益",
    "np_parent": "归母净利润",
    "np_parent_growth": "归母净利润增长率",
    "pb": "市净率",
    "pe": "市盈率",
    "revenue": "营业收入",
    "revenue_growth": "营业收入增长率",
    "roe": "净资产收益率",
}
METRIC_CODE_ALIASES = {
    **{code: code for code in METRIC_CODE_NAMES},
    **{name: code for code, name in METRIC_CODE_NAMES.items()},
    "EPS": "eps",
    "PE": "pe",
    "PB": "pb",
    "ROE": "roe",
}
METRIC_NAME_ALIASES = {
    **{name: name for name in METRIC_CODE_NAMES.values()},
    "EPS": "每股收益",
    "净利润": "归母净利润",
    "净利润增长率": "归母净利润增长率",
    "收入": "营业收入",
    "收入增长率": "营业收入增长率",
}

# The database views use Chinese column aliases.  Keep the protocol fields
# stable and deliberately do not expose the physical view column names.
REPORT_FIELDS: Dict[str, str] = {
    "report_id": "`r`.`研报ID`",
    "code": "`r`.`股票代码`",
    "name": "`r`.`公司名称`",
    "institution": "`r`.`研究机构`",
    "analyst": "`r`.`研究员`",
    "report_date": "`r`.`报告发布日期`",
    "rating": "`r`.`评级`",
    "rating_change": "`r`.`变动`",
    "change_reason": "`r`.`变动原因`",
    "investment_highlights": "`r`.`投资要点`",
    "risk_warnings": "`r`.`风险提示`",
    # The current view aliases are reversed.  These mappings preserve the
    # semantic API contract without changing the external report database.
    "target_price_lower": "`r`.`预测最高价`",
    "target_price_upper": "`r`.`预测最低价`",
}

METRIC_FIELDS: Dict[str, str] = {
    "report_id": "`m`.`研报ID`",
    "code": "`m`.`股票代码`",
    "name": "`m`.`公司名称`",
    "institution": "`m`.`研究机构`",
    "report_date": "`m`.`报告发布日期`",
    "metric_code": "`m`.`指标编码`",
    "metric_name": "`m`.`指标名称`",
    "forecast_year": "`m`.`预测年份`",
    "value_type": "`m`.`数值类型`",
    "metric_value": "`m`.`指标数值`",
    "unit": "`m`.`单位名称`",
    "source_locator": "`m`.`来源定位`",
    "created_at": "`m`.`创建时间`",
}

ALL_FIELDS = {**REPORT_FIELDS, **METRIC_FIELDS}
REPORT_ONLY_FIELDS = set(REPORT_FIELDS) - {"report_id", "code", "name", "institution", "report_date"}
METRIC_ONLY_FIELDS = set(METRIC_FIELDS) - {"report_id", "code", "name", "institution", "report_date"}
FIELD_ALIASES = {
    "stock_code": "code",
    "security_code": "code",
    "stock_name": "name",
    "security_name": "name",
    "publish_date": "report_date",
    "publish_time": "report_date",
    "report_period": "report_date",
    "metric": "metric_code",
    "value": "metric_value",
    "target_low": "target_price_lower",
    "target_high": "target_price_upper",
}

OPS = {"=": "=", "==": "=", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<=", "like": "LIKE"}
FILTER_RE = re.compile(
    r"(?:(?P<connector>\band\b|\bor\b)\s+)?"
    r"(?P<field>[A-Za-z_]\w*)\s*"
    r"(?P<op>in|like|==|!=|>=|<=|=|>|<)\s*"
    r"(?P<value>\[[^\]]+\]|\([^)]*\)|[^,;]+?)"
    r"(?=\s+(?:and|or)\s+[A-Za-z_]\w*\s*(?:in|like|==|!=|>=|<=|=|>|<)|[,;]|$)",
    flags=re.IGNORECASE,
)


def execute_report_api(*, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    requested = _requested_fields(outputs)
    if not requested:
        requested = ["report_id", "code", "name", "institution", "report_date", "rating"]
    referenced = _referenced_fields(args)
    metric_requested = bool((set(requested) | referenced) & METRIC_ONLY_FIELDS)
    report_requested = bool((set(requested) | referenced) & REPORT_ONLY_FIELDS)
    join_report = metric_requested and report_requested
    source_fields = _field_map(join_report=join_report, metric_requested=metric_requested)
    invalid = sorted(set(requested) - set(source_fields))
    if invalid:
        return _result("unsupported", args, requested, [], f"unsupported report fields: {invalid}")
    try:
        where_sql, params = _build_filter(args=args, join_report=join_report, metric_requested=metric_requested)
    except ValueError as exc:
        return _result("unsupported", args, requested, [], str(exc))
    order_sql = _build_order(args=args, join_report=join_report, metric_requested=metric_requested)
    limit = _bounded_limit(args.get("limit"))
    select_sql = ", ".join(f"{source_fields[field]} AS `{field}`" for field in requested)
    from_sql = f"`{METRIC_VIEW}` AS `m`"
    if join_report:
        from_sql += f" LEFT JOIN `{REPORT_VIEW}` AS `r` ON `m`.`研报ID` = `r`.`研报ID`"
    elif not metric_requested:
        from_sql = f"`{REPORT_VIEW}` AS `r`"
    sql = f"SELECT {select_sql} FROM {from_sql} WHERE {where_sql} ORDER BY {order_sql} LIMIT %s"
    params.append(limit)

    try:
        db = _connect_report_db()
    except Exception as exc:  # noqa: BLE001 - provider boundary returns a stable error.
        return _result("provider_error", args, requested, [], f"report database connection failed: {exc}")
    try:
        with db.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
        rows = [{column: _normalize_value(row.get(column)) for column in requested} for row in raw_rows]
        return _result(
            "ok",
            args,
            requested,
            rows,
            sql_shape={"source": METRIC_VIEW if metric_requested else REPORT_VIEW, "joined_report": join_report, "where": where_sql, "order": order_sql, "limit": limit},
        )
    except Exception as exc:  # noqa: BLE001 - provider boundary returns a stable error.
        return _result("provider_error", args, requested, [], f"report query failed: {exc}")
    finally:
        db.close()


def _connect_report_db():
    raw_url = str(os.getenv("REPORT_DB_URL") or "").strip()
    if not raw_url:
        raise RuntimeError("REPORT_DB_URL is not configured")
    parsed = urlparse(raw_url.replace("mysql+pymysql://", "mysql://", 1))
    if parsed.scheme != "mysql" or not parsed.hostname or not parsed.path.strip("/"):
        raise RuntimeError("REPORT_DB_URL must be a MySQL URL")
    return pymysql.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=parsed.path.lstrip("/"),
        charset="utf8mb4",
        connect_timeout=20,
        read_timeout=60,
        write_timeout=60,
    )


def _requested_fields(outputs: List[str]) -> List[str]:
    fields: List[str] = []
    for output in outputs:
        text = str(output or "").strip()
        if " as " in text.lower():
            text = re.split(r"\s+as\s+", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        token = text.rsplit(".", 1)[-1]
        token = FIELD_ALIASES.get(token, token)
        if token in ALL_FIELDS and token not in fields:
            fields.append(token)
    return fields


def _referenced_fields(args: Mapping[str, Any]) -> set[str]:
    found: set[str] = set()
    for key in ("filter", "order"):
        text = str(args.get(key) or "")
        for token in re.findall(r"\b[A-Za-z_]\w*\b", text):
            token = FIELD_ALIASES.get(token, token)
            if token in ALL_FIELDS:
                found.add(token)
    return found


def _field_map(*, join_report: bool, metric_requested: bool) -> Dict[str, str]:
    if metric_requested and join_report:
        result = dict(METRIC_FIELDS)
        result.update({key: value for key, value in REPORT_FIELDS.items() if key not in result})
        return result
    return dict(METRIC_FIELDS if metric_requested else REPORT_FIELDS)


def _build_filter(*, args: Mapping[str, Any], join_report: bool, metric_requested: bool) -> tuple[str, List[Any]]:
    fields = _field_map(join_report=join_report, metric_requested=metric_requested)
    clauses: List[str] = []
    params: List[Any] = []
    for connector, field, op, value in _explicit_filters(str(args.get("filter") or "")):
        field = FIELD_ALIASES.get(field, field)
        expression = fields.get(field)
        if not expression or op not in OPS:
            continue
        # Report timestamps are stored at a time-of-day (often 16:00).  A
        # date-only protocol value means the calendar day, so compare DATE()
        # rather than accidentally excluding the requested end date.
        if field == "report_date" and _is_date_only(value):
            expression = f"DATE({expression})"
        prefix = connector if clauses else ""
        if op == "in":
            values = [_normalize_filter_value(field, item) for item in _list_value(value)]
            if not values:
                continue
            clauses.append(f"{prefix} {expression} IN ({', '.join(['%s'] * len(values))})".strip())
            params.extend(values)
        elif op == "like":
            clauses.append(f"{prefix} {expression} LIKE %s".strip())
            params.append(f"%{value}%")
        else:
            clauses.append(f"{prefix} {expression} {OPS[op]} %s".strip())
            params.append(_normalize_filter_value(field, value))
    return (" ".join(clauses) if clauses else "1=1"), params


def _explicit_filters(text: str) -> List[tuple[str, str, str, Any]]:
    rows: List[tuple[str, str, str, Any]] = []
    for match in FILTER_RE.finditer(text):
        rows.append((str(match.group("connector") or "AND").upper(), str(match.group("field") or "").strip(), str(match.group("op") or "").lower(), _clean_value(str(match.group("value") or ""))))
    return rows


def _build_order(*, args: Mapping[str, Any], join_report: bool, metric_requested: bool) -> str:
    fields = _field_map(join_report=join_report, metric_requested=metric_requested)
    parts: List[str] = []
    for item in str(args.get("order") or "").split(","):
        tokens = re.split(r"\s+", item.strip(), maxsplit=1)
        field = FIELD_ALIASES.get(tokens[0], tokens[0]) if tokens and tokens[0] else ""
        if field not in fields:
            continue
        direction = "ASC" if len(tokens) > 1 and tokens[1].lower().startswith("asc") else "DESC"
        parts.append(f"{fields[field]} {direction}")
    if parts:
        return ", ".join(parts)
    return "`m`.`报告发布日期` DESC, `m`.`研报ID` DESC" if metric_requested else "`r`.`报告发布日期` DESC, `r`.`研报ID` DESC"


def _clean_value(value: str) -> Any:
    text = str(value or "").strip().strip("\"'")
    try:
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        if re.fullmatch(r"-?\d+\.\d+", text):
            return float(text)
    except ValueError:
        pass
    return text


def _is_date_only(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}|\d{8}", str(value or "").strip()))


def _normalize_filter_value(field: str, value: Any) -> Any:
    """Adapt the common ``600519.SH`` protocol identity to report DB codes.

    The report views store six-digit codes, while the rest of the finance
    catalog conventionally uses ``CODE.EXCHANGE``.  This belongs at the
    provider boundary so every report query gets the same behavior.
    """
    if field != "code":
        if field == "metric_code":
            text = str(value or "").strip()
            normalized = METRIC_CODE_ALIASES.get(text) or METRIC_CODE_ALIASES.get(text.upper())
            if not normalized:
                allowed = ", ".join(f"{code}({name})" for code, name in METRIC_CODE_NAMES.items())
                raise ValueError(f"metric_code must be one of: {allowed}")
            return normalized
        if field == "metric_name":
            text = str(value or "").strip()
            normalized = METRIC_NAME_ALIASES.get(text) or METRIC_NAME_ALIASES.get(text.upper())
            if not normalized:
                allowed = ", ".join(METRIC_CODE_NAMES.values())
                raise ValueError(f"metric_name must be one of: {allowed}")
            return normalized
        return value
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{6})\.(?:SH|SZ|BJ)", text, flags=re.IGNORECASE)
    return match.group(1) if match else value


def _list_value(value: Any) -> List[Any]:
    text = str(value or "").strip()
    if text[:1] in "[(" and text[-1:] in ")]":
        text = text[1:-1]
    return [_clean_value(item) for item in text.split(",") if str(item).strip()]


def _bounded_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = 100
    return 500 if parsed <= 0 else min(parsed, 500)


def _normalize_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _result(
    status: str,
    args: Mapping[str, Any],
    columns: List[str],
    rows: List[Dict[str, Any]],
    reason: str | Mapping[str, Any] = "",
    *,
    sql_shape: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": status,
        "api": "stock.report",
        "subject": "stock",
        "dataview": "report",
        "source": [REPORT_VIEW, METRIC_VIEW],
        "arguments": dict(args),
        "columns": columns,
        "available_fields": sorted(ALL_FIELDS),
        "rows": rows,
        "row_count": len(rows),
    }
    if sql_shape is not None:
        result["sql_shape"] = dict(sql_shape)
    elif isinstance(reason, Mapping):
        result["sql_shape"] = dict(reason)
    elif reason:
        result["reason"] = reason
    return result

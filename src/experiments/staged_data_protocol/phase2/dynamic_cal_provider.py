from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping

import numpy as np
import pandas as pd
import pymysql

from src.experiments.staged_data_protocol.phase2.intraday_quote_provider import (
    MINUTE_SESSION_START,
    _minute_bar_cte,
)
from src.experiments.staged_data_protocol.phase2.quote_provider import (
    QUOTE_SOURCES,
    TRADE_CALENDAR_TABLE,
    QuoteSource,
    _build_identity_where,
    _calendar_as_of,
    _calendar_market_code,
    _has_unresolved_ref,
    _normalize_value,
)
from src.utils.mysql_utils import StockInfoDbUtils


PROMPT_PATH = Path("phase2_dynamic_cal_code_prompt.md")
REALTIME_SNAPSHOT_TABLE = "aiia_stock_realtime_minute_snapshot"
REALTIME_FIELDS = {
    "code",
    "name",
    "tradedate",
    "minute_index",
    "minute_time",
    "snapshot_time",
    "snapshot_slot",
    "preclose",
    "open",
    "close",
    "high",
    "low",
    "differ",
    "pct",
    "amount",
    "volumn",
    "minute_amount",
    "minute_volumn",
    "source",
    "is_fallback",
}
REALTIME_ONLY_FIELDS = {
    "minute_index",
    "minute_time",
    "snapshot_time",
    "snapshot_slot",
    "minute_amount",
    "minute_volumn",
    "source",
    "is_fallback",
}
REALTIME_QUOTE_SOURCE = QuoteSource(
    subject="stock",
    table=REALTIME_SNAPSHOT_TABLE,
    base_table=REALTIME_SNAPSHOT_TABLE,
    join_on="",
    fields={field: field for field in REALTIME_FIELDS},
)
BLOCKED_CALLS = {"eval", "exec", "open", "compile", "__import__", "globals", "locals", "vars", "dir", "input"}
SAFE_BUILTINS = {
    "abs": abs,
    "bool": bool,
    "dict": dict,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "set": set,
    "str": str,
    "sum": sum,
    "tuple": tuple,
}


def execute_dynamic_quote_api(*, subject: str, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    source = QUOTE_SOURCES.get(subject)
    api_name = f"{subject}.quote.dynamic_cal"
    output_columns = [_output_column(item) for item in outputs]
    if not source:
        return {
            "status": "unsupported",
            "api": api_name,
            "subject": subject,
            "reason": "dynamic_cal quote provider is only available for configured quote subjects",
            "rows": [],
        }
    if _has_unresolved_ref(args):
        return _standard_result(
            status="requires_upstream_values",
            source=source,
            args=args,
            columns=output_columns,
            api_name=api_name,
            reason="filter contains result references that must be materialized by the execution engine",
        )

    task = str(args.get("task") or "").strip()
    result_source = source
    if subject == "stock" and _is_realtime(args):
        result_source = REALTIME_QUOTE_SOURCE
        fields = _dynamic_realtime_fields(args=args)
        raw_rows = _load_realtime_quote_window_rows(args=args, fields=fields)
    else:
        fields = _dynamic_fields(source=source, args=args)
        raw_rows = _load_quote_window_rows(source=source, args=args, fields=fields)
    if isinstance(raw_rows, dict):
        return _standard_result(
            status=str(raw_rows.get("status") or "provider_error"),
            source=result_source,
            args=args,
            columns=output_columns,
            api_name=api_name,
            reason=str(raw_rows.get("reason") or ""),
        )

    df = pd.DataFrame(raw_rows)
    if df.empty:
        return _standard_result(status="ok", source=result_source, args=args, columns=output_columns, api_name=api_name, rows=[])

    code_result = _generate_compute_code(task=task, columns=fields, output_columns=output_columns)
    if code_result.get("status") != "ok":
        return _standard_result(
            status=str(code_result.get("status") or "codegen_error"),
            source=result_source,
            args=args,
            columns=output_columns,
            api_name=api_name,
            reason=str(code_result.get("reason") or ""),
            code=str(code_result.get("code") or ""),
            output_schema=_safe_schema(code_result.get("output_schema")),
        )
    code = str(code_result["code"])
    output_schema = _safe_schema(code_result.get("output_schema"))
    run_result = _run_compute_code(code=code, df=df)
    if run_result.get("status") != "ok":
        return _standard_result(
            status=str(run_result.get("status") or "runtime_error"),
            source=result_source,
            args=args,
            columns=output_columns,
            api_name=api_name,
            reason=str(run_result.get("reason") or ""),
            code=code,
            output_schema=output_schema,
        )

    try:
        result_df = _normalize_result_df(run_result["df"], output_columns=output_columns, args=args)
    except Exception as exc:  # noqa: BLE001 - output projection errors should remain inspectable.
        return _standard_result(
            status="output_validation_error",
            source=result_source,
            args=args,
            columns=output_columns,
            api_name=api_name,
            reason=str(exc),
            code=code,
            output_schema=output_schema,
        )
    return _standard_result(
        status="ok",
        source=result_source,
        args=args,
        columns=output_columns,
        api_name=api_name,
        rows=result_df.to_dict(orient="records"),
        code=code,
        output_schema=output_schema,
        raw_row_count=len(df),
    )


def _load_quote_window_rows(*, source: QuoteSource, args: Mapping[str, Any], fields: List[str]) -> List[Dict[str, Any]] | Dict[str, str]:
    k = _bounded_k(args.get("k"))
    max_rows = _bounded_max_rows(args.get("max_rows"))
    identity_sql, identity_params = _build_identity_where(source=source, args=args)
    select_sql = ", ".join(f"{source.fields[field]} AS `{field}`" for field in fields)
    sql = f"""
        {_kd_dates_cte()}
        SELECT {select_sql}
        FROM {source.table} q
        LEFT JOIN {source.base_table} b ON {source.join_on}
        WHERE {source.fields['tradedate']} IN (SELECT trade_date FROM kd_dates)
        {identity_sql}
        ORDER BY {source.fields['code']} ASC, {source.fields['tradedate']} ASC
        LIMIT %s
    """
    params = [_calendar_market_code(args), _calendar_market_code(args), _calendar_as_of(args), k, *identity_params, max_rows]
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return [{field: _normalize_value(row.get(field)) for field in fields} for row in rows]
    except Exception as exc:  # noqa: BLE001 - experiment boundary returns structured failures.
        return {"status": "provider_error", "reason": str(exc)}
    finally:
        db.close_db()


def _load_realtime_quote_window_rows(*, args: Mapping[str, Any], fields: List[str]) -> List[Dict[str, Any]] | Dict[str, str]:
    k = _bounded_k(args.get("k"))
    max_rows = _bounded_max_rows(args.get("max_rows"))
    slot = _realtime_slot(args)
    if not slot.get("trade_date") or not slot.get("minute_index"):
        return []
    history_dates = _realtime_history_dates(anchor_date=str(slot["trade_date"]), k=k)
    trade_dates = [str(slot["trade_date"]), *history_dates]
    placeholders = ", ".join(["%s"] * len(trade_dates))
    identity_sql, identity_params = _realtime_identity_where(args)
    select_sql = ", ".join(f"`{field}`" for field in fields)
    sql = f"""
        {_minute_bar_cte(date_predicate=f"s.trade_date IN ({placeholders})")}
        SELECT {select_sql}
        FROM base
        WHERE minute_index = %s {identity_sql}
        ORDER BY code ASC, tradedate ASC
        LIMIT %s
    """
    params = [*trade_dates, int(slot["minute_index"]), *identity_params, max_rows]
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return [{field: _normalize_value(row.get(field)) for field in fields} for row in rows]
    except Exception as exc:  # noqa: BLE001
        return {"status": "provider_error", "reason": str(exc)}
    finally:
        db.close_db()


def _realtime_slot(args: Mapping[str, Any]) -> Dict[str, Any]:
    filters = _simple_filter_items(str(args.get("filter") or ""))
    requested_date = ""
    requested_minute = None
    requested_slot = ""
    for field_name, op, value in filters:
        if field_name in {"tradedate", "trade_date"} and op in {"=", "=="} and not value.startswith("-"):
            requested_date = value[:10]
        elif field_name == "minute_index" and op in {"=", "=="}:
            try:
                requested_minute = int(value)
            except Exception:
                requested_minute = None
        elif field_name == "snapshot_slot" and op in {"=", "=="}:
            requested_slot = value

    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if requested_date:
                trade_date = requested_date
            else:
                cursor.execute(f"SELECT MAX(trade_date) AS trade_date FROM {REALTIME_SNAPSHOT_TABLE}")
                trade_date = str((cursor.fetchone() or {}).get("trade_date") or "")
            if requested_minute is not None:
                minute_index = requested_minute
            elif requested_slot:
                cursor.execute(
                    f"""
                    SELECT MAX(minute_index) AS minute_index
                    FROM {REALTIME_SNAPSHOT_TABLE}
                    WHERE trade_date = %s
                      AND snapshot_slot = %s
                      AND TIME(snapshot_time) >= '{MINUTE_SESSION_START}'
                    """,
                    (trade_date, requested_slot),
                )
                minute_index = int((cursor.fetchone() or {}).get("minute_index") or 0)
            else:
                cursor.execute(
                    f"""
                    SELECT MAX(minute_index) AS minute_index
                    FROM {REALTIME_SNAPSHOT_TABLE}
                    WHERE trade_date = %s
                      AND TIME(snapshot_time) >= '{MINUTE_SESSION_START}'
                    """,
                    (trade_date,),
                )
                minute_index = int((cursor.fetchone() or {}).get("minute_index") or 0)
    finally:
        db.close_db()
    return {"trade_date": trade_date, "minute_index": minute_index}


def _realtime_history_dates(*, anchor_date: str, k: int) -> List[str]:
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                f"""
                SELECT trade_date
                FROM {REALTIME_SNAPSHOT_TABLE}
                WHERE trade_date < %s
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT %s
                """,
                (anchor_date, k),
            )
            return [str(row.get("trade_date")) for row in cursor.fetchall() if row.get("trade_date")]
    finally:
        db.close_db()


def _realtime_identity_where(args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    for field_name, op, value in _simple_filter_items(str(args.get("filter") or "")):
        if field_name not in {"code", "name"}:
            continue
        if op == "in":
            values = [_code6(item) if field_name == "code" else item for item in _list_values(value)]
            if not values:
                continue
            clauses.append(f"AND `{field_name}` IN ({', '.join(['%s'] * len(values))})")
            params.extend(values)
        elif op in {"=", "=="}:
            clauses.append(f"AND `{field_name}` = %s")
            params.append(_code6(value) if field_name == "code" else value)
    return (" ".join(clauses), params)


def _generate_compute_code(*, task: str, columns: List[str], output_columns: List[str]) -> Dict[str, Any]:
    from src.utils.ai_service import chat_qwen_flash

    prompt = (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{columns}}", ", ".join(columns))
        .replace("{{output_columns}}", ", ".join(output_columns))
        .replace("{{task}}", task)
    )
    try:
        raw, _usage = chat_qwen_flash([{"role": "user", "content": prompt}], enable_think=False)
    except Exception as exc:  # noqa: BLE001 - keep provider result inspectable.
        return {"status": "codegen_error", "reason": str(exc), "code": ""}
    parsed = _parse_codegen_response(str(raw or ""))
    if parsed.get("status") != "ok":
        return parsed
    code = str(parsed.get("code") or "")
    output_schema = _safe_schema(parsed.get("output_schema"))
    schema_errors = _validate_output_schema(output_schema, output_columns=output_columns, base_columns=columns)
    if schema_errors:
        return {"status": "schema_validation_error", "reason": "; ".join(schema_errors), "code": code, "output_schema": output_schema}
    errors = _validate_code_ast(code)
    if errors:
        return {"status": "code_validation_error", "reason": "; ".join(errors), "code": code, "output_schema": output_schema}
    return {"status": "ok", "code": code, "output_schema": output_schema}


def _run_compute_code(*, code: str, df: pd.DataFrame) -> Dict[str, Any]:
    namespace: Dict[str, Any] = {}
    try:
        exec(compile(code, "<dynamic_cal>", "exec"), {"__builtins__": SAFE_BUILTINS, "pd": pd, "np": np}, namespace)
        compute = namespace.get("compute")
        if not callable(compute):
            return {"status": "runtime_error", "reason": "compute(df) is not defined"}
        result = compute(df.copy())
        if not isinstance(result, pd.DataFrame):
            return {"status": "runtime_error", "reason": "compute(df) must return a pandas DataFrame"}
        return {"status": "ok", "df": result}
    except Exception as exc:  # noqa: BLE001 - keep dynamic runtime errors inspectable.
        return {"status": "runtime_error", "reason": str(exc)}


def _validate_code_ast(code: str) -> List[str]:
    if not code.strip():
        return ["empty code"]
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]
    function_defs = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(function_defs) != 1 or function_defs[0].name != "compute":
        return ["code must define exactly one compute(df) function"]
    if any(not isinstance(node, ast.FunctionDef) for node in tree.body):
        return ["top-level code other than compute(df) is not allowed"]
    errors: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            errors.append("imports are not allowed")
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in BLOCKED_CALLS:
                errors.append(f"blocked call: {name}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            errors.append("dunder attributes are not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            errors.append("dunder names are not allowed")
    return errors


def _normalize_result_df(df: pd.DataFrame, *, output_columns: List[str], args: Mapping[str, Any]) -> pd.DataFrame:
    rows = df.copy()
    if "value" in rows.columns:
        rows = _apply_value_filters(rows, str(args.get("filter") or ""))
    rows = _apply_order(rows, str(args.get("order") or ""))
    limit = _bounded_output_limit(args.get("limit"))
    if limit > 0:
        rows = rows.head(limit)
    for column in output_columns:
        if column not in rows.columns and column != "value" and "value" in rows.columns:
            rows[column] = rows["value"]
    missing = [column for column in output_columns if column not in rows.columns]
    if missing:
        raise ValueError(f"dynamic result missing output columns: {missing}")
    return rows[output_columns]


def _apply_value_filters(df: pd.DataFrame, filter_text: str) -> pd.DataFrame:
    rows = df
    for op, value in re.findall(r"\bvalue\s*(>=|<=|>|<|=|==|!=)\s*(-?\d+(?:\.\d+)?)", filter_text, flags=re.IGNORECASE):
        number = float(value)
        if op in {"=", "=="}:
            rows = rows[rows["value"] == number]
        elif op == "!=":
            rows = rows[rows["value"] != number]
        elif op == ">":
            rows = rows[rows["value"] > number]
        elif op == ">=":
            rows = rows[rows["value"] >= number]
        elif op == "<":
            rows = rows[rows["value"] < number]
        elif op == "<=":
            rows = rows[rows["value"] <= number]
    return rows


def _apply_order(df: pd.DataFrame, order_text: str) -> pd.DataFrame:
    text = str(order_text or "").strip()
    if not text or text.lower() == "none":
        return df
    parts = text.split()
    field = parts[0]
    ascending = len(parts) > 1 and parts[1].lower().startswith("asc")
    if field not in df.columns:
        field = "value" if "value" in df.columns else ""
    return df.sort_values(field, ascending=ascending) if field else df


def _dynamic_fields(*, source: QuoteSource, args: Mapping[str, Any]) -> List[str]:
    requested = _field_list(args.get("fields"))
    fields = [field for field in requested if field in source.fields]
    if "code" not in fields and "code" in source.fields:
        fields.insert(0, "code")
    if "name" not in fields and "name" in source.fields:
        fields.insert(1 if fields and fields[0] == "code" else 0, "name")
    if fields:
        return fields
    return [field for field in ["code", "name", "tradedate", "open", "close", "high", "low", "pct", "amount", "volumn"] if field in source.fields]


def _dynamic_realtime_fields(*, args: Mapping[str, Any]) -> List[str]:
    requested = _field_list(args.get("fields"))
    fields = [field for field in requested if field in REALTIME_FIELDS]
    if "code" not in fields:
        fields.insert(0, "code")
    if "name" not in fields:
        fields.insert(1 if fields and fields[0] == "code" else 0, "name")
    if fields:
        return fields
    return ["code", "name", "tradedate", "minute_index", "snapshot_slot", "close", "pct", "amount", "volumn", "minute_amount", "minute_volumn"]


def _field_list(raw_fields: object) -> List[str]:
    if isinstance(raw_fields, (list, tuple, set)):
        return [str(item).strip() for item in raw_fields if str(item).strip()]
    return [item.strip() for item in str(raw_fields or "").split(",") if item.strip()]


def _is_realtime(args: Mapping[str, Any]) -> bool:
    requested_fields = {field.strip() for field in _field_list(args.get("fields"))}
    if requested_fields & REALTIME_ONLY_FIELDS:
        return True
    raw = args.get("realtime", 1)
    if raw in (None, ""):
        return True
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return int(raw) in {1, 2}
    text = str(raw).strip().lower()
    if text in {"0", "false", "no", "history", "historical"}:
        return False
    return True


def _simple_filter_items(filter_text: str) -> List[tuple[str, str, str]]:
    rows: List[tuple[str, str, str]] = []
    for match in re.finditer(
        r"(?P<field>[A-Za-z_]\w*)\s*(?P<op>in|=|==|!=|>=|<=|>|<)\s*(?P<value>\[[^\]]+\]|\([^)]+\)|[^,;]+?)(?=\s+(?:and|or)\s+[A-Za-z_]\w*\s*(?:in|=|==|!=|>=|<=|>|<)|[,;]|$)",
        str(filter_text or ""),
        flags=re.IGNORECASE,
    ):
        rows.append((str(match.group("field") or "").strip(), str(match.group("op") or "").strip().lower(), _clean_filter_value(str(match.group("value") or ""))))
    return rows


def _list_values(value: str) -> List[str]:
    text = _clean_filter_value(value)
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return [_clean_filter_value(item) for item in text.split(",") if _clean_filter_value(item)]


def _clean_filter_value(value: str) -> str:
    text = str(value or "").strip()
    if (text.startswith("'") and text.endswith("'")) or (text.startswith('"') and text.endswith('"')):
        text = text[1:-1]
    return text.strip()


def _code6(value: str) -> str:
    text = str(value or "").strip()
    if "." in text:
        text = text.split(".", 1)[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else text


def _parse_codegen_response(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    json_text = _extract_json_text(raw)
    try:
        payload = json.loads(json_text)
    except Exception as exc:  # noqa: BLE001 - keep raw response inspectable.
        legacy_code = _extract_code(raw)
        if legacy_code.startswith("def compute"):
            return {
                "status": "schema_validation_error",
                "reason": f"missing JSON output_schema: {exc}",
                "code": legacy_code,
                "output_schema": [],
            }
        return {"status": "codegen_parse_error", "reason": str(exc), "code": raw, "output_schema": []}
    if not isinstance(payload, Mapping):
        return {"status": "codegen_parse_error", "reason": "codegen response must be a JSON object", "code": raw, "output_schema": []}
    return {
        "status": "ok",
        "code": str(payload.get("code") or "").strip(),
        "output_schema": _safe_schema(payload.get("output_schema")),
    }


def _extract_json_text(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1].strip()
    return text.strip()


def _extract_code(text: str) -> str:
    fenced = re.search(r"```(?:python)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    return fenced.group(1).strip() if fenced else text.strip()


def _safe_schema(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            {
                "output_var_name": str(item.get("output_var_name") or item.get("var_name") or "").strip(),
                "output_var_display_name": str(
                    item.get("output_var_display_name") or item.get("var_display_name") or ""
                ).strip(),
                "output_var_desc": str(item.get("output_var_desc") or item.get("var_desc") or "").strip(),
            }
        )
    return rows


def _validate_output_schema(
    output_schema: List[Dict[str, str]], *, output_columns: List[str], base_columns: List[str]
) -> List[str]:
    errors: List[str] = []
    schema_names = {item.get("output_var_name") for item in output_schema if item.get("output_var_name")}
    system_columns = set(base_columns) | {"k", "end_date", "tradedate", "window_count"}
    required_schema_columns = [column for column in output_columns if column not in system_columns]
    for column in output_columns:
        if column in required_schema_columns and column not in schema_names:
            errors.append(f"output_schema missing output_var_name={column}")
    for item in output_schema:
        name = item.get("output_var_name") or ""
        if name and name not in set(output_columns):
            errors.append(f"output_schema has unknown output_var_name={name}")
        if name and not re.match(r"^[A-Za-z_]\w*$", name):
            errors.append(f"output_schema output_var_name={name} must be a valid identifier")
        if name and not item.get("output_var_display_name"):
            errors.append(f"output_schema {name} missing output_var_display_name")
        if name and not item.get("output_var_desc"):
            errors.append(f"output_schema {name} missing output_var_desc")
    return errors


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _output_column(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text:
        return text.split(" as ", 1)[1].strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _bounded_k(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = 20
    return max(1, min(parsed, 250))


def _bounded_max_rows(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = 200000
    return max(100, min(parsed, 200000))


def _bounded_output_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = -1
    return min(parsed, 10000) if parsed > 0 else -1


def _kd_dates_cte() -> str:
    return f"""
        WITH kd_dates AS (
            SELECT calendar_date AS trade_date
            FROM {TRADE_CALENDAR_TABLE}
            WHERE market_code = %s
              AND is_trade_day = 1
              AND trade_seq <= (
                  SELECT trade_seq
                  FROM {TRADE_CALENDAR_TABLE}
                  WHERE market_code = %s
                    AND is_trade_day = 1
                    AND calendar_date < %s
                  ORDER BY calendar_date DESC
                  LIMIT 1
              )
            ORDER BY trade_seq DESC
            LIMIT %s
        )
    """


def _standard_result(
    *,
    status: str,
    source: QuoteSource,
    args: Mapping[str, Any],
    columns: List[str],
    api_name: str,
    rows: List[Dict[str, Any]] | None = None,
    reason: str = "",
    code: str = "",
    output_schema: List[Dict[str, str]] | None = None,
    raw_row_count: int | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": status,
        "api": api_name,
        "subject": source.subject,
        "source": list(dict.fromkeys([item for item in [source.table, source.base_table] if item])),
        "arguments": dict(args),
        "columns": columns,
        "rows": rows or [],
    }
    if reason:
        payload["reason"] = reason
    if code:
        payload["code"] = code
    if output_schema is not None:
        payload["output_schema"] = output_schema
    if raw_row_count is not None:
        payload["raw_row_count"] = raw_row_count
    payload["row_count"] = len(payload["rows"])
    return payload

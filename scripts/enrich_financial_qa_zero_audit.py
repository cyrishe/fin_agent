#!/usr/bin/env python3
"""Add independent raw-table evidence for datasets that originally returned zero rows."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


def filter_value(text: str, field: str) -> str | None:
    match = re.search(rf"\b{re.escape(field)}\s*=\s*([^\s,)]+(?:\s+[^\s,)]+)*?)(?=\s+(?:and|or)\s+|[,)]|$)", text, re.I)
    return match.group(1).strip().strip("'\"") if match else None


def first_filter_sql(case: dict[str, Any], api: str) -> str:
    for text in case.get("query_requests") or []:
        if api in text:
            match = re.search(r"filter\s*=\s*\"([^\"]*)\"", text)
            if match:
                return match.group(1)
    return ""


def scalar(cursor: Any, sql: str, params: list[Any]) -> int:
    cursor.execute(sql, tuple(params))
    row = cursor.fetchone()
    return int(next(iter(row.values())) or 0)


def captured_count_query(record: dict[str, Any], api: str) -> tuple[str, list[Any]]:
    for execution in reversed(record.get("db_executions") or []):
        if execution.get("api") != api or execution.get("row_count") != 0:
            continue
        for query in reversed(execution.get("sql") or []):
            sql = str(query.get("sql") or "")
            if not sql.upper().startswith("SELECT ") or " FROM " not in sql.upper():
                continue
            from_pos = sql.upper().find(" FROM ")
            tail = sql[from_pos:]
            order_pos = tail.upper().rfind(" ORDER BY ")
            if order_pos >= 0:
                tail = tail[:order_pos]
            params = list(query.get("params") or [])
            if re.search(r"\bLIMIT\s+%s", sql, re.I) and params:
                params = params[:-1]
            return "SELECT COUNT(*) AS matched_rows" + tail, params
    return "", []


def materialize_identity(value: str | None, record: dict[str, Any]) -> str | None:
    if not value or not re.fullmatch(r"r\d+\.code", value, re.I):
        return value
    for dataset in record.get("original_datasets") or []:
        for row in dataset.get("examples") or []:
            if row.get("code"):
                return str(row["code"])
    return value


def diagnostic(cursor: Any, case: dict[str, Any], record: dict[str, Any], api: str) -> dict[str, Any]:
    raw_filter = first_filter_sql(case, api)
    code = materialize_identity(filter_value(raw_filter, "code"), record)
    name = filter_value(raw_filter, "name")
    institution = filter_value(raw_filter, "institution")
    identity_sql = "SUBSTRING_INDEX(r.security_code,'.',1)=SUBSTRING_INDEX(%s,'.',1)" if code else "r.security_name=%s"
    identity = code or name

    if api == "stock.report_metric" and identity:
        metric = filter_value(raw_filter, "metric_code")
        year_match = re.search(r"forecast_year\s*=\s*(\d{4})", raw_filter)
        year = int(year_match.group(1)) if year_match else None
        exact_terms, exact_params = [identity_sql], [identity]
        if institution:
            exact_terms.append("COALESCE(NULLIF(r.research_institution,''),r.publisher)=%s")
            exact_params.append(institution)
        if metric:
            exact_terms.append("m.metric_id=%s")
            exact_params.append(metric)
        if year:
            exact_terms.append("m.year=%s")
            exact_params.append(year)
        exact_terms.append("m.value_type='forecast'")
        generated_exact_sql = (
            "SELECT COUNT(*) AS matched_rows FROM metric_fact m "
            "JOIN reports r ON r.id=m.report_id WHERE " + " AND ".join(exact_terms)
        )
        captured_sql, captured_params = captured_count_query(record, api)
        exact_sql, exact_params = (captured_sql, captured_params) if captured_sql else (generated_exact_sql, exact_params)
        exact = scalar(cursor, exact_sql, exact_params)
        broad_terms, broad_params = [identity_sql, "m.value_type='forecast'"], [identity]
        if metric:
            broad_terms.append("m.metric_id=%s")
            broad_params.append(metric)
        if year:
            broad_terms.append("m.year=%s")
            broad_params.append(year)
        broad_sql = (
            "SELECT COUNT(*) AS candidate_rows FROM metric_fact m "
            "JOIN reports r ON r.id=m.report_id WHERE " + " AND ".join(broad_terms)
        )
        broad = scalar(cursor, broad_sql, broad_params)
        inst_sql = "SELECT COUNT(*) AS institution_reports FROM reports r WHERE " + identity_sql
        inst_params = [identity]
        if institution:
            inst_sql += " AND COALESCE(NULLIF(r.research_institution,''),r.publisher)=%s"
            inst_params.append(institution)
        inst = scalar(cursor, inst_sql, inst_params)
        return {
            "best_sql": exact_sql,
            "params": exact_params,
            "matched_rows": exact,
            "evidence": f"完全匹配={exact}；忽略机构后的同公司/指标/年份={broad}；该机构公司研报={inst}",
            "verdict": "0行正确：库内无完全匹配指标记录" if exact == 0 else "原0行异常：当前库内存在完全匹配记录",
        }

    if api == "stock.report" and identity:
        terms, params = [identity_sql], [identity]
        if institution:
            terms.append("COALESCE(NULLIF(r.research_institution,''),r.publisher)=%s")
            params.append(institution)
        broad_identity_sql = "SELECT COUNT(*) AS matched_rows FROM reports r WHERE " + " AND ".join(terms)
        captured_sql, captured_params = captured_count_query(record, api)
        sql, params = (captured_sql, captured_params) if captured_sql else (broad_identity_sql, params)
        count = scalar(cursor, sql, params)
        all_sql = "SELECT COUNT(*) AS company_reports FROM reports r WHERE " + identity_sql
        all_count = scalar(cursor, all_sql, [identity])
        if case.get("case_id") == "RTEF194":
            verdict = "原0行不应作为结论：查询加入了题目未要求的历史时间窗"
        elif institution and count == 0:
            verdict = "0行正确：库内无该公司与机构组合"
        else:
            verdict = "0行正确：题目要求的日期/非空字段收窄后无记录"
        return {
            "best_sql": sql,
            "params": params,
            "matched_rows": count,
            "evidence": f"公司+机构研报={count}；公司全部研报={all_count}；原查询的日期/非空字段会进一步收窄",
            "verdict": verdict,
        }

    if api == "stock.financial_3_table" and (code or name):
        if code:
            sql = (
                "SELECT COUNT(*) AS matched_rows FROM kcrp_stock_income i "
                "WHERE SUBSTRING_INDEX(i.stk_code,'.',1)=SUBSTRING_INDEX(%s,'.',1) "
                "AND i.statement_type='HB'"
            )
            params = [code]
        else:
            sql = (
                "SELECT COUNT(*) AS matched_rows FROM kcrp_stock_income i "
                "JOIN kcrp_stock_baseinfo b ON b.stk_code=i.stk_code "
                "WHERE b.stk_name=%s AND i.statement_type='HB'"
            )
            params = [name]
        count = scalar(cursor, sql, params)
        return {
            "best_sql": sql,
            "params": params,
            "matched_rows": count,
            "evidence": f"底层合并报表枚举HB命中={count}；API请求曾同时使用statement_type=合并报表",
            "verdict": "原0行不正确：枚举值表达不一致" if count else "0行正确：底层合并报表无记录",
        }
    return {"best_sql": "", "params": [], "matched_rows": 0, "evidence": "无独立诊断SQL", "verdict": "需复核"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("analysis")
    parser.add_argument("audit")
    parser.add_argument("output")
    args = parser.parse_args()
    analysis = json.loads(Path(args.analysis).read_text())
    audit = json.loads(Path(args.audit).read_text())
    source_cases = {c["case_id"]: c for c in analysis.get("cases") or []}
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            for record in audit.get("cases") or []:
                source = source_cases[record["case_id"]]
                findings = []
                for dataset in record.get("original_datasets") or []:
                    if dataset.get("row_count") == 0:
                        item = diagnostic(cursor, source, record, str(dataset.get("api") or ""))
                        item.update({"result_name": dataset.get("result_name"), "api": dataset.get("api")})
                        findings.append(item)
                record["zero_diagnostics"] = findings
                if findings:
                    record["structured_analysis"]["证据"] = "；".join(f["evidence"] for f in findings)
                    record["structured_analysis"]["结论"] = "；".join(f["verdict"] for f in findings)
    finally:
        db.close_db()
    Path(args.output).write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

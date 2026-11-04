from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping

from src.services.search_gateway_service import SearchGatewayService


DEFAULT_FIELDS = ["code", "name", "publish_time", "source", "title", "url", "snippet"]
AVAILABLE_FIELDS = {
    "code",
    "name",
    "publish_time",
    "source",
    "title",
    "url",
    "snippet",
    "category",
    "score",
    "document_id",
}
FILTER_RE = re.compile(
    r"(?:(?:\band\b|\bor\b)\s+)?"
    r"(?P<field>[A-Za-z_]\w*)\s*"
    r"(?P<op>in|==|!=|>=|<=|=|>|<)\s*"
    r"(?P<value>\[[^\]]*\]|\([^)]*\)|[^,;]+?)"
    r"(?=\s+(?:and|or)\s+[A-Za-z_]\w*\s*(?:in|==|!=|>=|<=|=|>|<)|[,;]|$)",
    flags=re.IGNORECASE,
)


def execute_stock_news_api(*, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    requested = _requested_fields(outputs) or list(DEFAULT_FIELDS)
    invalid = sorted(set(requested) - AVAILABLE_FIELDS)
    if invalid:
        return _result(
            status="unsupported",
            args=args,
            columns=requested,
            rows=[],
            reason=f"unsupported stock.news fields: {invalid}",
        )
    try:
        filters = _parse_filters(str(args.get("filter") or ""))
        query, identity = _search_query(filters)
        start_time, end_time = _time_range(filters)
        source_scope = _scope(filters, "source")
        category_scope = _scope(filters, "category")
        limit = _bounded_limit(args.get("limit", 10))
        sort = _sort_mode(str(args.get("order") or ""))
    except ValueError as exc:
        return _result(
            status="invalid_request",
            args=args,
            columns=requested,
            rows=[],
            reason=str(exc),
        )

    response = SearchGatewayService().search(
        query=query,
        limit=limit,
        start_time=start_time,
        end_time=end_time,
        category_scope=category_scope,
        source_scope=source_scope,
        sort=sort,
        entity=identity,
    )
    if response.get("status") != "ok":
        return _result(
            status=str(response.get("status") or "provider_error"),
            args=args,
            columns=requested,
            rows=[],
            reason=str(response.get("reason") or "stock news search failed"),
            provider=str(response.get("provider") or ""),
            coverage=str(response.get("coverage") or ""),
        )

    rows: list[dict[str, Any]] = []
    for item in response.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        normalized = {
            "code": identity.get("code", ""),
            "name": identity.get("name", ""),
            "publish_time": str(item.get("publish_time") or ""),
            "source": str(item.get("source") or ""),
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("snippet") or ""),
            "category": str(item.get("category") or ""),
            "score": float(item.get("score") or 0.0),
            "document_id": str(item.get("document_id") or ""),
        }
        rows.append({field: normalized.get(field) for field in requested})
    return _result(
        status="ok",
        args=args,
        columns=requested,
        rows=rows,
        provider=str(response.get("provider") or ""),
        coverage=str(response.get("coverage") or ""),
        total=int(response.get("total") or 0),
    )


def _requested_fields(outputs: List[str]) -> list[str]:
    result: list[str] = []
    for output in outputs:
        token = str(output or "").strip()
        if " as " in token:
            token = token.split(" as ", 1)[0].strip()
        if "." in token:
            token = token.rsplit(".", 1)[-1]
        if token:
            result.append(token)
    return result


def _parse_filters(text: str) -> list[tuple[str, str, Any]]:
    if not text.strip():
        return []
    filters: list[tuple[str, str, Any]] = []
    for match in FILTER_RE.finditer(text):
        field = str(match.group("field") or "").strip().lower()
        op = str(match.group("op") or "=").strip().lower()
        raw_value = str(match.group("value") or "").strip()
        values = _list_value(raw_value) if op == "in" else [_clean_value(raw_value)]
        filters.extend((field, op, value) for value in values)
    if not filters:
        raise ValueError("stock.news filter could not be parsed")
    allowed = {"code", "name", "query", "keyword", "publish_time", "published_at", "source", "category"}
    invalid = sorted({field for field, _, _ in filters if field not in allowed})
    if invalid:
        raise ValueError(f"unsupported stock.news filter fields: {invalid}")
    return filters


def _search_query(filters: list[tuple[str, str, Any]]) -> tuple[str, dict[str, str]]:
    identity: dict[str, str] = {"code": "", "name": ""}
    terms: list[str] = []
    for field, op, value in filters:
        if field not in {"code", "name", "query", "keyword"} or op not in {"=", "==", "in"}:
            continue
        text = str(value or "").strip()
        if not text:
            continue
        if field == "code":
            code, name = _stock_identity(text)
            identity["code"] = code or text.upper()
            if name:
                identity["name"] = name
                terms.append(name)
            terms.append(code or text)
        elif field == "name":
            code, name = _stock_identity(text)
            identity["code"] = code
            identity["name"] = name or text
            terms.append(name or text)
            if code:
                terms.append(code)
        else:
            terms.append(text)
    deduped = list(dict.fromkeys(item for item in terms if item))
    if not deduped:
        raise ValueError("stock.news requires code, name, query, or keyword in filter")
    return " ".join(deduped), identity


def _time_range(filters: list[tuple[str, str, Any]]) -> tuple[str, str]:
    start_time = ""
    end_time = ""
    for field, op, value in filters:
        if field not in {"publish_time", "published_at"}:
            continue
        text = str(value or "").strip()
        if op in {">", ">="}:
            start_time = text
        elif op in {"<", "<="}:
            end_time = text
        elif op in {"=", "=="}:
            start_time = text
            end_time = text
    return start_time, end_time


def _scope(filters: list[tuple[str, str, Any]], field_name: str) -> list[str]:
    return [str(value).strip() for field, op, value in filters if field == field_name and op in {"=", "==", "in"} and str(value).strip()]


def _sort_mode(order: str) -> str:
    normalized = order.strip().lower()
    if normalized.startswith(("publish_time asc", "published_at asc")):
        return "date_asc"
    if normalized.startswith(("publish_time", "published_at")):
        return "date_desc"
    return "relevance"


@lru_cache(maxsize=1)
def _stock_maps() -> tuple[dict[str, str], dict[str, str]]:
    code_to_name: dict[str, str] = {}
    name_to_code: dict[str, str] = {}
    path = Path("stock_name.tsv")
    if not path.exists():
        return code_to_name, name_to_code
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        code, name = parts[0].strip().upper(), parts[1].strip()
        if code and name:
            code_to_name[code] = name
            code_to_name[code.split(".", 1)[0]] = name
            name_to_code[name] = code
    return code_to_name, name_to_code


def _stock_identity(value: str) -> tuple[str, str]:
    code_to_name, name_to_code = _stock_maps()
    text = str(value or "").strip()
    upper = text.upper()
    if upper in code_to_name:
        code = upper if "." in upper else next((item for item in code_to_name if item.startswith(f"{upper}.") and "." in item), upper)
        return code, code_to_name[upper]
    if text in name_to_code:
        return name_to_code[text], text
    return "", text if not re.fullmatch(r"\d{6}(?:\.(?:SH|SZ|BJ))?", upper) else ""


def _list_value(value: str) -> list[str]:
    text = value.strip()
    if text[:1] in "[(" and text[-1:] in ")]":
        text = text[1:-1]
    return [_clean_value(item) for item in text.split(",") if item.strip()]


def _clean_value(value: str) -> str:
    return str(value or "").strip().strip("\"'")


def _bounded_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 10
    return min(50, max(1, parsed))


def _result(
    *,
    status: str,
    args: Mapping[str, Any],
    columns: list[str],
    rows: list[dict[str, Any]],
    reason: str = "",
    provider: str = "",
    coverage: str = "",
    total: int = 0,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": status,
        "api": "stock.news",
        "subject": "stock",
        "dataview": "news",
        "arguments": dict(args),
        "columns": columns,
        "available_fields": sorted(AVAILABLE_FIELDS),
        "rows": rows,
        "row_count": len(rows),
        "provider": provider,
        "coverage": coverage,
        "total": total,
    }
    if reason:
        result["reason"] = reason
    return result

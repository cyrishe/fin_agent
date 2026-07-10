from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.utils.mysql_utils import StockInfoDbUtils


_GENERIC_PLATE_NAMES = {
    "融资融券",
    "沪股通",
    "深股通",
    "转融券标的",
    "上证180",
    "上证380",
    "深证100R",
    "MSCI中国",
    "标普道琼斯A股",
    "富时罗素",
    "央企国资改革",
}
_GENERIC_PLATE_KEYWORDS = [
    "沪股通",
    "深股通",
    "融资融券",
    "转融券",
    "罗素",
    "MSCI",
    "指数",
    "成份",
]


def _normalize_params(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    if isinstance(raw, str):
        return {"stock": raw}
    return {"stock": str(raw)}


def _normalize_top_k(value: Any, default: int = 3) -> int:
    try:
        top_k = int(value or default)
    except (TypeError, ValueError):
        return default
    return max(1, min(top_k, 10))


def _is_generic_plate(name: str) -> bool:
    normalized = str(name or "").strip()
    if not normalized:
        return True
    if normalized in _GENERIC_PLATE_NAMES:
        return True
    return any(keyword.lower() in normalized.lower() for keyword in _GENERIC_PLATE_KEYWORDS)


def _select_top_concepts(rows: List[Dict[str, Any]], top_k: int) -> List[str]:
    concepts: List[str] = []
    for row in rows:
        plate_name = str(row.get("plate_name") or "").strip()
        if not plate_name or _is_generic_plate(plate_name):
            continue
        if plate_name in concepts:
            continue
        concepts.append(plate_name)
        if len(concepts) >= top_k:
            break
    if concepts:
        return concepts
    fallback: List[str] = []
    for row in rows:
        plate_name = str(row.get("plate_name") or "").strip()
        if not plate_name or plate_name in fallback:
            continue
        fallback.append(plate_name)
        if len(fallback) >= top_k:
            break
    return fallback


def run(params: Any, db: Optional[StockInfoDbUtils] = None) -> Dict[str, Any]:
    payload = _normalize_params(params)
    stock = str(payload.get("stock") or payload.get("query") or payload.get("company") or "").strip()
    as_of = str(payload.get("as_of") or payload.get("query_date") or "").strip()
    top_k = _normalize_top_k(payload.get("top_k"), default=3)
    if not stock:
        return {
            "tool": "get_company_taxonomy_profile",
            "ok": False,
            "data": {
                "stock": "",
                "code": "",
                "name": "",
                "as_of": as_of,
                "plates": [],
            },
            "error": "stock_required",
        }

    owns_db = db is None
    db = db or StockInfoDbUtils()
    try:
        profile = db.get_company_concept_profile(stock, query_date=as_of or None)
    except Exception as exc:
        return {
            "tool": "get_company_taxonomy_profile",
            "ok": False,
            "data": {
                "stock": stock,
                "code": "",
                "name": "",
                "as_of": as_of,
                "plates": [],
            },
            "error": str(exc),
        }
    finally:
        if owns_db:
            db.close_db()

    concept_rows = profile.get("concept_rows") if isinstance(profile.get("concept_rows"), list) else []
    selected_plates = _select_top_concepts(concept_rows, top_k)
    plate_rows = []
    for row in concept_rows:
        plate_name = str(row.get("plate_name") or "").strip()
        if not plate_name or plate_name not in selected_plates:
            continue
        plate_rows.append(
            {
                "plate_code": str(row.get("plate_code") or "").strip(),
                "plate_name": plate_name,
                "trade_date": str(row.get("trade_date") or "").strip(),
                "amount": float(row.get("amount") or 0),
            }
        )

    return {
        "tool": "get_company_taxonomy_profile",
        "ok": True,
        "data": {
            "stock": stock,
            "code": str(profile.get("stk_code") or "").strip(),
            "name": str(profile.get("company") or stock).strip(),
            "as_of": str(profile.get("query_date") or as_of).strip(),
            "plates": plate_rows,
        },
        "error": "",
    }

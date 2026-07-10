from __future__ import annotations

from typing import Any, Dict

from src.services.kingdomai_capital_flow_service import KingdomaiCapitalFlowService


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload or {})
    subject_type = str(data.pop("subject_type", "") or "").strip()
    data.pop("source", None)
    data.pop("row_count", None)
    data.pop("coverage", None)
    data.pop("date_range", None)
    code = str(data.pop("subject_code", "") or "").strip()
    name = str(data.pop("subject_name", "") or "").strip()
    if subject_type and subject_type != "stock":
        data["plate_type"] = subject_type
    if code:
        data["code"] = code
    if name:
        data["name"] = name
    rows = []
    for row in data.get("rows") or []:
        if not isinstance(row, dict):
            continue
        normalized = dict(row)
        row_code = str(normalized.pop("subject_code", "") or "").strip()
        row_name = str(normalized.pop("subject_name", "") or "").strip()
        normalized["code"] = row_code or code
        normalized["name"] = row_name or name
        rows.append(normalized)
    data["rows"] = rows
    return data


def _meta_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": payload.get("source", ""),
        "subject_type": payload.get("subject_type", ""),
        "date_range": payload.get("date_range", {}),
        "row_count": payload.get("row_count", 0),
        "coverage": payload.get("coverage", {}),
    }


def run_fixed_subject_type(args: Dict[str, Any], *, subject_type: str, tool_name: str) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiCapitalFlowService().query(
            subject_type=subject_type,
            subject=str(params.get("stock") or params.get("subject") or params.get("name") or params.get("code") or "").strip(),
            start=str(params.get("start") or "").strip(),
            end=str(params.get("end") or "").strip(),
            days=int(params.get("days", 20) or 20),
            granularity=str(params.get("granularity") or "daily").strip(),
            limit=int(params.get("limit", 50) or 50),
        )
        return {
            "tool": tool_name,
            "ok": True,
            "data": _normalize_payload(payload),
            "_meta": _meta_from_payload(payload),
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": tool_name,
            "ok": False,
            "data": {},
            "_meta": {},
            "error": str(exc),
        }


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiCapitalFlowService().query(
            subject_type=str(params.get("subject_type") or "stock").strip(),
            subject=str(params.get("subject") or params.get("name") or params.get("code") or "").strip(),
            start=str(params.get("start") or "").strip(),
            end=str(params.get("end") or "").strip(),
            days=int(params.get("days", 20) or 20),
            granularity=str(params.get("granularity") or "daily").strip(),
            limit=int(params.get("limit", 50) or 50),
        )
        return {
            "tool": "capital_flow_query",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "capital_flow_query",
            "ok": False,
            "data": {},
            "error": str(exc),
        }

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from src.services.finance_data_tool_runtime_service import (
    FinanceDataToolRuntimeService,
)

_STOCK_CODE_RE = re.compile(r"^\d{6}(?:\.(?:SH|SZ|BJ))?$", flags=re.IGNORECASE)
_OHLC_COLUMNS = ("open", "high", "low", "close")


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _rows(result_ref: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sample = (
        result_ref.get("sample")
        if isinstance(result_ref.get("sample"), Mapping)
        else {}
    )
    raw_rows = sample.get("rows") if isinstance(sample.get("rows"), list) else []
    return [dict(row) for row in raw_rows if isinstance(row, Mapping)]


class FinancialQaCompanionEvidenceService:
    """Add small, deterministic market context without another Agent turn."""

    def __init__(self, *, finance_runtime: FinanceDataToolRuntimeService) -> None:
        self.finance_runtime = finance_runtime

    def build(
        self,
        result_refs: Sequence[Mapping[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        refs = [item for item in result_refs or [] if isinstance(item, Mapping)]
        if self._already_has_price_series(refs):
            return []
        target = self._single_quote_target(refs)
        if not target:
            return []

        request = (
            f'r0 = stock.quote(filter = "code = {target}", '
            'order = "tradedate desc", limit = 22, realtime = 0) '
            "-> code, name, tradedate, open, high, low, close, volumn, amount, pct"
        )
        try:
            payload = self.finance_runtime.execute_request(request=request)
        except Exception:
            return []
        if payload.get("ok") is not True:
            return []
        result = (
            payload.get("result")
            if isinstance(payload.get("result"), Mapping)
            else {}
        )
        data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
        raw_rows = data.get("rows") if isinstance(data.get("rows"), list) else []
        rows = [
            dict(row)
            for row in raw_rows
            if isinstance(row, Mapping)
            and _trim(row.get("tradedate") or row.get("trade_date") or row.get("date"))
            and all(row.get(column) is not None for column in _OHLC_COLUMNS)
        ]
        rows.sort(
            key=lambda row: _trim(
                row.get("tradedate") or row.get("trade_date") or row.get("date")
            )
        )
        if len(rows) < 2:
            return []
        columns = [
            _trim(item)
            for item in result.get("columns") or []
            if _trim(item)
        ]
        return [
            {
                "api": "stock.quote",
                "goal": "近一个月价格走势",
                "display_title": "近一个月日 K 走势",
                "row_count": len(rows),
                "sample_complete": True,
                "schema": {"columns": columns},
                "sample": {"rows": deepcopy(rows)},
                "step_evidence": {
                    "execution_completed": True,
                    "selection_applied": {
                        "filter": f"code = {target}",
                        "order": "tradedate desc",
                        "limit": 22,
                        "realtime": 0,
                    },
                    "sample_complete": True,
                },
                "meta": {
                    "origin": "automatic_companion",
                    "presentation_only": True,
                },
            }
        ]

    @staticmethod
    def _already_has_price_series(
        result_refs: Sequence[Mapping[str, Any]],
    ) -> bool:
        for result_ref in result_refs:
            rows = _rows(result_ref)
            if len(rows) < 2:
                continue
            if all(all(row.get(column) is not None for column in _OHLC_COLUMNS) for row in rows):
                return True
        return False

    @staticmethod
    def _single_quote_target(
        result_refs: Sequence[Mapping[str, Any]],
    ) -> str:
        codes: set[str] = set()
        has_price_fact = False
        for result_ref in result_refs:
            if _trim(result_ref.get("api")) != "stock.quote":
                continue
            rows = _rows(result_ref)
            for row in rows:
                code = _trim(
                    row.get("code")
                    or row.get("stock_code")
                    or row.get("security_code")
                    or row.get("symbol")
                ).upper()
                if not _STOCK_CODE_RE.fullmatch(code):
                    return ""
                codes.add(code)
                has_price_fact = has_price_fact or any(
                    row.get(column) is not None
                    for column in ("close", "price", "pct", "change", "differ")
                )
        return next(iter(codes)) if len(codes) == 1 and has_price_fact else ""

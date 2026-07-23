from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Mapping, Optional

from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService


class StockIdentityResolverService:
    """Resolve a user-facing stock name/code through the finance data API."""

    _FULL_CODE_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$", re.IGNORECASE)
    _SHORT_CODE_RE = re.compile(r"^\d{6}$")
    _EXCHANGES = ("SH", "SZ", "BJ")

    def __init__(self, runtime: Optional[FinanceDataToolRuntimeService] = None) -> None:
        self.runtime = runtime or FinanceDataToolRuntimeService()

    def resolve(self, value: Any) -> Optional[Dict[str, str]]:
        query = str(value or "").strip()
        if not query:
            return None

        candidates = (
            [query.upper()]
            if self._FULL_CODE_RE.fullmatch(query)
            else [f"{query}.{exchange}" for exchange in self._EXCHANGES]
            if self._SHORT_CODE_RE.fullmatch(query)
            else []
        )
        rows = []
        if candidates:
            for code in candidates:
                rows.extend(self._query(field="code", value=code))
        else:
            rows.extend(self._query(field="name", value=query))

        unique = {
            str(row.get("code") or "").strip().upper(): row
            for row in rows
            if isinstance(row, Mapping) and str(row.get("code") or "").strip()
        }
        if len(unique) != 1:
            return None
        row = next(iter(unique.values()))
        return {
            "kind": "stock",
            "query": query,
            "code": str(row.get("code") or "").strip().upper(),
            "name": str(row.get("name") or "").strip(),
        }


    def _query(self, *, field: str, value: str) -> Iterable[Mapping[str, Any]]:
        safe_value = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
        payload = self.runtime.execute_request(
            request=f'r1 = stock.basic_info(filter = "{field} = {safe_value}", limit = 5) -> code, name'
        )
        if not bool((payload.get("validation") or {}).get("ok")):
            return []
        result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
        data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
        rows = data.get("rows") if isinstance(data.get("rows"), list) else []
        return [row for row in rows if isinstance(row, Mapping)]

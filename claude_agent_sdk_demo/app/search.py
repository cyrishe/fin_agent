from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class SearchBackend(Protocol):
    async def search(self, query: str, *, limit: int) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SearxngSearchBackend:
    base_url: str
    timeout_seconds: float = 15.0

    async def search(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        normalized = query.strip()
        if not normalized or len(normalized) > 500:
            raise ValueError("query must contain 1 to 500 characters")
        bounded_limit = min(max(int(limit), 1), 8)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
            response = await client.get(
                f"{self.base_url}/search",
                params={"q": normalized, "format": "json", "language": "all"},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        raw_results = payload.get("results") if isinstance(payload, dict) else []
        results = []
        for item in raw_results if isinstance(raw_results, list) else []:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "url": str(item.get("url") or "").strip(),
                    "snippet": str(item.get("content") or "").strip()[:2_000],
                    "source": "searxng",
                }
            )
            if len(results) >= bounded_limit:
                break
        return {"query": normalized, "results": results, "result_count": len(results)}

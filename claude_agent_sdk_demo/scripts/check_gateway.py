from __future__ import annotations

import asyncio
import json
import sys

import httpx

from app.config import Settings


async def main() -> int:
    settings = Settings.from_env()
    readiness = settings.readiness()
    print(json.dumps(readiness, ensure_ascii=False, indent=2))
    if settings.provider != "gateway" or not readiness["ready"]:
        return 2
    headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if settings.auth_token:
        headers["authorization"] = f"Bearer {settings.auth_token}"
    elif settings.anthropic_api_key:
        headers["x-api-key"] = settings.anthropic_api_key
    body = {
        "model": settings.model,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            response = await client.post(
                f"{settings.base_url}/v1/messages/count_tokens",
                headers=headers,
                json=body,
            )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        print(f"gateway probe failed: {type(exc).__name__}: {str(exc)[:500]}", file=sys.stderr)
        return 1
    print(json.dumps({"count_tokens_ok": True, "response": payload}, ensure_ascii=False, indent=2))
    print("Note: this probe does not prove streaming/tool-use compatibility; run the real SSE smoke test next.")
    return 0


raise SystemExit(asyncio.run(main()))

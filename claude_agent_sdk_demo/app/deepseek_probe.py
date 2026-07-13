from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Iterable

import httpx

from .config import Settings


class DeepSeekProbeError(RuntimeError):
    """A safe-to-display DeepSeek compatibility probe failure."""


@dataclass(frozen=True)
class DirectToolProbeResult:
    model: str
    event_types: list[str]
    tool_name: str
    tool_input_keys: list[str]
    stop_reason: str
    message_stop_seen: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_anthropic_sse(model: str, data_payloads: Iterable[str]) -> DirectToolProbeResult:
    """Validate the minimum Anthropic streaming/tool-use contract Claude Code needs."""
    event_types: list[str] = []
    tool_name = ""
    tool_input_keys: list[str] = []
    tool_json_chunks: list[str] = []
    stop_reason = ""
    message_stop_seen = False

    for raw in data_payloads:
        if raw == "[DONE]":
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeepSeekProbeError("provider returned a non-JSON SSE data frame") from exc
        if not isinstance(payload, dict):
            raise DeepSeekProbeError("provider returned a non-object SSE data frame")
        event_type = str(payload.get("type") or "")
        if event_type:
            event_types.append(event_type)
        if event_type == "content_block_start":
            block = payload.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_name = str(block.get("name") or "")
                initial_input = block.get("input")
                if isinstance(initial_input, dict):
                    tool_input_keys = sorted(str(key) for key in initial_input)
        elif event_type == "content_block_delta":
            delta = payload.get("delta")
            if isinstance(delta, dict) and delta.get("type") == "input_json_delta":
                tool_json_chunks.append(str(delta.get("partial_json") or ""))
        elif event_type == "message_delta":
            delta = payload.get("delta")
            if isinstance(delta, dict):
                stop_reason = str(delta.get("stop_reason") or stop_reason)
        elif event_type == "message_stop":
            message_stop_seen = True

    if tool_json_chunks:
        try:
            tool_input = json.loads("".join(tool_json_chunks))
        except json.JSONDecodeError as exc:
            raise DeepSeekProbeError("streamed tool input was not valid JSON") from exc
        if isinstance(tool_input, dict):
            tool_input_keys = sorted(str(key) for key in tool_input)
    if tool_name != "get_current_time":
        raise DeepSeekProbeError("provider did not return the forced get_current_time tool call")
    if not message_stop_seen:
        raise DeepSeekProbeError("provider stream ended without message_stop")

    return DirectToolProbeResult(
        model=model,
        event_types=event_types,
        tool_name=tool_name,
        tool_input_keys=tool_input_keys,
        stop_reason=stop_reason,
        message_stop_seen=message_stop_seen,
    )


async def probe_deepseek_anthropic_stream(settings: Settings) -> DirectToolProbeResult:
    """Make one small paid request that forces a standard Anthropic tool call."""
    if settings.provider != "deepseek":
        raise DeepSeekProbeError("CLAUDE_PROVIDER must be deepseek")
    provider_env = settings.provider_env()
    headers = {
        "accept": "text/event-stream",
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if token := provider_env.get("ANTHROPIC_AUTH_TOKEN"):
        headers["authorization"] = f"Bearer {token}"
    elif api_key := provider_env.get("ANTHROPIC_API_KEY"):
        headers["x-api-key"] = api_key
    else:
        raise DeepSeekProbeError("DeepSeek credentials are missing")

    request_body = {
        "model": settings.model,
        "max_tokens": 128,
        "stream": True,
        "thinking": {"type": "disabled"},
        "system": "This is a protocol test. Call the requested tool and do not answer in prose.",
        "messages": [
            {
                "role": "user",
                "content": "Call get_current_time exactly once with an empty JSON object.",
            }
        ],
        "tools": [
            {
                "name": "get_current_time",
                "description": "Return the current UTC time.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            }
        ],
        "tool_choice": {"type": "tool", "name": "get_current_time"},
    }
    timeout = httpx.Timeout(connect=15.0, read=90.0, write=15.0, pool=15.0)
    data_payloads: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                "POST",
                f"{settings.base_url}/v1/messages",
                headers=headers,
                json=request_body,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise DeepSeekProbeError(
                        f"DeepSeek Anthropic endpoint returned HTTP {response.status_code}"
                    )
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data_payloads.append(line.removeprefix("data:").strip())
    except DeepSeekProbeError:
        raise
    except httpx.HTTPError as exc:
        raise DeepSeekProbeError(f"DeepSeek request failed: {type(exc).__name__}") from exc

    return summarize_anthropic_sse(settings.model, data_payloads)

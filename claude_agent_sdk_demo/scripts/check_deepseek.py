from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from app.backend import ClaudeAgentBackend
from app.config import Settings
from app.contracts import BackendRequest
from app.deepseek_probe import DeepSeekProbeError, probe_deepseek_anthropic_stream


async def _collect_sdk_events(settings: Settings, *, web_search: bool) -> list[Any]:
    if web_search:
        question = (
            "请调用 financial-research Skill，并且必须调用 WebSearch 一次，查找 DeepSeek V4 Flash "
            "的官方 API 文档。最后只输出 DEEPSEEK_WEB_SEARCH_OK 和官方文档 URL。"
        )
    else:
        question = (
            "这是一次金融研究 harness 连通性测试。请先调用 financial-research Skill，再调用 "
            "get_current_time 工具。最后只输出 DEEPSEEK_SDK_OK 和工具返回的 UTC 时间；不要调用 WebSearch。"
        )
    request = BackendRequest(
        run_id="run_deepseek_probe",
        question=question,
        skill_names=["financial-research"],
        enable_web_search=web_search,
    )
    backend = ClaudeAgentBackend(settings)
    events: list[Any] = []
    async for event in backend.stream(request):
        events.append(event)
    return events


async def _run_sdk_probe(settings: Settings, *, web_search: bool) -> dict[str, Any]:
    try:
        events = await asyncio.wait_for(
            _collect_sdk_events(settings, web_search=web_search),
            timeout=float(settings.hard_timeout_seconds),
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Claude Agent SDK probe timed out") from exc

    tool_names = sorted(
        {
            str(event.data.get("tool_name") or "")
            for event in events
            if event.type in {"tool.started", "tool.requested", "tool.completed"}
            and event.data.get("tool_name")
        }
    )
    results = [event for event in events if event.type == "backend.result"]
    if not results:
        raise RuntimeError("Claude Agent SDK stream ended without ResultMessage")
    final = results[-1]
    if final.data.get("status") != "completed":
        raise RuntimeError(
            f"Claude Agent SDK returned status={final.data.get('status')}, subtype={final.data.get('subtype')}"
        )
    required_tool = "WebSearch" if web_search else "mcp__demo__get_current_time"
    if required_tool not in tool_names:
        raise RuntimeError(f"Claude Agent SDK did not complete required tool: {required_tool}")
    if "Skill" not in tool_names:
        raise RuntimeError("Claude Agent SDK did not invoke the requested financial-research Skill")
    result_text = str(final.data.get("result") or "")
    return {
        "ok": True,
        "web_search": web_search,
        "event_types": [event.type for event in events],
        "tool_names": tool_names,
        "result_preview": result_text[:500],
        "session_id_present": bool(final.data.get("session_id")),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify DeepSeek V4 Flash streaming, tools, Skills, and Claude Agent SDK through "
            "the official DeepSeek or Alibaba Cloud Model Studio Anthropic endpoint."
        )
    )
    parser.add_argument(
        "--with-web-search",
        action="store_true",
        help="also make a second paid SDK run that must invoke Claude Code WebSearch",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    readiness = settings.readiness()
    print(json.dumps(readiness, ensure_ascii=False, indent=2))
    if (
        settings.provider not in {"deepseek", "dashscope"}
        or settings.backend != "claude"
        or not readiness["ready"]
    ):
        print("DeepSeek/DashScope profile is not ready; see issues above.", file=sys.stderr)
        return 2

    try:
        direct = await probe_deepseek_anthropic_stream(settings)
        print(
            json.dumps(
                {"stage": "anthropic_stream_and_tool", "ok": True, **direct.to_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
        sdk = await _run_sdk_probe(settings, web_search=False)
        print(json.dumps({"stage": "claude_agent_sdk", **sdk}, ensure_ascii=False, indent=2))
        if args.with_web_search:
            web = await _run_sdk_probe(settings, web_search=True)
            print(json.dumps({"stage": "web_search", **web}, ensure_ascii=False, indent=2))
    except (DeepSeekProbeError, RuntimeError) as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Provider/SDK exception text can contain request metadata. Keep the
        # unexpected failure useful without echoing credentials or raw traces.
        print(f"probe failed unexpectedly: {type(exc).__name__}", file=sys.stderr)
        return 1

    print("DeepSeek V4 Flash is compatible with the tested Claude Agent SDK path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

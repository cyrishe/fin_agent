from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.agent_providers.claude import ClaudeSdkSkillHarness


def _content(value: Any) -> Iterable[Any]:
    content = getattr(value, "content", None)
    return content if isinstance(content, list) else ()


async def _receive_turn(client: Any) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "session_id": "",
        "result": "",
        "result_ok": False,
        "stream_event_count": 0,
        "text_delta_count": 0,
        "tool_names": [],
        "subagent_message_count": 0,
        "duration_ms": 0,
    }
    started_at = time.monotonic()
    tool_names: set[str] = set()
    async for message in client.receive_response():
        class_name = type(message).__name__
        if class_name == "StreamEvent":
            evidence["stream_event_count"] += 1
            event = getattr(message, "event", None)
            event = event if isinstance(event, dict) else {}
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    evidence["text_delta_count"] += 1
        if getattr(message, "parent_tool_use_id", None):
            evidence["subagent_message_count"] += 1
        if class_name == "AssistantMessage":
            for block in _content(message):
                if type(block).__name__ == "ToolUseBlock":
                    name = str(getattr(block, "name", "") or "")
                    if name:
                        tool_names.add(name)
        if class_name == "ResultMessage":
            evidence["session_id"] = str(getattr(message, "session_id", "") or "")
            evidence["result"] = str(getattr(message, "result", "") or "")[:2_000]
            evidence["result_ok"] = (
                not bool(getattr(message, "is_error", False))
                and str(getattr(message, "subtype", "") or "") == "success"
            )
    evidence["tool_names"] = sorted(tool_names)
    evidence["duration_ms"] = round((time.monotonic() - started_at) * 1_000)
    return evidence


def _provider_env(provider: str, config_dir: Path) -> dict[str, str]:
    harness = ClaudeSdkSkillHarness(provider=provider, model="deepseek-v4-flash", query_impl=lambda **_: None)
    env = harness.provider_env()
    env.update(
        {
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
        }
    )
    return env


def _build_mcp_server(calls: list[str]) -> Any:
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool(
        "echo_probe",
        "Echo the supplied probe value. Use only when the user explicitly asks for the capability probe.",
        {
            "type": "object",
            "properties": {"value": {"type": "string", "minLength": 1, "maxLength": 100}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    async def echo_probe(args: dict[str, Any]) -> dict[str, Any]:
        value = str(args.get("value") or "")
        calls.append(value)
        return {
            "content": [{"type": "text", "text": f"MCP_OK:{value}"}],
            "structuredContent": {"ok": True, "value": value},
        }

    return create_sdk_mcp_server(name="fin_probe", version="1.0.0", tools=[echo_probe])


def _core_options(*, provider: str, env: dict[str, str], cwd: Path, server: Any, resume: str = "") -> Any:
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        tools=[],
        allowed_tools=["mcp__fin_probe__echo_probe"],
        disallowed_tools=["Bash", "Edit", "Write", "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Agent", "Task"],
        system_prompt=(
            "You are running a transport capability probe. Follow the user request exactly. "
            "Do not invent tool results and do not perform unrelated work."
        ),
        mcp_servers={"fin_probe": server},
        strict_mcp_config=True,
        permission_mode="dontAsk",
        setting_sources=[],
        cwd=str(cwd),
        include_partial_messages=True,
        max_turns=4,
        model="deepseek-v4-flash",
        effort="high",
        resume=resume or None,
        env=env,
    )


async def _run_core_once(provider: str, workspace: Path, config_dir: Path) -> dict[str, Any]:
    from claude_agent_sdk import ClaudeSDKClient

    nonce = f"FIN-{secrets.token_hex(4)}"
    calls: list[str] = []
    env = _provider_env(provider, config_dir)
    server = _build_mcp_server(calls)

    async with ClaudeSDKClient(options=_core_options(provider=provider, env=env, cwd=workspace, server=server)) as client:
        await client.query(
            f"Call echo_probe exactly once with value {nonce}. Then reply exactly FIRST_OK:{nonce}."
        )
        first = await _receive_turn(client)

    session_id = str(first.get("session_id") or "")
    resume: dict[str, Any]
    if session_id:
        resumed_server = _build_mcp_server(calls)
        async with ClaudeSDKClient(
            options=_core_options(
                provider=provider,
                env=env,
                cwd=workspace,
                server=resumed_server,
                resume=session_id,
            )
        ) as client:
            await client.query(
                "Continue the previous conversation. Without calling any tool, reply exactly "
                "RESUME_OK followed by a colon and the probe value from the previous user message."
            )
            resume = await _receive_turn(client)
    else:
        resume = {"result_ok": False, "result": "missing session id", "stream_event_count": 0}

    first_result = str(first.get("result") or "")
    resume_result = str(resume.get("result") or "")
    checks = {
        "mcp_executed": calls.count(nonce) == 1,
        "first_result": bool(first.get("result_ok")) and f"FIRST_OK:{nonce}" in first_result,
        "streaming": int(first.get("stream_event_count") or 0) > 0 and int(first.get("text_delta_count") or 0) > 0,
        "resume": bool(resume.get("result_ok")) and f"RESUME_OK:{nonce}" in resume_result,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "first": first,
        "resume": resume,
        "mcp_call_count": len(calls),
    }


async def _run_agent_tool_once(provider: str, workspace: Path, config_dir: Path) -> dict[str, Any]:
    from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ClaudeSDKClient

    marker = f"AGENT-{secrets.token_hex(4)}"
    options = ClaudeAgentOptions(
        tools=["Agent"],
        allowed_tools=["Agent"],
        disallowed_tools=["Bash", "Edit", "Write", "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Task"],
        system_prompt="This is an Agent Tool capability probe. Invoke the requested named agent exactly once.",
        agents={
            "probe-agent": AgentDefinition(
                description="Return the exact marker requested by the main agent.",
                prompt="Return only the marker supplied by the main agent, with no explanation and no tools.",
                tools=[],
                maxTurns=2,
            )
        },
        permission_mode="dontAsk",
        setting_sources=[],
        cwd=str(workspace),
        include_partial_messages=True,
        max_turns=4,
        model="deepseek-v4-flash",
        effort="high",
        env=_provider_env(provider, config_dir),
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            f"Use the Agent tool with subagent_type probe-agent and ask it to return {marker}. "
            f"Then reply exactly AGENT_OK:{marker}."
        )
        evidence = await _receive_turn(client)
    result = str(evidence.get("result") or "")
    checks = {
        "agent_tool_requested": "Agent" in evidence.get("tool_names", []),
        "agent_result": bool(evidence.get("result_ok")) and f"AGENT_OK:{marker}" in result,
    }
    return {"passed": all(checks.values()), "checks": checks, "evidence": evidence}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    core_runs: list[dict[str, Any]] = []
    agent_runs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="fin_cc_probe_") as tmp:
        root = Path(tmp)
        workspace = root / "workspace"
        config_dir = root / "claude"
        workspace.mkdir()
        config_dir.mkdir()
        for _ in range(args.runs):
            core_runs.append(
                await asyncio.wait_for(
                    _run_core_once(args.provider, workspace, config_dir),
                    timeout=args.timeout,
                )
            )
            if not args.skip_agent_tool:
                agent_runs.append(
                    await asyncio.wait_for(
                        _run_agent_tool_once(args.provider, workspace, config_dir),
                        timeout=args.timeout,
                    )
                )
    return {
        "provider": args.provider,
        "model": "deepseek-v4-flash",
        "runs": args.runs,
        "core_passed": all(item["passed"] for item in core_runs),
        "agent_tool_passed": all(item["passed"] for item in agent_runs) if agent_runs else None,
        "agent_tool_is_release_gate": False,
        "core": core_runs,
        "agent_tool": agent_runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe ClaudeSDKClient resume, in-process MCP, streaming, and Agent Tool behavior."
    )
    parser.add_argument("--provider", choices=["dashscope", "deepseek"], default="dashscope")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--skip-agent-tool", action="store_true")
    args = parser.parse_args()
    if args.runs < 1 or args.runs > 5:
        parser.error("--runs must be between 1 and 5")
    load_dotenv(REPO_ROOT / ".env", override=False)
    try:
        report = asyncio.run(_run(args))
    except asyncio.TimeoutError:
        print(json.dumps({"core_passed": False, "error": "probe timed out"}, ensure_ascii=False))
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {"core_passed": False, "error_type": type(exc).__name__, "error": str(exc)[:500]},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["core_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

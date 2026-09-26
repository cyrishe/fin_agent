from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.agent_providers import AgentCapabilityPolicy, ClaudeSdkSkillHarness, resolve_agent_profile
from src.services.agent_providers.claude import DEFAULT_CLAUDE_MODEL, DEFAULT_CLAUDE_PROVIDER


SMOKE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok"]},
        "message": {"type": "string"},
    },
    "required": ["status", "message"],
    "additionalProperties": False,
}


def _safe_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(result.get("ok")),
        "error": str(result.get("error") or "")[:500],
        "provider_session_id": str(result.get("provider_session_id") or ""),
        "duration_ms": int(result.get("duration_ms") or 0),
        "llm_usage": dict(result.get("llm_usage") or {}),
        "final": dict(result.get("final") or {}),
        "last_message": str(result.get("last_message") or "")[:1_000],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one paid Claude Agent SDK structured-output smoke test (default: DeepSeek official API + V4 Flash)."
    )
    parser.add_argument("--provider", choices=["anthropic", "deepseek", "dashscope", "gateway"])
    parser.add_argument("--model")
    parser.add_argument("--complexity", choices=["fastest", "fast", "mid", "high"])
    args = parser.parse_args()
    load_dotenv(REPO_ROOT / ".env")
    if args.provider:
        os.environ["CLAUDE_PROVIDER"] = args.provider
    if args.model:
        os.environ["CLAUDE_MODEL"] = args.model

    provider = os.environ.get("CLAUDE_PROVIDER", DEFAULT_CLAUDE_PROVIDER)
    profile = (
        resolve_agent_profile("claude", args.complexity, claude_transport_provider=provider)
        if args.complexity
        else None
    )
    harness = ClaudeSdkSkillHarness(
        provider=provider,
        model=args.model or (profile.model if profile else os.environ.get("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)),
        effort=profile.reasoning_effort if profile else "high",
        thinking=profile.thinking if profile else "",
        complexity_level=profile.level.value if profile else "",
        capabilities=AgentCapabilityPolicy(),
        timeout_seconds=90,
        hard_timeout_seconds=180,
        # Structured Output may need one model step to call the SDK-managed
        # output tool and another to finish the result envelope.
        max_turns=profile.max_turns if profile else 4,
        max_budget_usd=0.10,
    )
    if not harness.available():
        print(json.dumps({"ok": False, "error": "claude-agent-sdk is not installed"}, ensure_ascii=False))
        return 2
    result = harness.run_turn(
        prompt="Return status ok and a short confirmation that structured output works.",
        developer_instructions="This is a connectivity probe. Do not use tools. Return only the requested schema.",
        output_schema=SMOKE_SCHEMA,
        stage="provider_smoke",
    )
    print(json.dumps(_safe_summary(result), ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

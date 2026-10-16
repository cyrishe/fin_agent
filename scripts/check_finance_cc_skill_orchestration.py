from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.finance_cc_system_tools import FinanceCcSystemTools
from src.services.finance_claude_session_service import FinanceClaudeSessionService


def main() -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    with tempfile.TemporaryDirectory(prefix="finance_cc_skill_") as tmp:
        root = Path(tmp)
        service = FinanceClaudeSessionService(
            enabled=True,
            provider="dashscope",
            root_dir=root / "sessions",
            log_path=root / "events.jsonl",
            system_tools=FinanceCcSystemTools(),
        )
        result = service.run_turn(
            thread_id=1,
            owner_id="probe-owner",
            turn_id=1,
            user_text="/custom_tool create 我想做一个选股工具",
            context={
                "selected_agent": "investment_analyst",
                "turn_mode": "tool_development",
                "entry": "custom_tool_flow",
                "custom_tool_state": {},
            },
        )
    artifacts = [dict(item) for item in result.get("artifact_updates") or []]
    passed = (
        bool(result.get("ok"))
        and "Skill" in result.get("agent_tool_names", [])
        and any(item.get("artifact_type") == "requirement" for item in artifacts)
    )
    evidence = {
        "ok": result.get("ok"),
        "duration_ms": result.get("duration_ms"),
        "stream_event_count": result.get("stream_event_count"),
        "result": result.get("result"),
        "agent_tool_names": result.get("agent_tool_names"),
        "skill_results": result.get("skill_results"),
        "tool_calls": result.get("tool_calls"),
        "interaction_requests": result.get("interaction_requests"),
        "artifact_updates": result.get("artifact_updates"),
        "error": result.get("error"),
    }
    print(json.dumps({"passed": passed, "evidence": evidence}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

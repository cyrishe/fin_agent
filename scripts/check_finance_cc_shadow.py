from __future__ import annotations

import json
from pathlib import Path
import secrets
import sys
import tempfile

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.finance_claude_session_service import FinanceClaudeSessionService


def main() -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    marker = f"FINANCE-CC-{secrets.token_hex(4)}"
    with tempfile.TemporaryDirectory(prefix="finance_cc_shadow_") as tmp:
        root = Path(tmp)
        service = FinanceClaudeSessionService(
            enabled=True,
            provider="dashscope",
            root_dir=root / "sessions",
            log_path=root / "events.jsonl",
        )
        first = service.run_turn(
            thread_id=1,
            owner_id="probe-owner",
            turn_id=1,
            user_text=f"请记住标记 {marker}，只回复已记住。",
            context={"selected_agent": "investment_analyst", "turn_mode": "normal_qa", "entry": "agent_route"},
        )
        second = service.run_turn(
            thread_id=1,
            owner_id="probe-owner",
            turn_id=2,
            user_text="请只回复上一轮让我记住的完整标记。",
            context={"selected_agent": "investment_analyst", "turn_mode": "normal_qa", "entry": "agent_route"},
        )
    passed = bool(first.get("ok")) and bool(second.get("ok")) and bool(second.get("resumed")) and marker in str(second.get("result") or "")
    report = {
        "passed": passed,
        "provider": "dashscope",
        "model": "deepseek-v4-flash",
        "first": first,
        "second": second,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

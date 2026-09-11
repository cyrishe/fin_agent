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
    with tempfile.TemporaryDirectory(prefix="finance_cc_tools_") as tmp:
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
            user_text=(
                "这是系统工具连通性测试。必须只调用一次 read_finance_asset，asset_type=api_catalog，"
                "不要调用其他工具。读取成功后回复 TOOL_OK 和目录中的任意一个 subject 名称。"
            ),
            context={"selected_agent": "investment_analyst", "turn_mode": "normal_qa", "entry": "agent_route"},
        )
    calls = [dict(item) for item in result.get("tool_calls") or []]
    passed = bool(result.get("ok")) and any(item.get("tool") == "read_finance_asset" for item in calls) and "TOOL_OK" in str(result.get("result") or "")
    print(json.dumps({"passed": passed, "result": result}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

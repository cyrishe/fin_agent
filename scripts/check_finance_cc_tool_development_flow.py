from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.custom_tool_service import CustomToolAgentService, CustomToolRuntimeService, CustomToolStoreService
from src.services.finance_cc_system_tools import FinanceCcSystemTools
from src.services.finance_claude_session_service import FinanceClaudeSessionService


def main() -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    os.environ["FINANCE_CC_TOOL_DEVELOPMENT_ENABLED"] = "1"
    with tempfile.TemporaryDirectory(prefix="finance_cc_flow_") as tmp:
        root = Path(tmp)
        store = CustomToolStoreService(root_dir=str(root / "tools"))
        runtime = CustomToolRuntimeService(store=store)
        agent = CustomToolAgentService(store=store, runtime=runtime)
        finance_cc = FinanceClaudeSessionService(
            enabled=True,
            provider="dashscope",
            root_dir=root / "sessions",
            log_path=root / "events.jsonl",
            system_tools=FinanceCcSystemTools(
                custom_tool_store=store,
                custom_tool_runtime=runtime,
                implementation_runner=agent.implement_dynamic_tool,
            ),
        )
        agent.set_finance_cc_service(finance_cc)
        state = {}
        records = []
        events = []
        turns = [
            "我想做一个选股工具。",
            "你提出的问题都按推荐默认值处理，请更新并确认需求；这一轮先不要设计和实现。",
            "需求已经确认，请形成模块与流程设计；这一轮先不生成流程图，也不要实现代码。",
            "请根据已经保存的设计生成流程图，不要实现代码。",
        ]
        for index, text in enumerate(turns, start=1):
            result = agent.handle_turn(
                text,
                state=state,
                owner_id="finance-cc-flow-probe",
                thread_id=401,
                turn_id=index,
                event_sink=events.append,
            )
            state = dict(result.get("state") or {})
            cc = result.get("finance_cc") if isinstance(result.get("finance_cc"), dict) else {}
            records.append(
                {
                    "turn": index,
                    "input": text,
                    "ok": cc.get("ok"),
                    "resumed": cc.get("resumed"),
                    "duration_ms": cc.get("duration_ms"),
                    "message": result.get("message"),
                    "error": cc.get("error"),
                    "saved_artifacts": [
                        item.get("artifact_type")
                        for item in cc.get("artifact_updates") or []
                        if isinstance(item, dict)
                    ],
                    "tool_calls": [item.get("tool") for item in cc.get("tool_calls") or [] if isinstance(item, dict)],
                }
            )
            design = state.get("design_contract") if isinstance(state.get("design_contract"), dict) else {}
            if design and isinstance(design.get("flow"), dict) and design.get("flow"):
                break

    saved = [item for record in records for item in record["saved_artifacts"]]
    design = state.get("design_contract") if isinstance(state.get("design_contract"), dict) else {}
    event_types = sorted({f"{item.get('source')}:{item.get('type')}" for item in events if isinstance(item, dict)})
    passed = (
        "requirement" in saved
        and "design" in saved
        and "flow" in saved
        and bool(design.get("flow"))
        and all(record.get("ok") is True for record in records)
        and any(record.get("resumed") is True for record in records[1:])
        and "claude:tool_call" in event_types
    )
    print(
        json.dumps(
            {
                "passed": passed,
                "records": records,
                "tool_name": design.get("tool_name"),
                "flow_steps": len((design.get("flow") or {}).get("steps") or []),
                "event_types": event_types,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

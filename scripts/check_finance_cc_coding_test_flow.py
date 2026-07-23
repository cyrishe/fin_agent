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
    fixture = json.loads((REPO_ROOT / "tests/fixtures/golden_cross_30_60_design.json").read_text(encoding="utf-8"))
    design = dict(fixture.get("design") or {})
    state = {
        "owner_id": "finance-cc-coding-test-probe",
        "requirement_text": str((fixture.get("understanding") or {}).get("goal") or ""),
        "understanding": dict(fixture.get("understanding") or {}),
        "design_contract": design,
        "tool_name": str(design.get("tool_name") or ""),
    }
    with tempfile.TemporaryDirectory(prefix="finance_cc_coding_test_") as tmp:
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
        events = []
        coding = agent.handle_turn(
            "我确认当前设计，请开始实现；根据真实技术测试结果处理实现问题，完成后给我看实际样例结果。",
            state=state,
            owner_id=state["owner_id"],
            thread_id=501,
            turn_id=1,
            event_sink=events.append,
        )
        state = dict(coding.get("state") or state)
        testing = agent.handle_turn(
            "请进入测试阶段，选择一个能说明核心逻辑的真实样例运行，并把实际结果和核心中间指标给我确认。",
            state=state,
            owner_id=state["owner_id"],
            thread_id=501,
            turn_id=2,
            event_sink=events.append,
        )

    coding_cc = coding.get("finance_cc") if isinstance(coding.get("finance_cc"), dict) else {}
    testing_cc = testing.get("finance_cc") if isinstance(testing.get("finance_cc"), dict) else {}
    implementations = [dict(item) for item in coding_cc.get("implementation_runs") or [] if isinstance(item, dict)]
    implementation = implementations[-1] if implementations else {}
    meta = implementation.get("implementation_meta") if isinstance(implementation.get("implementation_meta"), dict) else {}
    artifacts = [
        item.get("artifact_type")
        for item in testing_cc.get("artifact_updates") or []
        if isinstance(item, dict)
    ]
    dynamic_runs = [dict(item) for item in testing_cc.get("dynamic_runs") or [] if isinstance(item, dict)]
    passed = (
        bool(implementation)
        and meta.get("provider") == "codex"
        and meta.get("complexity") == "mid"
        and (implementation.get("test_result") or {}).get("execution_ok") is True
        and bool(dynamic_runs)
        and "test_evidence" in artifacts
    )
    print(
        json.dumps(
            {
                "passed": passed,
                "coding": {
                    "ok": coding_cc.get("ok"),
                    "error": coding_cc.get("error"),
                    "duration_ms": coding_cc.get("duration_ms"),
                    "tool_calls": coding_cc.get("tool_calls"),
                    "message": coding.get("message"),
                    "implementation": implementation,
                },
                "testing": {
                    "ok": testing_cc.get("ok"),
                    "error": testing_cc.get("error"),
                    "duration_ms": testing_cc.get("duration_ms"),
                    "tool_calls": testing_cc.get("tool_calls"),
                    "message": testing.get("message"),
                    "dynamic_run_count": len(dynamic_runs),
                    "saved_artifacts": artifacts,
                },
                "event_types": sorted(
                    {f"{item.get('source')}:{item.get('type')}" for item in events if isinstance(item, dict)}
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

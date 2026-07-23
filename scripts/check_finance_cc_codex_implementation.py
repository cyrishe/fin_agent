from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.custom_tool_service import CustomToolAgentService, CustomToolRuntimeService, CustomToolStoreService
from src.services.finance_cc_system_tools import FinanceCcSystemTools


def main() -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    fixture = json.loads((REPO_ROOT / "tests/fixtures/golden_cross_30_60_design.json").read_text(encoding="utf-8"))
    design = dict(fixture.get("design") or {})
    with tempfile.TemporaryDirectory(prefix="finance_cc_codex_store_") as tmp:
        store = CustomToolStoreService(root_dir=tmp)
        runtime = CustomToolRuntimeService(store=store)
        agent = CustomToolAgentService(store=store, runtime=runtime)
        system_tools = FinanceCcSystemTools(
            custom_tool_store=store,
            custom_tool_runtime=runtime,
            implementation_runner=agent.implement_dynamic_tool,
        )
        tools, _, tracker = system_tools.build_tools(
            owner_ids=["finance-cc-implementation-probe"],
            tool_context={
                "custom_tool_state": {
                    "owner_id": "finance-cc-implementation-probe",
                    "requirement_text": str((fixture.get("understanding") or {}).get("goal") or ""),
                    "understanding": dict(fixture.get("understanding") or {}),
                    "design_contract": design,
                    "tool_name": str(design.get("tool_name") or ""),
                }
            },
        )
        tool_map = {item.name: item for item in tools}
        asyncio.run(
            tool_map["implement_dynamic_tool"].handler(
                {"instruction": "根据已确认的近30/60交易日金叉设计实现动态工具，并完成必要的样例技术测试。"}
            )
        )
        record = dict((tracker.get("implementation_runs") or [{}])[-1])

    meta = record.get("implementation_meta") if isinstance(record.get("implementation_meta"), dict) else {}
    bundle = meta.get("context_bundle") if isinstance(meta.get("context_bundle"), dict) else {}
    test_result = record.get("test_result") if isinstance(record.get("test_result"), dict) else {}
    evidence = {
        "ok": record.get("ok"),
        "message": record.get("message"),
        "tool": record.get("tool"),
        "test_summary": test_result.get("summary"),
        "test_execution_ok": test_result.get("execution_ok"),
        "provider": meta.get("provider"),
        "complexity": meta.get("complexity"),
        "model": meta.get("model"),
        "reasoning_effort": meta.get("reasoning_effort"),
        "duration_ms": meta.get("duration_ms"),
        "session_id": meta.get("session_id"),
        "provider_session_id": meta.get("provider_session_id"),
        "context_bundle": bundle,
    }
    passed = (
        bool(record.get("ok"))
        and meta.get("provider") == "codex"
        and bool(bundle.get("bundle_dir"))
        and test_result.get("execution_ok") is True
    )
    print(json.dumps({"passed": passed, "evidence": evidence}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

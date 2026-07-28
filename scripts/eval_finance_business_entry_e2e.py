from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval_finance_data_chat_e2e import (  # noqa: E402
    _FixtureAgentToolAdapter,
    _FixtureFinanceRuntime,
)
from src.scenarios.financial_qa.business_skills import (  # noqa: E402
    FinanceBusinessSkillCatalog,
)
from src.scenarios.financial_qa.service import FinancialQaCcService  # noqa: E402
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools  # noqa: E402
from src.services.application_runtime_service import ApplicationRuntimeService  # noqa: E402
from src.services.assistant_dispatch_planner import AssistantDispatchPlanner  # noqa: E402
from src.services.finance_claude_session_service import (  # noqa: E402
    FinanceClaudeSessionService,
)
from src.services.session_variable_store_service import (  # noqa: E402
    SessionVariableStoreService,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default="tests/evals/finance_business_entry_v1.json",
    )
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument(
        "--output",
        default="outputs/financial_qa_cc/eval_finance_business_entry_v1.json",
    )
    return parser.parse_args()


def _top_matches(
    plan: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    return all(str(plan.get(key) or "") == str(value) for key, value in expected.items())


def _cc_matches(
    *,
    expected_entry: str,
    expected_skill_id: str,
    tool_calls: list[dict[str, Any]],
    skill_entries: list[dict[str, Any]],
) -> bool:
    tool_names = [str(item.get("tool") or "") for item in tool_calls]
    skill_ids = [str(item.get("skill_id") or "") for item in skill_entries]
    if expected_entry == "direct":
        return not tool_names and not skill_ids
    if expected_entry == "finance_query":
        return "finance_query" in tool_names and not skill_ids
    if expected_entry == "skill":
        return expected_skill_id in skill_ids
    return expected_entry == "outside_financial_qa"


def main() -> int:
    args = _args()
    load_dotenv(ROOT / ".env", override=False)
    dataset = json.loads((ROOT / args.dataset).read_text(encoding="utf-8"))
    selected_ids = {str(item) for item in args.case}
    cases = [
        item
        for item in dataset.get("cases") or []
        if not selected_ids or str(item.get("id") or "") in selected_ids
    ]
    if args.limit > 0:
        cases = cases[: args.limit]

    runtime_root = (
        ROOT
        / "outputs"
        / "financial_qa_cc"
        / "_skill_entry_eval_runtime"
        / str(int(time.time() * 1_000))
    )
    system_tools = FinanceDataQueryCcTools(
        finance_runtime=_FixtureFinanceRuntime(),
        result_store=SessionVariableStoreService(data_root=runtime_root / "data"),
        tool_adapter=_FixtureAgentToolAdapter(),
    )
    skill_catalog = FinanceBusinessSkillCatalog()
    session_service = FinanceClaudeSessionService(
        enabled=True,
        root_dir=runtime_root / "sessions",
        log_path=runtime_root / "events.jsonl",
        system_tools=system_tools,
        system_prompt_path="src/scenarios/financial_qa/system.md",
        skill_root=skill_catalog.root,
        skill_names=skill_catalog.qualified_skill_names(),
        runtime_scope_prefix="financial_qa_entry_eval",
        max_turns=max(2, int(args.max_turns)),
        system_context_paths=[
            "src/scenarios/financial_qa/finance_api_protocol.md",
            "src/scenarios/financial_qa/data_query.md",
        ],
        effort="medium",
    )
    service = FinancialQaCcService(
        enabled=True,
        session_service=session_service,
        system_tools=system_tools,
        business_skill_catalog=skill_catalog,
    )
    planner = AssistantDispatchPlanner(
        agent_owned_runtime_names={"investment_analyst"},
    )
    application_context = ApplicationRuntimeService().get_application_context(
        "investment_workbench"
    )
    rows: list[dict[str, Any]] = []
    try:
        for index, case in enumerate(cases, start=1):
            case_id = str(case.get("id") or f"case-{index}")
            question = str(case.get("question") or "")
            started = time.monotonic()
            print(
                f"[{index}/{len(cases)}] {case_id}: planning",
                flush=True,
            )
            plan = planner.plan_turn(
                text=question,
                attachments=[],
                thread_context={},
                application_context=application_context,
            )
            expected_top = (
                case.get("expected_top")
                if isinstance(case.get("expected_top"), Mapping)
                else {}
            )
            top_ok = _top_matches(plan, expected_top)
            expected_cc_entry = str(case.get("expected_cc_entry") or "")
            expected_skill_id = str(case.get("expected_skill_id") or "")
            tool_calls: list[dict[str, Any]] = []
            skill_entries: list[dict[str, Any]] = []
            answer = ""
            error = ""
            progress_events: list[dict[str, Any]] = []
            accepted = service.accepts(dispatch_plan=plan)
            if expected_cc_entry != "outside_financial_qa" and accepted:
                print(
                    f"[{index}/{len(cases)}] {case_id}: Finance CC",
                    flush=True,
                )

                def capture(event: Mapping[str, Any]) -> None:
                    progress_events.append(
                        {
                            "elapsed_ms": round(
                                (time.monotonic() - started) * 1_000
                            ),
                            "type": str(event.get("type") or ""),
                            "content": str(event.get("content") or ""),
                        }
                    )

                result = service.answer(
                    thread_id=f"finance-business-entry-{case_id}",
                    turn_id=1,
                    owner_id="finance-business-entry-eval",
                    user_text=question,
                    dispatch_plan=plan,
                    application_context=application_context,
                    event_sink=capture,
                )
                evidence = (
                    result.get("financial_qa")
                    if isinstance(result.get("financial_qa"), Mapping)
                    else {}
                )
                tool_calls = [
                    dict(item)
                    for item in evidence.get("tool_calls") or []
                    if isinstance(item, Mapping)
                ]
                skill_entries = [
                    dict(item)
                    for item in evidence.get("skill_entries") or []
                    if isinstance(item, Mapping)
                ]
                answer = str(result.get("message") or "")
                error = str(evidence.get("error") or "")
            cc_ok = (
                not accepted
                if expected_cc_entry == "outside_financial_qa"
                else accepted
                and _cc_matches(
                    expected_entry=expected_cc_entry,
                    expected_skill_id=expected_skill_id,
                    tool_calls=tool_calls,
                    skill_entries=skill_entries,
                )
            )
            row = {
                "id": case_id,
                "question": question,
                "passed": top_ok and cc_ok,
                "duration_ms": round((time.monotonic() - started) * 1_000),
                "expected_top": dict(expected_top),
                "actual_top": {
                    "selected_agent": plan.get("selected_agent"),
                    "turn_mode": plan.get("turn_mode"),
                    "entry": plan.get("entry"),
                },
                "accepted_by_financial_qa": accepted,
                "expected_cc_entry": expected_cc_entry,
                "expected_skill_id": expected_skill_id,
                "tool_calls": tool_calls,
                "skill_entries": skill_entries,
                "answer": answer,
                "error": error,
                "first_progress_ms": (
                    progress_events[0]["elapsed_ms"] if progress_events else None
                ),
                "progress_event_count": len(progress_events),
            }
            rows.append(row)
            print(
                (
                    f"[{index}/{len(cases)}] {case_id}: "
                    f"{'PASS' if row['passed'] else 'FAIL'} "
                    f"top={row['actual_top']} skills={skill_entries} "
                    f"tools={[item.get('tool') for item in tool_calls]}"
                ),
                flush=True,
            )
    finally:
        service.close()

    report = {
        "version": dataset.get("version"),
        "max_turns": max(2, int(args.max_turns)),
        "passed": sum(1 for item in rows if item["passed"]),
        "total": len(rows),
        "cases": rows,
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"summary: {report['passed']}/{report['total']} passed; {output}",
        flush=True,
    )
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

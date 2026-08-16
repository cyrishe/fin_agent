from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog  # noqa: E402
from src.scenarios.financial_qa.research_mode import normalize_research_mode  # noqa: E402
from src.scenarios.financial_qa.service import FinancialQaCcService  # noqa: E402
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools  # noqa: E402
from src.services.application_runtime_service import ApplicationRuntimeService  # noqa: E402
from src.services.assistant_dispatch_planner import AssistantDispatchPlanner  # noqa: E402
from src.services.finance_claude_session_service import FinanceClaudeSessionService  # noqa: E402
from src.services.session_variable_store_service import SessionVariableStoreService  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default="tests/evals/finance_business_skills_real_v1.json",
    )
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-turns", type=int, default=24)
    parser.add_argument(
        "--output",
        default="outputs/financial_qa_cc/finance_business_skills_real_v1.json",
    )
    return parser.parse_args()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _event_row(event: Mapping[str, Any], *, started: float) -> dict[str, Any]:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
    return {
        "elapsed_ms": round((time.monotonic() - started) * 1_000),
        "source": str(event.get("source") or ""),
        "type": str(event.get("type") or ""),
        "content": str(event.get("content") or ""),
        "stage": str(metadata.get("stage") or ""),
        "title": str(metadata.get("title") or ""),
        "status": str(metadata.get("status") or ""),
    }


def _steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        stage = str(event.get("stage") or "").strip()
        title = str(event.get("title") or "").strip()
        content = str(event.get("content") or "").strip()
        event_type = str(event.get("type") or "").strip()
        if not (stage or title or content):
            continue
        if event_type not in {
            "stage_start",
            "stage_result",
            "progress",
            "reasoning_summary_delta",
            "tool_start",
            "tool_result",
            "assistant",
            "error",
        }:
            continue
        key = (stage, title, content)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "elapsed_ms": event.get("elapsed_ms"),
                "stage": stage,
                "title": title,
                "type": event_type,
                "content": content,
                "status": str(event.get("status") or ""),
            }
        )
    return rows


def _tool_metrics(tool_calls: list[dict[str, Any]]) -> dict[str, int]:
    finance_queries = [
        item for item in tool_calls if str(item.get("tool") or "") == "finance_query"
    ]
    return {
        "catalog_read_count": sum(
            1
            for item in tool_calls
            if str(item.get("tool") or "") == "read_finance_catalog"
        ),
        "reference_read_count": sum(
            1
            for item in tool_calls
            if str(item.get("tool") or "") == "read_finance_skill_reference"
        ),
        "finance_query_count": len(finance_queries),
        "zero_row_query_count": sum(
            1
            for item in finance_queries
            if "row_count" in item and int(item.get("row_count") or 0) == 0
        ),
        "validation_error_query_count": sum(
            1 for item in finance_queries if item.get("validation_errors")
        ),
        "result_load_count": sum(
            1
            for item in tool_calls
            if str(item.get("tool") or "") == "load_finance_result"
        ),
        "supplemental_tool_count": sum(
            1
            for item in tool_calls
            if str(item.get("tool") or "")
            not in {
                "read_finance_catalog",
                "read_finance_skill_reference",
                "finance_query",
                "load_finance_result",
            }
        ),
    }


def _case_research_mode(case: Mapping[str, Any]) -> str:
    return normalize_research_mode(case.get("research_mode"))


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(report), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = _args()
    load_dotenv(ROOT / ".env", override=False)
    dataset = json.loads((ROOT / args.dataset).read_text(encoding="utf-8"))
    selected_ids = {str(item) for item in args.case}
    cases = [
        dict(item)
        for item in dataset.get("cases") or []
        if isinstance(item, Mapping)
        and (not selected_ids or str(item.get("id") or "") in selected_ids)
    ]
    if args.limit > 0:
        cases = cases[: args.limit]

    output = ROOT / args.output
    run_stamp = str(int(time.time() * 1_000))
    runtime_root = (
        ROOT
        / "outputs"
        / "financial_qa_cc"
        / "_real_skill_eval_runtime"
        / run_stamp
    )
    skill_catalog = FinanceBusinessSkillCatalog()
    system_tools = FinanceDataQueryCcTools(
        result_store=SessionVariableStoreService(data_root=runtime_root / "results")
    )
    session_service = FinanceClaudeSessionService(
        enabled=True,
        root_dir=runtime_root / "sessions",
        log_path=runtime_root / "events.jsonl",
        system_tools=system_tools,
        system_prompt_path="src/scenarios/financial_qa/system.md",
        skill_root=skill_catalog.runtime_root,
        skill_names=skill_catalog.qualified_skill_names(),
        skill_snapshot_provider=skill_catalog.runtime_binding,
        skill_snapshot_validator=skill_catalog.validate_runtime_binding,
        runtime_scope_prefix="finance_business_real_eval",
        max_turns=max(4, int(args.max_turns)),
        warm_pool_size=0,
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
        agent_owned_runtime_names={"investment_analyst"}
    )
    application_context = ApplicationRuntimeService().get_application_context(
        "investment_workbench"
    )
    report: dict[str, Any] = {
        "version": dataset.get("version"),
        "description": dataset.get("description"),
        "started_at": _now_iso(),
        "max_turns": max(4, int(args.max_turns)),
        "runtime_mode": "real_finance_cc_and_real_finance_data",
        "cases": [],
    }
    _write_report(output, report)
    try:
        for index, case in enumerate(cases, start=1):
            case_id = str(case.get("id") or f"case-{index}")
            question = str(case.get("question") or "").strip()
            expected_skill_id = str(case.get("expected_skill_id") or "").strip()
            research_mode = _case_research_mode(case)
            started = time.monotonic()
            events: list[dict[str, Any]] = []
            print(f"[{index}/{len(cases)}] {case_id}: start", flush=True)
            row: dict[str, Any] = {
                "id": case_id,
                "question": question,
                "expected_skill_id": expected_skill_id,
                "requested_research_mode": research_mode,
                "research_depth": str(case.get("research_depth") or ""),
                "review_criteria": [
                    str(item)
                    for item in case.get("review_criteria") or []
                    if str(item).strip()
                ],
                "anti_patterns": [
                    str(item)
                    for item in case.get("anti_patterns") or []
                    if str(item).strip()
                ],
                "started_at": _now_iso(),
            }
            try:
                plan_started = time.monotonic()
                plan = planner.plan_turn(
                    text=question,
                    attachments=[],
                    thread_context={},
                    application_context=application_context,
                )
                row["routing_duration_ms"] = round(
                    (time.monotonic() - plan_started) * 1_000
                )
                row["routing"] = {
                    "selected_agent": plan.get("selected_agent"),
                    "turn_mode": plan.get("turn_mode"),
                    "entry": plan.get("entry"),
                }
                if not service.accepts(dispatch_plan=plan):
                    raise RuntimeError(
                        f"Finance CC did not accept routing: {row['routing']}"
                    )

                def capture(event: Mapping[str, Any]) -> None:
                    events.append(_event_row(event, started=started))

                result = service.answer(
                    # FinancialQaPresentationService uses the production
                    # numeric thread identity when it builds stable block IDs.
                    # Keep the owner and runtime root unique for isolation.
                    thread_id=int(run_stamp) + index,
                    turn_id=1,
                    owner_id=f"finance-business-real-eval-{run_stamp}",
                    user_text=question,
                    dispatch_plan=plan,
                    application_context=application_context,
                    research_mode=research_mode,
                    event_sink=capture,
                )
                evidence = (
                    result.get("financial_qa")
                    if isinstance(result.get("financial_qa"), Mapping)
                    else {}
                )
                skill_entries = [
                    dict(item)
                    for item in evidence.get("skill_entries") or []
                    if isinstance(item, Mapping)
                ]
                tool_calls = [
                    dict(item)
                    for item in evidence.get("tool_calls") or []
                    if isinstance(item, Mapping)
                ]
                actual_skill_ids = [
                    str(item.get("skill_id") or "").strip()
                    for item in skill_entries
                    if str(item.get("skill_id") or "").strip()
                ]
                skill_match = (
                    expected_skill_id in actual_skill_ids
                    if expected_skill_id
                    else not actual_skill_ids
                )
                row.update(
                    {
                        "session_id": str(evidence.get("session_id") or ""),
                        "resumed": bool(evidence.get("resumed")),
                        "finance_cc_duration_ms": int(
                            evidence.get("duration_ms") or 0
                        ),
                        "turn_timeout_seconds": int(
                            evidence.get("turn_timeout_seconds") or 0
                        ),
                        "first_progress_ms": (
                            events[0]["elapsed_ms"] if events else None
                        ),
                        "actual_skill_ids": actual_skill_ids,
                        "skill_entries": skill_entries,
                        "skill_match": skill_match,
                        "tool_calls": tool_calls,
                        "tool_names": [
                            str(item.get("tool") or "")
                            for item in tool_calls
                            if str(item.get("tool") or "")
                        ],
                        "tool_call_count": len(tool_calls),
                        "tool_metrics": _tool_metrics(tool_calls),
                        "result_refs": [
                            dict(item)
                            for item in evidence.get("result_refs") or []
                            if isinstance(item, Mapping)
                        ],
                        "research_mode": (
                            dict(result.get("research_mode") or {})
                            if isinstance(result.get("research_mode"), Mapping)
                            else {}
                        ),
                        "answer": str(result.get("message") or ""),
                        "error": str(evidence.get("error") or ""),
                    }
                )
            except Exception as exc:
                row.setdefault("skill_match", False)
                row.setdefault("tool_calls", [])
                row.setdefault("tool_names", [])
                row.setdefault("tool_call_count", 0)
                row.setdefault("answer", "")
                row["error"] = str(exc)
            row["events"] = events
            row["steps"] = _steps(events)
            row["step_count"] = len(row["steps"])
            row["answer_char_count"] = len(str(row.get("answer") or ""))
            row["answer_maybe_truncated"] = (
                row["answer_char_count"] == 2_000
            )
            row["total_duration_ms"] = round(
                (time.monotonic() - started) * 1_000
            )
            row["completed_at"] = _now_iso()
            row["execution_completed"] = bool(
                row.get("answer") and not row.get("error")
            )
            report["cases"].append(row)
            report["completed_cases"] = len(report["cases"])
            report["skill_matches"] = sum(
                1 for item in report["cases"] if item.get("skill_match")
            )
            report["execution_completed_cases"] = sum(
                1 for item in report["cases"] if item.get("execution_completed")
            )
            _write_report(output, report)
            print(
                (
                    f"[{index}/{len(cases)}] {case_id}: "
                    f"skill={row.get('actual_skill_ids')} "
                    f"tools={row.get('tool_call_count')} "
                    f"duration={row['total_duration_ms']}ms "
                    f"error={row.get('error') or '-'}"
                ),
                flush=True,
            )
    finally:
        service.close()

    report["finished_at"] = _now_iso()
    report["total_cases"] = len(report["cases"])
    _write_report(output, report)
    print(
        (
            f"summary: skill_match={report.get('skill_matches', 0)}/"
            f"{report['total_cases']}, execution_completed="
            f"{report.get('execution_completed_cases', 0)}/"
            f"{report['total_cases']}; {output}"
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

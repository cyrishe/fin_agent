#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = REPO_ROOT / "tests" / "evals" / "conversation_intent_v1.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "runtime_traces" / "evals" / "conversation_intent_v1_latest.json"


def _load_repo_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        raise RuntimeError(f"missing env file: {env_path}")
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError("python-dotenv is required for live evals") from exc
    load_dotenv(env_path, override=False)
    if not (os.getenv("LLM_API_KEY") or os.getenv("LLM_KEY") or os.getenv("DASHSCOPE_API_KEY")):
        raise RuntimeError("missing LLM_API_KEY, LLM_KEY or DASHSCOPE_API_KEY after loading .env")


def _load_dataset(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("eval dataset must be an object with a cases array")
    return payload


def _contains_all(text: str, values: list[Any]) -> bool:
    return all(str(value) in text for value in values)


def _evaluate_expectations(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    resolved = str(actual.get("resolved_question") or "")
    refs = actual.get("context_refs") if isinstance(actual.get("context_refs"), list) else []
    agent_name = str(actual.get("agent_name") or "")
    turn_mode = str(actual.get("turn_mode") or "")

    if expected.get("agent_name") and agent_name != str(expected["agent_name"]):
        failures.append(f"agent_name expected={expected['agent_name']} actual={agent_name}")
    if isinstance(expected.get("agent_name_in"), list) and agent_name not in expected["agent_name_in"]:
        failures.append(f"agent_name expected one of {expected['agent_name_in']} actual={agent_name}")
    if expected.get("turn_mode") and turn_mode != str(expected["turn_mode"]):
        failures.append(f"turn_mode expected={expected['turn_mode']} actual={turn_mode}")
    if isinstance(expected.get("turn_mode_in"), list) and turn_mode not in expected["turn_mode_in"]:
        failures.append(f"turn_mode expected one of {expected['turn_mode_in']} actual={turn_mode}")
    if turn_mode in (expected.get("forbidden_turn_modes") or []):
        failures.append(f"turn_mode is forbidden: {turn_mode}")
    if not _contains_all(resolved, expected.get("resolved_contains_all") or []):
        failures.append(f"resolved_question missing required text: {expected.get('resolved_contains_all')}")
    for group in expected.get("resolved_contains_any_groups") or []:
        if isinstance(group, list) and group and not any(str(value) in resolved for value in group):
            failures.append(f"resolved_question missing any of: {group}")
    for prefix in expected.get("context_ref_prefixes") or []:
        if not any(str(ref).startswith(str(prefix)) for ref in refs):
            failures.append(f"context_refs missing prefix: {prefix}")
    if "min_context_refs" in expected and len(refs) < int(expected["min_context_refs"]):
        failures.append(f"context_refs expected at least {expected['min_context_refs']} actual={len(refs)}")
    if "max_context_refs" in expected and len(refs) > int(expected["max_context_refs"]):
        failures.append(f"context_refs expected at most {expected['max_context_refs']} actual={len(refs)}")

    llm_policy = str(expected.get("llm_policy") or "").strip()
    context_source = str(actual.get("context_source") or "")
    interaction_source = str(actual.get("interaction_source") or "")
    if llm_policy == "required":
        if context_source != "llm":
            failures.append(f"context LLM required but source={context_source}")
        if interaction_source != "llm":
            failures.append(f"intent LLM required but source={interaction_source}")
    elif llm_policy == "forbidden":
        if context_source == "llm" or interaction_source == "llm":
            failures.append(
                f"LLM forbidden but context_source={context_source} interaction_source={interaction_source}"
            )
        if int(actual.get("llm_call_count") or 0) != 0:
            failures.append(f"LLM forbidden but llm_call_count={actual.get('llm_call_count')}")
    return failures


def _build_actual(result: dict[str, Any]) -> dict[str, Any]:
    request = result.get("normalized_request") if isinstance(result.get("normalized_request"), dict) else {}
    context = result.get("context_resolution") if isinstance(result.get("context_resolution"), dict) else {}
    interaction = result.get("interaction") if isinstance(result.get("interaction"), dict) else {}
    dispatch = result.get("dispatch_plan") if isinstance(result.get("dispatch_plan"), dict) else {}
    usage = result.get("llm_usage") if isinstance(result.get("llm_usage"), dict) else {}
    return {
        "ori_question": str(request.get("ori_question") or ""),
        "resolved_question": str(request.get("resolved_question") or request.get("round_task_desc") or ""),
        "context_refs": request.get("context_refs") if isinstance(request.get("context_refs"), list) else [],
        "context_source": str(context.get("source") or ""),
        "agent_name": str(dispatch.get("selected_agent") or interaction.get("agent_name") or ""),
        "turn_mode": str(dispatch.get("turn_mode") or interaction.get("turn_mode") or ""),
        "interaction_source": str(interaction.get("source") or ""),
        "dispatch_entry": str(dispatch.get("entry") or ""),
        "llm_call_count": int(usage.get("call_count") or 0),
        "llm_usage": usage,
    }


def run_eval(dataset_path: Path, output_path: Path, selected_ids: set[str]) -> dict[str, Any]:
    _load_repo_env()
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from src.services.application_runtime_service import ApplicationRuntimeService
    from src.services.conversation_preprocess_service import ConversationPreprocessService

    dataset = _load_dataset(dataset_path)
    application_name = str(dataset.get("application_name") or "investment_workbench")
    application_context = ApplicationRuntimeService().get_application_context(application_name)
    service = ConversationPreprocessService()
    case_results: list[dict[str, Any]] = []

    for case in dataset["cases"]:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or "").strip()
        if selected_ids and case_id not in selected_ids:
            continue
        started = time.perf_counter()
        error = ""
        actual: dict[str, Any] = {}
        failures: list[str] = []
        try:
            result = service.preprocess(
                text=str(case.get("input") or ""),
                attachments=case.get("attachments") if isinstance(case.get("attachments"), list) else [],
                thread_context=case.get("thread_context") if isinstance(case.get("thread_context"), dict) else {},
                application_context=application_context,
                enable_llm=True,
            )
            actual = _build_actual(result)
            failures = _evaluate_expectations(actual, case.get("expected") or {})
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            failures = [error]
        case_results.append(
            {
                "case_id": case_id,
                "category": str(case.get("category") or ""),
                "input": str(case.get("input") or ""),
                "expected": case.get("expected") or {},
                "actual": actual,
                "passed": not failures,
                "failures": failures,
                "error": error,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        )
        mark = "PASS" if not failures else "FAIL"
        print(f"[{mark}] {case_id} ({case_results[-1]['latency_ms']} ms)")
        for failure in failures:
            print(f"  - {failure}")

    passed = sum(1 for item in case_results if item["passed"])
    report = {
        "eval_name": str(dataset.get("eval_name") or dataset_path.stem),
        "protocol_version": str(dataset.get("protocol_version") or ""),
        "application_name": application_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_path": str(dataset_path),
        "summary": {
            "total": len(case_results),
            "passed": passed,
            "failed": len(case_results) - passed,
            "pass_rate": round(passed / len(case_results), 4) if case_results else 0.0,
        },
        "cases": case_results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"report={output_path}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Live eval for context resolution -> top intent routing")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="eval dataset JSON")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON report path")
    parser.add_argument("--case", action="append", default=[], help="run only one case_id; repeatable")
    args = parser.parse_args()
    report = run_eval(Path(args.cases).resolve(), Path(args.output).resolve(), set(args.case))
    return 0 if int(report["summary"]["failed"]) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

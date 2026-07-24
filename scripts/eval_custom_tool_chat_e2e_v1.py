#!/usr/bin/env python3
"""Run real multi-turn custom-tool conversations through the Chat API."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests" / "evals" / "natural_tool_conversations_v1.json"
DEFAULT_OUTPUT = ROOT / "data" / "runtime_traces" / "evals" / "custom_tool_chat_e2e_v1_latest.json"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _state(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("state")
    return dict(value) if isinstance(value, dict) else {}


def _surface_ids(payload: dict[str, Any]) -> list[str]:
    blocks = payload.get("surface_blocks")
    if not isinstance(blocks, list):
        return []
    return [_text(item.get("block_id")) for item in blocks if isinstance(item, dict) and _text(item.get("block_id"))]


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    state = _state(payload)
    plan = payload.get("dispatch_plan") if isinstance(payload.get("dispatch_plan"), dict) else {}
    turn = payload.get("tool_turn") if isinstance(payload.get("tool_turn"), dict) else {}
    test = payload.get("test_result") if isinstance(payload.get("test_result"), dict) else {}
    review = payload.get("implementation_review") if isinstance(payload.get("implementation_review"), dict) else {}
    explanation = (
        payload.get("implementation_explanation")
        if isinstance(payload.get("implementation_explanation"), dict)
        else {}
    )
    coding_tests = [dict(item) for item in payload.get("coding_tests") or [] if isinstance(item, dict)]
    finance_cc = payload.get("finance_cc") if isinstance(payload.get("finance_cc"), dict) else {}
    return {
        "ok": payload.get("ok", True),
        "message": _text(payload.get("message")),
        "tool_name": _text(state.get("tool_name")),
        "has_requirement": bool(
            state.get("requirement_brief")
            or state.get("understanding")
            or state.get("requirement_text")
        ),
        "has_design": isinstance(state.get("design_contract"), dict) and bool(state.get("design_contract")),
        "design_revision": state.get("design_revision"),
        "implementation_revision": state.get("implementation_revision"),
        "dispatch_entry": _text(plan.get("entry")),
        "turn_action": _text(turn.get("action")),
        "surface_blocks": _surface_ids(payload),
        "test": {
            "execution_ok": test.get("execution_ok"),
            "summary": _text(test.get("summary")),
            "error": _text(test.get("error")),
            "case_count": len([
                item for item in test.get("cases") or []
                if isinstance(item, dict)
            ]),
            "auto_repair_attempts": test.get("auto_repair_attempts"),
        } if test else {},
        "implementation_review": review,
        "implementation_explanation": explanation,
        "coding_tests": coding_tests,
        "finance_cc_tool_calls": [
            _text(item.get("tool"))
            for item in finance_cc.get("tool_calls") or []
            if isinstance(item, dict) and _text(item.get("tool"))
        ],
        "llm_usage": payload.get("llm_usage") if isinstance(payload.get("llm_usage"), dict) else {},
    }


class ChatE2E:
    def __init__(self, base_url: str, *, timeout: int = 1200) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.thread_id: int | None = None

    def dispatch(self, text: str) -> dict[str, Any]:
        response = self.session.post(
            f"{self.base_url}/api/chat/dispatch",
            json={
                "text": text,
                "thread_id": self.thread_id,
                "application_name": "investment_workbench",
                "attachment_ids": [],
            },
            timeout=self.timeout,
        )
        payload = self._json(response)
        self._remember_thread(payload)
        return payload

    def interaction(self, response_payload: dict[str, Any]) -> dict[str, Any]:
        state = _state(response_payload)
        interaction_response = {
            "interaction_id": "custom_tool.design_review",
            "action_id": "custom_tool.confirm_design",
            "action": "accept",
            "expected_revision": int(state.get("design_revision") or 1),
            "label": "确认设计并开始实现",
        }
        started = self.session.post(
            f"{self.base_url}/api/custom_tool/stream/start",
            json={
                "text": "",
                "thread_id": self.thread_id,
                "application_name": "investment_workbench",
                "interaction_response": interaction_response,
            },
            timeout=60,
        )
        start_payload = self._json(started)
        stream_url = _text(start_payload.get("stream_url"))
        if not stream_url:
            raise RuntimeError(f"stream start returned no stream_url: {start_payload}")
        if stream_url.startswith("/"):
            stream_url = f"{self.base_url}{stream_url}"
        events: list[dict[str, Any]] = []
        result: dict[str, Any] | None = None
        with self.session.get(stream_url, stream=True, timeout=self.timeout) as stream:
            stream.raise_for_status()
            for raw_line in stream.iter_lines(decode_unicode=True):
                line = _text(raw_line)
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                event = json.loads(raw)
                if isinstance(event, dict):
                    events.append(event)
                    if isinstance(event.get("result"), dict):
                        result = dict(event["result"])
                    if _text(event.get("event")) == "error":
                        raise RuntimeError(_text(event.get("message")) or "custom tool stream error")
        if result is None:
            raise RuntimeError(f"custom tool stream ended without result; events={len(events)}")
        self._remember_thread(result)
        result["_stream_event_count"] = len(events)
        result["_stream_event_types"] = [_text(item.get("event")) for item in events if _text(item.get("event"))]
        return result

    def _remember_thread(self, payload: dict[str, Any]) -> None:
        value = payload.get("thread_id")
        if str(value or "").isdigit():
            self.thread_id = int(value)

    @staticmethod
    def _json(response: requests.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:2000]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"invalid JSON response HTTP {response.status_code}: {response.text[:2000]}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"response is not an object: {payload!r}")
        if payload.get("ok") is False:
            raise RuntimeError(_text(payload.get("error")) or "API returned ok=false")
        return payload


def _action_for_followup(followup: dict[str, Any]) -> str:
    interaction = followup.get("interaction")
    return "button_confirm" if isinstance(interaction, dict) and _text(interaction.get("action_id")) else "natural_language"


def _summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cases": len(reports),
        "passed_cases": sum(1 for item in reports if item["passed"]),
        "failed_cases": sum(1 for item in reports if not item["passed"]),
        "terminal_counts": {
            key: sum(1 for item in reports if item.get("terminal") == key)
            for key in sorted({item.get("terminal") for item in reports})
        },
    }


def run(
    cases_path: Path,
    output_path: Path,
    base_url: str,
    *,
    max_cases: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    dataset = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = list(dataset.get("cases") or [])
    cases = cases[offset:]
    if max_cases is not None:
        cases = cases[:max_cases]
    reports: list[dict[str, Any]] = []

    def persist_snapshot() -> None:
        snapshot = {
            "eval_name": "custom_tool_chat_e2e_v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "base_url": base_url,
            "dataset_path": str(cases_path),
            "scope": "real_chat_api_multi_turn_design_coding_dynamic_test",
            "stop_policy": "terminate_case_on_api_error_or_failed_coding_test_or_internal_auto_repair",
            "partial": len(reports) < len(cases),
            "summary": _summary(reports),
            "cases": reports,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    for index, case in enumerate(cases, start=1):
        case_id = _text(case.get("case_id"))
        request_text = _text(case.get("request"))
        report: dict[str, Any] = {
            "case_id": case_id,
            "category": _text(case.get("category")),
            "passed": False,
            "terminal": "",
            "turns": [],
        }
        client = ChatE2E(base_url)
        pending = list(case.get("followups") or [])
        payload: dict[str, Any] | None = None
        last_custom_payload: dict[str, Any] | None = None
        continuation_attempts = 0
        try:
            started = time.perf_counter()
            payload = client.dispatch(request_text)
            if _state(payload):
                last_custom_payload = payload
            report["turns"].append({
                "turn": 1,
                "kind": "initial_request",
                "input": request_text,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "result": _compact(payload),
            })
        except Exception as exc:
            report["terminal"] = "initial_api_error"
            report["error"] = f"{type(exc).__name__}: {exc}"
            reports.append(report)
            print(f"[FAIL] {index:02d}/{len(cases)} {case_id} initial_api_error", flush=True)
            continue

        turn_no = 2
        while True:
            state = _state(payload)
            if state:
                last_custom_payload = payload
            test = payload.get("test_result") if isinstance(payload.get("test_result"), dict) else {}
            auto_repairs = test.get("auto_repair_attempts")
            if isinstance(auto_repairs, int) and auto_repairs > 0:
                report["terminal"] = "coding_internal_auto_repair"
                report["final"] = _compact(payload)
                report["error"] = "Coding 测试曾失败并触发了系统自动修复；按测试约定终止该条。"
                break
            if test.get("execution_ok") is True and not pending:
                report["passed"] = True
                report["terminal"] = "coding_test_passed"
                report["final"] = _compact(payload)
                break
            if test and test.get("execution_ok") is False:
                report["terminal"] = "coding_test_failed"
                report["final"] = _compact(payload)
                break
            if pending:
                followup = dict(pending.pop(0))
                kind = _text(followup.get("kind")) or "natural"
                input_text = _text(followup.get("text"))
                action = _action_for_followup(followup)
                started = time.perf_counter()
                try:
                    if action == "button_confirm":
                        payload = client.interaction(payload)
                    else:
                        payload = client.dispatch(input_text)
                    report["turns"].append({
                        "turn": turn_no,
                        "kind": kind,
                        "route": action,
                        "input": input_text,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                        "result": _compact(payload),
                    })
                except Exception as exc:
                    report["terminal"] = "followup_api_error"
                    report["error"] = f"{type(exc).__name__}: {exc}"
                    report["turns"].append({
                        "turn": turn_no,
                        "kind": kind,
                        "route": action,
                        "input": input_text,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                        "error": report["error"],
                    })
                    break
                turn_no += 1
                continue
            has_design = isinstance(state.get("design_contract"), dict) and bool(state.get("design_contract"))
            has_implementation = bool(state.get("tool_name") or state.get("implementation_revision"))
            if has_design and not has_implementation:
                started = time.perf_counter()
                try:
                    payload = client.interaction(last_custom_payload or payload)
                    if _state(payload):
                        last_custom_payload = payload
                    report["turns"].append({
                        "turn": turn_no,
                        "kind": "automatic_user_confirmation",
                        "route": "button_confirm",
                        "input": "",
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                        "result": _compact(payload),
                    })
                    turn_no += 1
                    continue
                except Exception as exc:
                    report["terminal"] = "confirmation_api_error"
                    report["error"] = f"{type(exc).__name__}: {exc}"
                    break
            has_requirement = bool(state.get("understanding") or state.get("requirement_text"))
            if last_custom_payload is not None and has_requirement and not has_design and continuation_attempts < 2:
                continuation_attempts += 1
                input_text = (
                    "基于目前已经明确的信息，请完成设计并继续实现这个工具。"
                    if continuation_attempts == 1
                    else "请不要再扩展范围，直接给出当前核心需求的完整设计方案，随后进入实现。"
                )
                started = time.perf_counter()
                try:
                    payload = client.dispatch(input_text)
                    report["turns"].append({
                        "turn": turn_no,
                        "kind": "automatic_user_continuation",
                        "route": "natural_language",
                        "input": input_text,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                        "result": _compact(payload),
                    })
                    turn_no += 1
                    continue
                except Exception as exc:
                    report["terminal"] = "continuation_api_error"
                    report["error"] = f"{type(exc).__name__}: {exc}"
                    break
            report["terminal"] = "non_terminal_assets_without_next_input"
            report["final"] = _compact(payload)
            break

        reports.append(report)
        persist_snapshot()
        mark = "PASS" if report["passed"] else "FAIL"
        print(f"[{mark}] {index:02d}/{len(cases)} {case_id} terminal={report['terminal']}", flush=True)

    summary = _summary(reports)
    result = {
        "eval_name": "custom_tool_chat_e2e_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "dataset_path": str(cases_path),
        "scope": "real_chat_api_multi_turn_design_coding_dynamic_test",
        "stop_policy": "terminate_case_on_api_error_or_failed_coding_test",
        "summary": summary,
        "cases": reports,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    print(f"report={output_path}", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-url", default="http://127.0.0.1:22053")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    run(args.cases, args.output, args.base_url, max_cases=args.max_cases, offset=args.offset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

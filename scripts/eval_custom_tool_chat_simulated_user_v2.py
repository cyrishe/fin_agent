#!/usr/bin/env python3
"""Run the custom-tool cases with a response-driven simulated user.

The dataset supplies only the initial request. Every later user turn is chosen
from the live response, current assets, unresolved questions, and real errors.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from eval_custom_tool_chat_e2e_v1 import ChatE2E, _state, _text, _compact


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests/evals/natural_tool_conversations_v1.json"


def _stage(payload: dict[str, Any]) -> str:
    state = _state(payload)
    test = payload.get("test_result") if isinstance(payload.get("test_result"), dict) else {}
    if test or state.get("implementation_revision") or state.get("tool_name"):
        return "coding"
    if state.get("design_contract"):
        return "design"
    if state.get("requirement_brief") or state.get("understanding") or state.get("requirement_text"):
        return "requirement"
    return "normal"


def _questions(payload: dict[str, Any]) -> list[str]:
    state = _state(payload)
    candidates: list[Any] = []
    for key in ("questions", "open_questions", "clarifications", "pending_questions"):
        candidates.extend(state.get(key) or [])
    design = state.get("design_contract") if isinstance(state.get("design_contract"), dict) else {}
    for key in ("questions", "open_questions", "clarifications"):
        candidates.extend(design.get(key) or [])
    result: list[str] = []
    for item in candidates:
        value = item.get("question") if isinstance(item, dict) else item
        value = _text(value)
        if value:
            result.append(value)
    return result


def _has_asset(payload: dict[str, Any], name: str) -> bool:
    return any(name in item for item in (_compact(payload).get("surface_blocks") or []))


class SimulatedUser:
    def __init__(
        self,
        client: ChatE2E,
        case: dict[str, Any],
        *,
        coding_via_natural_language: bool = False,
    ) -> None:
        self.client = client
        self.case = case
        self.turn_no = 1
        self.design_payload: dict[str, Any] | None = None
        self.viewed_flow = False
        self.feedback_rounds = 0
        self.coding_failures = 0
        self.coding_via_natural_language = coding_via_natural_language

    def next_turn(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        stage = _stage(payload)
        state = _state(payload)
        if stage == "requirement":
            return "natural_language", {
                "text": "请结合金融数据能力先收敛成一个可执行的核心方案；不确定的细节请用合理默认值，并只列真正影响结果的口径。"
            }
        if stage == "design":
            self.design_payload = payload
            if _has_asset(payload, "flow") and not self.viewed_flow:
                self.viewed_flow = True
                return "natural_language", {"text": "请先展示当前设计的流程图，我确认逻辑结构后再继续。"}
            questions = _questions(payload)
            if questions and self.feedback_rounds < 2:
                self.feedback_rounds += 1
                question = questions[0]
                return "natural_language", {
                    "text": f"针对你提出的“{question}”，先按当前方案的默认口径执行；缺失数据保留为数据不足，不要扩展其他功能。"
                }
            state_revision = int(state.get("design_revision") or 1)
            if self.coding_via_natural_language:
                return "natural_language", {
                    "text": "当前设计符合我的要求，请按这个设计进入代码实现、运行验证和需求—Design—Code静态检查。"
                }
            return "button", {
                "payload": payload,
                "interaction_response": {
                    "interaction_id": "custom_tool.design_review",
                    "action_id": "custom_tool.confirm_design",
                    "action": "accept",
                    "expected_revision": state_revision,
                    "label": "确认设计并开始实现",
                },
            }
        if stage == "coding":
            test = payload.get("test_result") if isinstance(payload.get("test_result"), dict) else {}
            if test.get("execution_ok") is False:
                self.coding_failures += 1
                error = _text(test.get("error")) or _text(payload.get("message")) or "真实技术测试未通过"
                return "natural_language", {
                    "text": f"刚才真实运行反馈为：{error}。请只修复导致这次失败的相关模块，保留其他逻辑，并重新执行聚焦测试。"
                }
            if test.get("execution_ok") is True:
                return "done", {}
            return "natural_language", {
                "text": "请继续当前实现，完成入口联调和至少一个真实可解释的技术测试，并返回测试结果。"
            }
        if self.design_payload is not None:
            return "button", {
                "payload": self.design_payload,
                "interaction_response": {
                    "interaction_id": "custom_tool.design_review",
                    "action_id": "custom_tool.confirm_design",
                    "action": "accept",
                    "expected_revision": int(_state(self.design_payload).get("design_revision") or 1),
                    "label": "确认设计并开始实现",
                },
            }
        return "natural_language", {"text": "请回到刚才的工具需求，继续完成设计并实现核心功能。"}


def run_case(
    case: dict[str, Any],
    base_url: str,
    *,
    timeout: int,
    max_turns: int,
    coding_via_natural_language: bool = False,
) -> dict[str, Any]:
    case_id = _text(case.get("case_id"))
    client = ChatE2E(base_url, timeout=timeout)
    user = SimulatedUser(
        client,
        case,
        coding_via_natural_language=coding_via_natural_language,
    )
    started = time.perf_counter()
    report: dict[str, Any] = {
        "case_id": case_id,
        "category": _text(case.get("category")),
        "passed": False,
        "turns": [],
        "stage_elapsed_ms": {"requirement": 0, "design": 0, "coding": 0, "normal": 0, "view": 0},
        "coding_failure_count": 0,
        "failure_reasons": [],
    }
    try:
        payload = client.dispatch(_text(case.get("request")))
    except Exception as exc:
        report.update({"terminal": "initial_api_timeout" if isinstance(exc, requests.Timeout) else "initial_api_error", "error": f"{type(exc).__name__}: {exc}"})
        report["total_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return report

    for turn in range(1, max_turns + 1):
        current_stage = _stage(payload)
        if current_stage == "coding":
            test = payload.get("test_result") if isinstance(payload.get("test_result"), dict) else {}
            if test.get("execution_ok") is False:
                report["coding_failure_count"] += 1
                reason = _text(test.get("error")) or _text(payload.get("message")) or "技术测试失败"
                report["failure_reasons"].append(reason[:500])
            compact = _compact(payload)
            if test.get("execution_ok") is True:
                review = compact.get("implementation_review") or {}
                explanation = compact.get("implementation_explanation") or {}
                test_evidence = compact.get("test") or {}
                calls = compact.get("finance_cc_tool_calls") or []
                evidence_complete = (
                    bool(review)
                    and bool(explanation)
                    and int(test_evidence.get("case_count") or 0) > 0
                )
                implementation_is_terminal = (
                    not coding_via_natural_language
                    or not calls
                    or calls[-1] == "implement_dynamic_tool"
                )
                report["passed"] = evidence_complete and implementation_is_terminal
                report["terminal"] = (
                    "coding_verified"
                    if report["passed"]
                    else "coding_missing_review_or_terminal_boundary"
                )
                if not evidence_complete:
                    report["failure_reasons"].append(
                        "缺少实现说明、需求对齐说明或真实运行 case"
                    )
                if not implementation_is_terminal:
                    report["failure_reasons"].append(
                        f"implement_dynamic_tool 后仍有工具调用：{calls}"
                    )
        compact_payload = _compact(payload)
        report["turns"].append({"turn": turn, "stage": current_stage, "result": compact_payload})
        if report["passed"]:
            break
        if turn == max_turns:
            report["terminal"] = "max_turns_reached"
            break
        action, details = user.next_turn(payload)
        if action == "done":
            report["terminal"] = "coding_without_test_result"
            break
        if action == "button":
            call_started = time.perf_counter()
            try:
                payload = client.interaction(details["payload"])
            except Exception as exc:
                report["terminal"] = "button_api_timeout" if isinstance(exc, requests.Timeout) else "button_api_error"
                report["error"] = f"{type(exc).__name__}: {exc}"
                break
        else:
            call_started = time.perf_counter()
            try:
                payload = client.dispatch(details["text"])
            except Exception as exc:
                report["terminal"] = "followup_api_timeout" if isinstance(exc, requests.Timeout) else "followup_api_error"
                report["error"] = f"{type(exc).__name__}: {exc}"
                break
        elapsed = round((time.perf_counter() - call_started) * 1000, 2)
        response_stage = _stage(payload)
        if response_stage == "normal" and action == "natural_language":
            response_stage = "view" if any(word in details["text"] for word in ("流程图", "代码", "资产")) else "normal"
        report["stage_elapsed_ms"][response_stage] = report["stage_elapsed_ms"].get(response_stage, 0) + elapsed
    else:
        report["terminal"] = "max_turns_reached"
    report["coding_failure_count"] = user.coding_failures or report["coding_failure_count"]
    report["total_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    if not report.get("terminal"):
        report["terminal"] = "max_turns_reached"
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:22053")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--coding-via-natural-language", action="store_true")
    args = parser.parse_args()
    dataset = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = list(dataset.get("cases") or [])[args.offset:]
    if args.max_cases is not None:
        cases = cases[:args.max_cases]
    reports: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=args.offset + 1):
        report = run_case(
            case,
            args.base_url,
            timeout=args.timeout,
            max_turns=args.max_turns,
            coding_via_natural_language=args.coding_via_natural_language,
        )
        reports.append(report)
        mark = "PASS" if report.get("passed") else "FAIL"
        print(f"[{mark}] {index:02d}/{len(dataset.get('cases') or [])} {report['case_id']} {report.get('terminal')}", flush=True)
    summary = {
        "cases": len(reports),
        "passed_cases": sum(item.get("passed") is True for item in reports),
        "failed_cases": sum(item.get("passed") is not True for item in reports),
        "total_elapsed_ms": sum(float(item.get("total_elapsed_ms") or 0) for item in reports),
        "coding_failure_count": sum(int(item.get("coding_failure_count") or 0) for item in reports),
        "terminal_counts": {key: sum(item.get("terminal") == key for item in reports) for key in sorted({item.get("terminal") for item in reports})},
    }
    result = {
        "eval_name": "custom_tool_chat_simulated_user_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_path": str(args.cases.resolve()),
        "base_url": args.base_url,
        "simulation": "later turns are chosen from live response/state/assets/errors; dataset followups are ignored",
        "summary": summary,
        "cases": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    print(f"report={args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

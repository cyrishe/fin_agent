#!/usr/bin/env python3
"""Run response-driven Requirement -> Design -> Coding checks without conversation DB."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.custom_tool_service import (  # noqa: E402
    CustomToolAgentService,
    CustomToolRuntimeService,
    CustomToolStoreService,
)


CASES = [
    {
        "case_id": "e2e_momentum_drawdown",
        "request": (
            "创建一个个股动量工具，比较指定股票20日、60日和120日收益率，"
            "同时计算最近20日最大回撤，给出强弱判断和关键依据。"
        ),
    },
    {
        "case_id": "e2e_profit_cash_quality",
        "request": (
            "创建一个盈利质量评价工具，比较公司最近年度净利润增长和经营现金流增长，"
            "识别利润增长明显快于现金流的情况，并展示用于判断的核心数据。"
        ),
    },
]


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _questions(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in result.get("questions") or [] if isinstance(item, Mapping)]


def _reply_to_questions(result: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for item in _questions(result):
        candidates = [_trim(value) for value in item.get("candidate") or [] if _trim(value)]
        choice = candidates[0] if candidates else "按你的金融专业判断采用合理默认值"
        parts.append(f"关于“{_trim(item.get('question'))}”，采用“{choice}”。")
    return " ".join(parts) or "按当前需求和合理默认值继续完成设计。"


def _event_stages(result: Mapping[str, Any]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for event in result.get("events") or []:
        if not isinstance(event, Mapping):
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        if _trim(event.get("type")) != "stage_result":
            continue
        stage = _trim(metadata.get("stage")) or "unknown"
        totals[stage] = totals.get(stage, 0) + int(metadata.get("duration_ms") or 0)
    return totals


def run_case(case: Mapping[str, Any]) -> dict[str, Any]:
    owner_id = f"isolated-eval-{uuid.uuid4().hex[:12]}"
    report: dict[str, Any] = {
        "case_id": _trim(case.get("case_id")),
        "initial_request": _trim(case.get("request")),
        "turns": [],
        "passed": False,
    }
    started_case = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="fin_agent_isolated_eval_") as root:
        store = CustomToolStoreService(root_dir=root)
        agent = CustomToolAgentService(
            store=store,
            runtime=CustomToolRuntimeService(store=store),
        )
        state: dict[str, Any] = {}
        user_text = report["initial_request"]
        design_result: dict[str, Any] = {}
        for turn_no in range(1, 4):
            started = time.perf_counter()
            design_result = agent.start_create(
                user_text,
                owner_id=owner_id,
                state=state,
                selected_skills=["financial-tool-requirement"],
                turn_id=turn_no,
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            state = dict(design_result.get("state") or {})
            report["turns"].append({
                "turn": turn_no,
                "stage": "design" if state.get("design_contract") else "requirement",
                "user_input": user_text,
                "agent_message": _trim(design_result.get("message")),
                "elapsed_ms": elapsed_ms,
                "requirement_brief": _trim(
                    state.get("requirement_brief")
                    or (state.get("understanding") or {}).get("requirement_brief")
                ),
                "questions": _questions(design_result),
                "design": dict(state.get("design_contract") or {}),
                "stage_duration_ms": _event_stages(design_result),
            })
            if state.get("design_contract"):
                break
            if not _questions(design_result):
                report["terminal"] = "requirement_without_questions_or_design"
                break
            user_text = _reply_to_questions(design_result)

        if not state.get("design_contract"):
            report.setdefault("terminal", "design_not_reached")
            report["total_elapsed_ms"] = round((time.perf_counter() - started_case) * 1000, 2)
            return report

        started = time.perf_counter()
        coding = agent.continue_flow_action(
            "custom_tool.confirm_design",
            state=state,
            owner_id=owner_id,
            turn_id=len(report["turns"]) + 1,
        )
        coding_elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        tool = dict(coding.get("tool") or {})
        test_result = dict(coding.get("test_result") or {})
        modules = [dict(item) for item in tool.get("modules") or [] if isinstance(item, Mapping)]
        report["turns"].append({
            "turn": len(report["turns"]) + 1,
            "stage": "coding",
            "user_input": "确认当前设计并进入代码实现。",
            "agent_message": _trim(coding.get("message")),
            "elapsed_ms": coding_elapsed_ms,
            "implementation_explanation": dict(coding.get("implementation_explanation") or {}),
            "implementation_review": dict(coding.get("implementation_review") or {}),
            "coding_tests": [
                dict(item) for item in coding.get("coding_tests") or [] if isinstance(item, Mapping)
            ],
            "test_result": test_result,
            "modules": modules,
            "stage_duration_ms": {
                "coding": int((coding.get("implementation_meta") or {}).get("duration_ms") or 0),
            },
        })
        report["tool_name"] = _trim((tool.get("manifest") or {}).get("tool_name"))
        report["implementation_revision"] = int((tool.get("manifest") or {}).get("current_revision") or 0)
        report["execution_ok"] = test_result.get("execution_ok") is True
        report["contract_ok"] = test_result.get("contract_ok") is True
        report["passed"] = report["execution_ok"] and bool(modules)
        report["terminal"] = "coding_completed" if report["passed"] else "coding_failed"
        report["total_elapsed_ms"] = round((time.perf_counter() - started_case) * 1000, 2)
        return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/evals/requirement_design_coding_isolated_20260724.json"),
    )
    parser.add_argument("--max-cases", type=int, default=len(CASES))
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    reports: list[dict[str, Any]] = []
    for case in CASES[: max(0, args.max_cases)]:
        report = run_case(case)
        reports.append(report)
        print(
            f"[{'PASS' if report['passed'] else 'FAIL'}] "
            f"{report['case_id']} {report.get('terminal')} "
            f"{report.get('total_elapsed_ms')}ms",
            flush=True,
        )
    payload = {
        "eval_name": "requirement_design_coding_isolated",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "真实模型 Requirement -> Design -> Coding；使用隔离工具存储，"
            "不依赖当前不可连接的系统会话数据库。"
        ),
        "summary": {
            "cases": len(reports),
            "passed": sum(item.get("passed") is True for item in reports),
            "failed": sum(item.get("passed") is not True for item in reports),
        },
        "cases": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report={args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

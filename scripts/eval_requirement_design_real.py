#!/usr/bin/env python3
"""Run realistic, response-driven Requirement -> Design conversations."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


CASES = [
    {
        "case_id": "clear_gap_cross",
        "request": "/custom_tool create 输入指定A股，按前复权收盘价判断最近30个交易日内是否出现MA5上穿MA20；多次出现取最近一次，输出是否命中、日期、当日MA5和MA20，数据不足就明确返回数据不足。",
    },
    {
        "case_id": "medium_momentum",
        "request": "/custom_tool create 做一个股票动量工具，看看一只股票最近一段时间的走势是否比较强，给出结果和关键依据。",
    },
    {
        "case_id": "vague_selector",
        "request": "/custom_tool create 我想做一个选股工具，帮我找出值得关注的股票。",
    },
]


def text(value: Any, limit: int = 12000) -> str:
    if isinstance(value, str):
        result = value.strip()
    else:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return result if len(result) <= limit else result[:limit] + "…"


def state_of(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("state")
    return dict(value) if isinstance(value, dict) else {}


def questions_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in payload.get("questions") or [] if isinstance(item, dict)]


def design_of(payload: dict[str, Any]) -> dict[str, Any]:
    state = state_of(payload)
    value = state.get("design_contract")
    if isinstance(value, dict) and value:
        return value
    value = payload.get("design")
    return dict(value) if isinstance(value, dict) else {}


def dynamic_user_reply(payload: dict[str, Any]) -> str:
    questions = questions_of(payload)
    if questions:
        parts = []
        for item in questions:
            candidates = [str(value).strip() for value in item.get("candidate") or [] if str(value).strip()]
            choice = candidates[0] if candidates else "按你的默认建议处理"
            parts.append(f'针对“{text(item.get("question"), 300)}”，我按“{choice}”处理。')
        return " ".join(parts)
    return "目前信息已经足够，请基于已确认的需求继续形成完整设计方案。"


def context_summary(payload: dict[str, Any]) -> dict[str, Any]:
    state = state_of(payload)
    design = design_of(payload)
    cc = payload.get("finance_cc") if isinstance(payload.get("finance_cc"), dict) else {}
    return {
        "thread_id": payload.get("thread_id"),
        "requirement_brief": text(state.get("requirement_brief"), 2400),
        "has_design": bool(design),
        "design_keys": sorted(design),
        "question_count": len(questions_of(payload)),
        "cc_ok": cc.get("ok"),
        "cc_error": text(cc.get("error"), 1000),
        "artifact_types": [
            str(item.get("artifact_type"))
            for item in cc.get("artifact_updates") or []
            if isinstance(item, dict) and item.get("artifact_type")
        ],
    }


def judgment(payload: dict[str, Any], *, reached_design: bool, error: str = "") -> str:
    if error:
        return "外部/API异常：本轮未能完成判断。"
    if reached_design:
        return "基本可行：需求经过必要交互后自然形成了 Design，且没有额外流程确认关卡。"
    if questions_of(payload):
        return "基本可行：只提出了影响核心结果的待确认问题。"
    return "需要继续观察：本轮尚未形成 Design，也没有返回可回答的问题。"


def dispatch(session: requests.Session, base_url: str, thread_id: int | None, user_text: str, timeout: int) -> dict[str, Any]:
    response = session.post(
        f"{base_url}/api/chat/dispatch",
        json={
            "text": user_text,
            "thread_id": thread_id,
            "application_name": "investment_workbench",
            "attachment_ids": [],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("API response is not an object")
    return payload


def run(base_url: str, output: Path, max_turns: int = 3) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for case in CASES:
        session = requests.Session()
        thread_id: int | None = None
        turns: list[dict[str, Any]] = []
        current_text = case["request"]
        started_case = time.perf_counter()
        terminal = ""
        for turn_number in range(1, max_turns + 1):
            started = time.perf_counter()
            try:
                payload = dispatch(session, base_url, thread_id, current_text, timeout=900)
                thread_id = int(payload.get("thread_id")) if str(payload.get("thread_id") or "").isdigit() else thread_id
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                design = design_of(payload)
                reached_design = bool(design)
                turns.append({
                    "dialogue_id": thread_id,
                    "turn_id": payload.get("turn_id") or turn_number,
                    "turn": turn_number,
                    "stage": "design" if reached_design else "requirement",
                    "user_question": current_text,
                    "agent_answer": text(payload.get("message")),
                    "context": context_summary(payload),
                    "elapsed_ms": elapsed_ms,
                    "judgment": judgment(payload, reached_design=reached_design),
                    "evidence_or_issue": "已保存 requirement、design 和/或 flow 资产。" if reached_design else "等待真实返回的问题或下一步需求收敛。",
                    "route": "natural_language",
                    "response_keys": sorted(payload),
                })
                if reached_design:
                    terminal = "design_reached"
                    break
                current_text = dynamic_user_reply(payload)
            except Exception as exc:  # Keep the现场 in the report and continue other cases.
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                turns.append({
                    "dialogue_id": thread_id,
                    "turn_id": turn_number,
                    "turn": turn_number,
                    "stage": "error",
                    "user_question": current_text,
                    "agent_answer": "",
                    "context": {"thread_id": thread_id},
                    "elapsed_ms": elapsed_ms,
                    "judgment": judgment({}, reached_design=False, error=str(exc)),
                    "evidence_or_issue": f"{type(exc).__name__}: {exc}",
                    "route": "natural_language",
                    "response_keys": [],
                })
                terminal = "api_error"
                break
        if not terminal:
            terminal = "max_turns_without_design"
        reports.append({
            "case_id": case["case_id"],
            "category": "requirement_to_design_realistic",
            "passed": terminal == "design_reached",
            "terminal": terminal,
            "total_elapsed_ms": round((time.perf_counter() - started_case) * 1000, 2),
            "turns": turns,
        })
        print(f"[{terminal}] {case['case_id']} turns={len(turns)}", flush=True)
    result = {
        "eval_name": "requirement_design_realistic_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "scope": "real_response_driven_requirement_to_design_only",
        "cases": reports,
        "summary": {
            "cases": len(reports),
            "design_reached": sum(item["terminal"] == "design_reached" for item in reports),
            "api_errors": sum(item["terminal"] == "api_error" for item in reports),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False), flush=True)
    print(f"report={output}", flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:22053")
    parser.add_argument("--output", type=Path, default=Path("data/runtime_traces/evals/requirement_design_realistic_v1.json"))
    parser.add_argument("--max-turns", type=int, default=3)
    args = parser.parse_args()
    run(args.base_url, args.output, args.max_turns)

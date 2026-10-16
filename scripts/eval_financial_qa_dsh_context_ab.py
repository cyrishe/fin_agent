#!/usr/bin/env python3
"""Isolated real-model finance QA replay; run separately in each code snapshot.

Uses the canonical deployment env without copying credentials. No HTTP users,
monitoring records, or production conversations are created. Full trace/data
are internal artifacts; the console only prints aggregate case metrics.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path


CASES = [
    ("quote5", "贵州茅台最近五个交易日的收盘价和涨跌幅是多少？"),
    ("quote10", "查询宁德时代最近10个交易日的收盘价和涨跌幅。"),
    ("report", "不同机构对今世缘的估值方法有何差异？"),
    ("multi", "给我最近三个月研报对中际旭创看多看空的比例，和企业的净利润走势"),
    ("boundary", "兴森科技2024年第四季度的出货量是多少？"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", default=",".join(key for key, _ in CASES))
    parser.add_argument("--cases-file", type=Path, help="JSON list of {id, question}; used without rewriting questions")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--execution-mode", choices=["standard", "fast"], default="standard")
    parser.add_argument("--data-only", action="store_true")
    parser.add_argument("--query-reasoning", choices=["low", "off"])
    parser.add_argument("--empty-result-early-stop", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.chdir(root)
    from dotenv import load_dotenv

    load_dotenv(args.env_file.resolve(), override=True)
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    tools = FinanceDataQueryCcTools()
    policy = {}
    if args.query_reasoning:
        policy["budgets"] = {"query": {"reasoningEffort": args.query_reasoning}}
    if args.empty_result_early_stop is not None:
        policy["emptyResultEarlyStop"] = args.empty_result_early_stop
    dsh = FinanceDeepSeekHarnessSessionService(
        enabled=True, system_tools=tools, worker_count=1,
        root_dir=output / "runtime", log_path=output / "events.jsonl",
        loop_policy_config=policy or None,
    )
    # CC is deliberately not instantiated in this DSH-only replay.
    service = FinancialQaCcService(
        enabled=True, system_tools=tools, session_service=object(),
        dsh_session_service=dsh,
    )
    selected = set(args.cases.split(","))
    cases = (
        [(item["id"], item["question"]) for item in json.loads(args.cases_file.read_text(encoding="utf-8"))]
        if args.cases_file else [(key, question) for key, question in CASES if key in selected]
    )
    try:
        for repeat in range(args.repeat):
            for case_id, question in cases:
                request_id = uuid.uuid4().hex
                started = time.monotonic()
                response = service.answer(
                    thread_id=request_id, turn_id=request_id, owner_id="context-ab-evaluation",
                    user_text=question,
                    dispatch_plan={"selected_agent": "investment_analyst", "turn_mode": "normal_qa",
                                   "entry": "agent_route", "semantic_turn": {"resolved_question": question}},
                    runtime="dsh", research_mode="fast", execution_mode=args.execution_mode,
                    data_only=args.data_only, isolated_request=True, response_data_max_rows=5,
                )
                record = json.loads((output / "events.jsonl").read_text().splitlines()[-1])
                row = {
                    "id": case_id, "repeat": repeat + 1, "question": question,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "record": record, "response": response,
                }
                (output / f"{case_id}-{repeat + 1}.json").write_text(
                    json.dumps(row, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
                )
                print(json.dumps({
                    "case": case_id, "repeat": repeat + 1,
                    "elapsed_ms": row["elapsed_ms"], "error": record.get("error"),
                    "usage": record.get("llm_usage"),
                    "apis": [r.get("api") for r in record.get("result_refs", [])],
                    "rows": [r.get("row_count") for r in record.get("result_refs", [])],
                }, ensure_ascii=False), flush=True)
                query_without_results = (
                    any(c.get("tool") == "finance_query" for c in record.get("tool_calls", []))
                    and not record.get("result_refs")
                )
                if record.get("error") or query_without_results:
                    raise RuntimeError(f"Stopped at incomplete case {case_id}; inspect the saved trace before continuing")
    finally:
        dsh.close()


if __name__ == "__main__":
    main()

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

from src.scenarios.financial_qa import FinancialQaCcService
from src.scenarios.financial_qa import FinanceDataQueryCcTools
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.services.application_runtime_service import ApplicationRuntimeService
from src.services.session_variable_store_service import SessionVariableStoreService
from src.skill_runtime.tool_adapter import ToolAdapter


class _FixtureFinanceRuntime:
    """Exercise the real query protocol without depending on a live business DB."""

    def execute_request(
        self,
        *,
        request: str,
        previous_results: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        handles = dict(previous_results or {})
        call = parse_api_call(request)
        validation = validate_call(call, handles)
        payload: dict[str, Any] = {
            "protocol": "finance_data_tool.v1",
            "request": call.raw,
            "call": {
                "result_id": call.result_id,
                "api": call.api,
                "args": call.args,
                "outputs": call.outputs,
            },
            "validation": {
                "ok": validation.ok,
                "errors": validation.errors,
                "warnings": validation.warnings,
            },
        }
        if not validation.ok:
            payload["result"] = None
            return payload
        columns = [self._column_name(item) for item in call.outputs]
        entity = self._entity(request)
        if "999999" in request or "测试" in request:
            rows = []
        elif call.api == "plate.basic_info":
            rows = [
                {
                    column: self._value(
                        column,
                        entity={"code": code, "name": name},
                    )
                    for column in columns
                }
                for code, name in (
                    ("885001", "机器人"),
                    ("885002", "人工智能"),
                    ("885003", "半导体"),
                    ("885004", "创新药"),
                    ("885005", "低空经济"),
                )
            ]
        elif call.api.startswith("plate.") and (
            int(call.args.get("limit") or 0) == 3
            or call.api == "plate.constitution.agg"
        ):
            rows = [
                {
                    column: self._value(
                        column,
                        entity={
                            "code": code,
                            "name": name,
                        },
                    )
                    for column in columns
                }
                for code, name in (
                    ("885001", "机器人"),
                    ("885002", "人工智能"),
                    ("885003", "半导体"),
                )
            ]
        else:
            rows = [
                {column: self._value(column, entity=entity) for column in columns}
            ]
        payload["result"] = {
            "name": call.result_id,
            "api": call.api,
            "columns": columns,
            "data": {"rows": rows, "row_count": len(rows)},
            "step_id": "",
            "task": "",
        }
        return payload

    @staticmethod
    def _column_name(value: str) -> str:
        text = str(value or "").strip()
        if " as " in text.lower():
            return text.rsplit(" ", 1)[-1].strip()
        return text.rsplit(".", 1)[-1].strip()

    @staticmethod
    def _entity(request: str) -> dict[str, str]:
        if "plate." in request:
            return {"code": "885001", "name": "机器人"}
        for token, code, name in (
            ("000858", "000858.SZ", "五粮液"),
            ("五粮液", "000858.SZ", "五粮液"),
            ("300750", "300750.SZ", "宁德时代"),
            ("宁德时代", "300750.SZ", "宁德时代"),
            ("002594", "002594.SZ", "比亚迪"),
            ("比亚迪", "002594.SZ", "比亚迪"),
        ):
            if token in request:
                return {"code": code, "name": name}
        return {"code": "600519.SH", "name": "贵州茅台"}

    @staticmethod
    def _value(column: str, *, entity: Mapping[str, str]) -> Any:
        values = {
            "code": entity["code"],
            "stock_code": entity["code"],
            "name": entity["name"],
            "stock_name": entity["name"],
            "plate_code": entity["code"],
            "plate_name": entity["name"],
            "industry_code": "801120",
            "industry_name": "食品饮料",
            "tradedate": "2026-07-24",
            "trade_date": "2026-07-24",
            "open": 1488.0,
            "close": 1500.0,
            "high": 1512.0,
            "low": 1480.0,
            "pct": 0.8,
            "amount": 2500000000.0,
            "total_amount": 25000000000.0,
            "financing_balance": 12800000000.0,
            "financing_net_buy": 86000000.0,
            "pe": 23.5,
            "pb": 7.2,
        }
        return values.get(column, 1.0)


class _FixtureAgentToolAdapter:
    """Expose the configured agent tools while keeping fixture runs deterministic."""

    def __init__(self) -> None:
        self._specs = ToolAdapter()

    def list_tool_specs(self, allowed_tools):
        return self._specs.list_tool_specs(allowed_tools)

    def execute(self, name, arguments):
        return {
            "ok": True,
            "data": [
                {
                    "tool": name,
                    "query": json.dumps(arguments, ensure_ascii=False),
                    "title": "fixture result",
                    "published_at": "2026-07-27",
                }
            ],
        }


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default="tests/evals/finance_data_chat_v1.json",
    )
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument(
        "--output",
        default="outputs/financial_qa_cc/eval_finance_data_chat_v1.json",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    load_dotenv(ROOT / ".env", override=False)
    dataset = json.loads((ROOT / args.dataset).read_text(encoding="utf-8"))
    cases = [
        item
        for item in dataset.get("cases") or []
        if not args.case or str(item.get("id") or "") in set(args.case)
    ]
    if args.limit > 0:
        cases = cases[: args.limit]

    runtime_root = (
        ROOT
        / "outputs"
        / "financial_qa_cc"
        / "_eval_runtime"
        / str(int(time.time() * 1000))
    )
    tool_kwargs: dict[str, Any] = {
        "result_store": SessionVariableStoreService(data_root=runtime_root / "data")
    }
    if args.fixture:
        tool_kwargs["finance_runtime"] = _FixtureFinanceRuntime()
        tool_kwargs["tool_adapter"] = _FixtureAgentToolAdapter()
    application_context = ApplicationRuntimeService().get_application_context(
        "investment_workbench"
    )
    service = FinancialQaCcService(
        enabled=True,
        system_tools=FinanceDataQueryCcTools(**tool_kwargs),
        root_dir=runtime_root / "sessions",
        log_path=runtime_root / "events.jsonl",
    )
    rows = []
    try:
        for case_index, case in enumerate(cases, start=1):
            case_id = str(case.get("id") or f"case_{case_index}")
            turns = []
            all_calls = []
            for turn_index, question in enumerate(case.get("turns") or [], start=1):
                started = time.monotonic()
                result = service.answer(
                    thread_id=f"eval-{case_id}",
                    turn_id=turn_index,
                    owner_id="financial-qa-eval",
                    user_text=str(question),
                    dispatch_plan={
                        "selected_agent": "investment_analyst",
                        "turn_mode": "normal_qa",
                        "entry": "agent_route",
                        "semantic_turn": {
                            "ori_question": str(question),
                            "resolved_question": str(question),
                        },
                    },
                    application_context=application_context,
                )
                evidence = result.get("financial_qa") or {}
                calls = [
                    dict(item)
                    for item in evidence.get("tool_calls") or []
                    if isinstance(item, dict)
                ]
                all_calls.extend(calls)
                turns.append(
                    {
                        "question": question,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "answer": result.get("message"),
                        "error": evidence.get("error"),
                        "tool_calls": calls,
                        "agent_tool_names": evidence.get("agent_tool_names") or [],
                        "skill_results": evidence.get("skill_results") or [],
                        "result_refs": evidence.get("result_refs") or [],
                    }
                )
            used_query = any(item.get("tool") == "finance_query" for item in all_calls)
            forbidden = [
                item
                for item in all_calls
                if "implement" in str(item.get("tool") or "").lower()
                or "codex" in str(item.get("tool") or "").lower()
            ]
            has_results = all(
                bool(item.get("result_refs"))
                for item in turns
            )
            has_answers = all(
                bool(str(item.get("answer") or "").strip())
                and "暂时没有返回内容" not in str(item.get("answer") or "")
                for item in turns
            )
            rows.append(
                {
                    "id": case_id,
                    "passed": (
                        (not case.get("expects_finance_query") or used_query)
                        and (not case.get("expects_finance_query") or has_results)
                        and has_answers
                        and not forbidden
                        and all(not str(item.get("error") or "").strip() for item in turns)
                    ),
                    "focus": case.get("focus") or [],
                    "turns": turns,
                }
            )
    finally:
        service.close()

    report = {
        "dataset": dataset.get("version"),
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
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

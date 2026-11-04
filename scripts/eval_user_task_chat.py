#!/usr/bin/env python3
"""Real-model conversational task submission/query/cancel with isolated durable storage.

Default entry: FinancialQaCcService + DSH. --allow-bridge-fallback explicitly permits
an OpenAI-compatible real-model loop through the actual authenticated task handlers
when DSH cannot start. The fallback is recorded as narrower coverage, never DSH proof.
No worker is launched: the submitted synthetic research stays pending until cancelled.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

QUESTIONS = [
    "请立即提交一个后台股票AutoML任务，只用合成demo数据验证流程，不连接真实行情。"
    "使用2023-01-02到2023-12-29的6家合成公司，持有1个交易日，"
    "只比较线性分类（逻辑回归）和线性回归（Ridge），最多4个实验、1轮、2个时间验证折。"
    "生成训练、回归评估和留出回测报告，不调用大模型评审，运行预算最长300秒。"
    "请现在提交并给我任务回执，不要在对话里同步训练，也无需再次确认。",
    "请查询刚才提交的后台任务的实际状态，并给我任务入口。现在还没有启动worker，不要声称训练已完成。",
    "请取消刚才那个尚未运行的任务，确认实际状态并保留记录。",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runtime", choices=("dsh", "bridge"), default="dsh")
    parser.add_argument("--allow-bridge-fallback", action="store_true")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    out = (args.output or ROOT / "outputs/task_chat_eval" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")).resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.chdir(ROOT)
    os.environ.update(TASK_SQLITE_PATH=str(out / "tasks.sqlite3"), TASK_ARTIFACT_ROOT=str(out / "artifacts"),
                      SYSTEM_DB_URL="", FINANCE_DSH_FINANCIAL_QA_ENABLED="1", FINANCE_DSH_TURN_TIMEOUT_SECONDS="120")
    os.environ.setdefault("LLM_BASE_URL", os.getenv("LLM_ENDPOINT", ""))
    os.environ.setdefault("LLM_API_KEY", os.getenv("LLM_KEY", ""))
    if not all(os.getenv(key) for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_DEFAULT_MODEL")):
        raise SystemExit("Configured compatible LLM endpoint, key and model are required")
    secrets = [os.getenv(key, "") for key in ("LLM_API_KEY", "LLM_KEY", "DASHSCOPE_API_KEY", "BI_DB_URL", "BUSINESS_DB_URL") if os.getenv(key)]
    def clean(value):
        if isinstance(value, dict):
            return {str(key): clean(item) for key, item in value.items()
                    if not any(term in str(key).lower() for term in ("api_key", "password", "authorization", "lease_token"))}
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[redacted]")
            return value
        return value
    def save(name, value):
        (out / name).write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, default=str))
    files = [Path(__file__), ROOT / "src/scenarios/financial_qa/service.py", ROOT / "src/scenarios/financial_qa/dsh_service.py",
             ROOT / "src/services/user_task_agent_tools.py", ROOT / "src/tools/user_task_tools.py",
             ROOT / "src/services/scheduled_task_compiler.py", ROOT / "src/services/scheduled_task_store.py",
             ROOT / "config/deepseek_harness/finance_query.patch.yml",
             ROOT / "src/prompts/system/assistant.scheduled_task_compile.system.md",
             ROOT / "src/prompts/system/assistant.scheduled_task_compile.user.md"]
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(),
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
                "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
                "model": os.getenv("LLM_DEFAULT_MODEL"), "requested_runtime": args.runtime,
                "scope": "Real LLM + authenticated conversation task tools + isolated SQLite; no HTTP, browser, worker, real-market data or production storage.",
                "isolation": {"task_storage": "local SQLite", "system_db_disabled": os.environ["SYSTEM_DB_URL"] == ""},
                "questions": QUESTIONS}
    save("manifest.json", manifest)
    from src.services.scheduled_task_service import ScheduledTaskService
    service = ScheduledTaskService()
    def stored(owner):
        definitions = service.list(owner_user_id=owner)
        runs = [run for definition in definitions for run in service.list_runs(owner_user_id=owner, schedule_id=definition["task_id"])]
        return {"tasks": definitions, "runs": runs}
    def verify(owner, records):
        snapshots = [record["stored"] for record in records]
        calls = [call.get("tool") for record in records for call in record.get("tool_calls", [])]
        checks = {
            "all_responses_present": all(bool(record.get("message", "").strip()) for record in records),
            "submit_called": "task_submit" in calls,
            "query_called": "task_get" in calls,
            "cancel_called": "task_cancel" in calls,
            "exactly_one_task": len(snapshots[-1]["tasks"]) == 1,
            "one_durable_run": len(snapshots[-1]["runs"]) == 1,
            "pending_after_submit": any(run["status"] == "pending" for run in snapshots[0]["runs"]),
            "pending_after_query": any(run["status"] == "pending" for run in snapshots[1]["runs"]),
            "cancelled_after_cancel": bool(snapshots[-1]["runs"]) and all(run["status"] == "cancelled" for run in snapshots[-1]["runs"]),
            "source_conversation_linked": bool(snapshots[-1]["tasks"]) and bool(snapshots[-1]["tasks"][0].get("source_ref")),
            "no_other_owner_visibility": service.list(owner_user_id=owner + "-other") == [],
        }
        if snapshots[-1]["tasks"]:
            task = snapshots[-1]["tasks"][0]
            checks["bounded_300_second_budget"] = task.get("budget", {}).get("max_runtime_seconds") == 300
            steps = task.get("execution_plan", {}).get("steps", [])
            checks["automl_demo_asset_selected"] = any(step.get("target_ref", {}).get("name") == "stock_automl_research"
                and step.get("inputs", {}).get("source") == "demo" for step in steps)
        return {"checks": checks, "passed": all(checks.values())}

    def dsh_eval():
        from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService
        from src.scenarios.financial_qa.service import FinancialQaCcService
        from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
        from src.services.session_variable_store_service import SessionVariableStoreService
        tools = FinanceDataQueryCcTools(result_store=SessionVariableStoreService(data_root=out / "session_results"))
        dsh = FinanceDeepSeekHarnessSessionService(enabled=True, system_tools=tools, worker_count=1,
                                                  root_dir=out / "dsh", log_path=out / "dsh_events.jsonl")
        engine = FinancialQaCcService(enabled=True, system_tools=tools, session_service=object(), dsh_session_service=dsh)
        owner, thread = "task-chat-eval-dsh-" + uuid.uuid4().hex[:8], "task-chat-" + uuid.uuid4().hex
        records = []
        try:
            for index, question in enumerate(QUESTIONS, 1):
                print(json.dumps({"runtime": "dsh", "turn": index, "phase": "start"}), flush=True)
                response = engine.answer(thread_id=thread, turn_id=index, owner_id=owner, user_text=question,
                    dispatch_plan={"selected_agent": "investment_analyst", "turn_mode": "normal_qa", "entry": "agent_route",
                                   "semantic_turn": {"resolved_question": question}},
                    application_context={"_task_actor": {"user_id": owner, "user_type": "member"}},
                    runtime="dsh", execution_mode="standard", isolated_request=False, include_response_data=False)
                qa = response.get("financial_qa") or {}
                record = {"question": question, "message": response.get("message"), "tool_calls": qa.get("tool_calls", []),
                          "runtime": "FinancialQaCcService + DSH", "stored": stored(owner)}
                records.append(record)
                save("dsh_turns.json", records)
                if index == 1 and not record["stored"]["tasks"]:
                    raise RuntimeError("DSH turn returned without a durable task; see saved answer")
            return {"entry": "FinancialQaCcService + DSH", **verify(owner, records)}
        finally:
            dsh.close()

    def bridge_eval():
        import requests
        from src.services.user_task_agent_tools import build_user_task_agent_tools
        owner, thread = "task-chat-eval-bridge-" + uuid.uuid4().hex[:8], "task-chat-" + uuid.uuid4().hex
        runtime = SimpleNamespace(owner_ids=[owner], tool_context={"_task_actor": {"user_id": owner, "user_type": "member"},
            "_task_conversation_id": thread, "_task_turn_id": "1"}, tracker={"calls": []})
        handlers = {tool.name: tool for tool in build_user_task_agent_tools(runtime)}
        definitions = []
        for name in handlers:
            definition = json.loads((ROOT / "src/tools/definitions" / (name + ".tool.json")).read_text())
            definitions.append({"type": "function", "function": {"name": name, "description": definition["identity"]["description"],
                                                                   "parameters": definition["schemas"]["input"]}})
        messages = [{"role": "system", "content": "你是金融助手，当前可用工具用于管理用户后台任务。用户明确要求提交时直接提交完整要求，返回真实回执；查询和取消须调用工具读取或修改实际状态。不得声称尚未运行的训练已完成，也不得虚构任务ID。身份和权限由服务端提供。"}]
        records = []
        for index, question in enumerate(QUESTIONS, 1):
            print(json.dumps({"runtime": "real_model_tool_bridge", "turn": index, "phase": "start"}), flush=True)
            runtime.tool_context["_task_turn_id"] = str(index)
            runtime.tracker["calls"] = []
            messages.append({"role": "user", "content": question})
            tool_results = []
            answer = ""
            for iteration in range(6):
                response = requests.post(os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"]},
                    json={"model": os.environ["LLM_DEFAULT_MODEL"], "messages": messages, "tools": definitions,
                          "temperature": .1, "max_tokens": 4096, "enable_thinking": False}, timeout=(8, 90))
                if not response.ok:
                    raise RuntimeError(f"real model HTTP {response.status_code}")
                message = response.json()["choices"][0]["message"]
                messages.append({key: value for key, value in message.items() if key in ("role", "content", "tool_calls")})
                if not message.get("tool_calls"):
                    answer = message.get("content") or ""
                    break
                for call in message["tool_calls"]:
                    name = call["function"]["name"]
                    arguments = json.loads(call["function"]["arguments"])
                    result = asyncio.run(handlers[name].handler(arguments))
                    text = result["content"][0]["text"]
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
                    tool_results.append({"tool": name, "arguments": arguments, "result": json.loads(text)})
            record = {"question": question, "message": answer, "tool_calls": list(runtime.tracker["calls"]),
                      "tool_results": tool_results, "stored": stored(owner)}
            records.append(record)
            save("bridge_turns.json", records)
            if index == 1 and not record["stored"]["tasks"]:
                raise RuntimeError("real-model bridge did not create a durable task; see saved evidence")
        return {"entry": "Real compatible model + build_user_task_agent_tools; NOT DSH/HTTP/browser", **verify(owner, records)}

    start = time.monotonic()
    try:
        if args.runtime == "dsh":
            try:
                result = dsh_eval()
            except Exception as exc:
                save("dsh_unavailable.json", {"error_type": type(exc).__name__, "message": str(exc),
                     "coverage": "DSH conversation not verified"})
                print(json.dumps({"runtime": "dsh", "error_type": type(exc).__name__}), flush=True)
                if not args.allow_bridge_fallback:
                    raise
                result = bridge_eval()
                result["fallback_used"] = True
        else:
            result = bridge_eval()
        result["elapsed_seconds"] = round(time.monotonic() - start, 2)
        save("verification.json", result)
        print(json.dumps({"passed": result["passed"], "entry": result["entry"], "evidence": str(out)}, ensure_ascii=False), flush=True)
        if not result["passed"]:
            raise SystemExit(1)
    except Exception as exc:
        save("failure.json", {"error_type": type(exc).__name__, "message": str(exc), "elapsed_seconds": round(time.monotonic() - start, 2)})
        raise SystemExit(type(exc).__name__) from None


if __name__ == "__main__":
    main()

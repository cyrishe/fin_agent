"""Live Chat API diagnostic: real routing, skills, model, data and presentation.

Flask test client exercises /api/chat/dispatch without a socket/browser.
Only identity and conversation persistence are local test adapters. System
skills use an empty local personal-skill registry. No production session writes.
Every case is a fresh conversation; optional follow-ups reuse its context and evidence. Not a load benchmark.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


class LocalConversations:
    """Local conversation adapter with the production context-window shape."""
    def __init__(self, out, thread_id):
        self.out, self.thread_id, self.context = out, thread_id, {}
        self.turn_id, self.history = 0, []

    def ensure_thread(self, **kwargs):
        return self.thread_id

    def get_thread_context(self, **kwargs):
        return self.context

    def get_context_window(self, **kwargs):
        return list(self.history)

    def create_turn(self, **kwargs):
        save(self.out / "turn_input.json", kwargs)
        self.turn_id += 1
        self.pending_user = kwargs.get("user_input_text") or kwargs.get("user_text") or ""
        return self.turn_id

    def update_thread_context(self, *, patch, **kwargs):
        for key, value in patch.items():
            if value is None:
                self.context.pop(key, None)
            else:
                self.context[key] = value

    def complete_turn(self, **kwargs):
        save(self.out / "turn_output.json", kwargs)
        self.history.extend([{"round": self.turn_id, "role": "user", "text": self.pending_user},
            {"round": self.turn_id, "role": "assistant", "text": kwargs["assistant_output_text"]}])
        return {"thread_id": self.thread_id, "turn_id": self.turn_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases-file", type=Path, default=ROOT / "tests/evals/skill_chat_live_v1.json")
    parser.add_argument("--model", help="Defaults to .env_tmp MODEL_NAME, then LLM_DEFAULT_MODEL")
    parser.add_argument("--cases", default="technical,kline,relative,capital,quote")
    parser.add_argument("--analysis-max-tokens", type=int, default=32768)
    parser.add_argument("--step-max-tokens", type=int, default=8192)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--dsh-skill-task-experiment", action="store_true",
                        help="Mount one bounded DSH skill subtask in this local evaluation only")
    args = parser.parse_args()
    os.chdir(ROOT)
    from dotenv import load_dotenv, dotenv_values
    load_dotenv(ROOT / ".env", override=False)
    cfg = dotenv_values(ROOT / ".env_tmp")
    base_url = cfg.get("BASE_URL") or os.environ.get("LLM_BASE_URL")
    api_key = cfg.get("LLM_KEY") or os.environ.get("LLM_API_KEY")
    if cfg.get("LLM_VISION_MODEL"):
        os.environ["LLM_VISION_MODEL"] = cfg["LLM_VISION_MODEL"]
    model = args.model or cfg.get("MODEL_NAME") or os.environ.get("LLM_DEFAULT_MODEL")
    if not all((base_url, api_key, model)):
        parser.error("Configure LLM_BASE_URL, LLM_API_KEY and LLM_DEFAULT_MODEL in the environment or .env")
    os.environ.update(LLM_BASE_URL=base_url, LLM_API_KEY=api_key,
        LLM_DEFAULT_MODEL=model, LLM_FLASH_MODEL=model,
        LLM_REASONING_MODEL=model, FINANCE_CC_SHADOW_ENABLED="0",
        FINANCE_DSH_CUSTOM_TOOL_MODEL=model,
        FINANCE_DSH_CUSTOM_TOOL_BASE_URL=base_url,
        FINANCE_DSH_CUSTOM_TOOL_INTENT_MAX_TOKENS="2048",
        FINANCE_DSH_FINANCIAL_QA_ENABLED="1", FINANCE_CC_FINANCIAL_QA_ENABLED="0",
        FINANCE_DSH_MAX_TOKENS=str(args.analysis_max_tokens),
        FINANCE_DSH_TURN_TIMEOUT_SECONDS=str(args.timeout))
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    from src.web import flask_app as web
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService, _load_sdk_class
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.custom_tool.dsh_intent_router import CustomToolIntentDshRouter
    from src.services.skill_candidate_store_service import InMemorySkillCandidateStoreService
    from src.services.skill_hub_catalog_service import SkillHubCatalogService
    policy = {"skillAnalysisMaxTokens": args.analysis_max_tokens,
        "budgets": {stage: {"maxTokens": args.step_max_tokens}
                    for stage in ("catalog", "query", "fast_query", "details", "repair")}}
    policy["budgets"]["final"] = {"maxTokens": args.analysis_max_tokens}
    dsh = FinanceDeepSeekHarnessSessionService(enabled=True, worker_count=1,
        root_dir=out / "runtime", log_path=out / "events.jsonl", loop_policy_config=policy)
    if args.dsh_skill_task_experiment:
        experiment_patch = out / "finance_skill_task.patch.yml"
        experiment_patch.write_text(
            dsh.patch_path.read_text(encoding="utf-8")
            + "\n- insert:\n"
            + "    - id: fin-eval-subagent\n"
            + "      name: '@deepseek-ai/dsh-subagent'\n"
            + "    - id: fin-eval-subagent-spawn\n"
            + "      name: '@deepseek-ai/dsh-subagent-spawn-in-process'\n"
            + "      config:\n"
            + "        providerName: spawn\n"
            + "    - id: fin-eval-skill-task\n"
            + "      name: '@deepseek-ai/dsh-tool-subagent'\n"
            + "      config:\n"
            + "        provider: spawn\n"
            + "        toolName: finance_skill_task\n"
            + "        backgroundMode: one-shot\n"
            + "        maxDepth: 1\n"
            + "        toolFilter:\n"
            + "          allow: [mcp__finance__read_finance_skill, mcp__finance__read_finance_skill_reference, mcp__finance__load_finance_result]\n",
            encoding="utf-8",
        )
        dsh.patch_path = experiment_patch
        dsh.loop_policy_config["businessHint"] = (
            "复杂个股研究中，如果财报质量值得独立判断，先取得核心财务结果，再用 "
            "finance_skill_task 委托一次有界的业绩分析：description 简述局部目标，prompt 给子任务具体问题、证券与报告期、"
            "已有 result_ref；子任务从授权目录加载所需 Skill 并读取已有结果，"
            "只返回短判断、依据和缺口。主会话仍负责最终答案。"
        )
    DeepSeekHarness = _load_sdk_class()
    run_harness = DeepSeekHarness.run
    router = CustomToolIntentDshRouter(enabled=True, worker_count=1,
        root_dir=out / "router_runtime", log_path=out / "router_events.jsonl")
    hub = SkillHubCatalogService(candidate_store=InMemorySkillCandidateStoreService())
    service = FinancialQaCcService(enabled=True, session_service=object(),
        dsh_session_service=dsh, system_tools=dsh.system_tools, skill_hub_catalog_service=hub)
    all_cases = json.loads(args.cases_file.read_text())["cases"]
    cases = [c for c in all_cases if c["id"] in args.cases.split(",")]
    if len(cases) != len(set(args.cases.split(","))):
        raise ValueError("Unknown or repeated case selection")
    turns = []
    for case in cases:
        turns.append(case)
        turns.extend({"id": f"{case['id']}_followup{n}", "question": text, "parent": case["id"]}
                     for n, text in enumerate(case.get("followups", []), 1))
    tracked = subprocess.check_output(["git", "ls-files", "src", "config"], text=True).splitlines()
    tracked += [str(p.relative_to(ROOT)) for p in (ROOT / "src").rglob("*")
                if p.is_file() and p.suffix in {".py", ".mjs", ".md", ".json", ".yml"}]
    tracked += [str(Path(__file__).relative_to(ROOT)), "tests/evals/skill_chat_live_v1.json",
                "phase2_dynamic_cal_code_prompt.md"]
    hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
              for p in sorted(set(tracked)) if (ROOT / p).is_file()}
    save(out / "manifest.json", {"started_at": datetime.now(timezone.utc).isoformat(),
        "entry": __doc__, "cases": turns, "model": dsh.model,
        "vision_model": os.environ.get("LLM_VISION_MODEL", ""),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
        "source_hashes": hashes, "cases_file": str(args.cases_file),
        "cases_sha256": hashlib.sha256(args.cases_file.read_bytes()).hexdigest(), "skill_revision": service.business_skill_catalog.revision,
        "effective_policy": dsh.loop_policy_config, "timeout_seconds": args.timeout,
        "dsh_skill_task_experiment": args.dsh_skill_task_experiment,
        "excluded": ["browser rendering", "network HTTP transport", "production identity/quota/persistence", "personal skills", "background title generation"]})
    failures = []
    conversations = {}
    case_threads = {}
    try:
        for index, case in enumerate(turns):
            case_out = out / case["id"]
            case_out.mkdir()
            print("START", case["id"], flush=True)
            spans = []
            def measured(name, fn):
                def call(*a, **kw):
                    start = time.perf_counter()
                    try:
                        result = fn(*a, **kw)
                        if name in {"dispatch", "intent_route"}:
                            save(case_out / f"{name}.json", result)
                        if name == "dispatch" and result.get("entry") != "agent_route":
                            raise RuntimeError("Read-only QA evaluation stopped at unexpected route")
                        return result
                    finally:
                        spans.append({"name": name, "seconds": time.perf_counter() - start})
                        save(case_out / "api_spans.json", spans)
                        print("SPAN", case["id"], name, round(spans[-1]["seconds"], 2), flush=True)
                return call
            parent = case.get("parent") or case["id"]
            thread_id = case_threads.setdefault(parent, int(time.time() * 1000) + index)
            conversation = conversations.setdefault(parent, LocalConversations(case_out, thread_id))
            conversation.out = case_out
            started = time.perf_counter()
            endings = []
            def capture_endings(harness, *a, **kw):
                result = run_harness(harness, *a, **kw)
                tree = {"subagents": [], "sessions": {}, "delegation_results": []}
                delegation_calls = set()
                for notification in result.notifications:
                    payload = notification.payload
                    if notification.method == "subagent.started":
                        tree["subagents"].append({
                            "parent_session_id": payload.get("parentSessionId"),
                            "child_session_id": payload.get("childSessionId"),
                        })
                    if notification.method != "session.event":
                        continue
                    event = payload.get("event") or {}
                    data = event.get("data") or {}
                    session = tree["sessions"].setdefault(str(payload.get("sessionId") or ""),
                        {"model_calls": 0, "input_tokens": 0, "cache_read_tokens": 0,
                         "output_tokens": 0, "reasoning_tokens": 0, "tool_calls": []})
                    if event.get("type") == "tool/call":
                        session["tool_calls"].append(data.get("name"))
                        if data.get("name") == "finance_skill_task":
                            delegation_calls.add(data.get("callId"))
                    if event.get("type") == "tool/result" and data.get("callId") in delegation_calls:
                        tree["delegation_results"].append(str(data)[:1600])
                    if event.get("type") == "assistant/message" and isinstance(data.get("usage"), dict):
                        usage = data["usage"]
                        session["model_calls"] += 1
                        for key, source in (("input_tokens", "inputTokens"),
                                            ("cache_read_tokens", "cacheReadTokens"),
                                            ("output_tokens", "outputTokens"),
                                            ("reasoning_tokens", "reasoningTokens")):
                            session[key] += int(usage.get(source) or 0)
                save(case_out / "dsh_tree_summary.json", tree)
                endings.append({"session_id": result.session_id, "finish_reason": result.finish_reason,
                    "events": [event for event in result.events
                        if event.get("type") in {"step/end", "turn/end"}
                        or (event.get("type") == "assistant/chunk" and
                            (event.get("data", {}).get("chunk", {}).get("type") in {"finish", "error"}))]})
                save(case_out / "model_endings.json", endings)
                return result
            with ExitStack() as stack:
                stack.enter_context(patch.object(DeepSeekHarness, "run", capture_endings))
                stack.enter_context(patch.object(web, "runtime_conversation_service", conversation))
                stack.enter_context(patch.object(web, "_resolve_current_guest_identity", return_value={"user_id": "skill-chat-local-eval", "user_type": "member"}))
                stack.enter_context(patch.object(web, "financial_qa_cc_service", service))
                stack.enter_context(patch.object(web.assistant_dispatch_planner, "custom_tool_router", router))
                stack.enter_context(patch.object(router, "route", measured("intent_route", router.route)))
                stack.enter_context(patch.object(web.assistant_dispatch_planner, "plan_turn",
                    measured("dispatch", web.assistant_dispatch_planner.plan_turn)))
                stack.enter_context(patch.object(service, "answer", measured("financial_qa", service.answer)))
                stack.enter_context(patch.object(web, "_attach_answer_summary", measured("summary_followups", web._attach_answer_summary)))
                with web.app.test_client() as client:
                    response = client.post("/api/chat/dispatch", json={"text": case["question"],
                        "thread_id": thread_id, "financial_qa_runtime": "dsh",
                        "financial_qa_execution_mode": "standard", "research_mode": "auto"})
            body = response.get_json()
            save(case_out / "response.json", body)
            events = [json.loads(s) for s in (out / "events.jsonl").read_text().splitlines()] if (out / "events.jsonl").exists() else []
            events = [e for e in events if str(e.get("thread_id")) == str(thread_id) and str(e.get("turn_id")) == str(conversation.turn_id)]
            save(case_out / "dsh_events.json", events)
            finish = events[-1].get("finish_reason") if events else None
            success = response.status_code == 200 and body.get("ok") is True and finish == "completed"
            save(case_out / "completion.json", {"seconds": time.perf_counter() - started,
                "http_status": response.status_code, "finish_reason": finish, "completed": success})
            (case_out / "answer.md").write_text(str(body.get("message") or ""))
            if events:
                (case_out / "answer_raw.md").write_text(str(events[-1].get("result") or ""))
            if not success:
                failures.append(case["id"])
            print("DONE", case["id"], finish, round(time.perf_counter() - started, 2), flush=True)
    finally:
        dsh.close()
        router.close()
        changed = [p for p, sha in hashes.items() if not (ROOT / p).is_file()
                   or hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != sha]
        save(out / "run_end.json", {"failed_cases": failures, "source_changes_during_run": changed})
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())

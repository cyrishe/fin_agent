#!/usr/bin/env python3
"""Real CC/DSH progressive-catalog replay; observability wrappers do not alter calls."""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import sys
import time
import uuid


def plain(value):
    if is_dataclass(value):
        return {"_sdk_type": type(value).__name__, **asdict(value)}
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-root", type=Path, required=True)
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime", choices=["cc", "dsh"], required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--case-ids", help="Run only the remaining planned IDs after a reviewed failure")
    args = parser.parse_args()
    root, assets, output = args.code_root.resolve(), args.assets_root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = assets / "outputs/d4f10504-8df6-435e-9316-3d89b5fd1015/source_cases.json"
    general = {x["case_id"]: x for x in json.loads(source.read_text())["cases"]}
    reports = {x["case_id"]: x for x in json.loads((assets / "outputs/financial_qa_mainland_full_increment_20260903/cases_mainland_full_no_news.json").read_text())["cases"]}
    chat_cases = {x["id"]: x for x in json.loads((assets / "tests/evals/finance_data_chat_v1.json").read_text())["cases"]}
    cases = [
        {"id": "BUS010", "question": general["BUS010"]["question"], "thread_group": "progressive"},
        {"id": "CHAT_maotai_open_close", "question": chat_cases["maotai_open_close"]["turns"][0], "thread_group": "progressive"},
        {"id": "BUS168", "question": general["BUS168"]["question"], "thread_group": "progressive"},
        {"id": "BUS093", "question": general["BUS093"]["question"], "thread_group": "constitution"},
        {"id": "BUS141", "question": general["BUS141"]["question"], "thread_group": "window"},
        {"id": "RTEF012", "question": reports["RTEF012"]["question"], "thread_group": "aggregate"},
    ]
    if args.smoke:
        cases = cases[:1]
    if args.case_ids:
        wanted = set(args.case_ids.split(","))
        cases = [case for case in cases if case["id"] in wanted]
    sys.path.insert(0, str(root))
    os.chdir(root)
    from dotenv import load_dotenv
    load_dotenv(args.env_file.resolve(), override=True)
    from src.services.agent_providers.claude import ClaudeSdkSkillHarness
    default_provider = os.environ.get("FINANCE_CC_PROVIDER") or os.environ.get("CLAUDE_PROVIDER") or "dashscope"
    model = os.environ.get("FINANCE_CC_MODEL") or os.environ.get("LLM_DEFAULT_MODEL")
    default_env = ClaudeSdkSkillHarness(provider=default_provider, model=model).provider_env()
    default_diagnostic = {"provider": default_provider, "model": model,
                          "endpoint": default_env.get("ANTHROPIC_BASE_URL"),
                          "credential_available": bool(default_env.get("ANTHROPIC_AUTH_TOKEN") or default_env.get("ANTHROPIC_API_KEY"))}
    # Explicit comparison-only route override: use the same configured Bailian
    # model via each runtime's own provider protocol. Never modify .env.
    os.environ["FINANCE_CC_PROVIDER"] = "dashscope"
    if not os.environ.get("LLM_API_KEY") and os.environ.get("DASHSCOPE_API_KEY"):
        os.environ["LLM_API_KEY"] = os.environ["DASHSCOPE_API_KEY"]
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService, _load_sdk_class
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
    from src.services.finance_claude_session_service import FinanceClaudeSessionService

    captured = []
    original_response_messages = FinanceClaudeSessionService._response_messages
    original_runtime_options = FinanceClaudeSessionService._runtime_options

    def observe_cc_options(self, context):
        options = original_runtime_options(self, context)
        captured.append({"type": "cc/actual_system_prompt", "prompt": options["system_prompt"]})
        return options

    FinanceClaudeSessionService._runtime_options = observe_cc_options

    async def observe_cc(client, *, prompt, first_response_timeout_seconds):
        captured.append({"type": "host/query_start", "monotonic": time.monotonic(), "prompt": prompt})
        async for message in original_response_messages(client, prompt=prompt, first_response_timeout_seconds=first_response_timeout_seconds):
            captured.append({"type": "cc/sdk_message", "monotonic": time.monotonic(), "message": plain(message)})
            yield message

    FinanceClaudeSessionService._response_messages = staticmethod(observe_cc)

    class ObservedHarness:
        def __init__(self, actual):
            self.actual = actual

        def __getattr__(self, name):
            return getattr(self.actual, name)

        def run(self, *positional, **named):
            result = self.actual.run(*positional, **named)
            captured.extend({"type": "dsh/native_event", "event": dict(event)} for event in result.events)
            return result

    def observed_factory(**kwargs):
        return ObservedHarness(_load_sdk_class()(**kwargs))

    tools = FinanceDataQueryCcTools()
    dsh = FinanceDeepSeekHarnessSessionService(enabled=True, system_tools=tools,
        worker_count=1, root_dir=output / "dsh_runtime", log_path=output / "dsh_events.jsonl", harness_factory=observed_factory)
    service = FinancialQaCcService(enabled=True, system_tools=tools, dsh_session_service=dsh,
        root_dir=output / "cc_runtime", log_path=output / "cc_events.jsonl")
    cc = service.session_service
    actual_cc_env = ClaudeSdkSkillHarness(provider=cc.provider, model=cc.model).provider_env()
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for folder in ["src", "config"] for p in sorted((root / folder).rglob("*"))
              if p.is_file() and "__pycache__" not in p.parts}
    manifest = {"runtime": args.runtime, "snapshot": str(root), "cases": cases,
                "catalog_revision": tools.finance_catalog.catalog_revision(), "source_sha256": hashes,
                "default_cc_read_only_diagnostic": default_diagnostic,
                "cc_comparison_override": {"provider": cc.provider, "model": cc.model, "endpoint": actual_cc_env.get("ANTHROPIC_BASE_URL"), "effort": cc.effort, "max_turns": cc.max_turns, "sdk": metadata.version("claude-agent-sdk")},
                "dsh": {"model": dsh.model, "endpoint": dsh.base_url, "actual_global_system_prompt": dsh.system_prompt},
                "mode": {"data_only": True, "research_mode": "fast", "execution_mode": "standard"},
                "source_cases": [str(source.relative_to(assets)), "tests/evals/finance_data_chat_v1.json", "outputs/financial_qa_mainland_full_increment_20260903/cases_mainland_full_no_news.json"],
                "notes": "True native runtimes; observation-only wrappers. First three questions share one runtime conversation; remaining cases are independent. No server/database/env writes."}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    thread_ids = {}
    try:
        for case in cases:
            captured.clear()
            thread_id = thread_ids.setdefault(case["thread_group"], uuid.uuid4().hex)
            started = time.monotonic()
            response = service.answer(thread_id=thread_id, turn_id=uuid.uuid4().hex,
                owner_id="progressive-catalog-evaluation", user_text=case["question"],
                dispatch_plan={"selected_agent": "investment_analyst", "turn_mode": "normal_qa", "entry": "agent_route", "semantic_turn": {"resolved_question": case["question"]}},
                runtime=args.runtime, research_mode="fast", execution_mode="standard", data_only=True,
                isolated_request=False, response_data_max_rows=2)
            record = json.loads((output / f"{args.runtime}_events.jsonl").read_text().splitlines()[-1])
            row = {**case, "elapsed_ms": round((time.monotonic() - started) * 1000), "record": record,
                   "response": response, "native_events": captured.copy()}
            (output / f"{case['id']}.json").write_text(json.dumps(row, ensure_ascii=False, indent=2, default=str))
            print(json.dumps({"runtime": args.runtime, "id": case["id"], "seconds": row["elapsed_ms"] / 1000,
                              "error": record.get("error"), "resumed": record.get("resumed"),
                              "usage": record.get("llm_usage"), "apis": [r.get("api") for r in record.get("result_refs", [])]}, ensure_ascii=False), flush=True)
            if record.get("error"):
                raise RuntimeError(f"Stop at {case['id']}: saved failure requires review")
    finally:
        cc.close()
        dsh.close()


if __name__ == "__main__":
    main()

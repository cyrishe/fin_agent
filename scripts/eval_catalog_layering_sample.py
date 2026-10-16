#!/usr/bin/env python3
"""Replay ten unchanged historical questions through isolated local DSH sessions.

The source checkout may be an immutable temporary snapshot. Credentials are
loaded in process from the caller's existing env file and never serialized.
The service performs read-only finance queries; no HTTP user or production
conversation is created. Full internal traces stay in the selected output dir.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


CASES = [
    ("BUS010", ["stock.quote"]),
    ("BUS014", ["stock.moneyflow"]),
    ("BUS026", ["stock.financial_3_table"]),
    ("BUS093", ["index.constitution"]),
    ("BUS157", ["plate.constitution"]),
    ("BUS168", ["fund.basic_info"]),
    ("BUS141", ["plate.moneyflow"]),
    ("RTEF070", ["stock.report"]),
    ("RTEF112", ["stock.report_metric"]),
    ("BUS185", ["bond.basic_info"]),
]
SOURCES = [
    "outputs/d4f10504-8df6-435e-9316-3d89b5fd1015/source_cases.json",
    "outputs/financial_qa_mainland_full_increment_20260903/cases_increment_no_news.json",
    "outputs/mcp_both_sample_20260907/manifest.json",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-root", type=Path, required=True)
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", help="Comma-separated subset of fixed case IDs")
    args = parser.parse_args()
    code_root, assets_root = args.code_root.resolve(), args.assets_root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_rows = {}
    for source in SOURCES:
        payload = json.loads((assets_root / source).read_text(encoding="utf-8"))
        rows = payload.get("cases", payload.get("candidates", []))
        for row in rows:
            source_rows.setdefault(row.get("case_id"), (row, source))
    selected = set(args.cases.split(",")) if args.cases else None
    cases = []
    for case_id, entries in CASES:
        if selected and case_id not in selected:
            continue
        row, source = source_rows[case_id]
        cases.append({"id": case_id, "question": row["question"],
                      "required_entries": entries, "source": source})
    sys.path.insert(0, str(code_root))
    os.chdir(code_root)
    from dotenv import load_dotenv
    load_dotenv(args.env_file.resolve(), override=True)
    credential_source = "LLM_API_KEY"
    if not os.environ.get("LLM_API_KEY") and os.environ.get("DASHSCOPE_API_KEY"):
        # Existing local credential; only this child process receives the alias.
        os.environ["LLM_API_KEY"] = os.environ["DASHSCOPE_API_KEY"]
        credential_source = "DASHSCOPE_API_KEY (process-only alias)"
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
    code_hashes = {}
    for folder in ("src", "config"):
        for path in sorted((code_root / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                code_hashes[str(path.relative_to(code_root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "code_root": str(code_root),
        "source_head": subprocess.check_output(["git", "-C", str(assets_root), "rev-parse", "HEAD"], text=True).strip(),
        "working_tree_binding": "source content SHA256; HEAD alone is not the evaluated dirty tree",
        "model": os.environ.get("LLM_DEFAULT_MODEL"),
        "endpoint": os.environ.get("LLM_BASE_URL"),
        "credential_source": credential_source,
        "configuration": {"runtime": "dsh", "execution_mode": "standard", "research_mode": "fast", "data_only": True, "worker_count": 1},
        "score_scope": "Human review of selected API and executable protocol, independent of data availability; not a production readiness claim",
        "cases": cases, "code_sha256": code_hashes,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tools = FinanceDataQueryCcTools()
    dsh = FinanceDeepSeekHarnessSessionService(enabled=True, system_tools=tools, worker_count=1,
        root_dir=output / "runtime", log_path=output / "events.jsonl")
    service = FinancialQaCcService(enabled=True, system_tools=tools, session_service=object(), dsh_session_service=dsh)
    try:
        for case in cases:
            request_id = uuid.uuid4().hex
            started = time.monotonic()
            response = service.answer(thread_id=request_id, turn_id=request_id,
                owner_id="catalog-layering-evaluation", user_text=case["question"],
                dispatch_plan={"selected_agent": "investment_analyst", "turn_mode": "normal_qa", "entry": "agent_route",
                               "semantic_turn": {"resolved_question": case["question"]}},
                runtime="dsh", research_mode="fast", execution_mode="standard", data_only=True,
                isolated_request=True, response_data_max_rows=2)
            record = json.loads((output / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            row = {**case, "elapsed_ms": round((time.monotonic() - started) * 1000), "record": record, "response": response}
            (output / f"{case['id']}.json").write_text(json.dumps(row, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            print(json.dumps({"case": case["id"], "elapsed_ms": row["elapsed_ms"], "error": record.get("error"),
                              "usage": record.get("llm_usage"), "apis": [r.get("api") for r in record.get("result_refs", [])],
                              "rows": [r.get("row_count") for r in record.get("result_refs", [])]}, ensure_ascii=False), flush=True)
            if record.get("error"):
                raise RuntimeError(f"Stopped at failed case {case['id']}; saved trace requires review")
    finally:
        dsh.close()


if __name__ == "__main__":
    main()

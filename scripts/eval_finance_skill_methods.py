"""Isolated real-model Skill method checks with frozen synthetic evidence.

No financial data access. Stores responses for human review, not automatic
semantic pass labels. Reviewer criteria never enter the model request.
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
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.eval_skill_selection_only import replay_request, save


def build_request(case, catalog, model):
    method = catalog.load(case["skill_id"])
    if method.get("error"):
        raise ValueError("Unknown method")
    resources = [method["method"]]
    for reference in case["references"]:
        result = catalog.load_reference(case["skill_id"], reference)
        if result.get("error"):
            raise ValueError("Unknown reference")
        resources.append(result["content"])
    return {
        "model": model, "stream": False, "thinking": {"type": "enabled"},
        "reasoning_effort": "low", "max_tokens": 2200,
        "messages": [
            {"role": "system", "content": (ROOT / "src/scenarios/financial_qa/dsh_system.md").read_text()},
            {"role": "system", "content": "这是隔离的方法测试，以下证据是合成测试材料。当前没有外部工具。按已加载方法回答问题；证据缺口如实说明，不声称执行过查询。"},
            {"role": "system", "content": "已加载的业务方法与参考：\n\n" + "\n\n".join(resources)},
            {"role": "user", "content": case["question"] + "\n\n本轮可用证据：\n" + case["evidence"]},
        ],
    }


def main():
    from dotenv import load_dotenv
    from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-file", type=Path, default=ROOT / "tests/evals/finance_multi_asset_methods_v1.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env", override=False)
    out = args.output.resolve()
    if out.exists():
        raise SystemExit("Choose a new output directory; previous results are preserved")
    cases = json.loads(args.cases_file.read_text())["cases"]
    catalog = FinanceBusinessSkillCatalog()
    save(out / "manifest.json", {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "skill_revision": catalog.revision, "model": os.environ["LLM_DEFAULT_MODEL"],
        "cases": cases, "dataset_sha256": hashlib.sha256(args.cases_file.read_bytes()).hexdigest(),
        "scope": "Synthetic supplied evidence; real model; manually preloaded Skill/references; no tools or production data; human review required",
        "source_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__), ROOT / "scripts/eval_skill_selection_only.py", ROOT / "src/scenarios/financial_qa/dsh_system.md"]},
    })
    results = []
    for case in cases:
        started = time.monotonic()
        print("START " + case["id"], flush=True)
        request = build_request(case, catalog, os.environ["LLM_DEFAULT_MODEL"])
        save(out / case["id"] / "request.json", request)
        try:
            _, response = replay_request(request)
            save(out / case["id"] / "response.json", response)
            choice = (response.get("choices") or [{}])[0]
            row = {"id": case["id"], "finish_reason": choice.get("finish_reason"),
                   "response": choice.get("message", {}).get("content"), "usage": response.get("usage"),
                   "tool_calls": choice.get("message", {}).get("tool_calls", [])}
        except Exception as exc:
            row = {"id": case["id"], "error_type": type(exc).__name__}
        row["elapsed_ms"] = round((time.monotonic() - started)*1000)
        results.append(row)
        save(out / "results.json", results)
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

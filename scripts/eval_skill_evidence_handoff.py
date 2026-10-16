"""Three-arm, frozen-data synthesis replay using the existing DSH SDK.

--prepare persists all inputs. --run uses fresh sessions and no tools in each
arm, so no market API is called. This does NOT replace or claim to reproduce
the original live query loop; it tests the evidence -> synthesis boundary.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.experiments.skill_handoff import (
    endpoint_change, evidence_packet, freeze_results, price_range, synthesis_prompt, used_definitions,
)
from src.services.session_variable_store_service import SessionVariableStoreService


def dump(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(args, out: Path):
    out.mkdir(parents=True, exist_ok=False)
    source = (ROOT / args.source).resolve()
    event = json.loads((source / "events.jsonl").read_text().splitlines()[-1])
    manifest = json.loads((source / "manifest.json").read_text())
    refs = event["result_refs"]
    # This CLI operates on a locally selected, known test run. No cross-user API.
    session_id, _ = SessionVariableStoreService.parse_data_ref(refs[0]["result_ref"])
    frozen = freeze_results(SessionVariableStoreService(), session_id=session_id, result_refs=refs)
    dump(out / "frozen_results.json", frozen)
    catalog_path = ROOT / "src/tools/finance_data/catalog/api_view_catalog.json"
    catalog = json.loads(catalog_path.read_text())
    definitions = used_definitions(frozen, catalog)
    methods = list(event["skill_results"])
    context = json.loads(next((source / "runtime").rglob("turn_context.json")).read_text())
    snapshot = context["tool_context"]["_finance_skill_snapshot"]
    for step in event["execution_steps"]:
        if step.get("tool", "").endswith("read_finance_skill_reference"):
            call = step["arguments"]
            methods.append(snapshot["skills"][call["skill_id"]]["references"][call["reference"]]["content"])
    case = json.loads((ROOT / args.case).read_text())
    calculations = []
    indexed = {item["result_name"]: item for item in frozen}
    functions = {"endpoint_change": endpoint_change, "price_range": price_range}
    for spec in case["calculations"]:
        item = indexed[spec["result_name"]]
        kwargs = {k: v for k, v in spec.items() if k not in {"result_name", "operation"}}
        calculations.append({"result_ref": item["result_ref"], "source_sha256": item["sha256"],
                             "operation": spec["operation"],
                             "result": functions[spec["operation"]](item["rows"], **kwargs)})
    dump(out / "calculations.json", calculations)
    dump(out / "methods.json", methods)
    dump(out / "definitions.json", definitions)
    dump(out / "case.json", case)
    packet = evidence_packet(frozen)
    dump(out / "handoff.json", packet)
    common = dict(question=manifest["question"], methods=methods, definitions=definitions)
    prompts = {
        "envelopes": synthesis_prompt(**common, evidence=[
            {"source_envelope": item["source_envelope"], "registered_result": item["registered"]}
            for item in frozen]),
        "handoff": synthesis_prompt(**common, evidence=packet),
        "handoff_calculated": synthesis_prompt(**common, evidence=packet, calculations=calculations),
    }
    for arm, prompt in prompts.items():
        (out / f"{arm}.prompt.txt").write_text(prompt)
    (out / "system.txt").write_text((ROOT / "src/scenarios/financial_qa/dsh_system.md").read_text())
    dump(out / "no_tools.patch.json", [{"id": key, "disabled": True} for key in
                                      ["persistent-bash", "persistent-pwsh", "str-replace-editor"]])
    bound_paths = [Path(__file__), ROOT / "src/experiments/skill_handoff.py", catalog_path]
    code_snapshot = out / "source"
    code_snapshot.mkdir()
    for path in bound_paths[:2]:
        (code_snapshot / path.name).write_bytes(path.read_bytes())
    dump(out / "manifest.json", {
        "prepared_at": datetime.now(timezone.utc).isoformat(), "source_run": str(source),
        "source_manifest_sha256": sha(source / "manifest.json"),
        "source_events_sha256": sha(source / "events.jsonl"),
        "model": manifest["model"], "question": manifest["question"],
        "source_skill_revision": manifest["skill_revision"],
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
        "files": {str(p): sha(p) for p in bound_paths},
        "frozen_artifacts": {str(p.relative_to(out)): sha(p) for p in out.rglob("*") if p.is_file()},
        "prompt_chars": {key: len(value) for key, value in prompts.items()},
        "scope": "Offline frozen-evidence synthesis replay; same methods, definitions, model and output budget; not HTTP/UI or live autonomous handoff",
        "manual_review": case["manual_review"],
    })
    print("PREPARED", {key: len(value) for key, value in prompts.items()}, flush=True)


def run(args, out: Path):
    from dotenv import dotenv_values, load_dotenv
    from src.scenarios.financial_qa.dsh_service import (
        FinanceDeepSeekHarnessSessionService, _load_sdk_class, _llm_step_usages, _usage,
    )
    manifest = json.loads((out / "manifest.json").read_text())
    for name, expected in manifest["frozen_artifacts"].items():
        if sha(out / name) != expected:
            raise ValueError(f"Frozen artifact changed: {name}")
    load_dotenv(ROOT / ".env", override=False)
    cfg = dotenv_values(ROOT / ".env_tmp")
    os.environ.update(LLM_BASE_URL=cfg["BASE_URL"], LLM_API_KEY=cfg["LLM_KEY"],
                      LLM_DEFAULT_MODEL=manifest["model"])
    runtime = FinanceDeepSeekHarnessSessionService(enabled=True, worker_count=1)
    harness_class = _load_sdk_class()
    outcomes = []
    try:
        for arm in args.arms:
            for repeat in range(args.repeats):
                target = out / f"{arm}_{repeat + 1}"
                target.mkdir(exist_ok=False)
                dump(target / "config.json", {"model": manifest["model"], "reasoning_effort": "low",
                     "max_tokens": args.max_tokens, "source_prompt_sha256": sha(out / f"{arm}.prompt.txt"),
                     "system_sha256": sha(out / "system.txt"), "tools": [], "fresh_session": True})
                h = harness_class(provider=runtime.provider, model=manifest["model"], reasoning_effort="low",
                    max_tokens=args.max_tokens, cwd=str(ROOT), runtime_cwd=str(ROOT),
                    dsh_bin=str(runtime._dsh_bin()), profile="sdk-minimal",
                    patches=(str(out / "no_tools.patch.json"),), dsh_home=str(target / "home"),
                    env={"DSH_SYSTEM_PROMPT": (out / "system.txt").read_text()},
                    base_url=cfg["BASE_URL"], api_key=cfg["LLM_KEY"],
                    initialize_timeout_seconds=120, request_timeout_seconds=300, shutdown_timeout_seconds=3)
                start = time.perf_counter()
                print("RUNNING", target.name, flush=True)
                def progress(notification):
                    if notification.method != "session.event":
                        return
                    event = notification.payload.get("event") or {}
                    # Save the same replayable events exposed by SDK RunResult;
                    # never log the process environment or transport headers.
                    with (target / "progress.jsonl").open("a") as handle:
                        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                    if event.get("type") in {"turn/start", "step/start", "step/end", "turn/end"}:
                        print("EVENT", target.name, event["type"], flush=True)
                try:
                    result = h.run((out / f"{arm}.prompt.txt").read_text(), on_notification=progress)
                    (target / "answer.md").write_text(result.final_response)
                    dump(target / "events.json", result.events)
                    tools = [e for e in result.events if e.get("type") == "tool/call"]
                    summary = {"seconds": time.perf_counter() - start, "finish_reason": result.finish_reason,
                               "usage": _usage(_llm_step_usages(result.events)), "tool_call_count": len(tools)}
                    dump(target / "result.json", summary)
                    outcomes.append({"arm": arm, "repeat": repeat + 1, **summary})
                    print("FINISHED", target.name, summary, flush=True)
                except Exception as exc:
                    dump(target / "error.json", {"error_class": type(exc).__name__, "seconds": time.perf_counter() - start})
                    raise
                finally:
                    h.close()
    finally:
        runtime.close()
    dump(out / "run_summary.json", outcomes)
    return all(item["finish_reason"] == "completed" for item in outcomes) and bool(outcomes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="outputs/stock_technical_case_20260929_r2")
    parser.add_argument("--output", default="outputs/skill_evidence_handoff_20260929")
    parser.add_argument("--case", default="tests/evals/stock_skill_handoff_v1.json")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--arms", nargs="+", choices=["envelopes", "handoff", "handoff_calculated"],
                        default=["envelopes", "handoff", "handoff_calculated"])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=32768)
    args = parser.parse_args()
    os.chdir(ROOT)
    out = (ROOT / args.output).resolve()
    if args.prepare:
        prepare(args, out)
    if args.run:
        if not run(args, out):
            raise SystemExit(1)


if __name__ == "__main__":
    main()

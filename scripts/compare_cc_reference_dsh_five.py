"""Five existing questions, frozen CC versus a captured current DSH workspace.

Evaluation only: complete answers on both sides, no business/runtime patches.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "baselines/cc_20260902_rebuilt"
MODEL = "deepseek-v4-flash-0731"


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def helper():
    spec = importlib.util.spec_from_file_location("frozen_reference_launcher", BASELINE / "tools/run_cc_reference.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(output):
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("New output directory required")
    sources = [
        "outputs/d4f10504-8df6-435e-9316-3d89b5fd1015/source_cases.json",
        "outputs/financial_qa_mainland_full_increment_20260903/cases_mainland_full_no_news.json",
    ]
    index = {}
    for name in sources:
        for row in json.loads((REPO / name).read_text())["cases"]:
            index[row["case_id"]] = {**row, "source_file": name}
    ids = ["BUS010", "BUS093", "BUS141", "BUS168", "RTEF012"]
    write(output / "cases.json", {"cases": [
        {"case_id": key, "question": index[key]["question"], "source_file": index[key]["source_file"]}
        for key in ids]})
    snapshot = output / "dsh-workspace"
    snapshot.mkdir()
    for name in ("src", "config"):
        shutil.copytree(REPO / name, snapshot / name,
                        ignore=shutil.ignore_patterns("__pycache__", "._*", "*.pyc"))
    original = json.loads((BASELINE / "manifest.json").read_text())
    extras = set(original["source_allowlist"]["files"]) | {"scripts/dsh_source_runtime.sh", "scripts/dsh_source_runtime.py"}
    for name in sorted(extras):
        path = REPO / name
        if path.is_file():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    hashes = {str(path.relative_to(snapshot)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(snapshot.rglob("*")) if path.is_file()}
    write(output / "manifest.json", {
        "cc_tag": "cc-reference-20260902-rebuilt-v1", "cc_source": original["source_commit"],
        "cc_archive": original["source_archive"],
        "dsh_git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "dsh_source_sha256": hashes, "dsh_contains_working_changes": True,
        "provider": "dashscope", "model": MODEL, "reasoning_effort": "low",
        "mode": {"data_only": False, "research_mode": "auto", "execution_mode": "standard"},
        "notes": "Sequential isolated questions per runtime. CC and DSH use their respective Anthropic/OpenAI-compatible endpoints on the same Bailian model. Native loop policies are unchanged; budgets are recorded, not claimed identical.",
        "source_sha256": {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in sources},
    })


def worker(output):
    root = output / "dsh-workspace"
    sys.path.insert(0, str(root))
    os.chdir(root)
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService, _load_sdk_class
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
    from src.services.application_runtime_service import ApplicationRuntimeService
    from src.services.session_variable_store_service import SessionVariableStoreService
    from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
    dest = Path(os.environ.get("FIVE_CASE_DSH_OUTPUT") or output / "dsh")
    system_tools = FinanceDataQueryCcTools(result_store=SessionVariableStoreService(data_root=root / "data"))
    dsh = FinanceDeepSeekHarnessSessionService(enabled=True, system_tools=system_tools, worker_count=1,
        root_dir=dest / "runtime", log_path=dest / "runtime-records.jsonl")
    service = FinancialQaCcService(enabled=True, system_tools=system_tools, dsh_session_service=dsh,
        root_dir=dest / "unused-cc", log_path=dest / "unused-cc.jsonl",
        business_skill_catalog=FinanceBusinessSkillCatalog(root=root / "src/skills/finance-business",
            snapshot_root=dest / "skill-snapshots"))
    app = ApplicationRuntimeService().get_application_context("investment_workbench")
    sdk_cls = _load_sdk_class()
    sdk_path = Path(sys.modules[sdk_cls.__module__].__file__).resolve()
    write(dest / "metadata.json", {"model": dsh.model, "base_url": dsh.base_url,
        "effort": dsh.reasoning_effort, "max_tokens": dsh.max_tokens,
        "loop_policy": dsh.loop_policy_config, "catalog_revision": system_tools.finance_catalog.catalog_revision(),
        "sdk_module": str(sdk_path), "sdk_module_sha256": hashlib.sha256(sdk_path.read_bytes()).hexdigest(),
        "application_context": app, "source_root": str(root)})
    cases = json.loads((output / "cases.json").read_text())["cases"]
    try:
        with (dest / "results.jsonl").open("x", encoding="utf-8") as results:
            for index, case in enumerate(cases, 1):
                started = time.perf_counter()
                with (dest / f"case-{index:04d}.events.jsonl").open("x", encoding="utf-8") as events:
                    def capture(event):
                        events.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
                        events.flush()
                    try:
                        response = service.answer(thread_id=index, turn_id=1, owner_id="isolated-five-comparison",
                            user_text=case["question"], application_context=app,
                            dispatch_plan={"selected_agent": "investment_analyst", "turn_mode": "normal_qa",
                                "entry": "agent_route", "semantic_turn": {"resolved_question": case["question"]}},
                            runtime="dsh", research_mode="auto", execution_mode="standard", data_only=False,
                            isolated_request=True, include_response_data=True, event_sink=capture)
                        row = {**case, "response": response}
                    except Exception as exc:
                        row = {**case, "error": {"type": type(exc).__name__, "message": str(exc)}}
                row["wall_duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
                results.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                results.flush()
                print(case["case_id"], row["wall_duration_ms"], flush=True)
    finally:
        service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--runtime", choices=["cc", "dsh"])
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--dsh-output-name", default="dsh")
    args = parser.parse_args()
    out = args.output.resolve()
    if args.prepare:
        prepare(out)
        return
    if args.worker:
        worker(out)
        return
    if args.runtime == "cc":
        command = [str(REPO / ".venv-cc-reference-20260902/bin/python"), "-B",
            str(BASELINE / "tools/run_cc_reference.py"), "--env-file", str(REPO / ".env"),
            "--provider", "dashscope", "--model", MODEL, "--cases", str(out / "cases.json"),
            "--output-dir", str(out / "cc")]
        raise SystemExit(subprocess.call(command))
    if args.runtime != "dsh":
        parser.error("choose --prepare or --runtime")
    ref = helper()
    env, _ = ref.provider_environment(ref.external_environment(REPO / ".env", os.environ), "dashscope", MODEL)
    from dotenv import dotenv_values
    settings = dotenv_values(REPO / ".env")
    for key in ("FINANCE_DSH_NODE_BIN", "FINANCE_DSH_SDK_SOURCE", "FINANCE_DSH_SOURCE_ROOT"):
        if settings.get(key):
            env[key] = settings[key]
    env.update({key: os.environ[key] for key in ("PATH", "HOME", "USER", "LANG", "TZ") if key in os.environ})
    env.update({"FINANCE_DSH_PROVIDER": "deepseek-official", "FINANCE_DSH_REASONING_EFFORT": "low",
        "FINANCE_DSH_FINANCIAL_QA_ENABLED": "1", "FINANCE_CC_PROVIDER": "dashscope", "FINANCE_CC_MODEL": MODEL,
        "FINANCE_DSH_BIN": str(out / "dsh-workspace/scripts/dsh_source_runtime.sh"),
        "PYTHONDONTWRITEBYTECODE": "1"})
    dest = out / args.dsh_output_name
    dest.mkdir()
    # DSH deliberately scrubs secret-shaped parent variables before spawning
    # MCP. The existing application reloads its local .env; reproduce that
    # configuration mechanism only in this private temporary eval workspace.
    config_file = out / "dsh-workspace/.env"
    with config_file.open("x", encoding="utf-8") as config:
        config_file.chmod(0o600)
        for key, value in env.items():
            if key.startswith(("KINGDOMAI_", "LLM_", "DASHSCOPE_")):
                config.write(f"{key}={json.dumps(value, ensure_ascii=False)}\n")
    env["FIVE_CASE_DSH_OUTPUT"] = str(dest)
    try:
        with (dest / "stdout.log").open("x") as stdout, (dest / "stderr.log").open("x") as stderr:
            process = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                "--output", str(out), "--worker"], cwd=out, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                status = process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
                raise RuntimeError("Evaluation process reached 600s; partial evidence retained")
    finally:
        config_file.unlink(missing_ok=True)
    print("dsh exit", status)
    raise SystemExit(status)


if __name__ == "__main__":
    main()

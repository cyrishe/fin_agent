"""Run a sealed financial CC reference in its own extracted workspace.

Use a dedicated, pinned virtualenv. The launcher imports no application code in
the parent process; the child imports only the archive's ``src``. This is source
and session isolation, not a sandbox for generated compute code or external data.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any, Mapping
from urllib.parse import unquote, urlparse
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_BASELINE = ROOT if SCRIPT_DIR.name == "tools" else ROOT / "baselines/cc_20260902_rebuilt"
HISTORICAL_PROFILE = {
    "runtime": "cc", "provider": "deepseek", "model": "deepseek-v4-flash",
    "effort": "low", "max_turns": 12, "research_mode": "auto", "data_only": False,
}
# Deliberately excludes platform identity/storage, DSH, Codex and ambient prompt
# controls. Credentials are passed through the child's environment, never JSON.
ALLOWED_ENV = frozenset({
    "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL",
    "DASHSCOPE_ANTHROPIC_BASE_URL", "DASHSCOPE_ANTHROPIC_CA_BUNDLE",
    "ANTHROPIC_API_KEY", "CLAUDE_AUTH_TOKEN", "CLAUDE_BASE_URL",
    "LLM_API_KEY", "LLM_KEY", "LLM_BASE_URL", "LLM_ENDPOINT",
    "LLM_DEFAULT_MODEL", "LLM_FLASH_MODEL", "LLM_REASONING_MODEL",
    "LLM_DEFAULT_ENABLE_THINKING", "LLM_DEFAULT_MAX_TOKENS",
    "LLM_LONG_CONTEXT_MAX_TOKENS", "LLM_CLIENT_TIMEOUT_SECONDS",
    "LLM_CLIENT_MAX_RETRIES", "KINGDOMAI_DB_HOST", "KINGDOMAI_DB_PORT",
    "KINGDOMAI_DB_USER", "KINGDOMAI_DB_PASSWORD",
})
SECRET_ENV = frozenset(name for name in ALLOWED_ENV if any(
    token in name for token in ("KEY", "TOKEN", "PASSWORD")))


def external_environment(path: Path | None, inherited: Mapping[str, str]) -> dict[str, str]:
    """Read explicit .env without interpolation; process injection takes priority."""
    values: dict[str, str] = {}
    if path is not None:
        from dotenv import dotenv_values
        if not path.is_file():
            raise ValueError(f"env file does not exist: {path}")
        values.update({key: str(value) for key, value in
                       dotenv_values(path, interpolate=False).items() if value is not None})
    values.update({key: value for key, value in inherited.items() if value})
    selected = {key: values[key] for key in ALLOWED_ENV if values.get(key)}
    # The historical DB resolver can borrow credentials from a platform URL.
    # Resolve that indirection here, passing only explicit business DB settings.
    direct_url = values.get("KINGDOMAI_DB_URL", "")
    source_name = values.get("KINGDOMAI_DB_CREDENTIAL_SOURCE", "")
    source_url = values.get(source_name, "") if source_name else ""
    raw_url = direct_url or source_url
    if raw_url:
        parsed = urlparse(raw_url.replace("mysql+pymysql://", "mysql://", 1))
        if parsed.scheme != "mysql" or not parsed.hostname:
            raise ValueError("KINGDOMAI database credential source must be a MySQL URL")
        if direct_url and parsed.path.strip("/") != "kingdomai":
            raise ValueError("KINGDOMAI_DB_URL must target kingdomai")
        for key, value in {
            "KINGDOMAI_DB_HOST": parsed.hostname,
            "KINGDOMAI_DB_PORT": str(parsed.port or 3306),
            "KINGDOMAI_DB_USER": unquote(parsed.username or ""),
            "KINGDOMAI_DB_PASSWORD": unquote(parsed.password or ""),
        }.items():
            if value:
                selected.setdefault(key, value)
    return selected


def provider_environment(selected: Mapping[str, str], provider: str, model: str,
                         base_url: str | None = None) -> tuple[dict[str, str], dict[str, Any]]:
    endpoints = {
        "deepseek": ("https://api.deepseek.com/anthropic", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
        "dashscope": ("https://dashscope.aliyuncs.com/apps/anthropic",
                      "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    }
    cc_default, auxiliary_url, key_name = endpoints[provider]
    endpoint = (base_url or cc_default).rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("--base-url must be an HTTPS endpoint without credentials, query or fragment")
    if provider == "deepseek" and endpoint != cc_default:
        raise ValueError("historical deepseek provider uses the official Anthropic endpoint")
    env = {key: value for key, value in selected.items()
           if (key.startswith("KINGDOMAI_") or key == key_name
               or (provider == "dashscope" and key == "DASHSCOPE_ANTHROPIC_CA_BUNDLE"))}
    env.update({
        "CLAUDE_BASE_URL": endpoint, "LLM_BASE_URL": auxiliary_url,
        "LLM_DEFAULT_MODEL": model, "LLM_FLASH_MODEL": model, "LLM_REASONING_MODEL": model,
        "LLM_DEFAULT_ENABLE_THINKING": "false", "LLM_DEFAULT_MAX_TOKENS": "8192",
        "LLM_LONG_CONTEXT_MAX_TOKENS": "40960", "LLM_CLIENT_TIMEOUT_SECONDS": "45",
        "LLM_CLIENT_MAX_RETRIES": "1",
    })
    if selected.get(key_name):
        env["LLM_API_KEY"] = selected[key_name]
    if provider == "dashscope":
        env["DASHSCOPE_ANTHROPIC_BASE_URL"] = endpoint
        env["DASHSCOPE_BASE_URL"] = auxiliary_url
    return env, {"cc_base_url": endpoint, "auxiliary_base_url": auxiliary_url,
                 "credential_environment": key_name, "auxiliary_model": model}


def select_cases(query: str | None, path: Path | None, case_ids: str = "") -> list[dict[str, Any]]:
    if query is not None:
        if case_ids:
            raise ValueError("--case-ids requires --cases")
        return [{"case_id": "query-1", "question": query}]
    if path is None:
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("cases", []) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("cases must be a JSON list or an object with a cases list")
    cases = []
    for number, outer in enumerate(rows, 1):
        row = outer.get("case", outer) if isinstance(outer, dict) else {}
        question = str(row.get("question") or row.get("query") or "").strip()
        if not question:
            raise ValueError(f"case {number} has no question/query")
        cases.append({"case_id": str(row.get("case_id") or row.get("id") or number),
                      "question": question, "source": outer})
    known = [case["case_id"] for case in cases]
    if len(known) != len(set(known)):
        raise ValueError("case IDs must be unique")
    wanted = [item.strip() for item in case_ids.split(",") if item.strip()]
    if wanted:
        missing = set(wanted) - set(known)
        if missing:
            raise ValueError(f"unknown case IDs: {', '.join(sorted(missing))}")
        lookup = {case["case_id"]: case for case in cases}
        cases = [lookup[key] for key in dict.fromkeys(wanted)]
    return cases


def assert_archived_imports(workspace: Path, modules: Mapping[str, Any] | None = None) -> dict[str, str]:
    origins = {}
    for name, module in (sys.modules if modules is None else modules).items():
        if name != "src" and not name.startswith("src."):
            continue
        paths = list(getattr(module, "__path__", []))
        origin = getattr(module, "__file__", None)
        if origin:
            paths.append(origin)
            origins[name] = str(Path(origin).resolve())
        for value in paths:
            if not Path(value).resolve().is_relative_to(workspace):
                raise RuntimeError(f"live application code mixed into reference: {name}: {value}")
    return origins


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _package_metadata() -> dict[str, Any]:
    versions = {}
    for name in ("claude-agent-sdk", "mcp", "pandas", "numpy", "openai",
                 "pymysql", "anyio", "httpx", "python-dotenv"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    import claude_agent_sdk
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / (
        "claude.exe" if sys.platform == "win32" else "claude")
    if not bundled.is_file():
        raise RuntimeError("pinned Claude SDK must include its bundled CLI; PATH fallback is not a fixed baseline")
    with bundled.open("rb") as binary:
        cli_hash = hashlib.file_digest(binary, "sha256").hexdigest()
    return {"python": sys.version, "python_version": platform.python_version(), "executable": sys.executable,
            "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
            "packages": versions,
            "claude_cli": {"path": str(bundled), "size": bundled.stat().st_size,
                           "sha256": cli_hash}}


def verify_environment(lock_text: str, expected: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, Any]:
    checked, mismatches = {}, []
    for line in lock_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("==") != 1:
            raise ValueError("reference dependency lock must contain exact name==version pins")
        name, version = line.split("==")
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        checked[name] = installed
        if installed != version:
            mismatches.append(f"{name}: expected {version}, installed {installed}")
    if not checked:
        raise ValueError("reference dependency lock is empty")
    if mismatches:
        raise RuntimeError("reference dependency mismatch: " + "; ".join(mismatches))
    platform_info = actual["platform"]
    expected_os = str(expected.get("platform", "")).split("-", 1)[0]
    actual_os = {"Darwin": "macOS"}.get(platform_info["system"], platform_info["system"])
    same_target = expected_os == actual_os and expected.get("machine") == platform_info["machine"]
    expected_hash = (expected.get("claude_cli") or {}).get("sha256")
    if same_target and actual["claude_cli"]["sha256"] != expected_hash:
        raise RuntimeError("bundled Claude CLI hash differs from reference on the same OS/architecture")
    return {"checked_package_count": len(checked), "packages": checked,
            "python_matches": actual["python_version"] == expected.get("python"),
            "same_os_architecture": same_target, "cli_hash_verified": same_target,
            "note": "Cross-platform CLI binary is recorded but is not the reference binary." if not same_target else ""}


def _run_cases(service: Any, cases: list[dict[str, Any]], output: Path, run_id: str,
               application_context: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    records = []
    with (output / "results.jsonl").open("x", encoding="utf-8") as results_file:
        for index, case in enumerate(cases, 1):
            events: list[dict[str, Any]] = []
            event_path = output / f"case-{index:04d}.events.jsonl"
            started = time.perf_counter()
            with event_path.open("x", encoding="utf-8") as event_file:
                def capture(event: dict[str, Any]) -> None:
                    event = dict(event)
                    events.append(event)
                    event_file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
                    event_file.flush()
                try:
                    response = service.answer(
                        thread_id=index, turn_id=1,
                        owner_id=f"reference-{run_id}", user_text=case["question"],
                        dispatch_plan={"selected_agent": "investment_analyst", "turn_mode": "normal_qa",
                                       "entry": "agent_route", "semantic_turn": {"resolved_question": case["question"]}},
                        research_mode="auto", runtime="cc", data_only=False,
                        application_context=application_context,
                        isolated_request=True, include_response_data=True, event_sink=capture,
                    )
                    record = {"case_id": case["case_id"], "question": case["question"], "response": response}
                    if not str(response.get("message") or "").strip():
                        record["error"] = {"type": "EmptyAnswer", "message": "CC returned no answer text"}
                except Exception as exc:
                    record = {"case_id": case["case_id"], "question": case["question"],
                              "error": {"type": type(exc).__name__, "message": str(exc)}}
            record.update({"wall_duration_ms": round((time.perf_counter() - started) * 1000, 2),
                           "event_count": len(events), "events_file": event_path.name})
            results_file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            results_file.flush()
            records.append(record)
            print(f"[{index}/{len(cases)}] {case['case_id']} {record['wall_duration_ms']}ms", flush=True)
    return records


def _worker(config_path: Path) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    workspace, output = Path(config["workspace"]).resolve(), config_path.parent.resolve()
    assert_archived_imports(workspace)
    if (workspace / ".env").exists():
        raise RuntimeError("sealed source must not contain a .env file")
    # -I removes ambient PYTHONPATH/cwd. Also reject editable installs of any
    # different Fin Agent checkout before adding the one archived source root.
    for value in sys.path:
        candidate = Path(value).resolve()
        if candidate != workspace and (candidate / "src/scenarios/financial_qa/service.py").is_file():
            raise RuntimeError(f"live checkout on isolated Python path: {candidate}")
    sys.path.insert(0, str(workspace))
    os.chdir(workspace)
    if config["check_only"]:
        def deny_network(event: str, arguments: tuple[Any, ...]) -> None:
            if event in {"socket.connect", "socket.connect_ex", "subprocess.Popen"}:
                raise RuntimeError(f"check-only forbids network/runtime execution: {event}")
        sys.addaudithook(deny_network)
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
    from src.services.session_variable_store_service import SessionVariableStoreService
    from src.services.application_runtime_service import ApplicationRuntimeService

    metadata = {**config["metadata"], **_package_metadata()}
    metadata["dependency_verification"] = verify_environment(
        config["requirements_lock"], config["expected_environment"], metadata)
    application_context = ApplicationRuntimeService().get_application_context("investment_workbench")
    _write_json(output / "application-context.json", application_context)
    service = FinancialQaCcService(
        enabled=True, root_dir=workspace / "data/financial_qa_cc_sessions",
        log_path=workspace / "outputs/financial_qa_cc/events.jsonl",
        business_skill_catalog=FinanceBusinessSkillCatalog(
            root=workspace / "src/skills/finance-business",
            snapshot_root=workspace / "outputs/runtime_skill_snapshots/finance-business"),
        system_tools=FinanceDataQueryCcTools(
            result_store=SessionVariableStoreService(data_root=workspace / "data")),
    )
    try:
        metadata["module_origins"] = assert_archived_imports(workspace)
        metadata["loaded_profile"] = {"provider": service.session_service.provider,
                                      "model": service.session_service.model,
                                      "effort": service.session_service.effort,
                                      "max_turns": service.session_service.max_turns}
        metadata["skill_runtime_binding"] = service.business_skill_catalog.runtime_binding()
        metadata["catalog_revision"] = service.system_tools.finance_catalog.catalog_revision()
        _write_json(output / "metadata.json", metadata)
        if config["check_only"]:
            print(json.dumps({"check_only": "passed", "source_commit": metadata["source_commit"],
                              "loaded_profile": metadata["loaded_profile"], "workspace": str(workspace)}))
            return 0
        records = _run_cases(service, config["cases"], output, config["run_id"], application_context)
        metadata["module_origins"] = assert_archived_imports(workspace)
        metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
        metadata["case_count"] = len(records)
        _write_json(output / "metadata.json", metadata)
        return 0 if all(not row.get("error") and not row.get("response", {}).get("financial_qa", {}).get("error")
                        for row in records) else 1
    finally:
        service.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--query")
    inputs.add_argument("--cases", type=Path)
    parser.add_argument("--case-ids", default="")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--provider", choices=("deepseek", "dashscope"), default="deepseek")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", help="Explicit CC Anthropic-compatible HTTPS endpoint; auxiliary endpoint follows provider")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    cases = select_cases(args.query, args.cases, args.case_ids)
    if not args.check_only and not cases:
        parser.error("provide --query or --cases, or use --check-only")
    # Import only the archive utility, never live src. Import by script location
    # also supports invocation from a separate cwd and python -I.
    import importlib.util
    spec = importlib.util.spec_from_file_location("cc_archive_utility", SCRIPT_DIR / "build_cc_reference_archive.py")
    utility = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = utility
    spec.loader.exec_module(utility)
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("output directory must be empty; each reference run owns a fresh workspace")
    workspace = output / "workspace"
    baseline = args.baseline.expanduser().resolve()
    manifest = utility.extract_archive(baseline, workspace)
    selected, endpoints = provider_environment(external_environment(args.env_file, os.environ),
                                               args.provider, args.model, args.base_url)
    if not args.check_only and not selected.get(endpoints["credential_environment"]):
        raise ValueError(f"missing explicit provider credential: {endpoints['credential_environment']}")
    actual_profile = {**HISTORICAL_PROFILE, "provider": args.provider, "model": args.model}
    # Keep ordinary OS prerequisites but no user/model/project path inheritance.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ") if key in os.environ}
    env.update(selected)
    (workspace / "tmp").mkdir()
    env.update({
        "TMPDIR": str(workspace / "tmp"), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "CLAUDE_CONFIG_DIR": str(workspace / "data/claude"),
        "FINANCE_CC_FINANCIAL_QA_ENABLED": "1", "FINANCE_CC_PROVIDER": args.provider,
        "FINANCE_CC_MODEL": args.model, "FINANCE_CC_FINANCIAL_QA_EFFORT": "low",
        "FINANCE_CC_FINANCIAL_QA_MAX_TURNS": "12", "FINANCE_DSH_FINANCIAL_QA_ENABLED": "0",
    })
    metadata = {
        "source_commit": manifest["source_commit"], "source_archive": manifest["source_archive"],
        "catalog_sha256": manifest.get("catalog_sha256"),
        "reconstruction_note": manifest.get("reconstruction_note"),
        "historical_profile": HISTORICAL_PROFILE, "actual_profile": actual_profile,
        "actual_endpoints": endpoints,
        "provider_model_match_historical": actual_profile == HISTORICAL_PROFILE,
        "external_environment_names": sorted(selected),
        "auxiliary_model_config": {key: value for key, value in selected.items()
                                   if key.startswith("LLM_") and key not in SECRET_ENV},
        "started_at": datetime.now(timezone.utc).isoformat(), "workspace": str(workspace),
        "case_source": str(args.cases.resolve()) if args.cases else "--query",
        "case_source_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest() if args.cases else None,
        "check_only": args.check_only,
    }
    config_path = output / "run-config.json"
    _write_json(config_path, {"workspace": str(workspace), "run_id": uuid.uuid4().hex,
                             "metadata": metadata, "cases": cases, "check_only": args.check_only,
                             "requirements_lock": (baseline / "requirements.lock.txt").read_text(encoding="utf-8"),
                             "expected_environment": json.loads((baseline / "environment.json").read_text(encoding="utf-8"))})
    command = [str(args.python.expanduser().absolute()), "-I", "-B", str(Path(__file__).resolve()),
               "--_worker", str(config_path)]
    with (output / "worker.stdout.log").open("x") as stdout, (output / "worker.stderr.log").open("x") as stderr:
        result = subprocess.run(command, cwd=workspace, env=env, stdout=stdout, stderr=stderr, check=False)
    print(f"CC reference exit={result.returncode}; results: {output}")
    return result.returncode


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--_worker":
        raise SystemExit(_worker(Path(sys.argv[2])))
    raise SystemExit(main())

"""Offline launcher contracts; no provider, model, database or native CLI run."""
from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

_TEST_BASE = Path(__file__).resolve().parents[1]
if (_TEST_BASE / "history").is_dir() and (_TEST_BASE / "tools").is_dir():
    _spec = importlib.util.spec_from_file_location(
        "sealed_cc_reference_runner", _TEST_BASE / "tools/run_cc_reference.py")
    assert _spec is not None and _spec.loader is not None
    runner = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = runner
    _spec.loader.exec_module(runner)
else:
    from scripts import run_cc_reference as runner


def test_environment_resolves_business_credentials_without_platform_identity(tmp_path):
    env_file = tmp_path / "external.env"
    env_file.write_text(
        'KINGDOMAI_DB_HOST=47.94.1.2\nKINGDOMAI_DB_PORT=3312\n'
        'KINGDOMAI_DB_CREDENTIAL_SOURCE=PLATFORM_DB_URL\n'
        'PLATFORM_DB_URL=mysql+pymysql://account:p%24ss@old-host:3306/stock_agent\n'
        'SYSTEM_DB_URL=mysql://admin:secret@production/aiia_system\n'
        'BUSINESS_DB_URL=mysql://old:secret@old/kingdomai\n'
        'DEEPSEEK_API_KEY="not-real-${literal}"\n'
        'FINANCE_DSH_API_KEY=not-real-dsh\n', encoding="utf-8")
    env = runner.external_environment(env_file, {"DASHSCOPE_API_KEY": "injected-key"})
    assert env == {"KINGDOMAI_DB_HOST": "47.94.1.2", "KINGDOMAI_DB_PORT": "3312",
                   "KINGDOMAI_DB_USER": "account", "KINGDOMAI_DB_PASSWORD": "p$ss",
                   "DEEPSEEK_API_KEY": "not-real-${literal}", "DASHSCOPE_API_KEY": "injected-key"}


def test_environment_explicit_credentials_win_and_wrong_database_rejected():
    env = runner.external_environment(None, {
        "KINGDOMAI_DB_URL": "mysql://urluser:pwd@host:3312/kingdomai",
        "KINGDOMAI_DB_USER": "explicit-user", "PYTHONPATH": "/active/repo",
        "CLAUDE_CONFIG_DIR": "/personal/claude", "FINANCE_CC_MODEL": "ambient-model"})
    assert env["KINGDOMAI_DB_USER"] == "explicit-user"
    assert env["KINGDOMAI_DB_HOST"] == "host"
    assert not {"PYTHONPATH", "CLAUDE_CONFIG_DIR", "FINANCE_CC_MODEL"} & env.keys()
    with pytest.raises(ValueError, match="target kingdomai"):
        runner.external_environment(None, {"KINGDOMAI_DB_URL": "mysql://u:p@host/stock_agent"})


def test_provider_endpoint_and_auxiliary_model_do_not_follow_ambient_routes():
    env, metadata = runner.provider_environment({
        "CLAUDE_BASE_URL": "https://api.deepseek.com/anthropic",
        "LLM_BASE_URL": "https://old-provider.invalid/v1", "LLM_FLASH_MODEL": "old-model",
        "DASHSCOPE_API_KEY": "fixture-key", "LLM_API_KEY": "wrong-key",
        "CLAUDE_AUTH_TOKEN": "wrong-token", "DEEPSEEK_API_KEY": "other-provider-key",
    }, "dashscope", "deepseek-v4-flash-0731")
    assert env["CLAUDE_BASE_URL"] == "https://dashscope.aliyuncs.com/apps/anthropic"
    assert env["LLM_BASE_URL"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert env["LLM_API_KEY"] == "fixture-key"
    assert "CLAUDE_AUTH_TOKEN" not in env and "DEEPSEEK_API_KEY" not in env
    assert env["LLM_FLASH_MODEL"] == env["LLM_DEFAULT_MODEL"] == "deepseek-v4-flash-0731"
    assert "fixture-key" not in json.dumps(metadata)
    with pytest.raises(ValueError, match="without credentials"):
        runner.provider_environment({}, "dashscope", "model", "https://key:secret@example.com/api?token=x")


def test_dependency_pins_and_same_platform_cli_hash_are_enforced(monkeypatch):
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda name: "1.0")
    expected = {"platform": "macOS-test", "machine": "x86_64", "python": "3.12.14",
                "claude_cli": {"sha256": "sealed"}}
    actual = {"platform": {"system": "Darwin", "machine": "x86_64"},
              "python_version": "3.12.14", "claude_cli": {"sha256": "sealed"}}
    assert runner.verify_environment("package==1.0", expected, actual)["cli_hash_verified"]
    with pytest.raises(RuntimeError, match="dependency mismatch"):
        runner.verify_environment("package==2.0", expected, actual)
    with pytest.raises(RuntimeError, match="CLI hash differs"):
        runner.verify_environment("package==1.0", expected, {**actual, "claude_cli": {"sha256": "changed"}})
    other = {**actual, "platform": {"system": "Linux", "machine": "x86_64"}}
    assert not runner.verify_environment("package==1.0", expected, other)["cli_hash_verified"]


def test_cases_keep_historical_query_and_selection_order(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [
        {"case": {"case_id": "A", "question": "原题 A"}, "baseline": {"entry": "stock.quote"}},
        {"case_id": "B", "question": "原题 B"}]}), encoding="utf-8")
    selected = runner.select_cases(None, path, "B,A")
    assert [(row["case_id"], row["question"]) for row in selected] == [("B", "原题 B"), ("A", "原题 A")]
    assert selected[1]["source"]["baseline"] == {"entry": "stock.quote"}
    with pytest.raises(ValueError, match="unknown case IDs"):
        runner.select_cases(None, path, "missing")


def test_live_application_module_or_namespace_is_rejected(tmp_path):
    workspace = tmp_path / "sealed"
    good = SimpleNamespace(__file__=str(workspace / "src/service.py"))
    assert runner.assert_archived_imports(workspace, {"src.service": good}) == {"src.service": good.__file__}
    for module in (SimpleNamespace(__file__="/active/src/service.py"),
                   SimpleNamespace(__path__=["/active/src"])):
        with pytest.raises(RuntimeError, match="live application code"):
            runner.assert_archived_imports(workspace, {"src": module})


def test_run_uses_full_cc_answer_and_preserves_usage_tools_events(tmp_path):
    calls = []
    class Service:
        def answer(self, **kwargs):
            calls.append(kwargs)
            kwargs["event_sink"]({"type": "tool_result", "duration_ms": 9})
            return {"message": "原始 CC 回答", "llm_usage": {"total_tokens": 42},
                    "financial_qa": {"tool_calls": [{"tool_name": "finance_query"}],
                                     "llm_step_usages": [{"input_tokens": 30}], "error": ""}}
    records = runner._run_cases(Service(), [{"case_id": "A", "question": "问题"}], tmp_path, "isolated")
    assert calls[0]["runtime"] == "cc"
    assert calls[0]["research_mode"] == "auto"
    assert calls[0]["data_only"] is False
    assert calls[0]["include_response_data"] is True
    assert calls[0]["isolated_request"] is True
    assert calls[0]["owner_id"].startswith("reference-")
    assert calls[0]["thread_id"] == 1
    assert isinstance(calls[0]["thread_id"], int)
    assert calls[0]["turn_id"] == 1
    assert records[0]["response"]["llm_usage"]["total_tokens"] == 42
    assert records[0]["response"]["financial_qa"]["tool_calls"] == [{"tool_name": "finance_query"}]
    assert json.loads((tmp_path / "case-0001.events.jsonl").read_text())["duration_ms"] == 9


def test_run_preserves_failure_and_continues_next_case(tmp_path):
    class Service:
        def answer(self, **kwargs):
            if kwargs["user_text"] == "first":
                raise TimeoutError("fixture timeout")
            return {"message": "second answer"}
    result = runner._run_cases(Service(), [
        {"case_id": "A", "question": "first"}, {"case_id": "B", "question": "second"}], tmp_path, "run")
    assert result[0]["error"] == {"type": "TimeoutError", "message": "fixture timeout"}
    assert result[1]["response"]["message"] == "second answer"


def test_empty_answer_is_recorded_as_incomplete_not_business_failure(tmp_path):
    service = SimpleNamespace(answer=lambda **kwargs: {"message": " ", "financial_qa": {"error": ""}})
    records = runner._run_cases(service, [{"case_id": "A", "question": "question"}], tmp_path, "empty")
    assert records[0]["error"]["type"] == "EmptyAnswer"


def test_archived_presentation_accepts_numeric_reference_thread_id(tmp_path):
    """Real 2242990 presentation: session-backed table paging requires an int."""
    spec = importlib.util.spec_from_file_location(
        "cc_archive_presentation_test", Path(runner.__file__).parent / "build_cc_reference_archive.py")
    assert spec is not None and spec.loader is not None
    archive = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = archive
    spec.loader.exec_module(archive)
    workspace = tmp_path / "workspace"
    manifest = archive.extract_archive(runner.DEFAULT_BASELINE, workspace)
    assert manifest["source_commit"] == "2242990c9a092c00a4cd1bb8a0bbb42d9fbc7b7f"
    code = '''
import json, os, sys
from pathlib import Path
workspace = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(workspace))
os.chdir(workspace)
def offline(event, arguments):
    if event in {"socket.connect", "socket.connect_ex", "subprocess.Popen"}:
        raise RuntimeError("offline presentation regression")
sys.addaudithook(offline)
from src.scenarios.financial_qa.presentation import FinancialQaPresentationService
service = FinancialQaPresentationService()
refs = [{"api": "stock.report", "result_ref": "session://reference-fixture/v1",
         "schema": {"columns": ["title"]}, "sample": {"rows": [["fixture A"], ["fixture B"]]}, "row_count": 2}]
blocks = service.build("fixture answer", refs, thread_id=1)
table = next(block for block in blocks if block.get("payload", {}).get("shape") == "records")
assert table["payload"]["data"]["thread_id"] == 1
try:
    service.build("fixture answer", refs, thread_id="reference-uuid-1")
except ValueError:
    pass
else:
    raise AssertionError("historical numeric-ID contract was not exercised")
assert Path(sys.modules["src.scenarios.financial_qa.presentation"].__file__).resolve().is_relative_to(workspace)
print(json.dumps({"numeric_thread_id": table["payload"]["data"]["thread_id"], "old_string_id_rejected": True}))
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(workspace)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"numeric_thread_id": 1, "old_string_id_rejected": True}


@pytest.mark.parametrize("check_only", [True, False])
def test_real_isolated_child_uses_frozen_stub_not_ambient_src(tmp_path, check_only):
    """Actual -I child/import/runner; only the archived service is a fixture."""
    workspace = tmp_path / "workspace"
    files = {
        "src/scenarios/financial_qa/service.py": '''
from types import SimpleNamespace
class FinancialQaCcService:
    def __init__(self, **kwargs):
        self.session_service = SimpleNamespace(provider="deepseek", model="deepseek-v4-flash", effort="low", max_turns=12)
        self.business_skill_catalog = kwargs["business_skill_catalog"]
        self.system_tools = kwargs["system_tools"]
    def answer(self, **kwargs):
        assert kwargs["runtime"] == "cc" and kwargs["data_only"] is False
        assert isinstance(kwargs["thread_id"], int) and kwargs["thread_id"] > 0
        kwargs["event_sink"]({"type": "fixture-event"})
        return {"message": "frozen-fixture", "llm_usage": {"total_tokens": 7}, "financial_qa": {"error": ""}}
    def close(self):
        pass
''',
        "src/scenarios/financial_qa/business_skills.py": '''
class FinanceBusinessSkillCatalog:
    def __init__(self, **kwargs): pass
    def runtime_binding(self): return {"revision": "fixture-skills"}
''',
        "src/scenarios/financial_qa/tools.py": '''
from types import SimpleNamespace
class FinanceDataQueryCcTools:
    def __init__(self, **kwargs):
        self.finance_catalog = SimpleNamespace(catalog_revision=lambda: "fixture-catalog")
''',
        "src/services/session_variable_store_service.py": '''
class SessionVariableStoreService:
    def __init__(self, **kwargs): pass
''',
        "src/services/application_runtime_service.py": '''
class ApplicationRuntimeService:
    def get_application_context(self, name):
        assert name == "investment_workbench"
        return {"application_name": name, "default_agent": {"runtime_profile": {"sections": ["fixture-soul"]}}}
''',
    }
    for name, content in files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    # An ambient src with the same name must never be imported by the child.
    ambient = tmp_path / "ambient"
    (ambient / "src").mkdir(parents=True)
    (ambient / "src/__init__.py").write_text('raise RuntimeError("active-src-imported")')
    config = tmp_path / "run-config.json"
    actual_environment = runner._package_metadata()
    expected_environment = {"python": actual_environment["python_version"],
        "platform": {"Darwin": "macOS"}.get(actual_environment["platform"]["system"], actual_environment["platform"]["system"]),
        "machine": actual_environment["platform"]["machine"], "claude_cli": actual_environment["claude_cli"]}
    config.write_text(json.dumps({
        "workspace": str(workspace), "run_id": "fixture", "check_only": check_only,
        "metadata": {"source_commit": "fixture-only-not-historical-run"},
        "cases": [{"case_id": "A", "question": "fixture query"}],
        "requirements_lock": "claude-agent-sdk==" + actual_environment["packages"]["claude-agent-sdk"],
        "expected_environment": expected_environment,
    }), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(Path(runner.__file__).resolve()), "--_worker", str(config)],
        cwd=ambient, env={**os.environ, "PYTHONPATH": str(ambient)}, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    metadata = json.loads((tmp_path / "metadata.json").read_text())
    assert metadata["catalog_revision"] == "fixture-catalog"
    assert all(Path(value).is_relative_to(workspace) for value in metadata["module_origins"].values())
    if check_only:
        assert not (tmp_path / "results.jsonl").exists()
        assert '"check_only": "passed"' in result.stdout
    else:
        record = json.loads((tmp_path / "results.jsonl").read_text())
        assert record["response"]["message"] == "frozen-fixture"
        assert record["response"]["llm_usage"]["total_tokens"] == 7

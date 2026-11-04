"""Offline safety and completeness checks for the frozen CC historical evidence."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "history").is_dir() and (ROOT / "tools").is_dir():
    _spec = importlib.util.spec_from_file_location(
        "sealed_cc_reference_history", ROOT / "tools/freeze_cc_reference_history.py")
    assert _spec is not None and _spec.loader is not None
    history_module = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = history_module
    _spec.loader.exec_module(history_module)
    HISTORY = ROOT / "history"
else:
    from scripts import freeze_cc_reference_history as history_module
    HISTORY = ROOT / "baselines/cc_20260902_rebuilt/history"

EXPECTED_IDS = history_module.EXPECTED_IDS
REDACTED = history_module.REDACTED
SOURCE = history_module.SOURCE
build_history = history_module.build_history
project_cases = history_module.project_cases
sanitize = history_module.sanitize


def test_redaction_preserves_business_and_usage_without_mutating_source():
    original = {
        "question": "2026年EPS预测？", "answer": "2.1元/股，暂无机构B数据。",
        "headers": {"authorization": "fake-credential"}, "identity_cookies": {"sid": "fake-cookie"},
        "config": {"DASHSCOPE_API_KEY": "fake-key", "password": 12345},
        "usage": {"prompt_tokens": 12, "cache_read_tokens": 20, "total_tokens": 35},
        "request": "r1 = stock.report_metric(filter=\"forecast_year == 2026\") -> metric_value",
        "sample": [{"metric_value": 2.1, "unit": "元/股"}],
        "times": [0, None, 1.234],
    }
    untouched = copy.deepcopy(original)
    redactions = []
    safe = sanitize(original, redactions=redactions)
    assert original == untouched
    for key in ("question", "answer", "usage", "request", "sample", "times"):
        assert safe[key] == original[key]
    assert safe["headers"] == safe["identity_cookies"] == REDACTED
    assert safe["config"] == {"DASHSCOPE_API_KEY": REDACTED, "password": REDACTED}
    assert all(set(record) == {"path", "reason"} for record in redactions)
    assert "fake-credential" not in json.dumps(safe)
    assert "fake-key" not in json.dumps(redactions)


@pytest.mark.parametrize("text", [
    "Authorization: Bearer " + "example-credential-1234567890",
    "API_KEY=example-secret-value",
    "https://example-user:example-password@example.invalid/api",
    "https://example.invalid/?access_token=example-secret-value",
    "sk-" + "a" * 32,
    "-----BEGIN PRIVATE KEY-----\nexample private content\n-----END PRIVATE KEY-----",
    '{"nested":{"headers":{"x-api-key":"example-secret-value"}}}',
])
def test_embedded_credentials_are_removed_and_only_paths_recorded(text):
    redactions = []
    safe = sanitize({"trace": text}, redactions=redactions)
    assert REDACTED in safe["trace"]
    assert redactions and all(record["path"].startswith('$["trace"]') for record in redactions)
    assert text not in json.dumps(redactions)


def test_clean_embedded_json_keeps_original_format_and_all_values():
    original = {'trace': '{  "rows" : [ {"code": "600519.SH", "value": 2.1} ] }'}
    redactions = []
    assert sanitize(original, redactions=redactions) == original
    assert redactions == []


def test_frozen_history_has_all_cases_full_answers_usage_trace_and_valid_hashes():
    audit = json.loads((HISTORY / "audit.json").read_text())
    results = json.loads((HISTORY / "results.json").read_text())
    cases = json.loads((HISTORY / "cases.json").read_text())
    assert tuple(case["case_id"] for case in results["cases"]) == EXPECTED_IDS
    assert results["summary"] == {"selected_cases": 20, "completed_cases": 20, "ok_cases": 20, "error_cases": 0}
    assert cases == project_cases(results)
    assert len({case["thread_id"] for case in results["cases"]}) == 20
    for name in ("results.json", "cases.json"):
        assert hashlib.sha256((HISTORY / name).read_bytes()).hexdigest() == audit[name + "_sha256"]
    for case in results["cases"]:
        assert case["question"] == case["source_case"]["question"]
        assert case["done_result"]["message"]
        assert case["done_result"]["research_mode"]["requested"] == "auto"
        assert case["done_result"]["model_name"] == "deepseek-v4-flash"
        assert case["financial_qa"]["runtime"] == "cc"
        assert "tool_calls" in case["financial_qa"] and "result_refs" in case["financial_qa"]
        assert case["llm_usage"] and case["total_elapsed_ms"] > 0
        assert case["sse_events"] and case["diagnostic_trace"]
    remaining = []
    assert sanitize(results, redactions=remaining) == results
    assert remaining == []


@pytest.mark.skipif(not (ROOT / SOURCE).exists(), reason="Original non-Git outputs are not present in this clone")
def test_frozen_result_tree_is_lossless_except_explicitly_audited_redactions():
    raw = (ROOT / SOURCE).read_bytes()
    original = json.loads(raw)
    saved = json.loads((HISTORY / "results.json").read_text())
    audit = json.loads((HISTORY / "audit.json").read_text())
    assert hashlib.sha256(raw).hexdigest() == audit["source_sha256"]
    redactions = []
    assert saved == sanitize(original, redactions=redactions)
    assert redactions == audit["redactions"]
    # This particular source contains no matching credential fields: exact full
    # JSON equality is stronger than checking a convenient summary projection.
    assert redactions == []
    assert saved == original


def test_generator_rejects_existing_history_without_overwrite(tmp_path):
    if not (ROOT / SOURCE).exists():
        pytest.skip("Original non-Git outputs absent")
    destination = tmp_path / "history"
    destination.mkdir()
    sentinel = destination / "sentinel"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError):
        build_history(ROOT, destination)
    assert sentinel.read_text() == "keep"

"""Canonical query naming, legacy input compatibility and unchanged dispatch.

No model, network or database is used. Providers are stubbed at the execution
boundary, so the checks compare actual routing and argument handling.
"""

import json

import pytest

from src.experiments.staged_data_protocol.phase2 import api_runner
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.catalog import catalog_source, operation_for_api_pattern, resolve_api
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService


QUERIES = [
    (subject, view, function["api_name"])
    for subject, views in catalog_source()["subjects"].items()
    for view, definition in views.items()
    if not view.startswith("_")
    for function in definition["api"]
    if operation_for_api_pattern(function["api_name"]) == "query"
]


@pytest.mark.parametrize("subject,view,api", QUERIES)
def test_every_detail_pack_and_example_uses_explicit_query(subject, view, api):
    assert api == f"{subject}.{view}.query"
    pack = FinanceDataToolCatalogService().get_model_dataview(subject, view, "query")
    fn = pack["functions"][0]
    assert fn["api_name"] == api
    assert api + "(" in fn["request_pattern"]
    assert pack["available_operations"]["query"] == api
    for example in fn.get("examples", []):
        assert parse_api_call(example).api == api
    # Old spelling is retained only by the resolver, not as another advertised
    # method or a negative instruction to the model.
    assert api.removesuffix(".query") + "(" not in json.dumps(pack, ensure_ascii=False)


@pytest.mark.parametrize("subject,view,api", QUERIES)
def test_explicit_and_legacy_query_validate_and_dispatch_identically(monkeypatch, subject, view, api):
    legacy = api.removesuffix(".query")
    assert resolve_api(api) == resolve_api(legacy)
    calls = []

    def provider(name):
        def execute(**kwargs):
            calls.append((name, kwargs))
            return {"status": "ok", "rows": [], "row_count": 0, "columns": kwargs["outputs"]}
        return execute

    for name in vars(api_runner):
        if name.startswith("execute_") and name != "execute_api_call":
            monkeypatch.setattr(api_runner, name, provider(name))

    field = next(iter(catalog_source()["subjects"][subject][view]["fields"]))
    results = []
    for entry in (legacy, api):
        call = parse_api_call(f"result = {entry}(limit=2) -> {field}")
        validation = validate_call(call, previous_results={})
        assert validation.ok, validation.errors
        result = api_runner.execute_api_call(call)
        assert result.api == entry
        results.append(result.data)
    assert len(calls) == 2  # Both spellings reach a real provider route.
    assert calls[0] == calls[1]
    assert results[0] == results[1]


@pytest.mark.parametrize("api", [
    "stock.base_info", "stock.qoute", "stock.history_quote",
    "stock.realtime_minute_qoute", "stock.segment", "stock.holders",
])
def test_existing_view_aliases_keep_their_mode_and_route(api):
    assert resolve_api(api) is not None
    assert resolve_api(api) == resolve_api(api + ".query")


@pytest.mark.parametrize("api", [
    "stock.basic_info.query.query", "stock.missing.query", "missing.report.query",
])
def test_unresolvable_invocations_remain_structural_errors(api):
    validation = validate_call(parse_api_call(f"result = {api}(limit=2) -> code"), {})
    assert not validation.ok
    assert any("API_ERROR" in error for error in validation.errors)


def test_source_metric_paths_and_other_method_families_are_unchanged():
    assert resolve_api("stock.quote.kd_close_max")["type"] == "kd"
    assert resolve_api("stock.report.agg")["type"] == "agg"
    assert resolve_api("stock.quote.dynamic_cal")["type"] == "dynamic_cal"
    assert resolve_api("industry.constitution")["operation"] == "query"
    call = parse_api_call(
        'result = industry.constitution.agg(agg=avg(stock.quote.close), '
        'group_by="industry_code, industry_name") -> industry_code, industry_name, avg_close'
    )
    assert validate_call(call, {}).ok

import json
import os
import re
from pathlib import Path
from typing import Any, Dict

import pytest

from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.experiments.staged_data_protocol.phase2.catalog import (
    FinanceCatalogContractError,
    catalog_source,
    load_catalog_source,
    resolve_api,
    concrete_call_pattern,
    OPERATION_TYPES,
)
from src.experiments.staged_data_protocol.phrase1_stage import (
    build_subject_dataview_context,
)


def test_filter_syntax_has_one_source_and_loads_only_with_execution_catalog():
    service = FinanceDataToolCatalogService()
    syntax = catalog_source()["filter_syntax"]
    for view in ("report", "report_metric"):
        for operation in ("query", "aggregate"):
            model = service.get_model_dataview("stock", view, operation=operation)
            assert model["filter_syntax"] == syntax
            assert json.dumps(model, ensure_ascii=False).count(syntax) == 1
    assert "filter_syntax" not in json.dumps(service.get_subject("stock"), ensure_ascii=False)


def test_model_catalog_uses_one_fuzzy_match_without_prefix_suffix_guidance():
    service = FinanceDataToolCatalogService()
    for subject, views in catalog_source()['subjects'].items():
        for view in views:
            if view.startswith('_'):
                continue
            model = service.get_model_dataview(subject, view)
            text = json.dumps(model, ensure_ascii=False)
            assert 'startswith' not in text and 'endswith' not in text
    syntax = catalog_source()['filter_syntax']
    assert "'文本' in field" in syntax
    assert 'like' not in syntax
    finance = service.get_model_dataview('stock', 'financial_3_table', operation='query')
    text = json.dumps(finance, ensure_ascii=False)
    assert '年度数据筛选每年12月31日' not in text
    assert '年度数据用各年的12月31日' not in text


def test_legacy_step_context_loads_same_filter_syntax():
    from src.experiments.staged_data_protocol.phase2.context_builder import build_context_sections
    from src.experiments.staged_data_protocol.phase2.models import Step
    sections = build_context_sections(
        step=Step(step_id="S1", subject="stock", dataview="report", condition_desc="评级", raw="研报"),
        previous_results={},
    )
    assert catalog_source()["filter_syntax"] in sections["current_dataview"]


def test_report_metric_detail_guidance_stays_with_detail_operation():
    service = FinanceDataToolCatalogService()
    detail = service.get_model_dataview("stock", "report_metric", operation="query")
    aggregate = service.get_model_dataview("stock", "report_metric", operation="aggregate")

    assert [item["api_name"] for item in detail["functions"]] == ["stock.report_metric"]
    assert [item["api_name"] for item in aggregate["functions"]] == ["stock.report_metric.agg"]
    assert "原始" in detail["functions"][0]["api_function"]
    assert "明细对比" not in json.dumps(aggregate["functions"], ensure_ascii=False)
    assert "仍使用明细查询" not in json.dumps(aggregate["functions"], ensure_ascii=False)
    assert aggregate["available_operations"]["query"] == "stock.report_metric"


def _catalog_payload(*, desc: str = "行情视图") -> Dict[str, Any]:
    return {
        "version": "test-v1",
        "api_class_patterns": {
            "shared_query": {
                "desc": "共享查询合约",
                "call_pattern": "r{id} = {api_name}(filter) -> fields",
                "output_rule": "输出字段必须来自当前视图",
                "rules": ["只能使用目录字段"],
                "examples": ["r1 = stock.quote(filter = code = 1) -> code, close"],
                "methods": ["sum", "mean"],
                "args": {"required": [], "optional": ["filter"]},
            }
        },
        "subjects": {
            "stock": {
                "_meta": {"desc": "股票", "rules": ["股票规则"]},
                "quote": {
                    "desc": desc,
                    "rules": ["视图规则"],
                    "examples": ["视图示例"],
                    "fields": {
                        "code": {"aliases": ["证券代码"], "desc": "代码"},
                        "close": {"aliases": [], "desc": "收盘价"},
                        "volume": {"aliases": [], "desc": ""},
                    },
                    "api": [
                        {
                            "api_name": "stock.quote",
                            "api_function": "查询行情",
                            "api_class": "shared_query",
                        },
                        {
                            "api_name": "stock.quote.agg",
                            "api_function": "聚合行情",
                            "api_class": "shared_query",
                        },
                    ],
                    "kd": {"close": ["sum", "mean"]},
                    "computed": {"return": "close / preclose - 1"},
                    "aggregate_fields": {"code": ["count"]},
                    "value_domains": {"market": ["SH", "SZ"]},
                },
            }
        },
    }


def _write_catalog(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("argument, expected", [("filter", True), ("prefilter", False)])
def test_filter_syntax_requires_exact_declared_argument(tmp_path, argument, expected):
    payload = _catalog_payload()
    payload["filter_syntax"] = "catalog-owned syntax"
    payload["api_class_patterns"]["shared_query"]["args"]["optional"] = [argument]
    path = tmp_path / "catalog.json"
    _write_catalog(path, payload)
    model = FinanceDataToolCatalogService(catalog_path=str(path)).get_model_dataview("stock", "quote")
    assert ("filter_syntax" in model) is expected


def _without_empty_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: normalized
            for key, item in value.items()
            if (normalized := _without_empty_values(item)) not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [
            normalized
            for item in value
            if (normalized := _without_empty_values(item)) not in (None, "", [], {})
        ]
    return value


def test_catalog_snapshot_reuses_compiled_tree_and_safely_invalidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = tmp_path / "catalog.json"
    _write_catalog(catalog_path, _catalog_payload())
    service = FinanceDataToolCatalogService(catalog_path=str(catalog_path))

    build_calls = 0
    original_build = service._build_tree_from_catalog

    def counted_build(payload: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal build_calls
        build_calls += 1
        return original_build(payload)

    monkeypatch.setattr(service, "_build_tree_from_catalog", counted_build)

    first_tree = service.build_tree()
    assert service.get_subject("stock")["name"] == "stock"
    assert service.get_dataview("stock", "quote")["desc"] == "行情视图"
    assert service.get_model_dataview("stock", "quote")["desc"] == "行情视图"
    assert service.load_raw_catalog()["version"] == "test-v1"
    assert build_calls == 1

    # Public values are copies; mutating one result cannot poison the snapshot.
    first_tree["subjects"][0]["dataviews"][0]["desc"] = "mutated"
    assert service.get_dataview("stock", "quote")["desc"] == "行情视图"

    # A metadata-only timestamp change rechecks the content but reuses the tree.
    state = catalog_path.stat()
    os.utime(
        catalog_path,
        ns=(state.st_atime_ns, state.st_mtime_ns + 1_000_000),
    )
    assert service.build_tree()["version"] == "test-v1"
    assert build_calls == 1

    # An atomic replacement is detected even when its mtime is forced back.
    current_state = catalog_path.stat()
    replacement = catalog_path.with_suffix(".replacement")
    _write_catalog(replacement, _catalog_payload(desc="更新视图"))
    replacement.replace(catalog_path)
    os.utime(
        catalog_path,
        ns=(current_state.st_atime_ns, current_state.st_mtime_ns),
    )
    assert service.get_dataview("stock", "quote")["desc"] == "更新视图"
    assert build_calls == 2


def test_catalog_snapshot_preserves_legacy_tree_subject_and_dataview_shapes(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.json"
    _write_catalog(catalog_path, _catalog_payload())
    service = FinanceDataToolCatalogService(catalog_path=str(catalog_path))

    tree = service.build_tree()
    subject = service.get_subject("stock")
    dataview = service.get_dataview("stock", "quote")

    assert subject == tree["subjects"][0]
    assert dataview == subject["dataviews"][0]
    assert dataview["field_count"] == 3
    assert dataview["function_count"] == 2
    assert dataview["fields"] == [
        {"name": "code", "aliases": ["证券代码"], "desc": "代码"},
        {"name": "close", "aliases": [], "desc": "收盘价"},
        {"name": "volume", "aliases": [], "desc": ""},
    ]
    assert dataview["functions"][0]["methods"] == ["sum", "mean"]
    assert dataview["functions"][0]["args"] == {
        "required": [],
        "optional": ["filter"],
    }


def test_model_dataview_assembles_complete_contracts_without_losing_semantics(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.json"
    _write_catalog(catalog_path, _catalog_payload())
    service = FinanceDataToolCatalogService(catalog_path=str(catalog_path))

    full = service.get_dataview("stock", "quote")
    model = service.get_model_dataview("stock", "quote")

    assert model["name"] == full["name"]
    assert model["desc"] == full["desc"]
    assert model["rules"] == full["rules"]
    assert model["examples"] == full["examples"]
    assert set(model["fields"]) == {item["name"] for item in full["fields"]}
    assert model["fields"] == {
        "code": {"aliases": ["证券代码"], "desc": "代码"},
        "close": {"desc": "收盘价"},
        "volume": {},
    }
    for field in full["fields"]:
        model_field = model["fields"][field["name"]]
        assert model_field.get("aliases", []) == field["aliases"]
        assert model_field.get("desc", "") == field["desc"]
    assert [function["operation"] for function in model["functions"]] == ["query", "aggregate"]
    assert "api_classes" not in model
    for actual, source in zip(model["functions"], full["functions"]):
        assert actual["api_name"] == source["api_name"]
        assert actual["api_function"] == source["api_function"]
        assert "api_class" not in actual
        assert actual["request_pattern"] == f"r{{id}} = {actual['api_name']}(filter=...) -> fields"
        assert actual["args"] == {"optional": ["filter"]}
        assert actual["methods"] == ["sum", "mean"]
        assert actual["rules"] == ["只能使用目录字段"]
        assert actual["output_rule"] == "输出字段必须来自当前视图"
    assert model["functions"][0]["examples"] == ["r1 = stock.quote(filter = code = 1) -> code, close"]
    for key in ("kd", "computed", "aggregate_fields", "value_domains"):
        assert model[key] == full[key]
    assert "field_count" not in model
    assert "function_count" not in model

    full_size = len(json.dumps(full, ensure_ascii=False, separators=(",", ":")))
    model_size = len(json.dumps(model, ensure_ascii=False, separators=(",", ":")))
    assert model_size < full_size


@pytest.mark.parametrize(
    ("dataview", "minimum_reduction"),
    [("quote", 0.03), ("financial_3_table", 0.10)],
)
def test_model_dataview_is_smaller_for_representative_real_views(
    dataview: str,
    minimum_reduction: float,
) -> None:
    service = FinanceDataToolCatalogService()

    full = service.get_dataview("stock", dataview)
    model = service.get_model_dataview("stock", dataview)

    assert set(model["fields"]) == {field["name"] for field in full["fields"]}
    assert model.get("rules", []) == full["rules"]
    assert model.get("examples", []) == full["examples"]
    assert "api_classes" not in model
    for function, contract in zip(full["functions"], model["functions"]):
        assert contract["api_name"] == function["api_name"]
        assert contract["operation"] == function["operation"]
        assert contract["request_pattern"] == concrete_call_pattern(function["api_name"], function["request_pattern"])
        if contract["operation"] == "window":
            assert "methods" not in contract
            assert model["kd"] == full["kd"]
        else:
            assert contract.get("methods", []) == function["methods"]
        assert contract.get("args", {}) == _without_empty_values(function["args"])
        assert contract.get("rules", []) == function["rules"]
        assert contract.get("output_rule", "") == function["output_rule"]
        assert model["functions"][full["functions"].index(function)].get(
            "examples", []
        ) == [e.split("\nnote:", 1)[0].strip() for e in function["examples"]]
    for key in ("kd", "computed", "aggregate_fields", "value_domains"):
        assert model.get(key, {}) == full[key]
    # Compare the same information: the historical editor/tree shape predates
    # the catalog-owned syntax now required in executable model packs.
    full_size = len(json.dumps({**full, "filter_syntax": model.get("filter_syntax")}, ensure_ascii=False))
    model_size = len(json.dumps(model, ensure_ascii=False))
    assert model_size <= full_size * (1 - minimum_reduction)


def test_production_catalog_is_the_runtime_source_and_examples_are_operation_exact() -> None:
    service = FinanceDataToolCatalogService()
    raw = service.load_raw_catalog()

    assert raw == load_catalog_source()
    financial_view = raw["subjects"]["stock"]["financial_3_table"]
    assert set(financial_view["value_domains"]["statement_type"]) == {
        "HB",
        "HBTZ",
        "HBDJ",
        "HBTZDJ",
        "MGS",
        "MGSTZ",
        "MGSDJ",
        "MGSTZDJ",
    }
    financial_query = service.get_model_dataview(
        "stock", "financial_3_table", "query"
    )
    assert financial_query["value_domains"] == financial_view["value_domains"]
    assert "累计/单季" in financial_query["fields"]["statement_type"]["desc"]
    assert any(
        "report_period in ['2022-12-31'" in example
        for example in financial_query["functions"][0]["examples"]
    )
    assert "news" not in raw["subjects"]["stock"]
    assert resolve_api("stock.news") is None
    assert service.get_public_data_source(api="stock.report") == {
        "data_object": "股票",
        "data_type": "研报明细",
        "label": "股票 · 研报明细",
        "description": raw["subjects"]["stock"]["report"]["desc"],
    }
    assert service.get_public_data_source(
        subject="industry",
        dataview="constitution",
    )["label"] == "行业 · 成分股"
    operation_count = 0
    for subject, subject_cfg in raw["subjects"].items():
        for dataview, view in subject_cfg.items():
            if dataview.startswith("_") or not isinstance(view, dict):
                continue
            runtime_view_name = "base_info" if dataview == "basic_info" else dataview
            functions_by_operation: dict[str, list[dict[str, Any]]] = {}
            for function in view["api"]:
                api_name = function["api_name"]
                operation = service._operation_for_api_name(api_name)
                functions_by_operation.setdefault(operation, []).append(function)
                assert function.get("examples")
                pattern = re.escape(api_name)
                pattern = pattern.replace(re.escape("<field>"), r"[A-Za-z_]\w*")
                pattern = pattern.replace(re.escape("<method>"), r"[A-Za-z_]\w*")
                for example in function["examples"]:
                    match = re.search(
                        r"=\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*\(",
                        example.split("\nnote:", 1)[0],
                    )
                    assert match is not None
                    assert re.fullmatch(pattern, match.group(1))
                    resolved = resolve_api(match.group(1))
                    assert resolved is not None
                    assert resolved["operation"] == operation
                    assert resolved["dataview"] == runtime_view_name

            for operation, functions in functions_by_operation.items():
                operation_count += 1
                assert len(functions) == 1
                projection = service.get_model_dataview(
                    subject,
                    dataview,
                    operation,
                )
                assert projection["selected_operation"] == operation
                assert [item["api_name"] for item in projection["functions"]] == [
                    functions[0]["api_name"]
                ]
                contract = projection["functions"][0]
                source_class = raw["api_class_patterns"][functions[0]["api_class"]]
                assert "api_classes" not in projection and "api_class" not in contract
                assert contract["request_pattern"] == concrete_call_pattern(contract["api_name"], source_class["call_pattern"])
                assert contract.get("args", {}) == _without_empty_values(source_class["args"])
                assert projection["functions"][0]["examples"] == [
                    e.split("\nnote:", 1)[0].strip() for e in functions[0]["examples"]
                ]
                guidance = projection["functions"][0].get("guidance", [])
                assert guidance[:len(functions[0].get("guidance", []))] == functions[0].get("guidance", [])
                for index, example in enumerate(functions[0]["examples"], 1):
                    if "\nnote:" in example:
                        assert f"示例 {index}：{example.split(chr(10) + 'note:', 1)[1].strip()}" in guidance
                assert "examples" not in projection
                if operation != "window":
                    assert "kd" not in projection
                if operation != "aggregate":
                    assert "aggregate_fields" not in projection
                if operation not in {"query", "compute"}:
                    assert "computed" not in projection

    assert operation_count == 47


def test_operation_projection_does_not_mix_sibling_execution_guidance() -> None:
    service = FinanceDataToolCatalogService()

    margin_query = service.get_model_dataview("stock", "margin", "query")
    margin_window = service.get_model_dataview("stock", "margin", "window")
    report_query = service.get_model_dataview("stock", "report", "query")
    report_aggregate = service.get_model_dataview(
        "stock", "report", "aggregate"
    )
    metric_query = service.get_model_dataview(
        "stock", "report_metric", "query"
    )
    metric_aggregate = service.get_model_dataview(
        "stock", "report_metric", "aggregate"
    )

    assert "历史区间" in "".join(margin_query["functions"][0]["rules"])
    assert "K 日窗口" not in json.dumps(margin_query, ensure_ascii=False)
    assert "K 日窗口" in margin_window["functions"][0]["api_function"]
    assert "历史明细" not in json.dumps(margin_window, ensure_ascii=False)
    assert report_query["functions"][0]["api_name"] == "stock.report"
    assert report_aggregate["functions"][0]["api_name"] == "stock.report.agg"
    assert "聚合 metric_value" not in json.dumps(metric_query, ensure_ascii=False)
    assert "聚合 metric_value" in "".join(
        metric_aggregate["functions"][0]["rules"]
    )
    for operation in ("window", "aggregate", "compute"):
        quote = service.get_model_dataview("stock", "quote", operation)
        serialized = json.dumps(quote, ensure_ascii=False)
        assert "count表示每只股票返回的最近根数" not in serialized
        assert "period=1/3/5/10/15/30/60与count=根数" not in serialized


def test_selected_dataview_pack_preserves_declared_subject_guidance(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    _write_catalog(catalog_path, _catalog_payload())
    model = FinanceDataToolCatalogService(catalog_path=str(catalog_path)).get_model_dataview("stock", "quote", "query")
    assert model["subject_guidance"] == ["股票规则"]


def test_runtime_catalog_source_is_recursively_immutable() -> None:
    source = catalog_source()

    with pytest.raises(TypeError):
        source["version"] = "mutated"
    with pytest.raises(TypeError):
        source["subjects"]["stock"]["quote"]["fields"]["close"] = []
    with pytest.raises(AttributeError):
        source["subjects"]["stock"]["quote"]["api"].append({})


@pytest.mark.parametrize(
    "invalid_api_name",
    ["stock.other.agg", "stock.quote.alternate"],
)
def test_catalog_service_rejects_misplaced_or_duplicate_operation(
    tmp_path: Path,
    invalid_api_name: str,
) -> None:
    payload = _catalog_payload()
    payload["subjects"]["stock"]["quote"]["api"][1][
        "api_name"
    ] = invalid_api_name
    catalog_path = tmp_path / "catalog.json"
    _write_catalog(catalog_path, payload)

    with pytest.raises(FinanceCatalogContractError):
        FinanceDataToolCatalogService(
            catalog_path=str(catalog_path)
        ).build_tree()


def test_production_descriptions_are_the_single_route_source() -> None:
    tree = FinanceDataToolCatalogService().build_tree()
    raw = FinanceDataToolCatalogService().load_raw_catalog()
    forbidden = (
        "mode=",
        "period=",
        "count=",
        "filter=",
        "->",
        "api_class",
        "examples",
    )
    for subject in tree["subjects"]:
        for dataview in subject["dataviews"]:
            description = dataview["desc"]
            assert description
            assert dataview["route_summary"] == description
            assert not any(token in description for token in forbidden)
            assert "route_summary" not in raw["subjects"][subject["name"]][dataview["name"]]


def test_legacy_stage1_routing_uses_the_complete_description(
    tmp_path: Path,
) -> None:
    payload = _catalog_payload()
    quote = payload["subjects"]["stock"]["quote"]
    quote["desc"] = "股票行情数据；覆盖历史、分钟与当前行情。"
    quote["route_summary"] = "不应使用的旧摘要"
    catalog_path = tmp_path / "catalog.json"
    _write_catalog(catalog_path, payload)

    routing = build_subject_dataview_context(catalog_path=catalog_path)

    assert "quote: 股票行情数据；覆盖历史、分钟与当前行情。" in routing
    assert "不应使用的旧摘要" not in routing


def test_financial_period_and_statement_granularity_are_defined() -> None:
    view = FinanceDataToolCatalogService().get_model_dataview(
        "stock", "financial_3_table", "query"
    )

    rules = "\n".join(view["rules"])
    assert "statement_type=HB" in rules and "年初至报告期累计" in rules
    assert "累计口径" in view["value_domains"]["statement_type"]["HB"]
    assert "单季口径" in view["value_domains"]["statement_type"]["HBDJ"]


@pytest.mark.parametrize("subject", ["index", "industry", "plate"])
def test_constitution_is_distinct_with_legacy_query_compatibility(subject: str) -> None:
    service = FinanceDataToolCatalogService()
    model = service.get_model_dataview(subject, "constitution", "constitution")
    assert model == service.get_model_dataview(subject, "constitution", "query")
    assert model["selected_operation"] == "constitution"
    assert model["available_operations"] == {"constitution": f"{subject}.constitution", "aggregate": f"{subject}.constitution.agg"}
    assert model["functions"][0]["api_name"] == f"{subject}.constitution"
    assert f"{subject}_code" in model["functions"][0]["request_pattern"]
    assert "{subject_code_field}" not in json.dumps(model)
    resolved = resolve_api(f"{subject}.constitution")
    assert resolved["type"] == "base" and resolved["operation"] == "constitution"
    assert resolve_api(f"{subject}.constitution.agg")["operation"] == "aggregate"


def test_invalid_operation_still_rejected_and_quote_modes_match_execution() -> None:
    from src.experiments.staged_data_protocol.phase2.call_structure import stock_quote_provider_fields
    from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogError

    assert OPERATION_TYPES == {"query", "constitution", "aggregate", "window", "compute"}
    service = FinanceDataToolCatalogService()
    for operation in ("constitution", "missing"):
        with pytest.raises(FinanceDataToolCatalogError):
            service.get_model_dataview("stock", "quote", operation)
    quote = service.get_model_dataview("stock", "quote", "query")
    for mode in (0, 1, 2):
        declared = {name for name, field in quote["fields"].items() if mode in field["modes"]}
        assert declared == set(quote["fields"]) & stock_quote_provider_fields(mode=mode)
    window = service.get_model_dataview("stock", "quote", "window")
    assert all("modes" not in field for field in window["fields"].values())


def test_quote_examples_execute_with_the_agent_tool_argument_contract() -> None:
    import ast
    from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
    from src.experiments.staged_data_protocol.phase2.call_structure import validate_call_structure

    view = FinanceDataToolCatalogService().get_model_dataview("stock", "quote", "query")
    for example in view["functions"][0]["examples"]:
        call = parse_api_call(example)
        assert validate_call_structure(call, previous_results={}) == []
        # Agent flows pass requests and previous result refs, not SDK bindings.
        if "codes" in call.args:
            assert isinstance(ast.literal_eval(call.args["codes"]), list)


def test_coding_pack_preserves_operation_and_field_domains(tmp_path: Path) -> None:
    from src.services.custom_tool_context_bundle_service import CustomToolContextBundleService

    service = CustomToolContextBundleService(root_dir=str(tmp_path))
    raw = load_catalog_source()
    for subject, name in [("stock", "report_metric"), ("plate", "constitution")]:
        source = raw["subjects"][subject][name]
        pack = service._compact_dataview(subject=subject, dataview=name, definition=source, patterns=raw["api_class_patterns"])
        assert pack["filter_syntax"] == raw["filter_syntax"]
        for key in ("aggregate_fields", "value_domains"):
            assert pack.get(key, {}) == source.get(key, {})
        for method in pack["methods"]:
            assert method["operation"] == resolve_api(method["name"])["operation"]
            assert "{api_name}" not in method["call"]
            assert "filter=..." in method["call"]

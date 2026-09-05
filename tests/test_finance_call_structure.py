from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_structure import (
    structure_call,
    validate_call_structure,
)
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.models import ResultHandle
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService


def test_existing_api_string_is_compiled_without_rewriting_it() -> None:
    request = (
        'r1 = stock.moneyflow(filter = "large_net > 0 and main_ratio > 5", '
        'order = "main_ratio desc", limit = 50, realtime = 1) '
        '-> code, name, tradedate, large_net, main_ratio, main_net'
    )
    call = parse_api_call(request)

    structured = structure_call(call)

    assert call.raw == request
    assert structured == {
        "result_id": "r1",
        "api": "stock.moneyflow",
        "api_class": "basic_query",
        "subject": "stock",
        "dataview": "moneyflow",
        "arguments": {"limit": 50, "realtime": 1},
        "filter": {
            "raw": "large_net > 0 and main_ratio > 5",
            "expression": {
                "and": [
                    {"field": "large_net", "operator": ">", "value": 0},
                    {"field": "main_ratio", "operator": ">", "value": 5},
                ]
            },
        },
        "order_by": [{"field": "main_ratio", "direction": "desc"}],
        "outputs": [
            {"expression": "code", "field": "code", "alias": ""},
            {"expression": "name", "field": "name", "alias": ""},
            {"expression": "tradedate", "field": "tradedate", "alias": ""},
            {"expression": "large_net", "field": "large_net", "alias": ""},
            {"expression": "main_ratio", "field": "main_ratio", "alias": ""},
            {"expression": "main_net", "field": "main_net", "alias": ""},
        ],
    }
    json.dumps(structured, ensure_ascii=False)


def test_structured_filter_preserves_boolean_groups_lists_and_result_refs() -> None:
    call = parse_api_call(
        'r2 = stock.quote(filter = "code in r1.code and '
        '(pct > 5 or amount > 500000000)", order = "pct desc", mode = 0) '
        '-> code, name, pct, amount'
    )

    structured = structure_call(call)

    assert structured["filter"]["expression"] == {
        "and": [
            {
                "field": "code",
                "operator": "in",
                "value": {"result": "r1", "field": "code"},
            },
            {
                "or": [
                    {"field": "pct", "operator": ">", "value": 5},
                    {"field": "amount", "operator": ">", "value": 500000000},
                ]
            },
        ]
    }


def test_static_check_uses_dataview_fields_without_judging_business_intent() -> None:
    unusual_but_structural = parse_api_call(
        'r1 = stock.financial_3_table(filter = "ann_date = 2026-04-30", '
        'order = "ann_date desc", limit = 20) -> code, name, ann_date, revenue'
    )
    balance_sum = parse_api_call(
        'r2 = stock.margin.kd_financing_balance_sum(k = 5, '
        'filter = "code = 600519.SH", realtime = 0) '
        '-> code, name, value as financing_balance_5d_sum'
    )

    assert validate_call_structure(unusual_but_structural) == []
    assert validate_call_structure(balance_sum) == []


@pytest.mark.parametrize("filter_text", ["all", "1=1", "1 = 1"])
def test_static_check_accepts_existing_noop_filter_forms(filter_text: str) -> None:
    call = parse_api_call(
        f'r1 = plate.basic_info(filter = "{filter_text}", limit = 20) '
        '-> plate_code, plate_name'
    )

    assert validate_call_structure(call) == []


@pytest.mark.parametrize(
    ("request_text", "error_prefix"),
    [
        (
            'r1 = stock.pricevalue(filter = "imaginary_metric > 10") '
            '-> code, name, pe',
            "FILTER_ERROR: field=imaginary_metric",
        ),
        (
            'r1 = stock.pricevalue(order = "imaginary_metric desc") '
            '-> code, name, pe',
            "ORDER_ERROR: field=imaginary_metric",
        ),
        (
            'r1 = stock.pricevalue(filtre = "pe > 10") -> code, name, pe',
            "ARG_ERROR: argument=filtre",
        ),
    ],
)
def test_static_check_rejects_only_mechanical_contract_errors(
    request_text: str,
    error_prefix: str,
) -> None:
    errors = validate_call_structure(parse_api_call(request_text))

    assert any(item.startswith(error_prefix) for item in errors)


def test_runtime_plate_filter_alias_is_canonicalized_for_compatibility() -> None:
    call = parse_api_call(
        'r2 = plate.quote(filter = "plate_code in r1.plate_code", '
        'order = "tradedate desc", limit = 10, realtime = 0) '
        '-> code, name, tradedate, close, pct, amount'
    )

    structured = structure_call(call)

    assert structured["filter"]["expression"]["field"] == "code"
    assert validate_call_structure(call) == []


def test_static_check_uses_previous_result_schema_for_references() -> None:
    previous = {
        "r1": ResultHandle(
            name="r1",
            api="stock.basic_info",
            columns=["code", "industry"],
            data={"status": "ok", "rows": []},
        )
    }
    call = parse_api_call(
        'r2 = industry.constitution(filter = "industry_name in r1.industry", '
        'order = "stock_code asc", limit = -1) '
        '-> industry_code, industry_name, stock_code, stock_name'
    )

    assert validate_call(call, previous).ok is True


def test_runtime_returns_structured_call_and_passes_original_args_to_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.finance_data_tool_runtime_service as runtime_module

    captured = {}

    def fake_execute(call, previous_results):
        captured["raw"] = call.raw
        captured["args"] = dict(call.args)
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=["code", "name", "pb"],
            data={"status": "ok", "rows": [], "row_count": 0},
        )

    monkeypatch.setattr(runtime_module, "execute_api_call", fake_execute)
    request = (
        'r1 = stock.pricevalue(filter = "pb > 1", order = "pb asc", limit = 20) '
        '-> code, name, pb'
    )

    result = FinanceDataToolRuntimeService().execute_request(request=request)

    assert result["validation"]["ok"] is True
    assert result["structured_call"]["filter"]["expression"] == {
        "field": "pb",
        "operator": ">",
        "value": 1,
    }
    assert captured == {
        "raw": request,
        "args": {"filter": "pb > 1", "order": "pb asc", "limit": 20},
    }


def test_runtime_blocks_provider_ignored_filter_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.finance_data_tool_runtime_service as runtime_module

    def must_not_execute(*_args, **_kwargs):
        raise AssertionError("Provider must not run after static validation fails")

    monkeypatch.setattr(runtime_module, "execute_api_call", must_not_execute)

    result = FinanceDataToolRuntimeService().execute_request(
        request=(
            'r1 = stock.quote(filter = "name like \'%测试股份999999%\'", '
            'mode = 0, limit = 5) -> code, name, tradedate, close'
        )
    )

    assert result["ok"] is False
    assert result["validation"]["ok"] is False
    assert result["result"] is None
    assert any(
        "operator=like is not supported" in error
        for error in result["validation"]["errors"]
    )


@pytest.mark.parametrize(
    "request_text",
    [
        (
            'r1 = stock.financial_3_table(filter = "revenue_yoy > 10", '
            'order = "revenue_yoy desc", limit = 20) '
            '-> code, name, report_date, revenue, revenue_yoy'
        ),
        'r1 = stock.basic_info(filter = "name like \'%茅台%\'") -> code, name',
        (
            'r1 = stock.quote(filter = "minute_index > 0", order = "bar_end_time desc", '
            'mode = 1, limit = 20) -> code, name, minute_index, bar_end_time, close'
        ),
        (
            'r1 = stock.quote.kd_pct_sum(k = 5, '
            'filter = "code = 600519.SH and value > 0", order = "value desc", mode = 0) '
            '-> code, name, value as pct_5d, window_count'
        ),
        (
            'r1 = stock.moneyflow.kd_main_net_sum(k = 5, '
            'filter = "name = 贵州茅台 and value > 0", order = "value desc") '
            '-> code, name, value as main_net_5d, window_count'
        ),
        (
            'r1 = stock.margin.kd_financing_balance_change(k = 5, '
            'filter = "current_value > 0", order = "window_count desc") '
            '-> code, name, start_value, current_value, value as balance_change, window_count'
        ),
        (
            'r1 = stock.pricevalue.kd_pe_percentile(k = 750, '
            'filter = "pe > 10 and current_value > 0", order = "current_value desc") '
            '-> code, name, pe, current_value, value as pe_percentile, window_count'
        ),
        (
            'r1 = stock.quote.kd_minute_amount_avg(k = 5, '
            'filter = "code = 600519.SH and minute_index = 120 and window_count >= 5", '
            'order = "end_date desc", mode = 1) '
            '-> code, name, minute_amount, k, end_date, value as minute_amount_avg'
        ),
    ],
)
def test_static_check_accepts_provider_consumed_contracts(request_text: str) -> None:
    assert validate_call(parse_api_call(request_text), {}).ok is True


@pytest.mark.parametrize(
    ("request_text", "error_fragment"),
    [
        (
            'r1 = stock.quote(filter = "name like \'%茅台%\'", mode = 0) '
            '-> code, name, close',
            "operator=like is not supported",
        ),
        (
            'r1 = stock.quote(filter = "code not in [600519.SH]") -> code, name, close',
            "operator=not in is not supported",
        ),
        (
            'r1 = stock.basic_info(order = "value desc") -> code, name',
            "ORDER_ERROR: field=value",
        ),
        (
            'r1 = stock.news(filter = "publish_time >= 2026-01-01") '
            '-> code, name, publish_time, title',
            "API_ERROR: unsupported api=stock.news",
        ),
        (
            'r1 = stock.quote(filter = "all") -> avg(close) as avg_close',
            "function expression=avg(close)",
        ),
        (
            'r1 = stock.quote(filter = "code in (select stock_code from '
            'industry.constitution where industry_code = 630701)") -> code, name, close',
            "not a SQL subquery",
        ),
        (
            'r1 = stock.quote(filter = "minute_index > 0", mode = 0) '
            '-> code, name, close',
            "FILTER_ERROR: field=minute_index",
        ),
        (
            'r1 = stock.quote(filter = "code = 600519.SH", mode = 1) '
            '-> code, name, adjclose',
            "OUTPUT_ERROR: field=adjclose",
        ),
        (
            'r1 = stock.quote.kd_pct_sum(k = 5, filter = "pct > 0", mode = 0) '
            '-> code, name, value as pct_5d',
            "FILTER_ERROR: field=pct",
        ),
        (
            'r1 = stock.quote.kd_pct_sum(k = 5, order = "window_count desc", mode = 0) '
            '-> code, name, value as pct_5d',
            "ORDER_ERROR: field=window_count",
        ),
        (
            'r1 = stock.moneyflow.kd_main_net_sum(k = 5, filter = "main_net > 0") '
            '-> code, name, value as main_net_5d',
            "FILTER_ERROR: field=main_net",
        ),
        (
            'r1 = stock.margin.kd_financing_balance_change(k = 5, '
            'filter = "window_count > 0") -> code, name, value as balance_change',
            "FILTER_ERROR: field=window_count",
        ),
        (
            'r1 = stock.pricevalue.kd_pe_percentile(k = 750, order = "pe desc") '
            '-> code, name, value as pe_percentile',
            "ORDER_ERROR: field=pe",
        ),
        (
            'r1 = stock.quote.kd_minute_amount_avg(k = 5, '
            'filter = "end_date = 2026-08-25", mode = 1) '
            '-> code, name, value as minute_amount_avg',
            "FILTER_ERROR: field=end_date",
        ),
        (
            'r1 = industry.constitution.agg(agg = avg(stock.quote.pct), '
            'group_by = "industry_code, industry_name") '
            '-> industry_code, avg_pct, extra_result',
            "exactly one aggregate result",
        ),
        (
            'r1 = industry.constitution.agg(agg = avg(stock.quote.pct), '
            'group_by = "industry_code, industry_name") -> industry_code, avg_pct',
            "missing group_by fields=['industry_name']",
        ),
        (
            'r1 = stock.quote.agg(agg = avg(stock.quote.pct), '
            'group_by = "turn_ratio", mode = 1) -> turn_ratio, avg_pct',
            "group_by field=turn_ratio",
        ),
        (
            'r1 = stock.base_info(filtre = "code = 600519.SH") -> code, name',
            "ARG_ERROR: argument=filtre",
        ),
    ],
)
def test_static_check_rejects_provider_ignored_shapes(
    request_text: str,
    error_fragment: str,
) -> None:
    validation = validate_call(parse_api_call(request_text), {})

    assert validation.ok is False
    assert any(error_fragment in item for item in validation.errors), validation.errors


def test_all_catalog_call_examples_remain_structurally_valid() -> None:
    catalog_path = (
        Path(__file__).parents[1]
        / "src"
        / "tools"
        / "finance_data"
        / "catalog"
        / "api_view_catalog.json"
    )
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    examples: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            example = value.get("example")
            if isinstance(example, str) and example.lstrip().startswith("r"):
                examples.append(example.splitlines()[0].strip())
            example_rows = value.get("examples")
            if isinstance(example_rows, list):
                examples.extend(
                    item.splitlines()[0].strip()
                    for item in example_rows
                    if isinstance(item, str) and item.lstrip().startswith("r")
                )
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(payload)
    failures: dict[str, list[str]] = {}
    for request in examples:
        call = parse_api_call(request)
        ref_columns: dict[str, set[str]] = {}
        for result_id, column in re.findall(r"\b(r\d+)\.([A-Za-z_]\w*)\b", request):
            if result_id != call.result_id:
                ref_columns.setdefault(result_id, set()).add(column)
        previous = {
            result_id: ResultHandle(
                name=result_id,
                api="catalog.synthetic",
                columns=sorted(columns | {"code", "stock_code"}),
                data={"status": "ok", "rows": []},
            )
            for result_id, columns in ref_columns.items()
        }
        validation = validate_call(call, previous)
        if not validation.ok:
            failures[request] = validation.errors

    assert len(examples) >= 100
    assert failures == {}

from copy import deepcopy

import pytest

from src.scenarios.financial_qa.result_view import select_result_page
from src.experiments.staged_data_protocol.phase2.models import ResultHandle


def table(rows):
    return {"data_type": "table", "rows": rows, "manifest": {"schema": {"columns": [{"name": "code"}, {"name": "value"}]}}}


def test_filter_and_order_full_result_before_paging_without_mutation():
    data = table([{"code": f"{n:06}", "value": n} for n in range(1000)])
    original = deepcopy(data)
    page = select_result_page(data, filter_text="value >= 900", order="value desc", offset=2, limit=3)
    assert [r["value"] for r in page["rows"]] == [997, 996, 995]
    assert page["page"] == {"offset": 2, "limit": 3, "returned": 3, "total": 100, "has_more": True}
    assert page["selection"]["source_row_count"] == 1000
    assert data == original


@pytest.mark.parametrize("expression", ["code.contains('债券')", "code = '债券基金'", "code == '债券基金'", "code in r3.code"])
def test_shared_filter_protocol_and_saved_reference_sets(expression):
    refs = {"r3": ResultHandle("r3", "fund.base_info.query", ["code"], {"rows": [{"code": "债券基金"}, {"code": "债券基金"}]})}
    result = select_result_page(table([{"code": "债券基金", "value": 3}, {"code": "股票基金", "value": 2}]), filter_text=expression, previous_results=refs)
    assert [r["code"] for r in result["rows"]] == ["债券基金"]


def test_nulls_last_and_stable_multicolumn_sort():
    rows = [{"code": "b", "value": 1}, {"code": "c", "value": None}, {"code": "a", "value": 1}]
    for direction in ("asc", "desc"):
        assert [r["code"] for r in select_result_page(table(rows), order=f"value {direction}, code asc")["rows"]] == ["a", "b", "c"]


@pytest.mark.parametrize("kwargs", [
    {"filter_text": "value.lower() == 'a'"},
    {"filter_text": "__import__('os').system('echo unsafe')"},
    {"filter_text": "missing > 0"},
    {"filter_text": "code in r99.code"},
    {"order": "missing desc"}, {"order": "value desc; drop table t"},
])
def test_invalid_expressions_are_rejected_even_for_empty_results(kwargs):
    with pytest.raises(ValueError):
        select_result_page(table([]), **kwargs)


def test_documents_are_not_silently_filtered_as_empty_tables():
    with pytest.raises(ValueError, match="table"):
        select_result_page({"data_type": "document", "text": "research"}, filter_text="value > 1")


def test_zero_matches_is_valid_and_not_a_missing_result():
    result = select_result_page(table([{"code": "a", "value": 1}]), filter_text="value > 100")
    assert result["rows"] == []
    assert result["page"]["total"] == 0
    assert result["selection"]["source_row_count"] == 1

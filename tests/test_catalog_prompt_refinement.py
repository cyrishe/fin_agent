"""Catalog disclosure regressions; keep execution semantics unchanged."""
import json

import pytest

from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService


def test_window_combinations_have_one_source_for_all_subjects():
    service = FinanceDataToolCatalogService()
    count = 0
    for subject in service.build_tree()["subjects"]:
        for view in subject["dataviews"]:
            if not any(fn["operation"] == "window" for fn in view["functions"]):
                continue
            pack = service.get_model_dataview(subject["name"], view["name"], "window")
            assert "methods" not in pack["functions"][0]
            for field, methods in pack["kd"].items():
                for method in methods:
                    api = pack["functions"][0]["api_name"].replace("<field>", field).replace("<method>", method)
                    resolved = resolve_api(api)
                    assert resolved and method in resolved["view"]["kd"][field]
                    count += 1
    assert count > 100
    assert service.get_model_dataview("stock", "pricevalue", "window")["kd"]["pe"] == ["percentile"]


def test_hot_event_relation_uses_real_entry_fields_and_row_limit():
    service = FinanceDataToolCatalogService()
    pack = service.get_model_dataview("hot_event", "member", "query")
    assert pack == service.get_model_dataview("hot_event", "member", "constitution")
    fn = pack["functions"][0]
    text = json.dumps(pack, ensure_ascii=False)
    assert fn["api_name"] == "hot_event.member.query"
    assert "hot_event_code" not in text and "hot_event_name" not in text
    assert "limit=-1" not in text
    assert "最多 500" in text
    assert resolve_api("hot_event.member")["type"] == "base"
    assert validate_call(parse_api_call(fn["examples"][0]), previous_results={}).ok
    assert resolve_api("hot_event.member.query") == resolve_api("hot_event.member")


@pytest.mark.parametrize("view", ["report", "report_metric"])
def test_detail_row_limit_is_not_given_to_aggregation(view):
    service = FinanceDataToolCatalogService()
    detail = service.get_model_dataview("stock", view, "query")
    aggregate = service.get_model_dataview("stock", view, "aggregate")
    assert "limit=-1" in "".join(detail["functions"][0]["guidance"])
    assert "limit=-1" not in json.dumps(aggregate, ensure_ascii=False)


def test_navigation_lists_available_operations_without_execution_details():
    tools = FinanceDataQueryCcTools()
    routing = tools._catalog_routing_index()
    assert "  - report [query/aggregate]：" in routing
    assert "  - member [query]：" in routing
    assert "  - quote [query/window/aggregate/compute]：" in routing
    assert "request_pattern" not in routing and "filter=" not in routing


def test_every_example_argument_is_documented_in_its_method():
    raw = FinanceDataToolCatalogService().load_raw_catalog()
    for views in raw["subjects"].values():
        for name, view in views.items():
            if name.startswith("_"):
                continue
            for fn in view["api"]:
                template = raw["api_class_patterns"][fn["api_class"]]
                documented = {arg.split("(", 1)[0] for group in template["args"].values() for arg in group}
                for example in fn.get("examples", []):
                    assert set(parse_api_call(example).args) <= documented, fn["api_name"]


@pytest.mark.parametrize("api_request, subject, view, operation, names", [
    ('r1 = stock.margin(start="2026-08-01", end="2026-08-31") -> code, financing_balance',
     "stock", "margin", "query", {"date", "start", "end"}),
    ('r1 = stock.quote.kd_pct_sum(k=5, as_of="2026-09-08") -> code, value',
     "stock", "quote", "window", {"as_of"}),
    ('r1 = plate.moneyflow.kd_main_net_sum(k=5, as_of="2026-09-08") -> code, value',
     "plate", "moneyflow", "window", {"as_of"}),
    ('r1 = stock.margin.kd_financing_balance_change(k=5, as_of="2026-09-08") -> code, value',
     "stock", "margin", "window", {"as_of"}),
])
def test_existing_date_parameters_are_visible_and_executable(api_request, subject, view, operation, names):
    pack = FinanceDataToolCatalogService().get_model_dataview(subject, view, operation)
    fn = pack["functions"][0]
    assert names <= {value.split("(", 1)[0] for value in fn["args"]["optional"]}
    assert all(f"{name}=" in fn["request_pattern"] for name in names)
    assert validate_call(parse_api_call(api_request), previous_results={}).ok

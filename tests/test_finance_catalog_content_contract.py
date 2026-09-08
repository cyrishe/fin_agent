"""Content contracts for the layered financial catalog; no model or data calls."""

from __future__ import annotations

import json
from pathlib import Path

from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.models import ResultHandle


CATALOG_PATH = Path(__file__).resolve().parents[1] / "src/tools/finance_data/catalog/api_view_catalog.json"


def _catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _views(catalog: dict):
    for subject, views in catalog["subjects"].items():
        for name, view in views.items():
            if isinstance(view, dict) and "api" in view:
                yield subject, name, view


def test_each_function_has_a_complete_reusable_call_definition():
    catalog = _catalog()
    patterns = catalog["api_class_patterns"]
    for _, _, view in _views(catalog):
        assert view["desc"]
        assert view["fields"]
        for function in view["api"]:
            definition = patterns[function["api_class"]]
            assert function["api_name"] and function["api_function"]
            assert definition["desc"] and definition["call_pattern"]
            assert definition["output_rule"]
            assert isinstance(definition["args"]["required"], list)
            assert isinstance(definition["args"]["optional"], list)


def test_examples_are_executable_requests_and_guidance_is_separate():
    for _, _, view in _views(_catalog()):
        for function in view["api"]:
            for example in function.get("examples", []):
                assert "\nnote:" not in example
                call = parse_api_call(example)
                assert call.result_id
                assert call.api
                assert call.outputs


def test_quote_modes_have_local_defaults_and_special_window_formulas():
    catalog = _catalog()
    definition = catalog["api_class_patterns"]["stock_quote_query"]
    optional = definition["args"]["optional"]
    assert "mode(0|1|2; default 0)" in optional
    assert "period(mode=1: 1|3|5|10|15|30|60; default 1)" in optional
    assert "count(mode=0: 1..5000 per code; mode=1: 1..1000 per code, default 240)" in optional
    modes = "\n".join(definition["rules"])
    assert "mode=0" in modes and "mode=1" in modes and "mode=2" in modes
    assert "is_finalized" in modes and "snapshot_time" in modes

    functions = catalog["subjects"]["stock"]["quote"]["api"]
    window = next(row for row in functions if "kd_<field>" in row["api_name"])
    formulas = "\n".join(window["guidance"])
    assert "pct.sum=(窗口末收盘价-窗口首收盘价)/窗口首收盘价*100" in formulas
    assert "amplitude.sum=(窗口最高价-窗口最低价)/窗口前一交易日收盘价*100" in formulas


def test_report_metric_scope_and_target_price_ownership_are_preserved():
    stock = _catalog()["subjects"]["stock"]
    assert stock["report_metric"]["value_domains"]["metric_code"] == {
        "eps": "每股收益",
        "np_parent": "归母净利润",
        "np_parent_growth": "归母净利润增长率",
        "pb": "市净率",
        "pe": "市盈率",
        "revenue": "营业收入",
        "revenue_growth": "营业收入增长率",
        "roe": "净资产收益率",
    }
    assert stock["report_metric"]["value_domains"]["value_type"] == {
        "forecast": "预测值", "actual": "实际值"
    }
    assert "target_price_lower" in stock["report"]["fields"]
    assert "target_price_upper" in stock["report"]["fields"]
    assert "target_price_lower" not in stock["report_metric"]["fields"]
    assert "target_price_upper" not in stock["report_metric"]["fields"]


def test_disclosure_sources_are_explicit_local_value_domains():
    stock = _catalog()["subjects"]["stock"]
    expected = {
        "shareholder": {"holderqty", "holdertop", "holdertop1", "holderfloat"},
        "pledge": {"pledgeratio", "pledgeinfo", "pledge_frozen"},
        "corporate_action": {"dividend", "add_issue", "rights_issue", "limit_share", "ipo"},
        "business_segment": {"salessegment", "top5opincome"},
    }
    for name, sources in expected.items():
        assert "source" in stock[name]["fields"]
        assert set(stock[name]["value_domains"]["source"]) == sources


def test_constituent_aggregate_namespaces_match_existing_execution_contract():
    rules = _catalog()["api_class_patterns"]["constituent_aggregate"]["rules"]
    assert any("agg 使用 method(metric)" in rule for rule in rules)
    assert any("filter 使用字段短名" in rule and "指标视图或上游结果列" in rule for rule in rules)

    direct = parse_api_call(
        'r1 = plate.constitution.agg('
        'filter="plate_name == \'新能源\' and pct > 0", '
        'agg=avg(stock.quote.amount), group_by="plate_code, plate_name") '
        '-> plate_code, plate_name, avg_amount'
    )
    assert validate_call(direct, previous_results={}).ok

    previous = ResultHandle(
        name="r1", api="stock.pricevalue", columns=["code", "pe"],
        data={"status": "ok", "rows": [{"code": "600519.SH", "pe": 20}]},
    )
    composed = parse_api_call(
        'r2 = industry.constitution.agg('
        'filter="industry_name == \'食品饮料\' and pe > 0", '
        'agg=avg(r1.pe), group_by="industry_code, industry_name") '
        '-> industry_code, industry_name, avg_pe'
    )
    assert validate_call(composed, previous_results={"r1": previous}).ok

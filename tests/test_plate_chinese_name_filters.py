from __future__ import annotations

import json
from pathlib import Path

import src.experiments.staged_data_protocol.phase2.base_info_provider as base_info_provider
from src.experiments.staged_data_protocol.phase2.base_info_provider import (
    BASE_INFO_SOURCES,
    _build_filter_clauses,
    execute_base_info_api,
)
from src.experiments.staged_data_protocol.phase2 import context_builder
from src.experiments.staged_data_protocol.phase2.models import Step
from src.experiments.staged_data_protocol.phase2.moneyflow_provider import (
    MONEYFLOW_SOURCES,
    _build_identity_where as build_moneyflow_identity_where,
)
from src.experiments.staged_data_protocol.phase2.quote_provider import (
    QUOTE_SOURCES,
    _build_identity_where as build_quote_identity_where,
)


def test_plate_basic_info_supports_parameterized_chinese_name_like() -> None:
    sql, params = _build_filter_clauses(
        source=BASE_INFO_SOURCES["plate"],
        args={"filter": "plate_name like '%光模块%'"},
    )

    assert sql == "b.plate_name LIKE %s"
    assert params == ["%光模块%"]


def test_plate_basic_info_normalizes_common_plate_suffix_for_exact_name() -> None:
    sql, params = _build_filter_clauses(
        source=BASE_INFO_SOURCES["plate"],
        args={"filter": "plate_name = 光模块板块"},
    )

    assert sql == "(b.plate_name = %s OR b.plate_name = %s)"
    assert params == ["光模块板块", "光模块"]


def test_plate_code_alias_is_accepted_by_downstream_quote_and_moneyflow() -> None:
    moneyflow_sql, moneyflow_params = build_moneyflow_identity_where(
        source=MONEYFLOW_SOURCES["plate"],
        args={"filter": "plate_code = 884003"},
    )
    quote_sql, quote_params = build_quote_identity_where(
        source=QUOTE_SOURCES["plate"],
        args={"filter": "plate_code = 884003"},
    )

    assert moneyflow_sql == "AND (q.plate_code = %s)"
    assert moneyflow_params == [884003]
    assert quote_sql == "AND (q.plate_code = %s)"
    assert quote_params == [884003]


def test_unparsed_basic_info_filter_fails_before_database_query() -> None:
    result = execute_base_info_api(
        subject="plate",
        args={"filter": "plate_name contains 光模块"},
        outputs=["plate_code", "plate_name"],
    )

    assert result["status"] == "invalid_filter"
    assert result["rows"] == []
    assert "filter was not parsed" in result["reason"]


def test_basic_info_noop_filters_compile_to_a_real_noop_clause() -> None:
    for filter_text in ("all", "none", "true", "*", "1=1", "1 = 1"):
        sql, params = _build_filter_clauses(
            source=BASE_INFO_SOURCES["plate"],
            args={"filter": filter_text},
        )

        assert sql == "1=1"
        assert params == []


class _FakeCursor:
    def __init__(self, result_sets):
        self.result_sets = list(result_sets)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.result_sets.pop(0)


class _FakeDb:
    def __init__(self, result_sets):
        self.cursor_instance = _FakeCursor(result_sets)
        self.conn = self

    def cursor(self, *_args, **_kwargs):
        return self.cursor_instance

    def close_db(self):
        return None


def test_unique_contains_candidate_is_resolved_inside_basic_info_tool(
    monkeypatch,
) -> None:
    fake_db = _FakeDb(
        [
            [],
            [{"plate_code": "884003", "plate_name": "光模块"}],
        ]
    )
    monkeypatch.setattr(base_info_provider, "StockInfoDbUtils", lambda **_kwargs: fake_db)

    result = execute_base_info_api(
        subject="plate",
        args={"filter": "plate_name = 光模", "limit": 5},
        outputs=["plate_code", "plate_name"],
    )

    assert result["rows"] == [{"plate_code": "884003", "plate_name": "光模块"}]
    assert result["name_resolution"] == {
        "status": "resolved",
        "method": "unique_contains",
        "requested": "光模",
        "resolved": {"plate_code": "884003", "plate_name": "光模块"},
    }
    assert "LOCATE" in fake_db.cursor_instance.calls[1][0]
    assert fake_db.cursor_instance.calls[1][1] == ("光模", 20)


def test_multiple_contains_candidates_remain_explicitly_ambiguous(
    monkeypatch,
) -> None:
    fake_db = _FakeDb(
        [
            [],
            [
                {"plate_code": "A", "plate_name": "光模块"},
                {"plate_code": "B", "plate_name": "高速光模块"},
            ],
        ]
    )
    monkeypatch.setattr(base_info_provider, "StockInfoDbUtils", lambda **_kwargs: fake_db)

    result = execute_base_info_api(
        subject="plate",
        args={"filter": "plate_name = 光"},
        outputs=["plate_code", "plate_name"],
    )

    assert result["rows"] == []
    assert result["name_resolution"]["status"] == "ambiguous"
    assert [
        item["plate_code"]
        for item in result["name_resolution"]["candidates"]
    ] == ["A", "B"]


def test_plate_catalog_teaches_name_resolution_before_data_query() -> None:
    catalog_path = (
        Path(__file__).parents[1]
        / "src"
        / "tools"
        / "finance_data"
        / "catalog"
        / "api_view_catalog.json"
    )
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    rules = catalog["subjects"]["plate"]["_meta"]["rules"]

    assert any("plate.basic_info" in rule and "plate_name" in rule for rule in rules)
    assert any("plate_code" in rule and "下游" in rule for rule in rules)
    assert any("LIKE" in rule and "不得假设" in rule for rule in rules)


def test_plate_name_resolution_rules_reach_the_step_context() -> None:
    sections = context_builder.build_context_sections(
        step=Step(
            step_id="S1",
            subject="plate",
            dataview="moneyflow",
            condition_desc="查询光模块板块近五个交易日资金流入",
            raw="S1 | plate | moneyflow | 查询光模块板块近五个交易日资金流入",
        ),
        previous_results={},
        result_id="r1",
    )

    assert "先通过 plate.basic_info 定位" in sections["current_dataview"]
    assert "优先使用返回的 plate_code 作为 code 条件" in sections["current_dataview"]

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import pytest

from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
import src.experiments.staged_data_protocol.phase2.report_provider as provider


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class _Db:
    def __init__(self, rows):
        self.cursor_obj = _Cursor(rows)
        self.closed = False

    def cursor(self, *_args, **_kwargs):
        return self.cursor_obj

    def close(self):
        self.closed = True


def test_runtime_and_static_report_catalogs_keep_four_apis_in_sync() -> None:
    payload = json.loads(
        Path("src/tools/finance_data/catalog/api_view_catalog.json").read_text(
            encoding="utf-8"
        )
    )
    static_views = payload["subjects"]["stock"]
    expected = {
        "report": {"stock.report", "stock.report.agg"},
        "report_metric": {"stock.report_metric", "stock.report_metric.agg"},
    }
    for dataview, api_names in expected.items():
        assert {item["api_name"] for item in static_views[dataview]["api"]} == api_names
        assert set(static_views[dataview]["fields"]) == set(
            resolve_api(f"stock.{dataview}")["view"]["fields"]
        )
        assert static_views[dataview]["aggregate_fields"] == resolve_api(
            f"stock.{dataview}"
        )["view"]["aggregate_fields"]


@pytest.mark.parametrize(
    ("request_text", "dataview", "api_type"),
    [
        (
            'r1 = stock.report(filter = "code = 300750.SZ", order = "report_date desc", limit = 10) -> code, report_date, rating',
            "report",
            "base",
        ),
        (
            'r1 = stock.report.agg(agg = count(stock.report.report_id), group_by = "code, name") -> code, name, count(stock.report.report_id) as report_count',
            "report",
            "agg",
        ),
        (
            'r1 = stock.report_metric(filter = "metric_code = eps and value_type = forecast") -> code, metric_code, forecast_year, metric_value',
            "report_metric",
            "base",
        ),
        (
            'r1 = stock.report_metric.agg(filter = "metric_code = eps", agg = avg(stock.report_metric.metric_value), group_by = "code, name") -> code, name, avg(stock.report_metric.metric_value) as avg_eps',
            "report_metric",
            "agg",
        ),
    ],
)
def test_report_catalog_and_validator_accept_four_apis(
    request_text: str,
    dataview: str,
    api_type: str,
) -> None:
    call = parse_api_call(request_text)
    resolved = resolve_api(call.api)
    assert resolved and resolved["dataview"] == dataview
    assert resolved["type"] == api_type
    assert validate_call(call, {}).ok is True


def test_report_provider_queries_reports_with_fixed_report_granularity(monkeypatch) -> None:
    db = _Db(
        [
            {
                "report_id": 1,
                "code": "300750",
                "rating": "买入",
                "target_price_upper": Decimal("321.50"),
                "analysts": '["张三", "李四"]',
            }
        ]
    )
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)
    result = provider.execute_report_api(
        args={
            "filter": "code = 300750.SZ and report_date >= 2026-01-01",
            "order": "report_date desc",
            "limit": 2,
        },
        outputs=["report_id", "code", "rating", "target_price_upper", "analysts"],
    )
    assert result["status"] == "ok"
    assert result["rows"] == [
        {
            "report_id": 1,
            "code": "300750.SZ",
            "rating": "买入",
            "target_price_upper": 321.5,
            "analysts": ["张三", "李四"],
        }
    ]
    assert "`reports` r" in db.cursor_obj.sql
    assert "metric_fact" not in db.cursor_obj.sql
    assert "REGEXP" in db.cursor_obj.sql
    assert "DATE(r.publish_at)" in db.cursor_obj.sql
    assert db.cursor_obj.params == ("300750", "2026-01-01", 2)
    assert db.closed is True


def test_report_metric_provider_joins_fact_report_and_definition(monkeypatch) -> None:
    db = _Db(
        [
            {
                "report_id": 1,
                "code": "600690",
                "institution": "中金公司",
                "metric_code": "np_parent",
                "forecast_year": 2027,
                "metric_value": Decimal("21500000000"),
                "unit": "元",
                "reliable": 1,
            }
        ]
    )
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)
    result = provider.execute_report_metric_api(
        args={
            "filter": (
                "institution = 中金公司 and code = 600690.SH "
                "and metric_code = np_parent and forecast_year = 2027 "
                "and value_type = forecast"
            ),
            "limit": 10,
        },
        outputs=[
            "report_id",
            "code",
            "institution",
            "metric_code",
            "forecast_year",
            "metric_value",
            "unit",
            "reliable",
        ],
    )
    assert result["status"] == "ok"
    assert result["rows"][0]["code"] == "600690.SH"
    assert result["rows"][0]["metric_value"] == 21_500_000_000.0
    assert result["rows"][0]["reliable"] is True
    assert "`metric_fact` m" in db.cursor_obj.sql
    assert "INNER JOIN `reports` r" in db.cursor_obj.sql
    assert "LEFT JOIN `metric_def` d" in db.cursor_obj.sql
    assert db.cursor_obj.params[:-1] == (
        "中金公司",
        "中金",
        "600690",
        "np_parent",
        2027,
        "forecast",
    )


def test_report_provider_matches_only_same_normalized_institution_identity(
    monkeypatch,
) -> None:
    db = _Db([])
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)

    result = provider.execute_report_api(
        args={"filter": "institution = 东方证券 and code = 688981.SH"},
        outputs=["code", "institution", "report_date"],
    )

    assert result["status"] == "ok"
    assert "TRIM(TRAILING '研究所'" in db.cursor_obj.sql
    assert db.cursor_obj.params[:-1] == ("东方证券", "东方证券", "688981")
    assert provider._normalize_institution_identity("东方证券研究所") == "东方证券"
    assert provider._normalize_institution_identity("海通证券") == "海通证券"
    assert provider._normalize_institution_identity("海通国际") == "海通国际"


def test_report_provider_normalizes_each_institution_in_list(monkeypatch) -> None:
    db = _Db([])
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)

    result = provider.execute_report_api(
        args={"filter": "institution in (东方证券研究所, 海通证券股份有限公司)"},
        outputs=["institution"],
    )

    assert result["status"] == "ok"
    assert " OR " in db.cursor_obj.sql
    assert db.cursor_obj.params[:-1] == (
        "东方证券研究所",
        "东方证券",
        "海通证券股份有限公司",
        "海通证券",
    )


def test_report_metric_provider_preserves_boolean_filter_structure(monkeypatch) -> None:
    db = _Db([])
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)
    result = provider.execute_report_metric_api(
        args={
            "filter": (
                "(metric_code = eps or metric_code = roe) "
                "and value_type = forecast"
            )
        },
        outputs=["metric_code", "metric_value"],
    )
    assert result["status"] == "ok"
    assert " OR " in db.cursor_obj.sql
    assert " AND " in db.cursor_obj.sql
    assert db.cursor_obj.params[:-1] == ("eps", "roe", "forecast")


def test_report_metric_provider_rejects_unknown_metric_code_before_query(monkeypatch) -> None:
    monkeypatch.setattr(
        provider,
        "_connect_report_db",
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be queried")),
    )
    result = provider.execute_report_metric_api(
        args={"filter": "code = 300750 and metric_code = gross_margin"},
        outputs=["code", "metric_name", "metric_value"],
    )
    assert result["status"] == "unsupported"
    assert "metric_code must be one of" in result["reason"]


def test_report_metric_provider_normalizes_metric_code_alias(monkeypatch) -> None:
    db = _Db([])
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)
    result = provider.execute_report_metric_api(
        args={"filter": "code = 300750.SZ and metric_code = EPS", "limit": 1},
        outputs=["code", "metric_value"],
    )
    assert result["status"] == "ok"
    assert "eps" in db.cursor_obj.params


def test_report_aggregate_counts_matching_rows_without_deduplication(monkeypatch) -> None:
    db = _Db([{"code": "300750", "name": "宁德时代", "rating": "买入", "report_count": 8}])
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)
    result = provider.execute_report_agg_api(
        dataview="report",
        args={
            "filter": "report_date >= 2026-01-01",
            "agg": "count(stock.report.report_id)",
            "group_by": "code, name, rating",
            "order": "report_count desc",
            "limit": 20,
        },
        outputs=[
            "code",
            "name",
            "rating",
            "count(stock.report.report_id) as report_count",
        ],
    )
    assert result["status"] == "ok"
    assert result["columns"] == ["code", "name", "rating", "report_count"]
    assert result["rows"][0]["code"] == "300750.SZ"
    assert "COUNT(r.id) AS `report_count`" in db.cursor_obj.sql
    assert "GROUP BY r.security_code, r.security_name, r.stock_rating" in db.cursor_obj.sql
    assert "DISTINCT" not in db.cursor_obj.sql


def test_report_metric_aggregate_uses_requested_numeric_aggregate(monkeypatch) -> None:
    db = _Db([{"code": "002938", "forecast_year": 2027, "avg_eps": Decimal("3.75")}])
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)
    result = provider.execute_report_agg_api(
        dataview="report_metric",
        args={
            "filter": "metric_code = eps and value_type = forecast",
            "agg": "avg(stock.report_metric.metric_value)",
            "group_by": "code, forecast_year",
            "order": "forecast_year asc",
        },
        outputs=[
            "code",
            "forecast_year",
            "avg(stock.report_metric.metric_value) as avg_eps",
        ],
    )
    assert result["status"] == "ok"
    assert result["rows"] == [
        {"code": "002938.SZ", "forecast_year": 2027, "avg_eps": 3.75}
    ]
    assert "AVG(m.value_num) AS `avg_eps`" in db.cursor_obj.sql


def test_report_metric_median_uses_window_ranks_not_avg_substitution(monkeypatch) -> None:
    db = _Db([{"forecast_year": 2027, "median_eps": Decimal("3.50")}])
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)
    result = provider.execute_report_agg_api(
        dataview="report_metric",
        args={
            "filter": "metric_code = eps and value_type = forecast",
            "agg": "median(stock.report_metric.metric_value)",
            "group_by": "forecast_year",
        },
        outputs=[
            "forecast_year",
            "median(stock.report_metric.metric_value) as median_eps",
        ],
    )
    assert result["status"] == "ok"
    assert result["rows"] == [{"forecast_year": 2027, "median_eps": 3.5}]
    assert "ROW_NUMBER() OVER" in db.cursor_obj.sql
    assert "COUNT(*) OVER" in db.cursor_obj.sql
    assert "DIV 2" in db.cursor_obj.sql
    assert result["sql_shape"]["aggregate"]["method"] == "median"


def test_report_aggregate_rejects_method_field_mismatch_without_query(monkeypatch) -> None:
    monkeypatch.setattr(
        provider,
        "_connect_report_db",
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be queried")),
    )
    result = provider.execute_report_agg_api(
        dataview="report",
        args={"agg": "avg(stock.report.rating)"},
        outputs=["avg(stock.report.rating) as avg_rating"],
    )
    assert result["status"] == "unsupported"
    assert "avg(rating) is not supported" in result["reason"]

    call = parse_api_call(
        "r1 = stock.report.agg(agg = avg(stock.report.rating)) "
        "-> avg(stock.report.rating) as avg_rating"
    )
    validation = validate_call(call, {})
    assert validation.ok is False
    assert any("agg=avg unsupported" in error for error in validation.errors)


def test_report_limit_minus_one_detects_overflow_instead_of_truncating(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FIN_AGENT_REPORT_HARD_ROW_LIMIT", "100")
    db = _Db([{"report_id": index} for index in range(101)])
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)
    result = provider.execute_report_api(
        args={"limit": -1},
        outputs=["report_id"],
    )
    assert result["status"] == "result_too_large"
    assert result["rows"] == []
    assert db.cursor_obj.params[-1] == 101


def test_api_runner_routes_report_metric_and_report_aggregates(monkeypatch) -> None:
    import src.experiments.staged_data_protocol.phase2.api_runner as api_runner

    monkeypatch.setattr(
        api_runner,
        "execute_report_metric_api",
        lambda **_: {
            "status": "ok",
            "columns": ["metric_code"],
            "rows": [{"metric_code": "eps"}],
        },
    )
    metric_call = parse_api_call(
        'r1 = stock.report_metric(filter = "metric_code = eps") -> metric_code'
    )
    metric_result = execute_api_call(metric_call)
    assert metric_result.data["rows"] == [{"metric_code": "eps"}]

    captured = {}

    def fake_aggregate(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "columns": ["report_count"], "rows": [{"report_count": 3}]}

    monkeypatch.setattr(api_runner, "execute_report_agg_api", fake_aggregate)
    aggregate_call = parse_api_call(
        "r2 = stock.report.agg(agg = count(stock.report.report_id)) "
        "-> count(stock.report.report_id) as report_count"
    )
    aggregate_result = execute_api_call(aggregate_call)
    assert captured["dataview"] == "report"
    assert aggregate_result.data["rows"] == [{"report_count": 3}]

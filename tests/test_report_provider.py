from __future__ import annotations

from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
from src.experiments.staged_data_protocol.phase2.models import ResultHandle
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

    def cursor(self, *_args, **_kwargs):
        return self.cursor_obj

    def close(self):
        pass


def test_report_catalog_and_validator_accept_new_dataview() -> None:
    resolved = resolve_api("stock.report")
    assert resolved and resolved["dataview"] == "report"
    call = parse_api_call(
        'r1 = stock.report(filter = "code = 300750 and report_date >= 2026-01-01", order = "report_date desc", limit = 10) -> code, report_date, rating'
    )
    validation = validate_call(call, {})
    assert validation.ok is True


def test_report_provider_queries_report_view_from_env(monkeypatch) -> None:
    db = _Db([{"code": "300750", "rating": "买入"}])
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)
    result = provider.execute_report_api(
        args={"filter": "code = 300750 and report_date >= 2026-01-01", "order": "report_date desc", "limit": 2},
        outputs=["code", "rating"],
    )
    assert result["status"] == "ok"
    assert result["rows"] == [{"code": "300750", "rating": "买入"}]
    assert "chatbi_report_v" in db.cursor_obj.sql
    assert "chatbi_metric_v" not in db.cursor_obj.sql
    assert db.cursor_obj.params[-1] == 2


def test_report_provider_uses_metric_view_and_can_join_report(monkeypatch) -> None:
    db = _Db([{"report_id": 1, "rating": "买入", "metric_value": 2.3}])
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)
    result = provider.execute_report_api(
        args={"filter": "rating = 买入 and metric_code = eps", "limit": 1},
        outputs=["report_id", "rating", "metric_value"],
    )
    assert result["status"] == "ok"
    assert "chatbi_metric_v" in db.cursor_obj.sql
    assert "chatbi_report_v" in db.cursor_obj.sql
    assert "JOIN" in db.cursor_obj.sql


def test_report_provider_rejects_unknown_metric_code(monkeypatch) -> None:
    monkeypatch.setattr(provider, "_connect_report_db", lambda: (_ for _ in ()).throw(AssertionError("DB must not be queried")))
    result = provider.execute_report_api(
        args={"filter": "code = 300750 and metric_code = gross_margin", "limit": 1},
        outputs=["code", "metric_name", "metric_value"],
    )
    assert result["status"] == "unsupported"
    assert "metric_code must be one of" in result["reason"]


def test_report_provider_normalizes_standard_metric_name_alias(monkeypatch) -> None:
    db = _Db([])
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)
    result = provider.execute_report_api(
        args={"filter": "code = 300750.SZ and metric_code = EPS", "limit": 1},
        outputs=["code", "metric_value"],
    )
    assert result["status"] == "ok"
    assert "eps" in db.cursor_obj.params


def test_report_provider_normalizes_metric_name_to_database_standard(monkeypatch) -> None:
    db = _Db([])
    monkeypatch.setattr(provider, "_connect_report_db", lambda: db)
    result = provider.execute_report_api(
        args={"filter": "code = 300750 and metric_name = EPS", "limit": 1},
        outputs=["code", "metric_value"],
    )
    assert result["status"] == "ok"
    assert "每股收益" in db.cursor_obj.params


def test_api_runner_executes_report_provider(monkeypatch) -> None:
    import src.experiments.staged_data_protocol.phase2.api_runner as api_runner

    monkeypatch.setattr(
        api_runner,
        "execute_report_api",
        lambda **_: {"status": "ok", "columns": ["code"], "rows": [{"code": "300750"}]},
    )
    call = parse_api_call('r1 = stock.report(filter = "code = 300750") -> code')
    result = execute_api_call(call)
    assert result.api == "stock.report"
    assert result.columns == ["code"]
    assert result.data["rows"] == [{"code": "300750"}]

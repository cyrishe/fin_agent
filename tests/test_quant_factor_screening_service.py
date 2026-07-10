from __future__ import annotations

from src.services.code_work_item_runner import CodeWorkItemRunner
from src.services.file_artifact_service import FileArtifactService
from src.services.python_execution_runtime import PythonExecutionRuntime
from src.services.quant_data_provider_service import QuantDataProviderService
from src.services.quant_factor_screening_service import QuantFactorScreeningError, QuantFactorScreeningService


def _provider(tmp_path, rows_by_source, *, overrides=None):
    artifact_service = FileArtifactService(data_root=tmp_path / "data")

    def execute(source, request):
        return list(rows_by_source.get(source["source_id"], []))

    provider = QuantDataProviderService(
        data_root=tmp_path / "data",
        file_artifact_service=artifact_service,
        query_executor=execute,
        source_status_overrides=overrides or {"daily_price": "verified"},
    )
    runner = CodeWorkItemRunner(
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runs"),
        file_artifact_service=artifact_service,
    )
    service = QuantFactorScreeningService(
        data_root=tmp_path / "data",
        provider_service=provider,
        code_runner=runner,
        file_artifact_service=artifact_service,
    )
    return service, artifact_service


def test_quant_factor_screening_runs_provider_and_code_runtime(tmp_path):
    service, artifact_service = _provider(
        tmp_path,
        {
            "daily_price": [
                {"stk_code": "600000.SH", "trade_date": "2026-05-28", "close": 10.0, "amount": 100000000},
                {"stk_code": "600000.SH", "trade_date": "2026-05-29", "close": 11.0, "amount": 120000000},
                {"stk_code": "000001.SZ", "trade_date": "2026-05-28", "close": 8.0, "amount": 90000000},
                {"stk_code": "000001.SZ", "trade_date": "2026-05-29", "close": 8.2, "amount": 80000000},
            ]
        },
    )

    result = service.run(
        {
            "user_text": "最近20个交易日涨幅靠前且成交额放大的股票",
            "universe": ["600000", "000001.SZ"],
            "date_range": {"start": "2026-05-28", "end": "2026-05-29"},
            "required_data": ["daily_price"],
            "top_n": 1,
        }
    )

    assert result["selected_stocks"][0]["stk_code"] == "600000.SH"
    assert result["data_coverage"]["selected_count"] == 1
    assert result["data_coverage"]["eligible_count"] == 2
    assert result["factor_table"]["artifact_ref"].startswith("runtime://artifact/art_")
    assert result["audit"]["factor_plan_hash"].startswith("sha256:")
    assert result["audit"]["code_hash"].startswith("sha256:")
    assert result["audit"]["runtime_status"] == "completed"
    manifest = artifact_service.read_manifest(result["factor_table"]["artifact_ref"])
    assert manifest["created_by_tool"] == "quant_factor_screening"


def test_quant_factor_screening_reports_data_gap_without_fabricating_candidates(tmp_path):
    service, _ = _provider(tmp_path, {}, overrides={})

    try:
        service.run(
            {
                "user_text": "资金连续流入且走势强的股票",
                "universe": ["600000"],
                "date_range": {"start": "2026-05-29", "end": "2026-05-29"},
                "required_data": ["daily_price", "moneyflow"],
            }
        )
    except QuantFactorScreeningError as exc:
        error = exc
    else:
        raise AssertionError("expected data_unavailable")

    assert error.failure_kind == "data_unavailable"
    assert error.data["selected_stocks"] == []
    assert error.data["data_coverage"]["missing_data_summary"]
    assert error.data["audit"]["runtime_status"] == "not_started"
    assert any("不构成收益承诺" in item for item in error.data["risk_warnings"])


def test_quant_factor_screening_infers_news_and_concept_sources_from_text(tmp_path):
    service, _ = _provider(
        tmp_path,
        {
            "daily_price": [
                {"stk_code": "600000.SH", "trade_date": "2026-05-29", "close": 10.0, "amount": 1},
            ],
            "news": [
                {"stk_code": "600000.SH", "trade_date": "2026-05-29", "title": "正向消息"},
            ],
        },
        overrides={"daily_price": "verified", "news": "verified"},
    )

    result = service.run(
        {
            "user_text": "最近有新闻催化且走势较强的股票",
            "universe": ["600000"],
            "date_range": {"start": "2026-05-29", "end": "2026-05-29"},
        }
    )

    assert "news" in result["factor_plan"]["required_data"]
    assert result["selected_stocks"][0]["catalyst_signal"] == 1

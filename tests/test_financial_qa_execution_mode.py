from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.finance_api.models import FinanceAnswerRequest, FinanceQueryRequest
from src.scenarios.financial_qa.execution_mode import (
    normalize_financial_qa_execution_mode,
)


def test_execution_mode_defaults_to_standard_and_accepts_fast() -> None:
    assert normalize_financial_qa_execution_mode(None) == "standard"
    assert normalize_financial_qa_execution_mode(" FAST ") == "fast"
    assert FinanceQueryRequest(query="查询贵州茅台").execution_mode == "standard"


def test_execution_mode_rejects_unknown_values_and_cc_fast_combination() -> None:
    with pytest.raises(ValueError, match="standard 或 fast"):
        normalize_financial_qa_execution_mode("deep")
    with pytest.raises(ValidationError, match="仅支持 runtime=dsh"):
        FinanceQueryRequest(
            query="查询贵州茅台",
            runtime="cc",
            execution_mode="fast",
        )
    with pytest.raises(ValidationError, match="仅支持 runtime=dsh"):
        FinanceAnswerRequest(
            query="查询贵州茅台",
            runtime="cc",
            execution_mode="fast",
        )


def test_chat_api_rejects_unknown_execution_mode_before_work() -> None:
    from src.web import flask_app as web

    client = web.app.test_client()
    dispatch = client.post(
        "/api/chat/dispatch",
        json={"text": "分析贵州茅台", "financial_qa_execution_mode": "deep"},
    )
    stream = client.post(
        "/api/chat/stream/start",
        json={"text": "分析贵州茅台", "financial_qa_execution_mode": "deep"},
    )

    assert dispatch.status_code == 400
    assert stream.status_code == 400
    assert "standard 或 fast" in dispatch.get_json()["error"]
    assert "standard 或 fast" in stream.get_json()["error"]

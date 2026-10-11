from __future__ import annotations

from src.scenarios.financial_qa.query_recovery import (
    provider_retry_allowed,
    query_recovery,
)


def test_successful_empty_result_is_not_retryable() -> None:
    recovery = query_recovery(
        validation={"ok": True},
        execution={"ok": True, "status": "ok"},
        result_data={"status": "ok", "rows": [], "row_count": 0},
    )

    assert recovery is not None
    assert recovery["category"] == "empty_success"
    assert recovery["retryable"] is False
    assert recovery["owner"] == "tool"


def test_tool_supplied_ambiguity_allows_one_semantic_resolution() -> None:
    recovery = query_recovery(
        validation={"ok": True},
        execution={"ok": True, "status": "ok"},
        result_data={
            "status": "ok",
            "rows": [],
            "row_count": 0,
            "name_resolution": {
                "status": "ambiguous",
                "candidates": [
                    {"plate_code": "A", "plate_name": "光模块"},
                    {"plate_code": "B", "plate_name": "高速光模块"},
                ],
            },
        },
    )

    assert recovery is not None
    assert recovery["category"] == "ambiguous_identity"
    assert recovery["retryable"] is True
    assert recovery["owner"] == "cc"
    assert recovery["max_retries"] == 1
    assert len(recovery["candidates"]) == 2


def test_provider_retry_is_exactly_once_for_declared_execution_failures() -> None:
    execution = {"ok": False, "status": "provider_error"}

    assert provider_retry_allowed(execution, retries_used=0) is True
    assert provider_retry_allowed(execution, retries_used=1) is False
    assert provider_retry_allowed(
        {"ok": False, "status": "unsupported"},
        retries_used=0,
    ) is False

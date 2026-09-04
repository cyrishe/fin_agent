from __future__ import annotations

from typing import Any

from scripts.eval_finance_api_static_validation import evaluate_case, summarize


REQUEST = (
    'r1 = stock.basic_info(filter = "code = 600519.SH", limit = 1) '
    '-> code, name'
)


class _Runtime:
    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None):
        self.result = result or {}
        self.error = error

    def execute_request(self, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.result


def _case(expected_static: str = "pass") -> dict[str, str]:
    return {
        "case_id": "EVAL_001",
        "request": REQUEST,
        "expected_static": expected_static,
    }


def _runtime_result(*, ok: bool, status: str, reason: str = "") -> dict[str, Any]:
    return {
        "execution": {"ok": ok, "status": status, "reason": reason},
        "result": {"data": {"status": status, "rows": [], "row_count": 0}},
    }


def test_no_execute_does_not_claim_provider_success() -> None:
    row = evaluate_case(
        _case(),
        runtime=_Runtime(),
        execute=False,
    )

    assert row["execution_attempted"] is False
    assert row["execution_ok"] is None
    summary = summarize([row])
    assert summary["static_decision_accuracy"] == 1.0
    assert summary["post_static_execution_attempted_count"] == 0
    assert summary["post_static_execution_success_rate"] is None


def test_execution_success_uses_runtime_ok_fact() -> None:
    row = evaluate_case(
        _case(),
        runtime=_Runtime(_runtime_result(ok=True, status="custom_success")),
        execute=True,
    )

    assert row["execution_ok"] is True
    assert summarize([row])["post_static_execution_success_rate"] == 1.0


def test_direct_timeout_is_recorded_as_evaluator_timeout() -> None:
    row = evaluate_case(
        _case(),
        runtime=_Runtime(error=TimeoutError("provider execution exceeded 1s")),
        execute=True,
        timeout_seconds=1,
    )

    assert row["execution_attempted"] is True
    assert row["execution_ok"] is False
    assert row["execution_timed_out"] is True


def test_provider_wrapped_timeout_is_recorded_without_guessing_other_failures() -> None:
    wrapped = evaluate_case(
        _case(),
        runtime=_Runtime(
            _runtime_result(
                ok=False,
                status="provider_error",
                reason="database interrupted: provider execution exceeded 15s",
            )
        ),
        execute=True,
        timeout_seconds=15,
    )
    ordinary = evaluate_case(
        _case(),
        runtime=_Runtime(
            _runtime_result(
                ok=False,
                status="provider_error",
                reason="database unavailable",
            )
        ),
        execute=True,
        timeout_seconds=15,
    )

    assert wrapped["execution_timed_out"] is True
    assert ordinary["execution_timed_out"] is False
    summary = summarize([wrapped, ordinary])
    assert summary["post_static_execution_timeout_count"] == 1
    assert summary["post_static_execution_non_timeout_failure_count"] == 1
    assert summary["post_static_execution_status_counts"] == {"provider_error": 2}

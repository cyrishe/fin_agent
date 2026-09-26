from __future__ import annotations

from typing import Any

import pytest

from src.experiments.staged_data_protocol.phase2 import constitution_provider


def test_explicit_full_constitution_limit_uses_overflow_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIN_AGENT_CONSTITUTION_HARD_ROW_LIMIT", "12000")

    policy = constitution_provider._constitution_limit_policy(-1)

    assert policy == {
        "fetch_limit": 12001,
        "hard_limit": 12000,
        "explicit_full": True,
        "detect_overflow": True,
        "rejected": False,
        "reason": "",
    }


def test_constitution_limit_above_safety_maximum_is_not_silently_clamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIN_AGENT_CONSTITUTION_HARD_ROW_LIMIT", "1000")

    policy = constitution_provider._constitution_limit_policy(1001)

    assert policy["rejected"] is True
    assert "exceeds the safety maximum" in policy["reason"]


def test_explicit_full_constitution_query_returns_every_row_below_hard_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_limits: list[int] = []

    class FakeCursor:
        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def execute(self, _sql: str, params: tuple[Any, ...]) -> None:
            requested_limits.append(int(params[-1]))

        def fetchall(self) -> list[dict[str, str]]:
            return [
                {
                    "plate_name": "CPO",
                    "stock_code": f"{index:06d}.SZ",
                }
                for index in range(183)
            ]

    class FakeConnection:
        def cursor(self, *_args: Any) -> FakeCursor:
            return FakeCursor()

    class FakeDb:
        conn = FakeConnection()

        def __init__(self, **_kwargs: Any) -> None:
            pass

        def close_db(self) -> None:
            pass

    monkeypatch.setattr(constitution_provider, "StockInfoDbUtils", FakeDb)

    result = constitution_provider.execute_constitution_api(
        subject="plate",
        args={"filter": "plate_name = CPO", "limit": -1},
        outputs=["plate_name", "stock_code"],
    )

    assert result["status"] == "ok"
    assert len(result["rows"]) == 183
    assert requested_limits == [10001]
    assert result["sql_shape"]["limit"] == -1


def test_explicit_full_constitution_query_reports_safety_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCursor:
        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def execute(self, _sql: str, _params: tuple[Any, ...]) -> None:
            return None

        def fetchall(self) -> list[dict[str, str]]:
            return [
                {"plate_name": "CPO", "stock_code": f"{index:06d}.SZ"}
                for index in range(101)
            ]

    class FakeConnection:
        def cursor(self, *_args: Any) -> FakeCursor:
            return FakeCursor()

    class FakeDb:
        conn = FakeConnection()

        def __init__(self, **_kwargs: Any) -> None:
            pass

        def close_db(self) -> None:
            pass

    monkeypatch.setenv("FIN_AGENT_CONSTITUTION_HARD_ROW_LIMIT", "100")
    monkeypatch.setattr(constitution_provider, "StockInfoDbUtils", FakeDb)

    result = constitution_provider.execute_constitution_api(
        subject="plate",
        args={"filter": "plate_name = CPO", "limit": -1},
        outputs=["plate_name", "stock_code"],
    )

    assert result["status"] == "result_too_large"
    assert result["rows"] == []
    assert "safety maximum of 100 rows" in result["reason"]

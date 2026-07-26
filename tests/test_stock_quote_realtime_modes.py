from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.experiments.staged_data_protocol.phase2 import api_runner
from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
from src.experiments.staged_data_protocol.phase2.intraday_quote_provider import _minute_bar_cte
from src.experiments.staged_data_protocol.phase2.models import ApiCall


@pytest.mark.parametrize(
    ("realtime", "expected_provider", "expected_latest_only"),
    [
        (0, "daily", None),
        (1, "minute", False),
        (2, "minute", True),
    ],
)
def test_stock_quote_routes_realtime_modes(
    monkeypatch: pytest.MonkeyPatch,
    realtime: int,
    expected_provider: str,
    expected_latest_only: bool | None,
) -> None:
    calls: list[tuple[str, bool | None]] = []

    def fake_daily(*, subject, args, outputs):
        calls.append(("daily", None))
        return {"status": "ok", "columns": ["code"], "rows": []}

    def fake_minute(*, args, outputs, latest_only=False):
        calls.append(("minute", latest_only))
        return {"status": "ok", "columns": ["code"], "rows": []}

    monkeypatch.setattr(api_runner, "execute_quote_api", fake_daily)
    monkeypatch.setattr(api_runner, "execute_intraday_quote_api", fake_minute)

    result = api_runner.execute_api_call(
        ApiCall(
            result_id="r1",
            api="stock.quote",
            args={"realtime": realtime},
            outputs=["code"],
            raw="",
        )
    )

    assert result.data["status"] == "ok"
    assert calls == [(expected_provider, expected_latest_only)]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, 0),
        (1, 1),
        (2, 2),
        ("history", 0),
        ("minute", 1),
        ("latest", 2),
        (False, 0),
        (True, 2),
    ],
)
def test_stock_quote_realtime_mode_compatibility(raw: object, expected: int) -> None:
    resolved = resolve_api("stock.quote")

    assert resolved is not None
    assert api_runner._quote_realtime_mode({"realtime": raw}, resolved) == expected


def test_minute_bar_sql_uses_previous_available_snapshot() -> None:
    sql = _minute_bar_cte(date_predicate="s.trade_date = %s")

    assert "TIME(s.snapshot_time) >= '09:30:00'" in sql
    assert "LAG(s.latest_price)" in sql
    assert "LAG(s.amount)" in sql
    assert "LAG(s.volume)" in sql
    assert "COALESCE(previous_close, snapshot_open, preclose) AS open" in sql
    assert (
        "GREATEST(COALESCE(cumulative_amount, 0) - "
        "COALESCE(previous_amount, 0), 0) AS amount"
    ) in sql
    assert (
        "GREATEST(COALESCE(cumulative_volumn, 0) - "
        "COALESCE(previous_volumn, 0), 0) AS volumn"
    ) in sql
    assert "s.high_price AS high" in sql
    assert "s.low_price AS low" in sql


def test_tool_catalog_explains_stock_quote_realtime_modes() -> None:
    catalog_path = (
        Path(__file__).parents[1]
        / "src"
        / "tools"
        / "finance_data"
        / "catalog"
        / "api_view_catalog.json"
    )
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    quote = catalog["subjects"]["stock"]["quote"]
    description = quote["desc"]
    rules = "\n".join(quote["rules"])

    assert "realtime=0" in description
    assert "realtime=1" in description
    assert "realtime=2" in description
    assert "盘前快照不返回" in rules
    assert "09:30第一根分钟K的open取当日开盘价" in rules
    assert "后续分钟open取上一分钟close" in rules

"""Contract tests for shared execution rows and the 14:40 exit adapter."""
from datetime import date
from decimal import Decimal

import pytest

from scripts.automl_strategy_execution import record_decisions, record_run, settle_previous


def test_schema_initialization_ignores_semicolons_in_comments(monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path
    from scripts import run_automl_1440_strategy as runner
    calls = []
    class Connection:
        @contextmanager
        def cursor(self):
            class StatementCursor:
                def execute(self, sql):
                    calls.append(sql)
            yield StatementCursor()
        def commit(self):
            pass
    @contextmanager
    def connection(_):
        yield Connection()
    monkeypatch.setattr(runner, "write_connection", connection)
    runner.init_db(Path("unused"))
    assert len(calls) == 4
    assert all(sql.startswith("CREATE TABLE IF NOT EXISTS") for sql in calls)


class Cursor:
    def __init__(self, *, existing=None, pending=None, bars=None):
        self.existing = existing
        self.pending = pending or []
        self.bars = bars or []
        self.calls = []
        self.rowcount = 1

    def execute(self, sql, params):
        self.calls.append((sql, params))
        self.sql = sql

    def fetchone(self):
        return self.existing

    def fetchall(self):
        if "FROM automl_strategy_execution AS e" in self.sql:
            return self.pending
        if "FROM aiia_stock_realtime_minute_snapshot_full" in self.sql:
            return self.bars
        raise AssertionError(self.sql)


def test_record_default_actionable_and_frozen_identity():
    cursor = Cursor()
    kwargs = dict(user_id="u", strategy_id="s", strategy_name="策略",
                  signal_date="2026-10-15", signal_at="2026-10-15 14:40:07",
                  model_sha256=None, environment={"python": "3.12"})
    pick = {"selection_rank": 1, "symbol6": "123456", "name": "测试",
            "signal_price": Decimal("10.50")}
    record_decisions(cursor, picks=[pick], **kwargs)
    insert = next(params for sql, params in cursor.calls if "INSERT INTO" in sql)
    assert insert[7] == 1
    assert insert[11] == Decimal("10.50")
    assert insert[12] == "14:40_signal_quote_proxy"
    assert insert[13] == "next_trade_day_0940_close"

    frozen = Cursor(existing={"stock_code": "123456", "actionable": 1,
                              "signal_price": Decimal("10.50"),
                              "model_sha256": None})
    record_decisions(frozen, picks=[pick], **kwargs)
    assert not any("INSERT INTO" in sql for sql, _ in frozen.calls)
    with pytest.raises(ValueError, match="已冻结"):
        record_decisions(Cursor(existing=frozen.existing),
                         picks=[{**pick, "symbol6": "654321"}], **kwargs)


def test_empty_successful_run_is_visible_and_cannot_be_rewritten():
    kwargs = dict(user_id="u", strategy_id="s", strategy_name="策略",
                  signal_date="2026-10-15", signal_at="2026-10-15 14:40:00",
                  candidate_count=0, selection_count=0,
                  model_sha256="a" * 64, environment={"python": "3.12"})
    cursor = Cursor()
    record_run(cursor, **kwargs)
    assert next(params for sql, params in cursor.calls if "INSERT INTO" in sql)[6] == 0
    with pytest.raises(ValueError, match="已冻结"):
        record_run(Cursor(existing={"candidate_count": 0, "selection_count": 0,
                                    "model_sha256": "a" * 64}),
                   **{**kwargs, "selection_count": 1})


def test_settlement_uses_next_trading_day_exact_final_minute_and_is_idempotent():
    cursor = Cursor(pending=[{"signal_date": date(2026, 10, 14),
                              "selection_rank": 1, "stock_code": "123456",
                              "entry_price": Decimal("10.00"),
                              "exit_date": date(2026, 10, 15)}],
                    bars=[{"latest_price": Decimal("10.25")}])
    completed = settle_previous(cursor, user_id="u", strategy_id="s",
                                as_of_date="2026-10-15")
    assert completed[0]["gross_return_pct"] == "2.500"
    price_query = next((sql, params) for sql, params in cursor.calls
                       if "FROM aiia_stock_realtime_minute_snapshot_full" in sql)
    assert "is_finalized=1" in price_query[0]
    assert "is_fallback=0" in price_query[0]
    assert price_query[1][-1] == "2026-10-15 09:40:00"
    update = next(params for sql, params in cursor.calls if "UPDATE" in sql)
    assert update[1] == Decimal("10.25")
    assert update[3] == Decimal("2.500")


def test_unavailable_or_ambiguous_exit_price_remains_pending():
    row = {"signal_date": date(2026, 10, 14), "selection_rank": 1,
           "stock_code": "123456", "entry_price": Decimal("10"),
           "exit_date": date(2026, 10, 15)}
    for bars in ([], [{"latest_price": 11}, {"latest_price": 11}]):
        cursor = Cursor(pending=[row], bars=bars)
        assert settle_previous(cursor, user_id="u", strategy_id="s",
                               as_of_date="2026-10-15") == []
        assert not any("UPDATE" in sql for sql, _ in cursor.calls)

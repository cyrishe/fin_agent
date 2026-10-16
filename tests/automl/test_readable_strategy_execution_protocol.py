"""Protocol checks for the readable strategy execution store."""
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.strategy_execution_store import record_daily_run, record_selection


class Cursor:
    def __init__(self, existing=None):
        self.existing = existing
        self.calls = []

    def execute(self, sql, args):
        self.calls.append((sql, args))

    def fetchone(self):
        return self.existing


def test_schema_has_readable_identity_and_compact_details():
    sql = Path("deploy/sql/strategy_execution.sql").read_text()
    assert sql.count("CREATE TABLE IF NOT EXISTS") == 2
    for field in ("strategy_name", "model_version", "version_desc",
                  "stock_code", "actionable", "buy_price", "sell_price",
                  "gross_return_pct", "details_json"):
        assert field in sql
    assert "p_lt0" not in sql
    assert "strategy_id" not in sql


def test_run_and_pick_use_chinese_name_version_and_optional_details():
    cursor = Cursor()
    record_daily_run(cursor, user_id="imac", strategy_name="收盘开盘",
                     model_version="v1.0", version_desc="20日滚动七因子",
                     signal_date="2026-10-16", signal_at="2026-10-16 14:40:00",
                     candidate_count=40, selection_count=1,
                     environment={"sklearn": "1.7.1"})
    run_sql, run_args = cursor.calls[-1]
    assert "INSERT INTO strategy_daily_run" in run_sql
    assert run_args[:5] == ("imac", "收盘开盘", "v1.0", "20日滚动七因子", "2026-10-16")
    assert '"sklearn": "1.7.1"' in run_args[-1]

    record_selection(cursor, user_id="imac", strategy_name="收盘开盘",
                     model_version="v1.0", version_desc="20日滚动七因子",
                     signal_date="2026-10-16", selection_rank=1,
                     stock_code="002636", stock_name="金安国纪", signal_price=71.09,
                     details={"predicted_class": "ge3", "legacy_strategy_id": "old-id"})
    pick_sql, pick_args = cursor.calls[-1]
    assert "INSERT INTO strategy_execution" in pick_sql
    assert pick_args[8] == 1  # missing actionable defaults to eligible
    assert '"legacy_strategy_id": "old-id"' in pick_args[-1]


def test_replay_is_idempotent_but_conflicting_identity_is_rejected():
    cursor = Cursor(existing={"version_desc": "20日滚动七因子",
                              "stock_code": "002636", "stock_name": "金安国纪",
                              "signal_price": Decimal("71.09")})
    kwargs = dict(user_id="imac", strategy_name="收盘开盘",
                  model_version="v1.0", version_desc="20日滚动七因子",
                  signal_date="2026-10-16", selection_rank=1,
                  stock_code="002636", stock_name="金安国纪",
                  signal_price=Decimal("71.09"))
    record_selection(cursor, **kwargs)
    assert len(cursor.calls) == 1
    with pytest.raises(ValueError, match="不一致"):
        record_selection(Cursor(existing=cursor.existing),
                         **{**kwargs, "stock_code": "002812"})


def test_unusable_identity_is_rejected_before_database_write():
    cursor = Cursor()
    with pytest.raises(ValueError, match="身份不完整"):
        record_selection(cursor, user_id="imac", strategy_name="收盘开盘",
                         model_version="v1.0", version_desc="20日滚动七因子",
                         signal_date="2026-10-16", selection_rank=1,
                         stock_code="坏代码", stock_name="股票")
    assert cursor.calls == []


def test_same_version_cannot_change_its_short_description():
    cursor = Cursor(existing={"version_desc": "20日滚动七因子"})
    with pytest.raises(ValueError, match="同一版本"):
        record_daily_run(cursor, user_id="imac", strategy_name="收盘开盘",
                         model_version="v1.0", version_desc="7日滚动新因子",
                         signal_date="2026-10-17", signal_at=None,
                         candidate_count=20, selection_count=1)
    assert not any("INSERT INTO" in sql for sql, _ in cursor.calls)


def test_readable_close_open_settlement_retains_next_trade_day_rule():
    from scripts.readable_strategy_execution import settle_previous, CLOSE_OPEN_ID
    from datetime import date
    class Prices(Cursor):
        rowcount = 1
        def fetchall(self):
            if 'FROM strategy_execution e' in self.calls[-1][0]:
                return [dict(signal_date=date(2026,10,16),selection_rank=1,
                    stock_code='002636',buy_price=Decimal('10'),exit_date=date(2026,10,19))]
            return [dict(latest_price=Decimal('10.25'))]
    c = Prices()
    assert settle_previous(c,user_id='imac',strategy_id=CLOSE_OPEN_ID,as_of_date='2026-10-18') == []
    assert not any(sql.lstrip().startswith('UPDATE') for sql,_ in c.calls)
    c = Prices()
    result = settle_previous(c,user_id='imac',strategy_id=CLOSE_OPEN_ID,as_of_date='2026-10-19')
    assert result[0]['gross_return_pct'] == '2.500'
    update = next(args for sql,args in c.calls if sql.lstrip().startswith('UPDATE'))
    assert update[5:7] == ('收盘开盘','v1.0')


def test_migration_preserves_b2_confirmed_buy_and_details(monkeypatch):
    from scripts import migrate_strategy_execution as m
    from datetime import date
    row = dict(user_id='imac',signal_date=date(2026,10,15),selection_rank=1,
        stock_code='002636',stock_name='金安国纪',actionable=1,signal_price=10,
        signal_at='2026-10-15 15:00:00',entry_price=10.1,entry_at='2026-10-16 09:35:00',
        entry_price_basis='completed_minute_open_simulation',exit_rule='d2',
        decision_json='{"chosen":true,"shape_confirmed_at":"09:34:00"}',model_sha256='a'*64)
    class Source(Cursor):
        def fetchall(self):
            sql,args=self.calls[-1]
            return [row] if 'FROM automl_strategy_execution' in sql and args==('B2',) else []
    runs=[];picks=[]
    monkeypatch.setattr(m,'record_daily_run',lambda c,**kw:runs.append(kw))
    monkeypatch.setattr(m,'record_selection',lambda c,**kw:picks.append(kw))
    m.migrate(Source(),execute=True)
    assert runs[0]['strategy_name']=='涨停冷静'
    assert picks[0]['buy_price']==10.1
    assert picks[0]['details']['shape_confirmed_at']=='09:34:00'
    assert picks[0]['details']['legacy_strategy_id']=='B2'


def test_b2_scan_updates_readable_trade_and_preserves_confirmation(monkeypatch):
    from scripts import run_automl_b2 as b
    from contextlib import contextmanager
    from datetime import datetime
    from types import SimpleNamespace
    class ScanCursor(Cursor):
        def fetchall(self):
            return [dict(selection_rank=1,stock_code='002636',actionable=0,entry_price=None,
                         decision_json='{"chosen":true,"choice_confirmed_at":"09:35:00"}')]
    c=ScanCursor()
    class DB:
        @contextmanager
        def cursor(self): yield c
        def commit(self): pass
        def rollback(self): pass
    @contextmanager
    def conn(_): yield DB()
    monkeypatch.setattr(b,'write_connection',conn)
    values=[dict(selection_rank=1,symbol6='002636',chosen=True,actionable=1,
                 entry_at='2026-10-16 09:35:00',entry_price=10.1)]
    b.persist_scan(SimpleNamespace(env_file=None,user_id='imac'),'2026-10-15',values,datetime(2026,10,16,9,36))
    sql,args=c.calls[-1]
    assert 'UPDATE strategy_execution SET actionable=%s,buy_at=%s,buy_price=%s' in sql
    assert args[6:8]==('涨停冷静','v1.0')
    import json
    saved=json.loads(args[4])
    assert saved['choice_confirmed_at']=='09:35:00' and saved['exit_rule']==b.EXIT_RULE

from datetime import date, datetime, timedelta

from src.finance_api.data_status import Dataset, DataStatusMonitor, TZ, describe, target_dates
from src.finance_api.data_status import minute_cutoff, minute_health, datasets
from datetime import time


def test_minute_sessions_and_freshness():
    def now(h, m):
        return datetime(2026, 9, 7, h, m, tzinfo=TZ)
    assert minute_cutoff(now(9, 29)) is None
    assert minute_cutoff(now(12, 30)) == time(11, 30)
    assert minute_cutoff(now(16, 0)) == time(15)
    assert not minute_health(now(12, 30), True, datetime(2026,9,7,11,30), 300)[1]
    assert minute_health(now(10, 30), True, datetime(2026,9,7,10,20), 300)[1]
    assert not minute_health(now(16, 0), False, None, 300)[1]
    assert minute_health(now(10, 30), None, None, 300)[1]
    assert not minute_health(now(9, 29), True, None, 300)[1]


def test_only_minute_source_is_realtime():
    specs = datasets()
    assert [s.id for s in specs if s.realtime] == ['stock.intraday_quote']
    assert all(s.lag == 1 for s in specs if s.daily and not s.realtime)


def test_previous_trading_day_and_zero_days_in_baseline():
    today = date(2026, 9, 7)
    calendar = {today-timedelta(days=n): (today-timedelta(days=n)).weekday()<5 for n in range(30)}
    spec = Dataset("stock.quote", "股票", "行情", "prices")
    days = target_dates(spec, datetime(2026, 9, 7, 16, tzinfo=TZ), calendar)
    assert days == [date(2026,9,4),date(2026,9,3),date(2026,9,2),date(2026,9,1),date(2026,8,31),date(2026,8,28)]
    assert describe(spec,[0,500,500,0,500,500],opened=True,due=True)[1]
    assert not describe(spec,[0]*6,opened=False,due=True)[1]
    assert not describe(spec,[0]*6,opened=True,due=False)[1]


def test_report_natural_days_and_disclosures_are_not_daily_jobs():
    report = Dataset("stock.report", "研报", "研报", "reports", "publish_at", trading=False)
    days = target_dates(report,datetime(2026,9,7,16,tzinfo=TZ),{})
    assert days[0] == date(2026,9,6)
    assert len(days) == 6
    assert not describe(report,[0]*6,opened=False,due=True)[1]
    assert describe(report,[0]*6,opened=None,due=True)[1]
    financial = Dataset("income", "财务", "利润表", "income", "ann_date", False, False)
    assert not describe(financial,[0]*6,opened=True,due=True)[1]


def test_calendar_gap_is_not_silently_a_weekend():
    import pytest
    spec = Dataset("stock.quote", "股票", "行情", "prices")
    with pytest.raises(ValueError,match="交易日历历史不足"):
        target_dates(spec,datetime(2026,9,7,16,tzinfo=TZ),{})


def test_failed_connection_preserves_inventory_and_never_looks_healthy(tmp_path):
    def fail():
        raise ConnectionError("test only")
    monitor=DataStatusMonitor(db_factory=fail,specs=[Dataset("stock.quote","股票","行情","prices")],snapshot_path=tmp_path/'status.json')
    snapshot=monitor.scan()
    assert snapshot['scan_failed']
    assert snapshot['stale']
    assert snapshot['items'][0]['attention']


def test_previous_snapshot_survives_restart(tmp_path):
    import json
    path=tmp_path/'status.json'
    path.write_text(json.dumps({'checked_at':'2020-01-01T16:00:00+08:00','items':[{'id':'stock.quote','count':42}]}))
    monitor=DataStatusMonitor(specs=[],snapshot_path=path)
    assert monitor.current()['items'][0]['count']==42
    assert monitor.current()['stale']

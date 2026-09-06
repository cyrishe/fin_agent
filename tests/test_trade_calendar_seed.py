from datetime import date

from scripts.init_trade_calendar_2026 import calendar_rows, sequence_updates, DDL


def test_full_year_and_weekend_makeup_days():
    rows = calendar_rows()
    days = {r[1]: r[2] for r in rows}
    assert len(days) == 365
    assert min(days) == date(2026, 1, 1)
    assert max(days) == date(2026, 12, 31)
    for text in ['2026-01-04','2026-02-14','2026-02-28','2026-05-09','2026-09-20','2026-10-10']:
        assert days[date.fromisoformat(text)] == 0


def test_holidays_and_reopening():
    days = {r[1]: r[2] for r in calendar_rows()}
    for text in ['2026-01-01','2026-02-23','2026-04-06','2026-05-05','2026-06-19','2026-09-25','2026-10-07']:
        assert days[date.fromisoformat(text)] == 0
    for text in ['2026-01-05','2026-02-24','2026-04-07','2026-05-06','2026-06-22','2026-09-28','2026-10-08']:
        assert days[date.fromisoformat(text)] == 1


def test_sequence_is_per_market_contiguous_and_skips_closed_days():
    rows=[('CN_A',date(2026,1,2),0,None),('CN_A',date(2026,1,5),1,None),
          ('CN_A',date(2026,1,6),1,None),('OTHER',date(2026,1,5),1,None)]
    assert sequence_updates(rows)==[(1,'CN_A',date(2026,1,5)),(2,'CN_A',date(2026,1,6)),(1,'OTHER',date(2026,1,5))]


def test_sequence_idempotence_and_stale_derived_value_repair():
    rows=[('CN_A',date(2026,1,2),0,3),('CN_A',date(2026,1,5),1,1),('CN_A',date(2026,1,6),1,9)]
    assert sequence_updates(rows)==[(None,'CN_A',date(2026,1,2)),(2,'CN_A',date(2026,1,6))]
    assert sequence_updates([('CN_A',date(2026,1,5),1,1),('CN_A',date(2026,1,6),1,2)])==[]
    assert 'trade_seq INT UNSIGNED NULL' in DDL
    assert 'idx_market_open_seq' in DDL

from datetime import date

from scripts.init_trade_calendar_2026 import calendar_rows


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

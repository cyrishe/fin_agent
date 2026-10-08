from scripts import backfill_automl_august_minute_full as backfill


def test_only_complete_minute_sessions_are_prepared_for_insert():
    minutes = list(range(9 * 60 + 31, 11 * 60 + 31)) + list(
        range(13 * 60 + 1, 15 * 60 + 1))

    def bar(day, minute):
        return {"sttDateTime": {"iDate": day, "shtTime": minute},
                "fOpen": 10, "fHigh": 11, "fLow": 9, "fClose": 10,
                "lVolume": 100, "fAmount": 100000, "dPreClose": 9.8}

    raw = [bar(20260807, minute) for minute in minutes]
    raw += [bar(20260810, minute) for minute in minutes[:100]]

    rows, counts = backfill.prepared_rows("300123", "亚光科技", raw,
                                          "2026-08-07", "2026-08-21")

    assert counts == {"20260807": 240, "20260810": 100}
    assert len(rows) == 240
    assert {str(row[0]) for row in rows} == {"2026-08-07"}
    assert rows[0][4].strftime("%H:%M") == "09:31"
    assert rows[-1][4].strftime("%H:%M") == "15:00"


def test_source_window_moves_toward_the_complete_last_day(monkeypatch):
    offsets = []

    def bars(symbol, offset, want):
        offsets.append(offset)
        last_minute = 15 * 60 if offset == 6300 else 14 * 60 + 40
        return [{"sttDateTime": {"iDate": 20260807, "shtTime": 9 * 60 + 31}},
                {"sttDateTime": {"iDate": 20260821, "shtTime": last_minute}}]

    monkeypatch.setattr(backfill, "request_bars", bars)

    _, offset = backfill.source_window("000582", "2026-08-07", "2026-08-21")

    assert offsets == [6500, 6300]
    assert offset == 6300

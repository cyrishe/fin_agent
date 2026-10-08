from scripts import audit_automl_minute_api_backfill as backfill


def test_extract_uses_exact_signal_and_next_morning_windows(monkeypatch):
    def bar(day, minute, *, close=10.0, low=9.0, high=11.0, volume=1):
        return {"sttDateTime": {"iDate": day, "shtTime": minute},
                "fOpen": 10.0, "fClose": close, "fLow": low,
                "fHigh": high, "lVolume": volume}

    bars = [bar(20260819, minute) for minute in sorted(backfill.THROUGH_1440)]
    bars += [bar(20260819, 14 * 60 + 50, close=10.2)]
    bars += [bar(20260819, 14 * 60 + 51, low=1.0, volume=99999)]
    bars += [bar(20260820, minute) for minute in sorted(backfill.MORNING)]
    bars += [bar(20260820, 9 * 60 + 41, high=99.0)]
    monkeypatch.setattr(backfill, "request_bars", lambda *args: bars)

    result = backfill.extract("300123", "2026-08-19", "2026-08-20", 6900, 600)

    assert result["status"] == "complete"
    assert result["minute_bars_1440"] == 220
    assert result["minute_volume_hands"] == 220
    assert result["min_low_so_far"] == 9
    assert result["entry_1450"] == 10.2
    assert result["next_high10"] == 11
    assert result["next_0940"] == 10

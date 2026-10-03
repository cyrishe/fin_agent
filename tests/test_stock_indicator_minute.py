from datetime import date, datetime
from unittest.mock import Mock
from contextlib import contextmanager

import pytest

from src.services.stock_indicator_minute import (
    MINUTE_FEATURES, SHANGHAI, _calculate, compute_minute_snapshot,
    aggregate_minute_bars, read_minute_indicators, session_grid,
)

DAY = date(2026, 9, 28)


def bars(n=65, period=1):
    return [dict(bar_end_time=t.to_pydatetime(), close=100+i, high=101+i, low=99+i,
                 volume=10+i, source_bar_count=period, is_finalized=1)
            for i, t in enumerate(session_grid(DAY, period)[:n])]


def compute(rows, at="11:30", period=1):
    return compute_minute_snapshot(rows, code="600519.SH", trade_date=DAY, period=period,
        as_of=datetime.fromisoformat(f"2026-09-28T{at}:00").replace(tzinfo=SHANGHAI))


def test_finite_bar_windows_and_lot_conversion():
    result = compute(bars())
    assert result["ma5"] == 162
    assert result["prior_close_high60"] == 163
    assert result["prior_volume_mean5"] == 7100
    assert result["return5"] == pytest.approx(164/159-1)
    assert result["lag_seconds"] == 55 * 60
    assert result["missing_bar_count"] == 55
    assert len(MINUTE_FEATURES) == 30
    assert "macd_dif" not in result and "volatility20" not in result
    assert result['session_rsi14'] == 100
    assert result['calculation_anchor'] == '2026-09-28T09:30:00+08:00'


def test_future_partial_and_missing_bars_do_not_invent_contiguous_windows():
    source = bars(10)
    source[7]["is_finalized"] = 0
    result = compute(source, at="09:39")
    assert result["data_as_of"].startswith("2026-09-28T09:39")
    assert result["ma5"] is None  # 09:38 gap occupies its real rolling position.
    assert result["input_bar_count"] == 8
    assert result["missing_bar_count"] == 1
    assert compute(bars(10), at="09:34")["ma5"] is None


def test_completed_market_clock_excludes_lunch_and_after_close():
    assert compute(bars(120), at="12:45")["lag_seconds"] == 0
    assert compute(bars(240), at="20:00")["lag_seconds"] == 0
    assert len(session_grid(DAY, 5)) == 48
    assert compute(bars(48, 5), at="20:00", period=5)["ma60"] is None
    result = compute(bars(), at="09:20")
    assert result["expected_bar_end"] is None and result["data_as_of"] is None


def test_cache_tracks_content_revision_but_freshness_uses_current_clock():
    _calculate.cache_clear()
    first = compute(bars(), at="10:35")
    later = compute(bars(), at="10:45")
    assert first["lag_seconds"] == 0 and later["lag_seconds"] == 600
    assert _calculate.cache_info().hits == 1
    corrected = bars()
    corrected[-1]["volume"] += 5
    revised = compute(corrected)
    assert revised["input_fingerprint"] != first["input_fingerprint"]
    assert revised["volume_ratio5"] != first["volume_ratio5"]
    assert _calculate.cache_info().misses == 2


def test_real_invalid_prices_and_duplicate_times_rejected():
    source = bars()
    source[-1]["low"] = 200
    with pytest.raises(ValueError, match="enclose"):
        compute(source)
    with pytest.raises(ValueError, match="unique"):
        compute(bars() + bars(1))
    source = bars(10, 5)
    source[-1]["source_bar_count"] = 4
    assert compute(source, period=5)["input_bar_count"] == 9


def test_reader_uses_bounded_existing_source_and_checks_full_identity():
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    conn = Mock()
    conn.cursor.return_value = cursor
    cursor.fetchall.side_effect = [[{"stk_code": "600519.SH"}],
                                  [{**r, "stk_code": "600519"} for r in bars()]]
    cursor.fetchone.return_value = {"trade_date": DAY}
    @contextmanager
    def connection():
        yield conn
    result = read_minute_indicators(codes=["600519.SH"], connection_factory=connection,
        as_of=datetime(2026,9,28,12,tzinfo=SHANGHAI))
    assert result["external_requests"] == 0 and result["row_count"] == 1
    assert "trade_date=%s" in cursor.execute.call_args[0][0]
    cursor.fetchall.side_effect = [[]]
    with pytest.raises(ValueError, match="stock master"):
        read_minute_indicators(codes=["000001.SH"], connection_factory=connection)
    with pytest.raises(ValueError, match="1..20"):
        read_minute_indicators(codes=[], connection_factory=connection)


def test_partial_period_uses_only_complete_one_minute_prefix():
    source = bars(10)
    now = datetime(2026,9,28,9,38,30,tzinfo=SHANGHAI)
    source = [dict(r, open=r['close']-.5, amount=r['volume']*100*r['close']) for r in source]
    closed = aggregate_minute_bars(source, trade_date=DAY, period=5, as_of=now)
    assert len(closed) == 1 and closed[0]['source_bar_count'] == 5
    partial = aggregate_minute_bars(source, trade_date=DAY, period=5, as_of=now, include_partial=True)
    assert len(partial) == 2 and partial[-1]['source_bar_count'] == 3
    r = compute_minute_snapshot(partial, code='600519.SH', trade_date=DAY, period=5,
                               as_of=now, include_partial=True)
    assert r['is_finalized'] is False and r['lag_seconds'] == 0
    assert r['data_as_of'].endswith('09:38:00+08:00')
    assert r['bar_end_time'].endswith('09:40:00+08:00')
    assert r['session_volume_shares'] == sum(x['volume'] for x in source[:8])*100
    assert r['session_vwap'] == pytest.approx(sum(x['amount'] for x in source[:8])/r['session_volume_shares'])
    missing = source[:6] + source[7:]
    assert len(aggregate_minute_bars(missing, trade_date=DAY, period=5, as_of=now, include_partial=True)) == 1


def test_stale_values_are_unavailable_only_when_freshness_is_requested():
    source = bars()
    now = datetime(2026,9,28,10,45,tzinfo=SHANGHAI)
    result = compute_minute_snapshot(source, code='600519.SH', trade_date=DAY, period=1,
                                    as_of=now, max_lag_seconds=120)
    assert result['lag_seconds'] == 600 and result['unavailable_reason']
    assert all(result[name] is None for name in MINUTE_FEATURES)
    assert compute(source, at='10:45')['ma20'] is not None


def test_session_metrics_warmup_gaps_and_closed_bar_compatibility():
    source = [dict(r, open=r['close']-1, amount=r['volume']*100*r['close']) for r in bars(65)]
    assert compute(source)['session_macd_dea'] is not None
    assert compute(source[:25])['session_macd_dif'] is None
    assert compute(source[:14])['session_rsi14'] is None
    broken = source[:60] + source[61:]
    result = compute(broken)
    assert result['session_vwap'] is None and result['session_volume_shares'] is None
    assert result['session_rsi14'] is None
    assert result['session_return_from_open'] == pytest.approx(164/99-1)
    now = datetime(2026,9,28,10,35,tzinfo=SHANGHAI)
    direct = compute(source, at='10:35')
    aggregated = compute(aggregate_minute_bars(source,trade_date=DAY,period=1,as_of=now),at='10:35')
    assert {k:direct[k] for k in MINUTE_FEATURES} == {k:aggregated[k] for k in MINUTE_FEATURES}

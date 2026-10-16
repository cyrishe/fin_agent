from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import json
import subprocess
import sys
import time

import pytest

from src.services import stock_indicator_realtime as rt
from src.services.stock_indicator_minute import SHANGHAI

DAY = date(2026,9,28)


def quote(at='10:30:00'):
    return {'code':'600519', 'snapshot_time':f'2026-09-28 {at}', 'close':110, 'open':100,
            'high':120, 'low':90, 'preclose':95, 'avg_price':105}


def calculate(q, at='10:30:20', day=DAY, lag=60):
    return rt.calculate_realtime_snapshot(q, code='600519.SH',
        now=datetime.fromisoformat(f'{day}T{at}').replace(tzinfo=SHANGHAI),
        trade_date=DAY, max_lag_seconds=lag)


def test_snapshot_formula_units_and_undefined_denominators():
    r = calculate(quote())
    assert r['unavailable_reason'] is None
    assert r['lag_seconds'] == 20
    assert r['return_from_open'] == pytest.approx(.1)
    assert r['return_from_preclose'] == pytest.approx(110/95-1)
    assert r['opening_gap'] == pytest.approx(100/95-1)
    assert r['intraday_amplitude'] == pytest.approx(30/95)
    assert r['drawdown_from_high'] == pytest.approx(110/120-1)
    assert r['rebound_from_low'] == pytest.approx(110/90-1)
    assert r['range_position'] == pytest.approx(2/3)
    assert r['avg_price_bias'] == pytest.approx(110/105-1)
    flat = {**quote(), 'high':110, 'low':110, 'preclose':0, 'avg_price':None}
    r = calculate(flat)
    assert r['range_position'] is None and r['return_from_preclose'] is None
    assert r['avg_price_bias'] is None
    assert r['return_from_open'] == pytest.approx(.1)


@pytest.mark.parametrize('q', [None, quote('10:20:00'), quote('10:31:00'),
    {**quote(), 'snapshot_time':'2026-09-25 15:00:00'}, {**quote(), 'close':0}, {**quote(), 'low':115}])
def test_stale_future_missing_or_invalid_prices_never_yield_current_signals(q):
    r = calculate(q)
    assert r['unavailable_reason']
    assert all(r[name] is None for name in rt.REALTIME_FEATURES)


def test_lunch_close_weekend_and_preopen_clocks():
    assert calculate(quote('11:30:00'), at='12:15:00')['lag_seconds'] == 0
    assert calculate(quote('15:00:00'), at='20:00:00')['lag_seconds'] == 0
    assert calculate(quote('15:00:00'), at='12:00:00',day=date(2026,10,1))['lag_seconds'] == 0
    assert calculate(quote('09:25:00'),at='09:29:00')['unavailable_reason']


def test_shared_cache_reuses_overlapping_codes_then_refreshes(tmp_path):
    calls=[];now=[100.]
    def fetch(codes):
        calls.append(codes)
        return {c:quote() for c in codes}
    def get(codes):return rt.cached_quotes(codes,cache_root=tmp_path,fetcher=fetch,epoch=lambda:now[0])
    assert get(['600519.SH'])[1]['external_requests'] == 1
    assert get(['600519.SH','000001.SZ'])[1]['cache_hits'] == 1
    assert calls == [['600519.SH'],['000001.SZ']]
    assert get(['000001.SZ','600519.SH'])[1]['external_requests'] == 0
    now[0] += 5
    assert get(['600519.SH'])[1]['external_requests'] == 1


def test_budget_counts_failed_requests_and_never_resets_on_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr(rt,'REQUESTS_PER_MINUTE',2)
    calls=[]
    def failed(codes):
        calls.append(codes)
        raise TimeoutError('source timeout')
    for code in ('600519.SH','000001.SZ'):
        values, evidence=rt.cached_quotes([code],cache_root=tmp_path,fetcher=failed,epoch=lambda:100.)
        assert values == {} and evidence['external_requests'] == 1 and evidence['fetch_note']
    values,evidence=rt.cached_quotes(['300750.SZ'],cache_root=tmp_path,fetcher=failed,epoch=lambda:100.)
    assert len(calls) == 2 and evidence['external_requests'] == 0 and evidence['fetch_note']
    rt.cached_quotes(['300750.SZ'],cache_root=tmp_path,fetcher=failed,epoch=lambda:161.)
    assert len(calls) == 3
    (tmp_path/'quotes.json').write_text('broken')
    with pytest.raises(RuntimeError,match='cache is unavailable'):
        rt.cached_quotes(['600519.SH'],cache_root=tmp_path,fetcher=failed)
    assert len(calls)==3


def test_concurrent_callers_share_one_source_request(tmp_path):
    calls=[]
    def fetch(codes):
        calls.append(codes);time.sleep(.04)
        return {c:quote() for c in codes}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:rt.cached_quotes(['600519.SH'],cache_root=tmp_path,fetcher=fetch),range(8)))
    assert len(calls)==1
    assert sum(r[1]['external_requests'] for r in results)==1


def test_shared_file_cache_across_python_processes(tmp_path):
    # Each process has its own module lock; the file lock must provide the merge.
    source = '''
from pathlib import Path
import sys,time
from src.services.stock_indicator_realtime import cached_quotes
root=Path(sys.argv[1])
def fetch(codes):
    with (root/'calls.txt').open('a') as f:f.write('request\\n')
    time.sleep(.1)
    return {c:{'snapshot_time':'2026-09-28 10:30:00'} for c in codes}
assert cached_quotes(['600519.SH'],cache_root=root,fetcher=fetch)[0]
'''
    processes=[subprocess.Popen([sys.executable,'-c',source,str(tmp_path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
    for p in processes:
        out,err=p.communicate(timeout=15)
        assert p.returncode==0,err.decode()
    assert (tmp_path/'calls.txt').read_text().splitlines()==['request']

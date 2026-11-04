from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from src.services import stock_technical_history as history
from src.experiments.staged_data_protocol.phase2 import technical_history_provider as provider
from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.services.technical_indicator_calculator import calculate_standard_technical_features


def bars(size=180):
    close = 20 + np.arange(size) * .02 + np.sin(np.arange(size)) * .2
    return pd.DataFrame({'open': close-.1, 'high': close+.3, 'low': close-.3,
        'close': close, 'raw_close': close/2, 'volume': 100000+np.arange(size)*100,
        'turn_ratio': 1.0}, index=pd.bdate_range('2025-01-01', periods=size))


def connection_factory(frame, seen):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=None):
            seen.append((sql, params))
            if params:
                self.selected = frame.loc[str(params[1]):str(params[2])]
        def fetchall(self):
            return self.selected.rename_axis('trade_date').reset_index().to_dict('records')
    class Connection:
        def cursor(self): return Cursor()
    @contextmanager
    def connect(): yield Connection()
    return connect


def test_history_computes_before_cropping_with_same_anchor_and_missing_semantics():
    frame, seen = bars(), []
    frame.iloc[160, frame.columns.get_loc('close')] = np.nan
    result = history.read_history(['000001.SZ'], count=5, as_of=date(2025, 8, 1),
        connection_factory=connection_factory(frame, seen))
    selected = frame.loc[:'2025-08-01']
    expected = calculate_standard_technical_features(selected).tail(5)
    assert len(result['rows']) == 5
    assert result['rows'][-1]['macd_hist'] == pytest.approx(expected.iloc[-1].macd_hist)
    assert result['rows'][-1]['trade_date'] == '2025-08-01'
    assert seen[0][0].endswith('READ ONLY')
    assert seen[1][1] == ('000001.SZ', history.HISTORY_ANCHOR, date(2025, 8, 1))
    assert result['evidence']['price_basis'] == 'hfq'
    short = history.read_history(['000001.SZ'], count=5, as_of=date(2025, 1, 7),
        connection_factory=connection_factory(frame, []))
    assert short['rows'][-1]['macd_hist'] is None


def test_pattern_scan_has_causal_cutoff_price_basis_and_explicit_unknowns():
    frame = bars()
    result = history.read_history(['000001.SZ'], count=10, as_of=date(2025, 8, 1), patterns=True,
        connection_factory=connection_factory(frame, []))
    source = result['evidence']['sources'][0]
    from src.experiments.kline_patterns.catalog import CATALOG
    assert source['pattern_count'] == len(CATALOG) and source['data_as_of'] == '2025-08-01'
    assert source['candidate_count'] == len(result['rows'])
    assert all(row['signal_date'] <= '2025-08-01' for row in result['rows'])
    assert '仅数值' in source['scope']
    assert f"{frame.loc['2025-08-01'].raw_close:.2f}" in source['summary']
    assert result['rows']
    assert all(f"截止日收盘{frame.loc['2025-08-01'].raw_close:.4f}" in row['evidence'] for row in result['rows'])
    assert all("信号日" in row['evidence'] and "截止日" in row['evidence'] for row in result['rows'])
    # Changing later prices cannot change the earlier scan or its evidence.
    future = frame.copy()
    future.loc['2025-08-04':, ['open', 'high', 'low', 'close']] *= 100
    repeated = history.read_history(['000001.SZ'], count=10, as_of=date(2025, 8, 1), patterns=True,
        connection_factory=connection_factory(future, []))
    assert repeated == result
    short = history.read_history(['000001.SZ'], count=2, as_of=date(2025, 1, 2), patterns=True,
        connection_factory=connection_factory(frame, []))
    assert short['evidence']['sources'][0]['unknown_evaluations'] > 0


def test_completed_day_does_not_include_intraday_or_future():
    now = datetime(2026, 9, 30, 10, tzinfo=ZoneInfo('Asia/Shanghai'))
    assert history.completed_cutoff(None, now=now) == date(2026, 9, 29)
    assert history.completed_cutoff(date(2030, 1, 1), now=now) == date(2026, 9, 29)
    assert history.completed_cutoff(date(2025, 1, 1), now=now) == date(2025, 1, 1)


@pytest.mark.parametrize('codes,count,patterns', [([], 10, False), (['000001'], 10, False),
    (['000001.SZ']*6, 10, False), (['000001.SZ'], 253, False), (['000001.SZ'], 61, True),
    (['000001.SZ'], True, False)])
def test_invalid_scope_rejected_before_io(codes, count, patterns):
    with pytest.raises(ValueError):
        history.read_history(codes, count=count, patterns=patterns,
            connection_factory=lambda: pytest.fail('must validate before IO'))


def test_catalog_provider_normal_compatible_and_invalid_contract(monkeypatch):
    seen = []
    def read(codes, **kwargs):
        seen.append((codes, kwargs))
        return {'rows': [{'code': '000001.SZ', 'trade_date': '2025-08-01', 'ma20': 23.0},
                         {'code': '600036.SH', 'trade_date': '2025-08-01', 'ma20': 45.0}],
                'evidence': {'price_basis': 'hfq', 'sources': [{'input_rows': 2000}]}}
    monkeypatch.setattr(provider, 'read_history', read)
    for api in ('stock.technical_series.query', 'stock.technical_series'):
        call = parse_api_call(f'r1 = {api}(codes=["000001.SZ","600036.SH"], count=2, filter="ma20 > 30", order="ma20 desc") -> code, ma20')
        assert validate_call(call, {}).ok
        result = execute_api_call(call).data
        assert result['rows'] == [{'code': '600036.SH', 'ma20': 45.0}]
        assert result['evidence']['price_basis'] == 'hfq'
    call = parse_api_call('r1 = stock.kline_patterns.query(codes=["000001.SZ"]) -> code, signal_date, evidence')
    assert validate_call(call, {}).ok
    for request in ('r1 = stock.technical_series.query() -> code',
                    'r1 = stock.kline_patterns.query(codes=["000001.SZ"]) -> win_probability',
                    'r1 = stock.technical_series.query(codes=["000001.SZ"], publish=True) -> code'):
        assert not validate_call(parse_api_call(request), {}).ok

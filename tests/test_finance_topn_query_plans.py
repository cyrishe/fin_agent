from datetime import date
import pytest
from src.experiments.staged_data_protocol.phase2 import quote_provider as q
from src.experiments.staged_data_protocol.phase2 import intraday_quote_provider as m
from src.experiments.staged_data_protocol.phase2 import pricevalue_provider as pv
from src.experiments.staged_data_protocol.phase2 import financial_provider as fin


@pytest.mark.parametrize('provider,execute', [(pv, pv.execute_pricevalue_api), (fin, fin.execute_financial_3_table_api)])
@pytest.mark.parametrize('limit,expected', [(-1, 600), (10, 10), (None, 100), (0, 100), (1000, 500)])
def test_explicit_all_limit_preserves_full_saved_table(monkeypatch, provider, execute, limit, expected):
    class DB:
        def __init__(self, **_): self.conn = self
        def cursor(self, *_): return self
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def close_db(self): pass
        def execute(self, sql, params):
            assert sql.count('%s') == len(params)
            self.count = params[-1] if sql.rstrip().endswith('LIMIT %s') else 600
            assert self.count == expected
        def fetchall(self): return [{'code': f'{n:06d}.SZ'} for n in range(self.count)]
    monkeypatch.setattr(provider, 'StockInfoDbUtils', DB)
    result = execute(subject='stock', args={'limit': limit}, outputs=['code'])
    assert result['status'] == 'ok'
    assert len(result['rows']) == expected


def test_financial_postprocessing_all_does_not_drop_last_row(monkeypatch):
    class DB:
        def __init__(self, **_): self.conn = self
        def cursor(self, *_): return self
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def close_db(self): pass
        def execute(self, sql, params):
            assert sql.count('%s') == len(params)
            assert not sql.rstrip().endswith('LIMIT %s')
        def fetchall(self): return [{'code': f'{n:06d}.SZ'} for n in range(600)]
    monkeypatch.setattr(fin, 'StockInfoDbUtils', DB)
    monkeypatch.setattr(fin, '_computed_order_field', lambda args: 'test_computed')
    monkeypatch.setattr(fin, '_computed_columns_for_request', lambda **kwargs: [])
    result = fin.execute_financial_3_table_api(subject='stock', args={'limit': -1}, outputs=['code'])
    assert result['status'] == 'ok'
    assert len(result['rows']) == 600


@pytest.mark.parametrize('provider,execute,field', [(q, q.execute_kd_quote_api, 'pct'), (pv, pv.execute_kd_pricevalue_api, 'pe')])
@pytest.mark.parametrize('limit,expected', [(-1, 600), (10, 10), (None, 100)])
def test_window_full_result_or_ordered_topn_is_computed_without_model_filtering(monkeypatch, provider, execute, field, limit, expected):
    class DB:
        def __init__(self, **_): self.conn = self
        def cursor(self, *_): return self
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def close_db(self): pass
        def execute(self, sql, params): assert sql.count('%s') == len(params)
        def fetchall(self):
            return [{'code': f'{n:06d}.SZ', 'name': str(n), 'value': n, 'window_count': 60} for n in range(600)]
    monkeypatch.setattr(provider, 'StockInfoDbUtils', DB)
    result = execute(subject='stock', field=field, method='sum', args={'k': 60, 'limit': limit, 'order': 'value desc'}, outputs=['code', 'value'])
    assert result['status'] == 'ok'
    assert len(result['rows']) == expected
    assert result['rows'][0]['value'] == 599


@pytest.mark.parametrize('subject', list(q.QUOTE_SOURCES))
def test_daily_single_code_uses_bounded_read_before_global_order(subject):
    source=q.QUOTE_SOURCES[subject]
    args={'filter':'code = 600519.SH and pct > 0','count':10,'order':'pct desc'}
    where,_=q._build_where(source=source,args=args)
    sql=q._build_per_entity_sql(source=source,fields=['code','tradedate','pct'],where_sql=where,args=args)
    assert 'ROW_NUMBER()' not in sql
    assert 'LATERAL' not in sql
    assert sql.index(source.fields['pct'] + ' > %s') < sql.index('LIMIT %s')
    assert sql.index('LIMIT %s') < sql.index('ORDER BY `__order_value`')


@pytest.mark.parametrize('subject', list(q.QUOTE_SOURCES))
def test_daily_multi_code_and_or_do_not_collapse_to_one_security(subject):
    for args in [{'codes':['600519.SH','000858.SZ'],'count':10},
                 {'filter':'code = 600519.SH or pct > 0','count':10}]:
        source=q.QUOTE_SOURCES[subject]
        where,_=q._build_where(source=source,args=args)
        sql=q._build_per_entity_sql(source=source,fields=['code','tradedate'],where_sql=where,args=args)
        assert 'JOIN LATERAL' in sql
        assert 'identities.' + source.fields['code'].split('.')[-1] in sql
        # OR filters cannot escape the correlation to the current security.
        assert 'WHERE (' + where + ')' in sql
        assert 'ROW_NUMBER()' not in sql
        assert 'MAX(trade_date)' not in sql  # must retain suspended securities' own latest rows


def test_explicit_historical_filter_is_not_combined_with_implicit_latest_date():
    for subject in ['stock','index','fund']:
        source=q.QUOTE_SOURCES[subject]
        where,params=q._build_where(source=source,args={'filter':'tradedate <= 2026-08-07'})
        assert 'MAX(' not in where
        assert '2026-08-07' in params
        where,_=q._build_where(source=source,args={'filter':'pct > 5'})
        assert 'MAX(' in where


@pytest.mark.parametrize('subject', list(q.QUOTE_SOURCES))
def test_multi_security_scope_bindings_are_separate_from_full_filters(subject):
    source=q.QUOTE_SOURCES[subject]
    args={'codes':['600519.SH','300750.SZ'],'count':10}
    scope,params=q._count_identity_scope(source=source,args=args)
    assert 'IN (%s, %s)' in scope
    assert params==['600519.SH','300750.SZ']
    assert q._count_identity_scope(source=source,args={'filter':'code = 600519.SH or pct > 0','count':10})==('',[])
    scope, params = q._count_identity_scope(source=source, args={'filter': 'code = 600519.SH or code = 300750.SZ', 'count': 10})
    assert ' OR ' in scope
    assert params == ['600519.SH', '300750.SZ']
    assert q._count_identity_scope(source=source, args={'filter': 'code = 600519.SH or name like 银行', 'count': 10}) == ('', [])


@pytest.mark.parametrize('subject', list(q.QUOTE_SOURCES))
@pytest.mark.parametrize('args', [
    {'code': '600519.SH', 'count': 20},
    {'codes': ['600519.SH', '300750.SZ'], 'count': 20, 'limit': 5},
    {'filter': 'name = 贵州茅台', 'count': 20},
    {'filter': 'code = 600519.SH or pct > 0', 'count': 1, 'date': '2025-01-02'},
    {'codes': ['600519.SH'], 'count': 20, 'start': '2025-01-01', 'end': '2025-01-31'},
])
def test_quote_execution_bindings_stay_aligned_for_every_subject(monkeypatch, subject, args):
    monkeypatch.setenv('FIN_AGENT_RECENT_SCAN_ENABLED', '0')
    captured = {}
    class DB:
        def __init__(self, **_): self.conn = self
        def cursor(self, *_): return self
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def close_db(self): pass
        def execute(self, sql, params): captured.update(sql=sql, params=params)
        def fetchall(self): return []
    monkeypatch.setattr(q, 'StockInfoDbUtils', DB)
    result = q.execute_quote_api(subject=subject, args=args, outputs=['code', 'close'])
    assert result['status'] == 'ok'
    assert captured['sql'].count('%s') == len(captured['params'])
    assert captured['params'][-2] == args['count']
    assert result['columns'] == ['code', 'close']


@pytest.mark.parametrize('subject', list(q.QUOTE_SOURCES))
def test_market_date_slice_uses_date_index_scan_not_per_security_lookups(subject):
    source = q.QUOTE_SOURCES[subject]
    for args in [{'date': '2026-09-04', 'count': 1},
                 {'start': '2025-01-01', 'end': '2025-01-31', 'count': 20},
                 {'filter': 'tradedate <= 2025-01-31', 'count': 20}]:
        where, _ = q._build_where(source=source, args=args)
        sql = q._build_per_entity_sql(source=source, fields=['code', 'tradedate'], where_sql=where, args=args)
        assert 'ROW_NUMBER()' in sql
        assert 'JOIN LATERAL' not in sql
        assert where in sql


def test_minute_count_and_limit_are_independent_and_keys_are_selected_first(monkeypatch):
    captured={}
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def execute(self,sql,params):captured.update(sql=sql,params=params)
        def fetchall(self):return []
    class DB:
        conn=None
        def __init__(self,**_):self.conn=self
        def cursor(self,*_):return Cursor()
        def close_db(self):pass
    monkeypatch.setattr(m,'StockInfoDbUtils',DB)
    r=m.execute_intraday_quote_api(args={'mode':1,'period':1,'limit':20,'order':'volumn desc'},outputs=['code','volumn'])
    assert r['status']=='ok'
    assert captured['params'][-2:]==(240,20)
    assert 'SELECT code, kline_type, tradedate, bar_end_time' in captured['sql']
    assert 'ROW_NUMBER()' not in captured['sql']
    m.execute_intraday_quote_api(args={'mode':1,'count':1,'limit':-1},outputs=['code'])
    assert captured['params'][-2:]==(1,m._intraday_hard_row_limit()+1)
    m.execute_intraday_quote_api(args={'mode':1,'count':10,'filter':'tradedate >= 2026-08-01'},outputs=['code'])
    assert captured['params'][0]=='2026-08-01'
    assert captured['sql'].index('s.trade_date >= %s') < captured['sql'].index('LIMIT %s')


def test_minute_rejects_explicit_result_limit_above_existing_safety_ceiling():
    r=m.execute_intraday_quote_api(args={'mode':1,'count':10,'limit':m._intraday_hard_row_limit()+1},outputs=['code'])
    assert r['status']=='result_too_large'

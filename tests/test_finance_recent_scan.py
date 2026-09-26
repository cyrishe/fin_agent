from datetime import date

import pytest

from src.experiments.staged_data_protocol.phase2 import recent_scan as r


@pytest.fixture(autouse=True)
def enable_probe(monkeypatch):
    monkeypatch.setenv('FIN_AGENT_RECENT_SCAN_ENABLED', '1')


class Cursor:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        value = next(self.pages)
        if isinstance(value, Exception):
            raise value
        return value


def test_three_calendar_months_and_disable(monkeypatch):
    assert r.recent_predicate(args={}, date_expression='q.date', today=date(2026, 5, 31)) == "q.date >= '2026-02-28'"
    assert r.recent_predicate(args={}, date_expression='q.date', today=date(2024, 5, 31)) == "q.date >= '2024-02-29'"
    monkeypatch.setenv('FIN_AGENT_RECENT_SCAN_ENABLED', '0')
    assert r.recent_predicate(args={}, date_expression='q.date') is None


@pytest.mark.parametrize('args', [{'start': '2020-01-01'}, {'end': '2020-01-01'},
    {'date': '2020-01-01'}, {'filter': 'tradedate < 2020-01-01'}, {'forecast_year': 2027},
    {'report_period': '2020-12-31'}, {'limit': -1}, {'as_of': '2020-01-01'}])
def test_explicit_scope_and_full_queries_are_not_narrowed(args):
    assert r.recent_predicate(args=args, date_expression='q.date') is None


@pytest.mark.parametrize('order', ['q.date ASC', 'q.price DESC', 'q.code ASC, q.date DESC'])
def test_global_ranking_not_chronological_topn(order):
    assert r.chronological_recent_where(args={}, where_sql='1=1', order_sql=order,
                                         date_expression='q.date') is None


def test_recent_enough_returns_one_read_and_short_probe_is_discarded():
    recent = [{'value': 1}, {'value': 2}]
    c = Cursor([recent])
    assert r.fetch_recent_first(c, sql='full', params=[2], recent_sql='recent', required_rows=2) == recent
    assert len(c.calls) == 1
    full = [{'value': 10}, {'value': 20}]
    c = Cursor([[{'value': 999}], full])
    evidence = {}
    assert r.fetch_recent_first(c, sql='full', params=[2], recent_sql='recent', required_rows=2, evidence=evidence) == full
    assert [x[0] for x in c.calls] == ['recent', 'full']
    assert evidence == {'recent_months': 3, 'sql_reads': 2}


def test_per_security_coverage_not_total_rows_and_empty_full_is_valid():
    c = Cursor([[{'code': 'A'}, {'code': 'A'}], [{'code': 'A'}, {'code': 'B'}]])
    result = r.fetch_recent_first(c, sql='full', params=[], recent_sql='recent',
                                  required_codes=['A', 'B'], per_code=1)
    assert result == [{'code': 'A'}, {'code': 'B'}]
    assert len(c.calls) == 2
    c = Cursor([[], []])
    assert r.fetch_recent_first(c, sql='full', params=[], recent_sql='recent', required_rows=1) == []
    assert len(c.calls) == 2


def test_db_error_is_not_retried_as_insufficient_history():
    c = Cursor([RuntimeError('timeout')])
    with pytest.raises(RuntimeError, match='timeout'):
        r.fetch_recent_first(c, sql='full', params=[], recent_sql='recent', required_rows=2)
    assert len(c.calls) == 1


def test_quote_keeps_hidden_coverage_key_out_of_raw_output(monkeypatch):
    from src.experiments.staged_data_protocol.phase2 import quote_provider as q
    c = Cursor([[{'code': '600519.SH', 'close': 1}, {'code': '000858.SZ', 'close': 2}]])
    class DB:
        def __init__(self, **_): self.conn = self
        def cursor(self, *_): return self
        def __enter__(self): return c
        def __exit__(self, *_): pass
        def close_db(self): pass
    monkeypatch.setattr(q, 'StockInfoDbUtils', DB)
    result = q.execute_quote_api(subject='stock', args={'codes': ['600519.SH', '000858.SZ'], 'count': 1}, outputs=['close'])
    assert result['rows'] == [{'close': 1}, {'close': 2}]
    assert len(c.calls) == 1
    assert 'AND q.trade_date >=' in c.calls[0][0]


def test_quote_global_output_limit_cannot_prove_per_security_coverage(monkeypatch):
    from src.experiments.staged_data_protocol.phase2 import quote_provider as q
    c = Cursor([[{'code': '600519.SH', 'close': 1}]])
    class DB:
        def __init__(self, **_): self.conn = self
        def cursor(self, *_): return self
        def __enter__(self): return c
        def __exit__(self, *_): pass
        def close_db(self): pass
    monkeypatch.setattr(q, 'StockInfoDbUtils', DB)
    q.execute_quote_api(subject='stock', args={'codes': ['600519.SH', '000858.SZ'], 'count': 2, 'limit': 1}, outputs=['close'])
    assert len(c.calls) == 1
    assert 'q.trade_date >=' not in c.calls[0][0]


def test_minute_normalizes_codes_and_keeps_probe_before_topn(monkeypatch):
    from src.experiments.staged_data_protocol.phase2 import intraday_quote_provider as m
    c = Cursor([[{'code': '600519', 'close': 1}, {'code': '300750', 'close': 2}]])
    class DB:
        def __init__(self, **_): self.conn = self
        def cursor(self, *_): return self
        def __enter__(self): return c
        def __exit__(self, *_): pass
        def close_db(self): pass
    monkeypatch.setattr(m, 'StockInfoDbUtils', DB)
    result = m.execute_intraday_quote_api(args={'mode': 1, 'count': 1,
        'filter': 'code in (600519.SH,300750.SZ)'}, outputs=['close'])
    assert result['rows'] == [{'close': 1}, {'close': 2}]
    assert len(c.calls) == 1
    assert c.calls[0][0].index('s.trade_date >=') < c.calls[0][0].index('LIMIT %s')
    assert result['sql_shape']['recent_scan']['sql_reads'] == 1


def test_chronological_probe_preserves_or_grouping():
    where = r.chronological_recent_where(args={}, where_sql='code = %s OR code = %s',
        order_sql='r.date DESC, r.id DESC', date_expression='r.date')
    assert where.startswith('(code = %s OR code = %s) AND r.date >=')


def test_default_is_cost_gated_and_global_switch_can_disable(monkeypatch):
    monkeypatch.delenv('FIN_AGENT_RECENT_SCAN_ENABLED')
    assert r.recent_predicate(args={}, date_expression='q.date') is None
    assert r.recent_predicate(args={}, date_expression='q.date', enabled_by_default=True)
    monkeypatch.setenv('FIN_AGENT_RECENT_SCAN_ENABLED', '0')
    assert r.recent_predicate(args={}, date_expression='q.date', enabled_by_default=True) is None


def test_corporate_limit_has_stable_tie_order_without_exposing_internal_fields():
    from src.experiments.staged_data_protocol.phase2 import stock_corporate_provider as c
    view = c.STOCK_CORPORATE_VIEWS['business_segment']
    sql = c._build_sql(view=view, query_columns=['code', 'project_name', 'report_period'],
        columns=['code', 'project_name'], where_sql='1=1', order_sql='u.`report_period` DESC')
    assert 'ORDER BY u.`report_period` DESC, BINARY u.`code` ASC, BINARY u.`project_name` ASC' in sql
    assert 'SELECT u.`code` AS `code`, u.`project_name` AS `project_name`' in sql


def test_calendar_window_filters_fixed_dates_instead_of_backfilling_per_security():
    from src.experiments.staged_data_protocol.phase2 import quote_provider as q
    for source in q.QUOTE_SOURCES.values():
        sql = q._build_kd_base_rows_sql(source=source, field='close', identity_sql='')
        assert "IN (SELECT trade_date FROM kd_dates)" in sql
        assert 'trade_seq' in sql
        assert 'ROW_NUMBER()' not in sql


def test_explicit_daily_range_is_unchanged_by_probe_switch(monkeypatch):
    from src.experiments.staged_data_protocol.phase2 import quote_provider as q
    args = {'codes': ['600519.SH'], 'count': 20, 'start': '2025-01-01', 'end': '2025-01-31'}
    c = Cursor([[{'code': '600519.SH', 'tradedate': '2025-01-27'}]])
    class DB:
        def __init__(self, **_): self.conn = self
        def cursor(self, *_): return self
        def __enter__(self): return c
        def __exit__(self, *_): pass
        def close_db(self): pass
    monkeypatch.setattr(q, 'StockInfoDbUtils', DB)
    result = q.execute_quote_api(subject='stock', args=args, outputs=['code', 'tradedate'])
    assert len(result['rows']) == 1
    assert len(c.calls) == 1
    assert 'q.trade_date BETWEEN %s AND %s' in c.calls[0][0]
    assert '2025-01-01' in c.calls[0][1] and '2025-01-31' in c.calls[0][1]
    assert result['sql_shape']['recent_scan'] == {}


@pytest.mark.parametrize('subject, expected_probe', [('stock', False), ('index', False), ('fund', False), ('bond', False), ('plate', False)])
def test_default_quote_probe_follows_physical_template(monkeypatch, subject, expected_probe):
    from src.experiments.staged_data_protocol.phase2 import quote_provider as q
    monkeypatch.delenv('FIN_AGENT_RECENT_SCAN_ENABLED')
    c = Cursor([[{'code': 'test', 'close': 1}]])
    class DB:
        def __init__(self, **_): self.conn = self
        def cursor(self, *_): return self
        def __enter__(self): return c
        def __exit__(self, *_): pass
        def close_db(self): pass
    monkeypatch.setattr(q, 'StockInfoDbUtils', DB)
    result = q.execute_quote_api(subject=subject, args={'code': 'test', 'count': 1}, outputs=['close'])
    assert result['status'] == 'ok'
    assert bool(result['sql_shape']['recent_scan']) == expected_probe
    assert result['rows'] == [{'close': 1}]

from contextlib import contextmanager
from datetime import date

from src.experiments.staged_data_protocol.phase2 import technical_provider as provider
from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.models import ResultHandle


def setup_daily(monkeypatch):
    @contextmanager
    def connection():
        yield object()
    monkeypatch.setattr(provider, 'market_connection', connection)
    monkeypatch.setattr(provider, 'read_daily_indicators', lambda *a, **k: {'rows': [
        {'stk_code': '600519.SH', 'trade_date': date(2026,9,28), 'volume_ratio5': 2.0, 'ma20': 100, 'batch_id': 'b1'},
        {'stk_code': '000001.SZ', 'trade_date': date(2026,9,28), 'volume_ratio5': None, 'ma20': 10, 'batch_id': 'b1'},
        {'stk_code': '300750.SZ', 'trade_date': date(2026,9,28), 'volume_ratio5': 3.0, 'ma20': 300, 'batch_id': 'b1'},
    ], 'formula_revision': 'technical_daily_v1'})


def test_catalog_routing_filter_order_and_reference(monkeypatch):
    setup_daily(monkeypatch)
    call = parse_api_call('r2 = stock.technical.query(filter="code in r1.code and (volume_ratio5 > 1 or ma20 < 20)", order="volume_ratio5 desc", limit=-1) -> code, trade_date, volume_ratio5')
    previous = {'r1': ResultHandle(name='r1', api='stock.base_info.query', columns=['code'],
        data={'rows': [{'code':'600519.SH'}, {'code':'000001.SZ'}]})}
    assert validate_call(call, previous).ok
    result = execute_api_call(call, previous).data
    assert result['status'] == 'ok'
    assert result['rows'] == [
        {'code':'600519.SH', 'trade_date':'2026-09-28', 'volume_ratio5':2.0},
        {'code':'000001.SZ', 'trade_date':'2026-09-28', 'volume_ratio5':None}]
    assert result['evidence']['batch_ids'] == ['b1']
    # Existing two-part compatibility form is provided by the common catalog.
    compatible = parse_api_call('r1 = stock.technical(limit=1) -> code, ma20')
    assert validate_call(compatible, {}).ok
    assert execute_api_call(compatible).data['row_count'] == 1


def test_minute_routes_existing_result_with_freshness(monkeypatch):
    monkeypatch.setattr(provider, 'read_minute_indicators', lambda **k: {
        'rows':[{'code':'600519.SH', 'data_as_of':'2026-09-28T10:30:00+08:00', 'lag_seconds':600, 'ma20':100}],
        'external_requests':0})
    call = parse_api_call('r1 = stock.technical_minute.query(codes=["600519.SH"], period=5) -> code, data_as_of, lag_seconds, ma20')
    assert validate_call(call, {}).ok
    result = execute_api_call(call).data
    assert result['rows'][0]['lag_seconds'] == 600
    assert result['evidence']['external_requests'] == 0


def test_partial_minute_and_realtime_catalog_paths(monkeypatch):
    received=[]
    def minute(**kwargs):
        received.append(kwargs)
        return {'rows':[{'code':'600519.SH','is_finalized':False,'session_rsi14':None,'unavailable_reason':None}]}
    monkeypatch.setattr(provider,'read_minute_indicators',minute)
    call=parse_api_call('r1 = stock.technical_minute.query(codes=["600519.SH"], period=5, include_partial=True, max_lag_seconds=60) -> code, is_finalized, session_rsi14, unavailable_reason')
    assert validate_call(call,{}).ok
    assert execute_api_call(call).data['rows'][0]['is_finalized'] is False
    assert received[0]['include_partial'] is True and received[0]['max_lag_seconds']==60
    monkeypatch.setattr(provider,'read_realtime_indicators',lambda **k:{'rows':[
        {'code':'600519.SH','data_as_of':'2026-09-28T10:30:00+08:00','lag_seconds':10,'return_from_open':.1,'unavailable_reason':None}]})
    call=parse_api_call('r1 = stock.technical_realtime.query(codes=["600519.SH"]) -> code, data_as_of, lag_seconds, return_from_open, unavailable_reason')
    assert validate_call(call,{}).ok
    assert execute_api_call(call).data['rows'][0]['return_from_open']==.1
    bad=parse_api_call('r1 = stock.technical_realtime.query(codes=["600519.SH"], as_of="2026-09-20") -> code, return_from_open')
    assert not validate_call(bad,{}).ok


def test_invalid_contract_and_provider_errors(monkeypatch):
    for expression in (
        'r1 = stock.technical_minute.query() -> code, ma20',
        'r1 = stock.technical_minute.query(codes=["600519.SH"]) -> code, macd_dif',
        'r1 = stock.technical.query(limit=0) -> code, ma20',
    ):
        assert not validate_call(parse_api_call(expression), {}).ok
    setup_daily(monkeypatch)
    result = provider.execute_technical_api(minute=False, args={'codes': ['000001']}, outputs=['code'])
    assert result['status'] == 'provider_error'
    result = provider.execute_technical_api(minute=False, args={'filter':"__import__('os')"}, outputs=['code'])
    assert result['status'] == 'provider_error'


def test_minute_window_catalog_route_preserves_all_rows_and_context(monkeypatch):
    seen=[]
    def window(**kwargs):
        seen.append(kwargs)
        return {'rows':[{'code':'600519.SH','bar_end_time':str(i),'close':100,'ma20':99}
                        for i in range(240)],'snapshots':[{'lag_seconds':600}], 'external_requests':0}
    monkeypatch.setattr(provider,'read_minute_window',window)
    call=parse_api_call('r1 = stock.technical_minute_series.query(codes=["600519.SH"], period=1, since="2026-09-28T10:00:00+08:00") -> code, bar_end_time, close, ma20')
    assert validate_call(call,{}).ok
    data=execute_api_call(call).data
    assert data['status']=='ok' and len(data['rows'])==240
    assert data['evidence']['snapshots'][0]['lag_seconds']==600
    assert seen[0]['since'].hour==10
    assert not validate_call(parse_api_call('r1 = stock.technical_minute_series.query(codes=["600519.SH"], include_partial=True) -> code, ma20'),{}).ok


def test_technical_field_meanings_are_descriptions_in_shared_catalog():
    from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
    catalog = FinanceDataToolCatalogService()
    samples = {
        'technical': ('ma20', '收盘价均线'),
        'technical_minute': ('session_macd_hist', '当日MACD柱'),
        'technical_minute_series': ('volume_ratio5', '当前成交量'),
        'technical_realtime': ('range_position', '日内区间位置'),
    }
    for view, (field, phrase) in samples.items():
        contract = catalog.get_model_dataview('stock', view, 'query')
        assert phrase in contract['fields'][field]['desc']
        assert contract['fields'][field].get('aliases', []) == []
        assert contract['functions'][0]['api_name'] == f'stock.{view}.query'


def test_implemented_indicator_fields_are_discoverable_with_meaning():
    from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
    from src.services.technical_indicator_calculator import STANDARD_TECHNICAL_FEATURES
    from src.services.stock_indicator_minute import MINUTE_FEATURES
    from src.services.stock_indicator_realtime import REALTIME_FEATURES
    catalog = FinanceDataToolCatalogService()
    for view, implemented in (
        ('technical', STANDARD_TECHNICAL_FEATURES),
        ('technical_minute', MINUTE_FEATURES),
        ('technical_minute_series', MINUTE_FEATURES),
        ('technical_realtime', REALTIME_FEATURES),
    ):
        fields = catalog.get_model_dataview('stock', view, 'query')['fields']
        assert set(implemented).issubset(fields), view
        assert all(fields[name].get('desc') for name in implemented), view


def test_daily_indicator_window_uses_published_dates_and_non_null_values(monkeypatch):
    from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
    from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
    @contextmanager
    def connection():
        yield object()
    seen = []
    def read(*_a, **kwargs):
        seen.append(kwargs)
        return {'rows': [
            {'stk_code':'600519.SH','trade_date':date(2026,9,24),'volume_ratio5':1.0,'batch_id':'b24'},
            {'stk_code':'600519.SH','trade_date':date(2026,9,25),'volume_ratio5':None,'batch_id':'b25'},
            {'stk_code':'600519.SH','trade_date':date(2026,9,28),'volume_ratio5':3.0,'batch_id':'b28'},
        ],'price_basis':'hfq','formula_revision':'technical_daily_v1'}
    monkeypatch.setattr(provider,'market_connection',connection)
    monkeypatch.setattr(provider,'read_daily_indicators',read)
    call = parse_api_call('r1 = stock.technical.kd_volume_ratio5_avg(codes=["600519.SH"], k=3, as_of="2026-09-28") -> code, trade_date, value as avg3, current_value, window_count')
    assert validate_call(call,{}).ok
    result = execute_api_call(call).data
    assert result['rows'] == [{'code':'600519.SH','trade_date':'2026-09-28','avg3':2.0,'current_value':3.0,'window_count':2}]
    assert seen[0]['count'] == 3 and seen[0]['limit'] == 60
    assert result['evidence']['batch_ids'] == ['b24','b25','b28']
    service = FinanceDataToolCatalogService()
    assert service.get_model_dataview('stock','technical','window')['functions'][0]['api_name'] == 'stock.technical.kd_<field>_<method>'
    assert resolve_api('stock.quote.kd_pct_avg')['dataview'] == 'quote'
    assert resolve_api('stock.technical_minute_series.agg') is None


def test_daily_indicator_aggregate_is_one_published_cross_section(monkeypatch):
    setup_daily(monkeypatch)
    call = parse_api_call('r1 = stock.technical.agg(agg=avg(stock.technical.volume_ratio5), filter="ma20 > 20") -> avg_ratio')
    assert validate_call(call,{}).ok
    result = execute_api_call(call).data
    assert result['rows'] == [{'avg_ratio':2.5}]
    assert result['evidence']['trade_date'] == '2026-09-28'
    assert result['evidence']['selected_count'] == 2
    assert result['evidence']['sample_count'] == 2
    assert result['evidence']['batch_ids'] == ['b1']
    count = parse_api_call('r1 = stock.technical.agg(agg=count(stock.technical.code), filter="volume_ratio5 is not None") -> stock_count')
    assert validate_call(count,{}).ok
    assert execute_api_call(count).data['rows'] == [{'stock_count':2}]
    selected = ResultHandle(name='r0',api='stock.base_info.query',columns=['code'],
        data={'rows':[{'code':'600519.SH'},{'code':'000001.SZ'}]})
    chained = parse_api_call('r1 = stock.technical.agg(agg=count(stock.technical.code), filter="code in r0.code") -> selected_count')
    assert validate_call(chained,{'r0':selected}).ok
    assert execute_api_call(chained,{'r0':selected}).data['rows'] == [{'selected_count':2}]
    assert not validate_call(parse_api_call('r1 = stock.technical.agg(agg=avg(stock.quote.pct)) -> average'),{}).ok


def test_daily_indicator_window_and_aggregate_reject_invalid_scope(monkeypatch):
    setup_daily(monkeypatch)
    cases = [
        'r1 = stock.technical.kd_ma20_avg(k=5) -> code, value',
        'r1 = stock.technical.kd_ma20_avg(codes=["600519.SH"], k=0) -> code, value',
        'r1 = stock.technical.kd_code_avg(codes=["600519.SH"], k=5) -> code, value',
        'r1 = stock.technical.agg(agg=avg(stock.technical.code)) -> average',
        'r1 = stock.technical.agg(agg=avg(stock.technical.volume_ratio5), group_by="trade_date") -> trade_date, average',
    ]
    for case in cases:
        assert not validate_call(parse_api_call(case),{}).ok, case
    @contextmanager
    def connection():
        yield object()
    monkeypatch.setattr(provider,'market_connection',connection)
    monkeypatch.setattr(provider,'read_daily_indicators',lambda *_a,**_k:{'rows':[
        {'stk_code':'600519.SH','trade_date':date(2026,9,25),'volume_ratio5':1,'batch_id':'b25'},
        {'stk_code':'600519.SH','trade_date':date(2026,9,28),'volume_ratio5':2,'batch_id':'b28'},
    ]})
    data = execute_api_call(parse_api_call('r1 = stock.technical.agg(agg=avg(stock.technical.volume_ratio5)) -> average')).data
    assert data['status'] == 'provider_error'
    assert 'multiple trade dates' in data['reason']

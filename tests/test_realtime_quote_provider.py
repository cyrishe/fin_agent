from copy import deepcopy

import pytest
import requests

from src.experiments.staged_data_protocol.phase2 import api_runner
from src.experiments.staged_data_protocol.phase2 import realtime_quote_provider as live
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.models import ApiCall, ResultHandle
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService


def quote(code='600519', name='贵州茅台', price=100, upper=110, lower=90, pct=0, time=150000):
    return {'sCode': code, 'sName': name, 'shtPrecise': 2,
            'stSimHq': {'fNowPrice': price, 'fClose': 100, 'fOpen': 100, 'fHigh': price,
                        'fLow': 95, 'fChgRatio': pct, 'fChgValue': price - 100,
                        'fAmount': 20000, 'lVolume': 200, 'fZhenfu': 5},
            'stExHq': {'iTradeDate': 20260907, 'iTradeTime': time, 'fZTPrice': upper,
                       'fDTPrice': lower, 'fAveragePrice': 100, 'fTurnoverRate': 1}}


@pytest.fixture
def quotes(monkeypatch):
    data = [quote('600519', pct=1), quote('300308', '中际旭创', price=120, upper=120.000001, lower=80, pct=20),
            quote('920821', '则成电子', price=23.59, upper=23.590001, lower=12.710001, pct=29.97)]
    monkeypatch.setattr(live, '_load_securities', lambda tree: [{'code': r['sCode'], 'setcode': 1} for r in data])
    monkeypatch.setattr(live, '_fetch_batch', lambda batch: [deepcopy(r) for r in data if r['sCode'] in {s['code'] for s in batch}])
    return data


@pytest.mark.parametrize('price,upper,lower,expected', [
    (10, 11, 9, 0), (11, 11.000001, 9, 1), (9, 11, 9.000001, 2),
    (23.59, 23.590001, 12.710001, 1), (250, 0, 0, 0), (0, 11, 9, None),
    (10, None, None, None), (10.99, 11, 9, 0),
])
def test_limit_prices_are_from_source_not_stock_code_rules(price, upper, lower, expected):
    assert live._normalize_quote(quote(price=price, upper=upper, lower=lower))['is_limit_price'] == expected


def test_source_time_aliases_and_unknown_bar_metadata():
    row = live._normalize_quote(quote(time=105000))
    assert row['snapshot_time'] == '2026-09-07 10:50:00'
    assert row['tradedate'] == row['trade_date'] == '2026-09-07'
    assert row['close'] == row['latest_price'] == 100
    assert row['volumn'] == row['volume'] == 200
    assert row['bar_end_time'] is None  # An API tick is not a fabricated K bar.
    assert row['minute_amount'] is None


@pytest.mark.parametrize('filter_text,expected', [
    ("(code == '600519.SH' or pct >= 20) and is_limit_price == 1", ['300308', '920821']),
    ("code in ['300308.SZ', '600519.SH'] and pct < 2", ['600519']),
    ("'中际' in name", ['300308']),
    ("name like '%中际%'", ['300308']),
    ("name like '中际'", []),
    ("code = 600519.SH", ['600519']),
    ("tradedate == '2026-09-06'", []),
    ("is_limit_price is None", []),
    ("code not in ['600519.SH']", ['300308', '920821']),
    ("False", []),
])
def test_current_and_legacy_filters_use_same_boolean_meaning(quotes, filter_text, expected):
    result = live.execute_realtime_quote_api(args={'filter': filter_text, 'limit': -1}, outputs=['code'])
    assert result['status'] == 'ok', result
    assert [row['code'] for row in result['rows']] == expected


def test_filter_sort_limit_and_output_alias_happen_after_fetch(quotes):
    result = live.execute_realtime_quote_api(args={'filter': 'pct > 10', 'order': 'pct desc', 'limit': 1}, outputs=['name', 'close as price'])
    assert result['rows'] == [{'name': '则成电子', 'price': 23.59}]
    assert result['diagnostics']['received_count'] == 3


@pytest.mark.parametrize('method,expected', [('count', 3), ('sum', 50.97), ('avg', 16.99), ('median', 20), ('max', 29.97), ('min', 1)])
def test_aggregates_consume_full_filtered_live_data(quotes, method, expected):
    result = live.execute_realtime_quote_api(args={'agg': f'{method}(stock.quote.pct)', 'limit': 1}, outputs=['value'], aggregate=True)
    assert result['status'] == 'ok', result
    assert result['rows'][0]['value'] == pytest.approx(expected)
    assert result['diagnostics']['received_count'] == 3


def test_grouping_and_limitup_count(quotes):
    result = live.execute_realtime_quote_api(args={'agg': 'count(stock.quote.code)', 'filter': 'is_limit_price == 1', 'group_by': 'tradedate'}, outputs=['tradedate', 'count(stock.quote.code) as limitups'], aggregate=True)
    assert result['rows'] == [{'tradedate': '2026-09-07', 'limitups': 2}]


def test_empty_aggregate_count_is_zero_and_average_null(quotes):
    for method, expected in [('count', 0), ('avg', None)]:
        result = live.execute_realtime_quote_api(args={'agg': f'{method}(stock.quote.pct)', 'filter': 'pct > 100'}, outputs=['value'], aggregate=True)
        assert result['rows'] == [{'value': expected}]


def test_explicit_full_overflow_is_not_silently_truncated(quotes, monkeypatch):
    monkeypatch.setattr(live.intraday, '_latest_quote_hard_limit', lambda: 2)
    result = live.execute_realtime_quote_api(args={'limit': -1}, outputs=['code'])
    assert result['status'] == 'result_too_large'
    assert result['rows'] == []


def test_failed_batch_is_failure_without_stored_fallback(quotes, monkeypatch):
    monkeypatch.setattr(live, 'BATCH_SIZE', 1)
    def fetch(batch):
        if batch[0]['code'] == '300308':
            raise requests.Timeout('timeout')
        return [quotes[0]]
    monkeypatch.setattr(live, '_fetch_batch', fetch)
    result = live.execute_realtime_quote_api(args={}, outputs=['code'])
    assert result['status'] == 'provider_error'
    assert result['rows'] == []
    assert result['source'] == ['upchina']


def test_missing_quote_is_reported_not_a_zero_price(quotes):
    quotes[1]['stExHq']['iTradeDate'] = 0
    result = live.execute_realtime_quote_api(args={}, outputs=['code', 'close'])
    assert result['row_count'] == 2
    assert result['diagnostics']['missing_codes'] == ['300308']


def test_api_payload_requests_both_basic_and_extended_quote(monkeypatch):
    sent = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'taf_ret': 0, 'stRsp': {'vStockHq': [quote()]}}
    def post(url, **kwargs):
        sent.append((url, kwargs))
        return Response()
    monkeypatch.setattr(live.requests, 'post', post)
    live._fetch_batch([{'code': '600519', 'setcode': 1}])
    assert sent[0][1]['json']['stReq'] == {'vStock': [{'shtSetcode': 1, 'sCode': '600519'}], 'eHqData': 3}
    assert sent[0][1]['timeout'] == 20


def test_code_scope_needs_no_database_and_mixed_or_cannot_push_down(monkeypatch):
    def unexpected(**kwargs): raise AssertionError('database access')
    monkeypatch.setattr(live, 'StockInfoDbUtils', unexpected)
    assert live._load_securities(live._condition({'filter': "code in ['600519.SH', '300308.SZ', '920821.BJ']"})) == [
        {'code': '300308', 'setcode': 0}, {'code': '600519', 'setcode': 1}, {'code': '920821', 'setcode': 7}]
    with pytest.raises(AssertionError, match='database access'):
        live._load_securities(live._condition({'filter': "code == '600519.SH' or pct > 10"}))


def test_existing_reference_protocol_routes_to_live_source(quotes):
    previous = {'r1': ResultHandle('r1', 'plate.constitution', ['stock_code'], {'rows': [{'stock_code': '300308.SZ'}, {'stock_code': '920821.BJ'}]})}
    call = ApiCall('r2', 'stock.quote', {'mode': 2, 'filter': 'code in r1.stock_code'}, ['code', 'is_limit_price'], '')
    assert validate_call(call, previous_results=previous).ok
    result = api_runner.execute_api_call(call, previous).data
    assert result['rows'] == [{'code': '300308', 'is_limit_price': 1}, {'code': '920821', 'is_limit_price': 1}]


@pytest.mark.parametrize('mode,valid', [(0, True), (1, False), (2, True)])
def test_limit_field_is_only_enabled_on_supported_modes(mode, valid):
    call = ApiCall('r1', 'stock.quote', {'mode': mode}, ['code', 'is_limit_price'], '')
    assert validate_call(call, previous_results={}).ok == valid


def test_query_alias_is_not_registered_and_navigation_shows_real_entry():
    service = FinanceDataToolCatalogService()
    pack = service.get_model_dataview('stock', 'report_metric', 'aggregate')
    assert pack['available_operations'] == {'query': 'stock.report_metric', 'aggregate': 'stock.report_metric.agg'}
    assert [f['api_name'] for f in pack['functions']] == ['stock.report_metric.agg']
    call = ApiCall('r1', 'stock.report_metric.query', {}, ['code'], '')
    assert not validate_call(call, previous_results={}).ok


def test_live_aggregate_uses_api_route_without_intraday_or_daily(quotes, monkeypatch):
    def unexpected(**kwargs): raise AssertionError('stored quote provider')
    monkeypatch.setattr(api_runner, 'execute_intraday_quote_agg_api', unexpected)
    monkeypatch.setattr(api_runner, 'execute_quote_agg_api', unexpected)
    call = ApiCall('r1', 'stock.quote.agg', {'mode': 2, 'agg': 'count(stock.quote.code)', 'filter': 'is_limit_price == 1'}, ['limitups'], '')
    assert validate_call(call, previous_results={}).ok
    assert api_runner.execute_api_call(call).data['rows'] == [{'limitups': 2}]

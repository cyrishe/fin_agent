import json
from pathlib import Path

import pytest

from src.experiments.staged_data_protocol.phase2 import report_provider as provider
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
from src.scenarios.financial_qa.dsh_mcp_server import _EXPOSED_TOOLS
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService


def test_catalog_and_execution_contract_are_report_only():
    tree = FinanceDataToolCatalogService().build_tree()
    assert [(s['name'], {v['name'] for v in s['dataviews']}) for s in tree['subjects']] == [
        ('stock', {'report', 'report_metric'})]
    selected = json.loads(Path('src/tools/finance_data/catalog/report_only_catalog.json').read_text())
    assert set(selected['subjects']) == {'stock'}
    for view in ('report', 'report_metric'):
        assert resolve_api(f'stock.{view}')
        assert resolve_api(f'stock.{view}.agg')
    call = parse_api_call('r1 = stock.quote(limit = 1) -> code')
    assert not validate_call(call, {}).ok


def test_agent_exposes_only_report_tools():
    tools, _, _ = FinanceDataQueryCcTools().build_tools(
        owner_ids=[], tool_context={'allowed_agent_tools': ['general_search', 'stock_kline_visual_analysis']})
    names = {tool.name for tool in tools}
    assert names == {'read_finance_catalog', 'finance_query', 'load_finance_result',
                     'read_finance_skill', 'read_finance_skill_reference'}
    assert _EXPOSED_TOOLS == names


def test_report_table_configuration_and_sql(monkeypatch):
    monkeypatch.setenv('FINANCE_REPORT_TABLE', 'customer_reports')
    monkeypatch.setenv('FINANCE_REPORT_METRIC_FACT_TABLE', 'customer_facts')
    monkeypatch.setenv('FINANCE_REPORT_METRIC_DEF_TABLE', 'customer_defs')
    captured = []
    def run(**kwargs):
        captured.append(kwargs['sql'])
        return {'rows': [], 'error': '', 'recent_scan': {}}
    monkeypatch.setattr(provider, '_run_query', run)
    result = provider.execute_report_metric_api(args={}, outputs=['report_id', 'metric_value'])
    assert result['status'] == 'ok' and result['row_count'] == 0
    assert all(name in captured[0] for name in ('`customer_reports`', '`customer_facts`', '`customer_defs`'))
    monkeypatch.setenv('FINANCE_REPORT_TABLE', 'reports`; DROP TABLE reports;--')
    with pytest.raises(ValueError):
        provider.execute_report_api(args={}, outputs=['report_id'])


def test_report_database_connection_url_and_legacy_default(monkeypatch):
    captured = []
    monkeypatch.setattr(provider, 'StockInfoDbUtils', lambda **kwargs: captured.append(kwargs))
    monkeypatch.setenv('FINANCE_REPORT_DB_URL', 'mysql+pymysql://reader:p%40ss@db.example:3307/client_reports')
    provider._connect_report_db()
    assert captured[-1] == {'host': 'db.example', 'port': 3307, 'user': 'reader',
                            'password': 'p@ss', 'database': 'client_reports'}
    monkeypatch.delenv('FINANCE_REPORT_DB_URL')
    monkeypatch.delenv('FINANCE_REPORT_DB_NAME', raising=False)
    provider._connect_report_db()
    assert captured[-1] == {'database': 'kingdomai'}
    monkeypatch.setenv('FINANCE_REPORT_DB_URL', 'https://db.example/client_reports')
    with pytest.raises(ValueError, match='FINANCE_REPORT_DB_URL'):
        provider._connect_report_db()


def test_mcp_bridge_report_query_and_blocked_market_data(monkeypatch, tmp_path):
    import asyncio
    from src.scenarios.financial_qa.dsh_mcp_server import FinanceDshMcpBridge
    from src.services.session_variable_store_service import SessionVariableStoreService

    sqls = []
    def run(**kwargs):
        sqls.append(kwargs['sql'])
        return {'rows': [{'report_id': 11, 'rating': '买入'}], 'error': '', 'recent_scan': {}}
    monkeypatch.setattr(provider, '_run_query', run)
    monkeypatch.setenv('FINANCE_REPORT_TABLE', 'client_reports')
    context = tmp_path / 'context.json'
    context.write_text(json.dumps({'revision': '1', 'owner_ids': ['client'],
                                   'tool_context': {'_agent_runtime_scope': 'report-test'}}))
    bridge = FinanceDshMcpBridge(context_path=context, trace_path=tmp_path / 'trace.json',
        system_tools=FinanceDataQueryCcTools(result_store=SessionVariableStoreService(data_root=tmp_path)))
    response = asyncio.run(bridge.call_tool('finance_query', {'steps': [
        {'goal': '研报评级', 'request': 'r1 = stock.report(limit = 1) -> report_id, rating'}]}))
    assert '`client_reports`' in sqls[0]
    assert '买入' in json.dumps(response, ensure_ascii=False)
    assert json.loads((tmp_path / 'trace.json').read_text())['tracker']['result_refs'][0]['row_count'] == 1
    blocked = asyncio.run(bridge.call_tool('finance_query', {'steps': [
        {'goal': '查询行情', 'request': 'r1 = stock.quote(limit = 1) -> code'}]}))
    assert len(sqls) == 1
    assert 'unsupported' in json.dumps(blocked, ensure_ascii=False)


def test_report_database_failure_is_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(provider, '_run_query', lambda **kwargs: {
        'rows': [], 'error': 'database unavailable', 'recent_scan': {}})
    result = provider.execute_report_api(args={}, outputs=['report_id'])
    assert result['status'] == 'provider_error'
    assert result['row_count'] == 0
    assert 'database unavailable' in result['reason']

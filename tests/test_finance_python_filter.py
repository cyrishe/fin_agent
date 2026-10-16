import json
import sqlite3

import pytest

from src.experiments.staged_data_protocol.phase2 import python_filter as pf
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_structure import parse_filter_expression
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.models import ResultHandle


def call(api, expression, outputs="code, name", extra=""):
    return parse_api_call(f"r1 = {api}(filter = {json.dumps(expression, ensure_ascii=False)}{extra}) -> {outputs}")


@pytest.mark.parametrize("expression,operator,value", [
    ("name.contains('债券')", "contains", "债券"),
    ('name.contains("O\'Reilly, A and B (集团)")', "contains", "O'Reilly, A and B (集团)"),
    ("'债券' in name", "contains", "债券"),
    ('name == "O\'Reilly, A and B (集团)"', "==", "O'Reilly, A and B (集团)"),
    ('name is None', "==", None),
    ('name is not None', "!=", None),
    ('code in ["000001.SZ", "600000.SH"]', "in", ["000001.SZ", "600000.SH"]),
    ('code not in ["000001.SZ"]', "not in", ["000001.SZ"]),
])
def test_python_conditions_preserve_literals(expression, operator, value):
    parsed = call("fund.basic_info", expression)
    tree = parse_filter_expression(parsed.args["filter"])
    assert tree["operator"] == operator
    assert tree["value"] == value
    assert validate_call(parsed, {}).ok


@pytest.mark.parametrize("expression,expected_sql,expected_params", [
    ("pct == 10", "pct = %s", [10]),
    ("pct != 10", "pct != %s", [10]),
    ("pct > -5", "pct > %s", [-5]),
    ("pct >= 0", "pct >= %s", [0]),
    ("pct < 5.5", "pct < %s", [5.5]),
    ("pct <= 10", "pct <= %s", [10]),
    ("reliable == True", "reliable = %s", [True]),
    ("reliable == False", "reliable = %s", [False]),
    ("pct is None", "pct IS NULL", []),
    ("pct is not None", "pct IS NOT NULL", []),
])
def test_documented_scalar_operators_have_deterministic_sql(expression, expected_sql, expected_params):
    tree = pf.parse_python_filter(expression)
    assert pf.compile_tree(tree, {"pct": "pct", "reliable": "reliable"}) == (expected_sql, expected_params)


@pytest.mark.parametrize("expression", [
    '__import__("os").system("id")', 'name.lower() == "x"',
    'name.__class__', '(lambda: True)()', '[x for x in name]',
    'name.startswith(open("/tmp/file").read())', 'name.startswith("a", 1)',
    'name == (1 + 2)', 'name == f"{danger()}"',
])
def test_no_arbitrary_python_execution(expression):
    with pytest.raises(pf.FilterSyntaxError):
        pf.parse_python_filter(expression)


@pytest.mark.parametrize('expression', [
    'name.contains()', 'name.contains(1)', 'name.contains(None)',
    'name.contains("a", "b")', 'name.contains(text="a")',
    'name.contains(["a"])', 'name.contains(*["a"])',
    'name.contains(r1.name)', 'name.lower().contains("a")',
    'r1.name.contains("a")', 'contains(name, "a")',
])
def test_contains_is_one_finite_protocol_form(expression):
    with pytest.raises(pf.FilterSyntaxError):
        pf.parse_python_filter(expression)
    assert not validate_call(call('fund.basic_info', expression), {}).ok


def test_contains_field_is_checked_against_the_selected_method():
    result = validate_call(call('fund.basic_info', 'unknown_field.contains("债券")'), {})
    assert not result.ok
    assert any('unknown_field' in error for error in result.errors)


@pytest.mark.parametrize('api,expression', [
    ('fund.basic_info', "name.startswith('中金')"),
    ('fund.basic_info', "name.endswith('ETF')"),
    ('stock.financial_3_table', "report_period.endswith('-12-31')"),
])
def test_removed_string_methods_are_not_part_of_filter_protocol(api, expression):
    with pytest.raises(pf.FilterSyntaxError):
        pf.parse_python_filter(expression)
    result = validate_call(call(api, expression), {})
    assert not result.ok
    assert any('FILTER_ERROR' in error for error in result.errors)


@pytest.mark.parametrize('name', ['债券ETF', '甲债券乙', '甲债券'])
def test_fuzzy_match_is_literal_contains_anywhere(name):
    tree = pf.parse_python_filter("name.contains('债券')")
    assert tree == pf.parse_python_filter("'债券' in name")  # Historical input compatibility.
    assert pf.evaluate(tree, {'name': name})
    assert pf.to_source(tree) == "name.contains('债券')"
    assert pf.parse_python_filter(pf.to_source(tree)) == tree
    sql, params = pf.compile_tree(tree, {'name': 'name'})
    assert 'LIKE BINARY %s' in sql
    assert params == ['%债券%']


@pytest.mark.parametrize("expression", [
    "name = 宁德时代", "name like '%债券%'", "report_period = 2025-12-31",
    "code in [600519.SH, 000001.SZ]", "value_type = forecast",
])
def test_legacy_still_parses(expression):
    assert parse_filter_expression(expression)


def test_pattern_characters_and_sql_injection_are_literal_parameters():
    literal = "50%_!\\' OR 1=1 --"
    tree = pf.parse_python_filter(f"name.contains({literal!r})")
    sql, params = pf.compile_tree(tree, {"name": "name"})
    assert literal not in sql
    assert params == ["%50!%!_!!\\' OR 1=1 --%"]
    assert "ESCAPE '!'" in sql


def test_grouping_matches_sql_and_result_stage():
    expression = "(name.contains('中金') or name.contains('债券')) and (value > 10 or value < -5)"
    tree = pf.parse_python_filter(expression)
    rows = [dict(name=n, value=v) for n in ['中金基金', '债券ETF', '其他'] for v in [-8, 0, 12]]
    sql, params = pf.compile_tree(tree, {'name': 'name', 'value': 'value'})
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE sample(name TEXT, value REAL)')
    db.executemany('INSERT INTO sample VALUES (?, ?)', [(r['name'], r['value']) for r in rows])
    db.execute('PRAGMA case_sensitive_like=ON')
    actual = db.execute('SELECT name,value FROM sample WHERE ' + sql.replace('LIKE BINARY', 'LIKE').replace('%s', '?'), params).fetchall()
    assert actual == [(r['name'], r['value']) for r in rows if pf.evaluate(tree, r)]
    assert len(actual) == 4


def test_no_partial_or_pushdown():
    tree = pf.parse_python_filter("code == 'A' or value > 10")
    assert pf.project(tree, {'code'}) is None
    tree = pf.parse_python_filter("code == 'A' and (value > 10 or value < -5)")
    assert list(pf.predicates(pf.project(tree, {'code'}))) == [{'field': 'code', 'operator': '==', 'value': 'A'}]


def test_result_binding_preserves_strings_and_empty_or():
    tree = pf.parse_python_filter("code in r1.code or name == 'r2.name'")
    result = ResultHandle('r1', 'stock.basic_info', ['code'], {'rows': []})
    bound = pf.bind_references(tree, {'r1': result})
    sql, values = pf.compile_tree(bound, {'code': 'code', 'name': 'name'})
    assert '0=1' in sql and values == ['r2.name']
    assert pf.evaluate(bound, {'code': 'x', 'name': 'r2.name'})
    assert validate_call(call('stock.basic_info', "name == 'r2.name'"), {}).ok


@pytest.mark.parametrize('module', ['quote', 'moneyflow', 'margin', 'pricevalue'])
def test_window_result_filters_preserve_or(module):
    import importlib
    provider = importlib.import_module(f'src.experiments.staged_data_protocol.phase2.{module}_provider')
    rows = [{'code': 'A', 'value': -8}, {'code': 'B', 'value': 0}, {'code': 'C', 'value': 12}]
    kwargs = {'field': 'pe'} if module == 'pricevalue' else {}
    assert provider._filter_kd_rows(rows, args={'filter': 'value > 10 or value < -5'}, **kwargs) == [rows[0], rows[2]]


@pytest.mark.parametrize("module", ['quote', 'moneyflow', 'margin', 'pricevalue'])
def test_window_contains_combines_with_set_and_numeric_conditions(module):
    import importlib
    provider = importlib.import_module(f'src.experiments.staged_data_protocol.phase2.{module}_provider')
    rows = [
        {'code': '600000.SH', 'name': '浦发银行', 'value': 12},
        {'code': '000001.SZ', 'name': '平安银行', 'value': -8},
        {'code': '601318.SH', 'name': '中国平安', 'value': 15},
    ]
    expression = "name.contains('银行') and code in ['600000.SH', '000001.SZ'] and value > 0"
    kwargs = {'field': 'pe'} if module == 'pricevalue' else {}
    assert provider._filter_kd_rows(rows, args={'filter': expression}, **kwargs) == [rows[0]]


@pytest.mark.parametrize('api,expression,outputs,extra', [
    ('stock.report', "institution.contains('中金')", 'code, institution, investment_highlights', ''),
    ('stock.report_metric', "institution.contains('中金') and metric_code == 'eps'", 'code, forecast_year, metric_value', ''),
    ('stock.quote.kd_pct_sum', "name.contains('银行') and value > 0", 'code, name, value', ', k=5'),
    ('plate.constitution', "plate_name.contains('新能源')", 'plate_code, stock_code', ''),
    ('plate.constitution.agg', "plate_name.contains('新能源') and pct > 0", 'plate_code, average', ', agg=avg(stock.quote.pct), group_by="plate_code"'),
])
def test_contains_reaches_each_existing_method_contract(api, expression, outputs, extra):
    result = validate_call(call(api, expression, outputs, extra), {})
    assert result.ok, result.errors


def test_basic_and_segment_use_same_contains_sql():
    from src.experiments.staged_data_protocol.phase2 import base_info_provider as b, stock_corporate_provider as c
    base = b._build_filter_clauses(source=b.BASE_INFO_SOURCES['fund'], args={'filter': "name.contains('债券')"})
    views = c.STOCK_CORPORATE_VIEWS
    segment = c._build_filter_clauses(view=views['business_segment'], args={'filter': "project_name.contains('新能源')"})
    assert base[1] == ['%债券%']
    assert segment[1] == ['%新能源%']
    assert "ESCAPE '!'" in base[0] and "ESCAPE '!'" in segment[0]


def test_window_special_formula_unchanged():
    from src.experiments.staged_data_protocol.phase2 import quote_provider as q
    sql = q._build_kd_aggregate_sql(source=q.QUOTE_SOURCES['index'], field='pct', method='sum', identity_sql='')
    assert 'start_date THEN close_value' in sql and '/ NULLIF' in sql
    assert 'SUM(q.' not in sql


def test_aggregate_filter_uses_tree_without_touching_metric():
    from src.experiments.staged_data_protocol.phase2 import quote_provider as q
    parsed = call('stock.quote.agg', "(name.contains('中') or code == '600519.SH') and pct > 0", 'market_code, average', ', agg = avg(stock.quote.pct), group_by = "market_code"')
    sql, params = q._build_where(source=q.QUOTE_SOURCES['stock'], args=parsed.args)
    assert ' OR ' in sql and '%s' in sql
    assert '%中%' in params and 0 in params
    assert parsed.args['agg'] == 'avg(stock.quote.pct)'


def test_constituent_aggregate_explicit_history_replaces_latest_default():
    from src.experiments.staged_data_protocol.phase2 import constitution_provider as c, quote_provider as q
    sql, params = c._metric_market_where(source=q.QUOTE_SOURCES['stock'], args={'filter': "tradedate == '2026-09-04' and (pct > 5 or pct < -5)"}, stock_codes=['600519.SH'])
    assert 'MAX(trade_date)' not in sql
    assert '2026-09-04' in params and ' OR ' in sql


def test_constituent_aggregate_does_not_split_cross_stage_or():
    tree = pf.parse_python_filter("plate_code == 'A' or pct > 5")
    with pytest.raises(pf.FilterSyntaxError, match='cannot be split'):
        pf.require_separable(tree, [{'plate_code'}, {'pct'}])
    pf.require_separable(pf.parse_python_filter("plate_code == 'A' and (pct > 5 or pct < -5)"), [{'plate_code'}, {'pct'}])


def test_intraday_identity_date_and_result_filters_keep_grouping():
    from src.experiments.staged_data_protocol.phase2 import intraday_quote_provider as q
    filters = q._filters_from_args({'filter': "(code == '600519.SH' or code == '000001.SZ') and tradedate >= '2026-08-01' and (pct > 10 or pct < -5)"})
    identity_sql, identity_values = q._snapshot_identity_where(filters)
    date_sql, date_values = q._trade_date_predicate(filters)
    sql, values = q._where_sql(filters=filters, slot={})
    assert ' OR ' in identity_sql and identity_values == ['600519', '000001']
    assert 's.trade_date >= %s' in date_sql and date_values == ['2026-08-01']
    assert sql.count(' OR ') == 2 and 10.0 in values and -5.0 in values


def test_report_aggregation_contains_does_not_change_aggregate_sql(monkeypatch):
    from src.experiments.staged_data_protocol.phase2 import report_provider as r
    from tests.test_report_provider import _Db
    db = _Db([])
    monkeypatch.setattr(r, '_connect_report_db', lambda: db)
    result = r.execute_report_agg_api(dataview='report', args={'filter': "institution.contains('中金') and report_date >= '2026-01-01'", 'agg': 'count(report_id)', 'group_by': 'code,name'}, outputs=['code', 'name', 'count(report_id) as reports'])
    assert result['status'] == 'ok'


def test_trade_date_resolution_uses_tree_not_literal_text():
    from datetime import date
    from src.experiments.staged_data_protocol.phase2.trade_date_resolver import TradeDateResolver
    resolver = TradeDateResolver(today=lambda: date(2026, 9, 7))
    resolver._load_trade_days = lambda **kw: [date(2026, 9, 4), date(2026, 9, 7)]
    parsed = call('index.quote', "tradedate == -1 and name == 'tradedate = 2026-01-01'")
    result = resolver.resolve(parsed)
    terms = list(pf.predicates(pf.condition(result.call.args)))
    assert terms[0]['value'] == '2026-09-04'
    assert terms[1]['value'] == 'tradedate = 2026-01-01'


def test_flow_binding_does_not_rewrite_literal_reference_text():
    from src.scenarios.financial_qa.result_registry import FinanceResultRegistry
    source = call('fund.basic_info', "code in step1.code or name == 'step2.name and r9.code'").raw
    resolved = FinanceResultRegistry.resolve_flow_refs(source, completed_steps={1: 'r3'})
    tree = pf.condition(parse_api_call(resolved).args)
    terms = list(pf.predicates(tree))
    assert terms[0]['value'] == {'result': 'r3', 'field': 'code'}
    assert terms[1]['value'] == 'step2.name and r9.code'
    assert FinanceResultRegistry.dependencies(resolved) == ['r3']


def test_contains_and_set_references_keep_one_tree_through_flow_binding():
    from src.scenarios.financial_qa.result_registry import FinanceResultRegistry
    source = call('fund.basic_info', "code in step1.code and name.contains('step2.name')").raw
    resolved = FinanceResultRegistry.resolve_flow_refs(source, completed_steps={1: 'r3'})
    terms = list(pf.predicates(pf.condition(parse_api_call(resolved).args)))
    assert terms == [
        {'field': 'code', 'operator': 'in', 'value': {'result': 'r3', 'field': 'code'}},
        {'field': 'name', 'operator': 'contains', 'value': 'step2.name'},
    ]
    assert FinanceResultRegistry.dependencies(resolved) == ['r3']


def test_count_scope_cannot_flatten_identity_or_into_single_code():
    from src.experiments.staged_data_protocol.phase2 import quote_provider as q
    args = {'filter': "code == '000001.SZ' or code == '600519.SH'", 'count': 20}
    assert not q._single_count_code(source=q.QUOTE_SOURCES['stock'], args=args)
    sql, params = q._count_identity_scope(source=q.QUOTE_SOURCES['stock'], args=args)
    assert ' OR ' in sql and params == ['000001.SZ', '600519.SH']


def test_plate_aliases_reach_window_provider_as_canonical_fields(monkeypatch):
    from src.experiments.staged_data_protocol.phase2 import api_runner as runner

    def execute(*, subject, field, method, args, outputs):
        assert subject == 'plate'
        tree = pf.condition(args)
        assert {p['field'] for p in pf.predicates(tree)} == {'name', 'value'}
        assert pf.evaluate(tree, {'name': '半导体', 'value': 12})
        return {'status': 'ok', 'rows': [], 'columns': []}

    monkeypatch.setattr(runner, 'execute_kd_quote_api', execute)
    parsed = call('plate.quote.kd_pct_sum', "plate_name == '半导体' and value > 10", 'code, name, value', ', k = 5')
    runner.execute_api_call(parsed)


def test_dynamic_result_filters_run_before_order_limit_and_projection():
    import pandas as pd
    from src.experiments.staged_data_protocol.phase2 import dynamic_cal_provider as d
    rows = pd.DataFrame([{'code': 'A', 'value': -8}, {'code': 'B', 'value': 0}, {'code': 'C', 'value': 12}])
    result = d._normalize_result_df(rows, output_columns=['code'], args={
        'filter': 'value < -5 or value > 10', 'order': 'value desc', 'limit': 1,
    })
    assert result.to_dict('records') == [{'code': 'C'}]


def test_dynamic_result_cannot_silently_drop_missing_or_branch():
    import pandas as pd
    from src.experiments.staged_data_protocol.phase2 import dynamic_cal_provider as d
    rows = pd.DataFrame([{'value': 0}, {'value': 12}])
    with pytest.raises(pf.FilterSyntaxError, match='cannot be split'):
        d._normalize_result_df(rows, output_columns=['value'], args={'filter': "code == 'A' or value > 10"})
    result = d._normalize_result_df(rows, output_columns=['value'], args={'filter': "code == 'A' and value > 10"})
    assert result.to_dict('records') == [{'value': 12}]


@pytest.mark.parametrize('values,expected', [([1, 2, 100], 2), ([1, 2, 10, 100], 6), ([None, 1, 2, 100], 2), ([None], None), ([], None)])
def test_aggregate_median_uses_middle_rows_not_mean(values, expected):
    from src.experiments.staged_data_protocol.phase2.agg_protocol import median_query
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE sample(value REAL)')
    db.executemany('INSERT INTO sample VALUES (?)', [(v,) for v in values])
    sql = median_query('SELECT value AS __metric_value FROM sample', group_fields=[], alias='result')
    assert db.execute(sql).fetchone()[0] == expected


def test_grouped_median_ranks_each_group_after_filter():
    from src.experiments.staged_data_protocol.phase2.agg_protocol import median_query
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE sample(code TEXT, value REAL)')
    db.executemany('INSERT INTO sample VALUES (?, ?)', [('A', 1), ('A', 2), ('A', 100), ('B', 2), ('B', 10), ('B', -100)])
    sql = median_query('SELECT code,value AS __metric_value FROM sample WHERE value > 0', group_fields=['code'], alias='result')
    assert db.execute(sql + ' ORDER BY result DESC LIMIT 1').fetchall() == [('B', 6)]


@pytest.mark.parametrize('mode', ['daily', 'minute', 'snapshot'])
def test_quote_aggregate_median_routes_to_ranked_template(monkeypatch, mode):
    from src.experiments.staged_data_protocol.phase2 import quote_provider as q, intraday_quote_provider as i
    from tests.test_report_provider import _Db
    db = _Db([])
    db.conn, db.close_db = db, db.close
    args = {'filter': "code == '600519.SH'", 'agg': 'median(stock.quote.pct)', 'group_by': 'code'}
    if mode == 'daily':
        monkeypatch.setattr(q, 'StockInfoDbUtils', lambda **kw: db)
        result = q.execute_quote_agg_api(subject='stock', args=args, outputs=['code', 'median(stock.quote.pct) as median_pct'])
    else:
        monkeypatch.setattr(i, 'StockInfoDbUtils', lambda **kw: db)
        monkeypatch.setattr(i, '_resolve_slot', lambda **kw: {'trade_date': '2026-09-07', 'minute_index': 600})
        result = i.execute_intraday_quote_agg_api(args=args, outputs=['code', 'median(stock.quote.pct) as median_pct'], latest_only=mode == 'snapshot')
    assert result['status'] == 'ok'
    assert 'ROW_NUMBER() OVER (PARTITION BY `code` ORDER BY __metric_value)' in db.cursor_obj.sql
    assert 'AVG(__metric_value)' in db.cursor_obj.sql
    assert '__median_row IN' in db.cursor_obj.sql
    assert db.closed

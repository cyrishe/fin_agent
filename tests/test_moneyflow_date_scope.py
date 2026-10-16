"""Execute generated predicates on real local rows; never contact market DBs."""
import sqlite3
import pytest
from src.experiments.staged_data_protocol.phase2.moneyflow_provider import MONEYFLOW_SOURCES, _build_where
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call


@pytest.mark.parametrize('subject', ['stock', 'plate'])
@pytest.mark.parametrize('args,expected', [
    ({'filter': "code == '001'"}, ['2026-09-29']),
    ({'filter': "(code == '001') and (tradedate >= '2026-09-27') and (tradedate <= '2026-09-28')"}, ['2026-09-27', '2026-09-28']),
    ({'filter': "(code == '001') and (tradedate == '2026-09-27')"}, ['2026-09-27']),
    ({'filter': "(code == '001') and ((tradedate == '2026-09-27') or (tradedate == '2026-09-29'))"}, ['2026-09-27', '2026-09-29']),
    ({'date': '2026-09-28', 'filter': "code == '001'"}, ['2026-09-28']),
    ({'start': '2026-09-27', 'end': '2026-09-28', 'filter': "code == '001'"}, ['2026-09-27', '2026-09-28']),
    ({'filter': "(code == '001') and (tradedate < '2026-01-01')"}, []),
])
def test_moneyflow_date_scope(subject, args, expected):
    source = MONEYFLOW_SOURCES[subject]
    code_column = 'stk_code' if subject == 'stock' else 'plate_code'
    with sqlite3.connect(':memory:') as db:
        db.execute(f'CREATE TABLE {source.table} ({code_column} TEXT, trade_date TEXT)')
        db.executemany(f'INSERT INTO {source.table} VALUES (?, ?)', [
            (code, day) for code in ['001', '002']
            for day in ['2026-09-27', '2026-09-28', '2026-09-29']])
        where, params = _build_where(source=source, args=args)
        rows = db.execute(f'SELECT q.trade_date FROM {source.table} q WHERE {where.replace("%s", "?")} ORDER BY q.trade_date', params).fetchall()
        assert [r[0] for r in rows] == expected


def test_unknown_filter_field_is_not_silently_treated_as_a_date():
    call = parse_api_call('r1 = stock.moneyflow.query(filter="unknown_date == \'2026-09-28\'") -> code, tradedate')
    assert not validate_call(call, {}).ok

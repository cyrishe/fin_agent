from datetime import date

import pytest

from src.services.request_usage_service import summarize, total_tokens


def test_usage_aliases_and_cache_are_not_double_counted():
    assert total_tokens(None) is None
    assert total_tokens({}) is None
    assert total_tokens({'total_tokens':0}) == 0
    assert total_tokens({'input_tokens':100,'cached_input_tokens':80,'output_tokens':10}) == 110
    assert total_tokens({'input_tokens':100,'cache_read_input_tokens':80,'cache_creation_input_tokens':20,'output_tokens':10}) == 210
    assert total_tokens({'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'cumulative_context_tokens':500,'reasoning_tokens':10}) == 520
    assert total_tokens({'accounting_total_tokens':None,'total_tokens':0}) is None


def test_daily_totals_and_averages_count_completed_requests_only():
    d=date(2026,9,6)
    result=summarize([(d,'chat',3,2,300),(d,'mcp',2,2,600),(d,'http_api',1,1,50)],today=d,days=2)
    assert result[0]['chat']['requests']==3
    assert result[0]['chat']['average_tokens']==150
    assert result[0]['chat']['unknown_usage_requests']==1
    assert result[0]['mcp']['average_tokens']==300
    assert result[0]['known_total_tokens']==950
    assert result[0]['total_tokens'] is None
    assert result[1]['total_tokens']==0


def test_gateway_records_one_request_not_internal_turns():
    import asyncio
    from src.finance_api.models import FinanceQueryRequest
    from src.finance_api.service import FinanceApiGateway
    from tests.test_finance_api_gateway import _Engine
    class Engine(_Engine):
        def answer(self,**kwargs):
            result=super().answer(**kwargs)
            result['llm_usage']={'total_tokens':900,'call_count':12}
            return result
    records=[]
    gateway=FinanceApiGateway(engine=Engine(),usage_recorder=lambda **r:records.append(r))
    asyncio.run(gateway.execute(FinanceQueryRequest(query='贵州茅台最近行情'),principal_id='a',request_channel='mcp'))
    assert len(records)==1
    assert records[0]['channel']=='mcp'
    assert total_tokens(records[0]['usage'])==900


def test_failed_request_preserves_count_with_unknown_tokens():
    import asyncio
    from src.finance_api.models import FinanceQueryRequest
    from src.finance_api.service import FinanceApiGateway
    class Engine:
        def answer(self,**kwargs):raise RuntimeError('upstream unavailable')
    records=[]
    gateway=FinanceApiGateway(engine=Engine(),usage_recorder=lambda **r:records.append(r))
    with pytest.raises(RuntimeError):
        asyncio.run(gateway.execute(FinanceQueryRequest(query='贵州茅台行情'),principal_id='a'))
    assert len(records)==1 and records[0]['usage'] is None
    assert not records[0]['succeeded']


def test_record_is_idempotent_and_uses_shanghai_completion_day(monkeypatch):
    import datetime as dt
    from src.services import request_usage_service as module
    executed=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql,args):executed.append((sql,args))
    class Connection:
        def cursor(self):return Cursor()
        def close(self):pass
    monkeypatch.setattr(module,'connect',Connection)
    finished=dt.datetime(2026,9,6,18,tzinfo=dt.timezone.utc)
    for _ in range(2):
        module.record_request(request_id='fq_test',channel='mcp',usage={'total_tokens':10},succeeded=True,finished_at=finished)
    assert executed[0][1][2]==dt.datetime(2026,9,7,2)
    assert 'ON DUPLICATE KEY UPDATE' in executed[0][0]
    assert executed[0][1]==executed[1][1]


def test_accounting_write_failure_does_not_fail_answer(monkeypatch,caplog):
    from src.services import request_usage_service as module
    def fail():raise ConnectionError('unavailable')
    monkeypatch.setattr(module,'connect',fail)
    module.record_request(request_id='fq_test',channel='http_api',usage={},succeeded=False)
    assert 'Request usage persistence failed' in caplog.text

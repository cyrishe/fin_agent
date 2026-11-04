import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from unittest.mock import Mock

import pytest

from src.services.stock_indicator_minute import SHANGHAI, compute_minute_series, session_grid
from src.services.stock_minute_cache import cached_source_rows
from src.services.stock_minute_signals import (
    evaluate_minute_signals, read_minute_signals, record_monitor_result, rule_config,
)

DAY = date(2026, 9, 28)
CODE = '600519.SH'


def history(margins, period=5):
    return [dict(code=CODE, trade_date=DAY.isoformat(), period_minutes=period,
                 bar_end_time=t.isoformat()+'+08:00', is_finalized=m is not None,
                 close=100+m if m is not None else None, ma20=100 if m is not None else None,
                 session_macd_hist=m, volume_ratio5=1.5+m if m is not None else None)
            for t, m in zip(session_grid(DAY, period), margins)]


def evaluate(margins, rules=None, **kwargs):
    return evaluate_minute_signals(history(margins), config=rule_config(rules), **kwargs)


def record(result, tmp_path, run='r1', owner='u1', step='s1', fingerprint='a'):
    data = copy.deepcopy(result)
    data.update(config=rule_config(['ma20_cross_up']), external_requests=0,
                snapshots=[dict(code=CODE, trade_date=DAY.isoformat(), input_fingerprint=fingerprint,
                                data_as_of='2026-09-28T10:00:00+08:00')])
    return record_monitor_result(data, owner_id=owner, schedule_id='task1', step_id=step,
                                 run_id=run, codes=[CODE], period=5, root=tmp_path)


def test_cross_and_return_remain_visible_even_when_endpoint_is_unchanged():
    result = evaluate([-1, 1, 2, -1, -2])
    assert [(e['rule'], e['bar_end_time'][11:16]) for e in result['events']] == [
        ('ma20_cross_up', '09:40'), ('ma20_cross_down', '09:50')]
    assert result['current_conditions'][1]['condition_holds'] is False  # sorted rules: down, up
    cutoff = datetime(2026, 9, 28, 9, 45, tzinfo=SHANGHAI)
    later = evaluate([-1, 1, 2, -1, -2], since=cutoff)
    assert len(later['events']) == 1 and later['events'][0]['rule'] == 'ma20_cross_down'


def test_confirmations_macd_and_volume_each_emit_episode_once():
    result = evaluate([-1, 1, 1, 1, 1, -1, 1, 1, 1],
        ['ma20_hold_above', 'session_macd_cross_up', 'session_macd_cross_down', 'volume_expansion'])
    assert len([e for e in result['events'] if e['rule']=='ma20_hold_above']) == 2
    assert len([e for e in result['events'] if e['rule']=='volume_expansion']) == 2
    assert len([e for e in result['events'] if e['rule']=='session_macd_cross_up']) == 2
    assert len([e for e in result['events'] if e['rule']=='session_macd_cross_down']) == 1
    gap = evaluate([-1, None, 1, 1], ['ma20_cross_up','ma20_hold_above'])
    assert gap['events'] == []


def test_series_warms_up_before_window_preserves_gaps_and_no_lookahead():
    grid=session_grid(DAY, 1)
    bars=[dict(bar_end_time=t.to_pydatetime(), open=100+i, close=100+i, high=101+i,
               low=99+i, volume=10, amount=(100+i)*1000, source_bar_count=1,is_finalized=True)
          for i,t in enumerate(grid[:65])]
    args=dict(code=CODE,trade_date=DAY,period=1,as_of=datetime(2026,9,28,10,30,tzinfo=SHANGHAI))
    rows,snapshot=compute_minute_series(bars,**args,since=datetime(2026,9,28,10,20,tzinfo=SHANGHAI))
    assert len(rows)==10 and rows[0]['ma20']==140.5 and rows[-1]['close']==159
    assert snapshot['input_bar_count']==60
    missing,_=compute_minute_series(bars[:55]+bars[56:],**args)
    assert not missing[55]['is_finalized'] and missing[55]['ma20'] is None
    assert missing[-1]['ma20'] is None and missing[-1]['session_vwap'] is None
    assert len(compute_minute_series(bars,**{**args,'max_lag_seconds':0})[0])==60
    assert not compute_minute_series(bars[:20],**{**args,'max_lag_seconds':0})[0]
    with pytest.raises(ValueError,match='timezone'):
        compute_minute_series(bars,**{**args,'as_of':datetime(2026,9,28,10,30)})


def test_no_lunch_gap_or_daily_state_carryover():
    rows=history([-1]*24+[1],period=5)
    result=evaluate_minute_signals(rows,config=rule_config(['ma20_cross_up']))
    assert result['events'][0]['bar_end_time'][11:16]=='13:05'
    next_day={**rows[-1], 'trade_date':'2026-09-29','bar_end_time':'2026-09-29T09:35:00+08:00'}
    assert evaluate_minute_signals([rows[0],next_day],config=rule_config(['ma20_cross_up']))['events']==[]


def test_task_records_deduplicate_and_correct_without_losing_observations(tmp_path):
    initial=evaluate([-1,1,-1], ['ma20_cross_up'])
    first=record(initial,tmp_path)
    assert len(first['new_events'])==1
    repeated=record(initial,tmp_path,run='r2')
    assert not repeated['new_events'] and repeated['events']==first['events']
    assert record(initial,tmp_path,run='r2')==repeated
    # Later supplied historical bars are rescanned; an end timestamp watermark cannot hide them.
    late=record(evaluate([-1,1,-1,1],['ma20_cross_up']),tmp_path,run='r3',fingerprint='b')
    assert len(late['new_events'])==1 and len(late['events'])==2
    corrected=record(evaluate([-1,2,-1,1],['ma20_cross_up']),tmp_path,run='r4',fingerprint='c')
    assert len(corrected['corrections'])==1 and not corrected['new_events']
    invalidated=record(evaluate([-1,-1,-1,1],['ma20_cross_up']),tmp_path,run='r5',fingerprint='d')
    assert invalidated['events'][0]['invalidated_at']
    restored=record(evaluate([-1,1,-1,1],['ma20_cross_up']),tmp_path,run='r6',fingerprint='e')
    assert 'invalidated_at' not in restored['events'][0]
    state=json.loads(open(first['event_artifact']).read())
    assert state['revisions'][0]['previous_observation']['input_fingerprint']=='a'
    assert state['revisions'][0]['previous_observation']['evidence']['current_margin']==1
    assert len(state['revisions'])==3


def test_missing_input_does_not_retract_an_observed_event_and_scopes_are_isolated(tmp_path):
    initial=evaluate([-1,1],['ma20_cross_up'])
    first=record(initial,tmp_path)
    missing=record(evaluate([None,1],['ma20_cross_up']),tmp_path,run='r2')
    assert missing['events']==first['events'] and not missing['corrections']
    other=record(initial,tmp_path,owner='u2')
    step=record(initial,tmp_path,step='s2')
    assert len(other['new_events'])==len(step['new_events'])==1
    assert len({other['event_artifact'],first['event_artifact'],step['event_artifact']})==3
    with pytest.raises(ValueError,match='trusted'):
        record(initial,tmp_path,owner='')


def test_overlapping_cache_reuse_expiry_and_content_correction(tmp_path):
    now=[100.]
    value=[10]
    calls=[]
    def loader(codes):
        calls.append(codes)
        return {c:[{'bar_end_time':datetime(2026,9,28,10), 'close':value[0]}] for c in codes}
    def read(codes):
        return cached_source_rows(codes,DAY,loader,root=tmp_path,clock=lambda:now[0])
    assert read([CODE])[1]==0
    assert read([CODE,'000001.SZ'])[1]==1 and calls[-1]==['000001.SZ']
    now[0]+=4
    assert read([CODE])[1]==1
    now[0]+=2;value[0]=11
    assert read([CODE])[0][CODE][0]['close']==11
    (tmp_path/f'{DAY}_{CODE}.json').write_text('invalid')
    assert read([CODE])[1]==0
    # Immutable-on-disk cache cannot be polluted by a caller mutation.
    got,_=read([CODE]);got[CODE][0]['close']=99
    assert read([CODE])[0][CODE][0]['close']==11


def test_concurrent_public_reads_are_coalesced(tmp_path):
    loader=Mock(return_value={CODE:[]})
    with ThreadPoolExecutor(max_workers=8) as pool:
        values=list(pool.map(lambda _:cached_source_rows([CODE],DAY,loader,root=tmp_path),range(8)))
    assert loader.call_count==1 and sum(hits for _,hits in values)==7


def test_reader_uses_one_clock_and_rejects_bad_rules_before_io():
    calls=[]
    def reader(**kwargs):
        calls.append(kwargs)
        return {'rows':[],'snapshots':[]}
    read_minute_signals(codes=[f'{i:06d}.SZ' for i in range(25)],reader=reader)
    assert len(calls)==2 and calls[0]['as_of'] is calls[1]['as_of']
    with pytest.raises(ValueError,match='supported'):
        read_minute_signals(codes=[CODE],rules=['bogus'],reader=reader)
    assert len(calls)==2


def test_tool_runtime_scope_and_scheduled_replay_contract(monkeypatch,tmp_path):
    from src.tools import stock_minute_signals_tool as tool
    from src.tools import registry
    seen=[]
    def read(**kwargs):
        seen.append(kwargs)
        return {**evaluate([-1,1],['ma20_cross_up']), 'config':rule_config(['ma20_cross_up']),
            'external_requests':0,'snapshots':[dict(code=CODE,trade_date=str(DAY),input_fingerprint='a',data_as_of=None,as_of='2026-09-28T10:00:00+08:00')]}
    monkeypatch.setattr(tool,'read_minute_signals',read)
    monkeypatch.setattr(tool,'record_monitor_result',lambda result,**kwargs:record_monitor_result(result,root=tmp_path,**kwargs))
    class Runtime:
        def execute_tool(self, *, tool_name,args,executor):
            clean = {k:v for k,v in args.items() if k != '_runtime'}
            return executor(clean)
    monkeypatch.setattr(registry,'_runtime_execution_service',Runtime())
    # User-supplied runtime must never grant access to another task's event ledger.
    result=registry.run_tool('stock_minute_signals',{'codes':[CODE],'_runtime':{'scheduled_task_id':'forged'}})
    assert result['ok'] and 'event_artifact' not in result['data']
    runtime=dict(owner_user_id='u1',scheduled_task_id='t1',scheduled_task_step_id='s1',scheduled_task_run_id='r1')
    result=registry.run_tool('stock_minute_signals',{'codes':[CODE]},runtime_ctx=runtime)
    assert len(result['data']['new_events'])==1
    n=len(seen)
    with pytest.raises(ValueError,match='current session'):
        tool.run({'codes':[CODE],'since':'2026-09-28T10:00:00+08:00'},runtime_ctx=runtime)
    assert len(seen)==n


def test_half_hour_full_replay_matches_ten_minute_detection(tmp_path):
    import math
    from src.services.stock_indicator_minute import aggregate_minute_bars
    source=[]
    for i,t in enumerate(session_grid(DAY,1)[:90]):
        price=100+math.sin(i/3)
        source.append(dict(bar_end_time=t.to_pydatetime(),open=price,close=price,high=price+1,
            low=price-1,volume=10,amount=price*1000,source_bar_count=1,is_finalized=True))
    outputs=[]
    for interval in (10,30):
        for end in range(interval,91,interval):
            now=source[end-1]['bar_end_time'].replace(tzinfo=SHANGHAI)
            aggregated=aggregate_minute_bars(source,trade_date=DAY,period=1,as_of=now)
            rows,snapshot=compute_minute_series(aggregated,code=CODE,trade_date=DAY,period=1,as_of=now)
            result=evaluate_minute_signals(rows,config=rule_config(['ma20_cross_up']))
            result.update(config=rule_config(['ma20_cross_up']),snapshots=[snapshot])
            saved=record_monitor_result(result,owner_id='u1',schedule_id=str(interval),step_id='signal',
                run_id=str(end),codes=[CODE],period=1,root=tmp_path)
        outputs.append({e['event_id'] for e in saved['events']})
    assert outputs[0]==outputs[1] and len(outputs[0])>=3


def test_active_registry_exposes_signal_contract():
    from src.services.active_tool_registry_service import ActiveToolRegistryService
    tools=ActiveToolRegistryService().list_active_tools()
    tool=next(t for t in tools if t['tool_name']=='stock_minute_signals')
    assert tool['sync_status']=='synced' and tool['planner_visible']
    assert tool['required_inputs']==['codes']


def test_real_runtime_tracking_keeps_trusted_schedule_scope_out_of_arguments(monkeypatch,tmp_path):
    from src.tools import stock_minute_signals_tool as tool,registry
    from src.services import runtime_execution_service as runtime
    from src.services.scheduled_task_executor import ScheduledTaskExecutor
    monkeypatch.setattr(runtime,'SystemDbUtils',Mock(side_effect=RuntimeError('no external trace DB in test')))
    monkeypatch.setattr(registry,'_runtime_execution_service',runtime.RuntimeExecutionService())
    monkeypatch.setattr(tool,'read_minute_signals',lambda **kwargs: {
        **evaluate([-1,1],['ma20_cross_up']),'config':rule_config(['ma20_cross_up']),
        'snapshots':[dict(code=CODE,trade_date=str(DAY),input_fingerprint='a',data_as_of=None,as_of='2026-09-28T10:00:00+08:00')],
        'external_requests':0})
    monkeypatch.setattr(tool,'record_monitor_result',lambda result,**kwargs:record_monitor_result(result,root=tmp_path,**kwargs))
    # Exercise the real registry with in-process fixtures; spawn supervision has
    # separate process tests and deliberately cannot inherit these monkeypatches.
    executor=ScheduledTaskExecutor(authorizer=lambda *_:None,tool_runner=registry.run_tool)
    run=dict(run_id='run1',schedule_id='schedule1',owner_user_id='owner1',execution_plan={'steps':[
        dict(step_id='signal',type='tool',target_ref={'name':'stock_minute_signals'},
             inputs={'codes':[CODE],'_runtime':{'owner_user_id':'forged'}},depends_on=[])]})
    result=executor.execute(run)
    assert result['steps'][0]['status']=='completed'
    result=result['outputs']['signal']['result']['data']
    assert len(result['new_events'])==1
    run['run_id']='run2'
    again=executor.execute(run)['outputs']['signal']['result']['data']
    assert again['events']==result['events'] and not again['new_events']


def test_scheduled_holiday_does_not_publish_previous_session_events(monkeypatch):
    from src.tools import stock_minute_signals_tool as tool
    monkeypatch.setattr(tool,'read_minute_signals',lambda **kwargs: {
        'events':[{'event_id':'old'}],'current_conditions':[{'condition_holds':True}],
        'evaluated':set(),'snapshots':[{'trade_date':'2026-09-25','as_of':'2026-09-26T10:00:00+08:00'}]})
    save=Mock()
    monkeypatch.setattr(tool,'record_monitor_result',save)
    result=tool.run({'codes':[CODE]},runtime_ctx={'scheduled_task_id':'task'})['data']
    assert result['events']==[] and result['observation_note'] and not save.called

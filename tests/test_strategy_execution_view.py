from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from flask import Flask

from src.services import strategy_execution_view_service as view_module
from src.web.strategy_execution_routes import create_strategy_execution_blueprint


class FakeView:
    def __init__(self):
        self.calls = []

    def load(self, **kwargs):
        self.calls.append(kwargs)
        return {"owner_user_id": kwargs["owner_user_id"], "owners": [],
                "trade_dates": [], "strategies": [], "runs": [], "picks": []}

    def load_today_market(self, **kwargs):
        self.calls.append(kwargs)
        return {"trade_date": "2026-10-15", "requested_minutes": 0,
                "stocks": [], "quote_error": None}


def _app(identity, service):
    app = Flask(__name__, template_folder="../src/web/templates")
    app.register_blueprint(create_strategy_execution_blueprint(
        identity_resolver=lambda: identity, service=service))
    return app


def test_api_scopes_member_and_allows_admin_owner_switch():
    service = FakeView()
    member = _app({"user_type": "member", "user_id": "alice"}, service)
    response = member.test_client().get("/api/strategies/executions?user_id=imac")
    assert response.status_code == 200
    assert service.calls[-1] == {"owner_user_id": "alice", "include_owners": False}
    assert response.headers["Cache-Control"] == "no-store, private"
    assert member.test_client().get("/api/strategies/today-market?user_id=imac").status_code == 200
    assert service.calls[-1] == {"owner_user_id": "alice"}

    admin = _app({"user_type": "admin", "user_id": "admin"}, service)
    response = admin.test_client().get("/api/strategies/executions?user_id=imac")
    assert response.status_code == 200
    assert service.calls[-1] == {"owner_user_id": "imac", "include_owners": True}

    guest = _app(None, service)
    assert guest.test_client().get("/api/strategies/executions").status_code == 403
    assert guest.test_client().get("/api/strategies/today-market").status_code == 403
    page = member.test_client().get("/strategies")
    assert page.status_code == 200
    assert "最近 20 个交易日" in page.get_data(as_text=True)


def test_completed_minute_count_respects_lunch_and_market_close():
    zone = ZoneInfo("Asia/Shanghai")
    cases = [(9, 30, 0), (9, 31, 1), (10, 0, 30), (11, 30, 120),
             (12, 45, 120), (13, 0, 120), (13, 1, 121),
             (14, 40, 220), (15, 0, 240), (20, 0, 240)]
    for hour, minute, expected in cases:
        now = datetime(2026, 10, 15, hour, minute, tzinfo=zone)
        assert view_module.completed_session_minutes(now) == expected


@pytest.mark.parametrize("table", ["automl_strategy_selection", "strategy_execution"])
def test_market_reads_owned_list_and_live_api_without_minute_database(monkeypatch, table):
    @contextmanager
    def connection(_name):
        yield object()
    calls = []
    def query(_conn, sql, args=()):
        calls.append((sql, args))
        assert "minute_snapshot" not in sql
        if "information_schema.tables" in sql:
            return [{"table_name": table}]
        if 'aiia_trade_calendar' in sql:
            return [{'calendar_date':date(2026,10,15)}, {'calendar_date':date(2026,9,15)}]
        if f"FROM {table}" in sql:
            return [{"stock_code":"002636", "stock_name":"金安国纪"}]
        if 'FROM kcrp_stock_price' in sql:
            assert args[0]=='002636.SZ' and args[-1]=='2026-10-15'
            return [dict(stk_code='002636.SZ',trade_date=date(2026,10,14),
                open=70,high=71,low=69,close=70.5,volume=100,amount=7050)]
        raise AssertionError(sql)
    monkeypatch.setattr(view_module, 'kingdom_connection', connection)
    monkeypatch.setattr(view_module, 'query', query)
    got=[]
    def minutes(code,now):
        got.append((code,now))
        return [dict(date='2026-10-15',time='09:31',open=70,high=71,low=69,close=70.5,volume=100)]
    now=datetime(2026,10,15,9,31,tzinfo=ZoneInfo('Asia/Shanghai'))
    result=view_module.StrategyExecutionViewService().load_today_market(
        owner_user_id='alice',now=now,minute_loader=minutes,
        quote_loader=lambda args: ([dict(code='002636',tradedate='2026-10-15',close=70.5)],{}))
    assert got==[('002636',now)]
    assert result['stocks'][0]['daily_bars'][0]['date']=='2026-10-14'
    assert result['stocks'][0]['bars'][0]['time']=='09:31'
    assert result['minute_source']=='upchina_1m_api'
    scoped = next(args for sql,args in calls if f'FROM {table}' in sql)
    assert scoped == ('alice','2026-10-15')


def test_live_minute_request_uses_one_minute_type_and_elapsed_count(monkeypatch):
    calls=[]
    class Response:
        def raise_for_status(self): pass
        def json(self):
            # Previous day, one complete bar, and a still-forming bar.
            return {'stRsp':{'vAnalyData':[dict(sttDateTime=dict(iDate=d,shtTime=t),
                fOpen=10,fHigh=11,fLow=9,fClose=10.5,lVolume=2,fAmount=2100)
                for d,t in [(20261014,900),(20261015,571),(20261015,572)]]}}
    def post(url,**kwargs): calls.append((url,kwargs));return Response()
    monkeypatch.setattr(view_module.requests,'post',post)
    now=datetime(2026,10,15,9,31,20,tzinfo=ZoneInfo('Asia/Shanghai'))
    bars=view_module.load_live_minutes('002636',now)
    request=calls[0][1]['json']['stReq']
    assert request['eLineType']==1 and request['shtStartxh']==0
    assert request['shtWantNum']==2  # one completed + the provider's forming bar
    assert len(bars)==1 and bars[0]['time']=='09:31' and bars[0]['date']=='2026-10-15'
    assert bars[0]['volume']==200


def test_minute_filter_excludes_history_future_lunch_and_duplicates():
    from datetime import timedelta
    now=datetime(2026,10,15,10,10,10,tzinfo=ZoneInfo('Asia/Shanghai'))
    start=datetime(2026,10,15,9,30)
    rows=[dict(end=start+timedelta(minutes=i),open=10,high=11,low=9,close=10.5)
          for i in range(-240,241)]
    rows += [rows[250],dict(end=datetime(2026,10,14,15),open=10,high=11,low=9,close=10)]
    bars=view_module.today_minute_bars(rows,now)
    assert len(bars)==40
    assert bars[0]['time']=='09:31' and bars[-1]['time']=='10:10'
    assert all(b['date']=='2026-10-15' for b in bars)
    assert view_module.today_minute_bars(rows,now.replace(hour=9,minute=30))==[]
    lunch=datetime(2026,10,15,12,30,tzinfo=ZoneInfo('Asia/Shanghai'))
    bars=view_module.today_minute_bars(rows,lunch)
    assert len(bars)==120 and bars[-1]['time']=='11:30'


def test_legacy_selection_stays_pending_without_db_outcome(monkeypatch):
    @contextmanager
    def connection(_name):
        yield object()

    def query(_conn, sql, _args=()):
        if "information_schema.tables" in sql:
            return [{"table_name": name} for name in
                    ("automl_strategy_definition", "automl_strategy_selection")]
        if "aiia_trade_calendar" in sql:
            return [{"calendar_date": date(2026, 10, 15)},
                    {"calendar_date": date(2026, 10, 14)}]
        if "FROM automl_strategy_definition" in sql:
            return [{"strategy_id": "s", "title": "策略S", "summary": "说明"}]
        if "FROM automl_strategy_selection" in sql:
            return [{"strategy_id": "s", "signal_date": date(2026, 10, 14),
                     "selection_rank": 1, "stock_code": "002812", "stock_name": "恩捷股份",
                     "actionable": 1, "signal_capture_at": None,
                     "signal_price": Decimal("46.3300")}]
        raise AssertionError(sql)

    monkeypatch.setattr(view_module, "kingdom_connection", connection)
    monkeypatch.setattr(view_module, "query", query)
    result = view_module.StrategyExecutionViewService().load(owner_user_id="imac")
    pick = result["picks"][0]
    assert pick["record_source"] == "selection_only"
    assert pick["signal_price"] == 46.33
    assert pick["entry_price"] is None
    assert pick["exit_price"] is None
    assert pick["gross_return_pct"] is None


def test_execution_row_takes_precedence_over_same_legacy_selection(monkeypatch):
    @contextmanager
    def connection(_name):
        yield object()

    def query(_conn, sql, _args=()):
        if "information_schema.tables" in sql:
            return [{"table_name": name} for name in
                    ("automl_strategy_definition", "automl_strategy_selection",
                     "automl_strategy_run", "automl_strategy_execution")]
        if "aiia_trade_calendar" in sql:
            return [{"calendar_date": date(2026, 10, 15)},
                    {"calendar_date": date(2026, 10, 14)}]
        if "FROM automl_strategy_definition" in sql:
            return [{"strategy_id": "s", "title": "策略S", "summary": "说明"}]
        if "FROM automl_strategy_run" in sql:
            return [{"strategy_id": "s", "signal_date": date(2026, 10, 14),
                     "candidate_count": 2, "selection_count": 1, "signal_at": None}]
        if "FROM automl_strategy_execution" in sql:
            return [{"strategy_id": "s", "signal_date": date(2026, 10, 14),
                     "selection_rank": 1, "stock_code": "002812", "stock_name": "恩捷股份",
                     "actionable": 1, "signal_at": None, "signal_price": Decimal("46.33"),
                     "entry_at": None, "entry_price": Decimal("46.33"),
                     "entry_price_basis": "14:40_signal_quote_proxy",
                     "exit_rule": "next_trade_day_0940_close",
                     "exit_at": None, "exit_price": None,
                     "exit_price_basis": None, "gross_return_pct": None}]
        if "FROM automl_strategy_selection" in sql:
            return [{"strategy_id": "s", "signal_date": date(2026, 10, 14),
                     "selection_rank": 1, "stock_code": "002812", "stock_name": "恩捷股份",
                     "actionable": 1, "signal_capture_at": None,
                     "signal_price": Decimal("46.33")}]
        raise AssertionError(sql)

    monkeypatch.setattr(view_module, "kingdom_connection", connection)
    monkeypatch.setattr(view_module, "query", query)
    result = view_module.StrategyExecutionViewService().load(owner_user_id="imac")
    assert len(result["picks"]) == 1
    assert result["picks"][0]["record_source"] == "execution"
    assert result["picks"][0]["gross_return_pct"] is None


def test_readable_versions_are_authoritative_and_keep_unsold_positions(monkeypatch):
    @contextmanager
    def connection(_): yield object()
    calls=[]
    def query(_conn,sql,args=()):
        calls.append(sql)
        if 'information_schema.tables' in sql:
            return [{'table_name':n} for n in ('strategy_daily_run','strategy_execution','automl_strategy_selection')]
        if 'aiia_trade_calendar' in sql:
            return [{'calendar_date':date(2026,10,16)}]
        if 'GROUP BY' in sql:
            return [dict(strategy_name='涨停冷静',model_version='v1.0',version_desc='四K形态',first_date=date(2026,9,1),last_date=date(2026,10,16))]
        if 'FROM strategy_daily_run' in sql: return []
        if 'FROM strategy_execution' in sql:
            assert 'buy_price IS NOT NULL AND sell_price IS NULL' in sql
            return [dict(strategy_name='涨停冷静',model_version='v1.0',signal_date=date(2026,9,1),selection_rank=1,
                details_json='{"exit_rule":"d2"}',buy_at=None,buy_price=Decimal('10.1'),buy_price_basis='simulation',
                sell_at=None,sell_price=None,sell_price_basis=None)]
        raise AssertionError(sql)
    monkeypatch.setattr(view_module,'kingdom_connection',connection)
    monkeypatch.setattr(view_module,'query',query)
    result=view_module.StrategyExecutionViewService().load(owner_user_id='imac')
    assert result['strategies'][0]['name']=='涨停冷静 / v1.0'
    assert result['picks'][0]['entry_price']==10.1
    assert result['picks'][0]['signal_date']=='2026-09-01'
    assert not any('FROM automl_strategy_selection' in s for s in calls)


def test_history_uses_selection_date_database_bars_and_never_live_api(monkeypatch):
    @contextmanager
    def connection(_): yield object()
    calls=[]
    def query(_conn,sql,args=()):
        calls.append((sql,args))
        if 'information_schema.tables' in sql:return [{'table_name':'strategy_execution'}]
        if 'aiia_trade_calendar' in sql:return [{'calendar_date':date(2026,10,16)},{'calendar_date':date(2026,9,11)}]
        if 'FROM strategy_execution' in sql:
            assert 'AND signal_date=%s' in sql and args[0]=='alice'
            return [{'stock_code':'000823','stock_name':'超声电子'}]
        if 'FROM kcrp_stock_price' in sql:
            assert 'trade_date<=%s' in sql
            day=date.fromisoformat(args[-1])
            return [dict(stk_code='000823.SZ',trade_date=day,preclose=20,open=20.1,high=22,low=20,close=21,volume=1000,amount=21000)]
        if 'FROM aiia_stock_realtime_minute_snapshot_full' in sql:
            assert args[1:-240] == ('000823',)
            assert 'bar_end_time IN (' in sql
            ends=args[-240:]
            assert len(set(ends))==240
            assert [ends[i].strftime('%H:%M') for i in (0,119,120,239)]==['09:31','11:30','13:01','15:00']
            assert all(t.date().isoformat()==args[0] for t in ends)
            assert 'is_finalized=1' in sql and 'is_fallback=0' in sql
            return [dict(stk_code='000823',end=datetime.fromisoformat(args[0]+' '+t),open=20,high=22,low=20,close=21,volume=100,amount=2100) for t in ['09:31:00','15:00:00']]
        raise AssertionError(sql)
    def forbidden(*args):raise AssertionError('Historical selections must not call a live API')
    monkeypatch.setattr(view_module,'kingdom_connection',connection)
    monkeypatch.setattr(view_module,'query',query)
    now=datetime(2026,10,16,10,55,tzinfo=ZoneInfo('Asia/Shanghai'))
    for day in ['2026-10-14','2026-10-15']:
        result=view_module.StrategyExecutionViewService().load_today_market(owner_user_id='alice',signal_date=day,now=now,quote_loader=forbidden,minute_loader=forbidden)
        assert result['trade_date']==day and result['is_live'] is False
        assert result['minute_source']=='minute_database' and result['requested_minutes']==240
        stock=result['stocks'][0]
        assert {b['date'] for b in stock['bars']}=={day}
        assert stock['daily_bars'][-1]['date']==day
        assert stock['quote']['close']==21 and stock['quote']['preclose']==20
        assert stock['quote']['pct']==pytest.approx(5)
        assert stock['quote']['snapshot_time']==day+' 15:00:00'
        assert next(args for sql,args in reversed(calls) if 'FROM strategy_execution' in sql)[-1]==day


def test_history_missing_bars_stay_empty_instead_of_using_today(monkeypatch):
    @contextmanager
    def connection(_):yield object()
    def query(_conn,sql,args=()):
        if 'information_schema.tables' in sql:return [{'table_name':'strategy_execution'}]
        if 'aiia_trade_calendar' in sql:return [{'calendar_date':date(2026,10,16)}]
        if 'FROM strategy_execution' in sql:return [{'stock_code':'000823','stock_name':'超声电子'}]
        return []
    monkeypatch.setattr(view_module,'kingdom_connection',connection)
    monkeypatch.setattr(view_module,'query',query)
    def forbidden(*args):raise AssertionError('No live fallback for missing history')
    result=view_module.StrategyExecutionViewService().load_today_market(owner_user_id='alice',signal_date='2026-10-14',
        now=datetime(2026,10,16,10,55,tzinfo=ZoneInfo('Asia/Shanghai')),quote_loader=forbidden,minute_loader=forbidden)
    stock=result['stocks'][0]
    assert stock['bars']==[] and stock['daily_bars']==[] and stock['quote'] is None
    assert stock['minute_error']=='该日分钟数据缺失'


def test_market_date_route_scopes_owner_and_rejects_invalid_or_future_date():
    service=FakeView()
    client=_app({'user_type':'member','user_id':'alice'},service).test_client()
    response=client.get('/api/strategies/market?date=2026-10-14&user_id=imac')
    assert response.status_code==200
    assert service.calls[-1]=={'owner_user_id':'alice','signal_date':'2026-10-14'}
    count=len(service.calls)
    for day in ['not-a-date','2026-02-30','9999-01-01']:
        assert client.get('/api/strategies/market?date='+day).status_code==400
    assert len(service.calls)==count
    assert _app(None,service).test_client().get('/api/strategies/market?date=2026-10-14').status_code==403


@pytest.mark.parametrize('offset,target', [(-2,'2026-10-12'),(-1,'2026-10-13'),(0,'2026-10-14'),(1,'2026-10-15'),(2,'2026-10-16')])
def test_minute_navigation_keeps_owned_selection_and_routes_target_source(monkeypatch,offset,target):
    @contextmanager
    def connection(_):yield object()
    calls=[]
    def query(_conn,sql,args=()):
        calls.append((sql,args))
        if 'information_schema.tables' in sql:return [{'table_name':'strategy_execution'}]
        if 'aiia_trade_calendar' in sql:
            if 'calendar_date<%s' in sql:days=['2026-10-13','2026-10-12']
            elif 'calendar_date>%s' in sql:days=['2026-10-15','2026-10-16']
            else:days=['2026-10-16','2026-09-11']
            return [{'calendar_date':date.fromisoformat(d)} for d in days]
        if 'FROM strategy_execution' in sql:
            assert args[0]=='alice' and args[-1]=='2026-10-14'
            return [{'stock_code':'000823','stock_name':'超声电子'}, {'stock_code':'002636','stock_name':'金安国纪'}]
        if 'FROM kcrp_stock_price' in sql:
            assert args[0]=='000823.SZ' and args[-1]==target
            return []
        if 'minute_snapshot' in sql:
            assert target!='2026-10-16' and args[:-240]==(target,'000823')
            return [dict(stk_code='000823',end=datetime.fromisoformat(target+' 09:31:00'),open=10,high=11,low=9,close=10,volume=10)]
        raise AssertionError(sql)
    monkeypatch.setattr(view_module,'kingdom_connection',connection)
    monkeypatch.setattr(view_module,'query',query)
    live_calls=[]
    def minutes(code,now):
        assert target=='2026-10-16'
        live_calls.append(code)
        return [dict(date=target,time='09:31',open=10,high=11,low=9,close=10,volume=10)]
    def quotes(args):
        assert target=='2026-10-16' and args=={'codes':['000823']}
        return [],{}
    result=view_module.StrategyExecutionViewService().load_today_market(owner_user_id='alice',signal_date='2026-10-14',
        stock_code='000823',minute_offset=offset,now=datetime(2026,10,16,10,10,tzinfo=ZoneInfo('Asia/Shanghai')),
        quote_loader=quotes,minute_loader=minutes)
    assert result['signal_date']=='2026-10-14' and result['trade_date']==target
    assert result['minute_dates']==['2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16']
    assert result['requested_minutes']==(40 if offset==2 else 240)
    assert result['minute_source']==('upchina_1m_api' if offset==2 else 'minute_database')
    assert len(result['stocks'])==1 and result['stocks'][0]['bars'][0]['date']==target
    assert live_calls==(['000823'] if offset==2 else [])


def test_minute_navigation_calendar_skips_weekend_and_blocks_future_and_unowned_code(monkeypatch):
    @contextmanager
    def connection(_):yield object()
    def query(_conn,sql,args=()):
        if 'information_schema.tables' in sql:return [{'table_name':'strategy_execution'}]
        if 'aiia_trade_calendar' in sql:
            if 'calendar_date<%s' in sql:days=['2026-10-16','2026-10-15']
            elif 'calendar_date>%s' in sql:days=[]
            else:days=['2026-10-19','2026-09-11']
            return [{'calendar_date':date.fromisoformat(d)} for d in days]
        if 'FROM strategy_execution' in sql:
            return [{'stock_code':'000823','stock_name':'超声电子'}]
        raise AssertionError('Unowned code must not query market data: '+sql)
    monkeypatch.setattr(view_module,'kingdom_connection',connection)
    monkeypatch.setattr(view_module,'query',query)
    args=dict(owner_user_id='alice',signal_date='2026-10-19',stock_code='999999',
              now=datetime(2026,10,19,10,tzinfo=ZoneInfo('Asia/Shanghai')))
    service=view_module.StrategyExecutionViewService()
    result=service.load_today_market(**args,minute_offset=-1)
    assert result['trade_date']=='2026-10-16'
    assert result['minute_dates']==['2026-10-15','2026-10-16','2026-10-19']
    assert result['stocks']==[]
    for offset in [-3,1,2,3]:
        with pytest.raises(ValueError):service.load_today_market(**args,minute_offset=offset)


def test_minute_navigation_route_validates_offset_and_retains_owner_scope():
    service=FakeView()
    client=_app({'user_type':'member','user_id':'alice'},service).test_client()
    response=client.get('/api/strategies/market?date=2026-10-14&minute_offset=2&stock_code=000823&user_id=imac')
    assert response.status_code==200
    assert service.calls[-1]==dict(owner_user_id='alice',signal_date='2026-10-14',minute_offset=2,stock_code='000823')
    count=len(service.calls)
    for query in ['minute_offset=-3','minute_offset=3','minute_offset=1.5','minute_offset=x','stock_code=abc','stock_code=000823.SZ']:
        assert client.get('/api/strategies/market?'+query).status_code==400
    assert len(service.calls)==count
    def unavailable(**kwargs):raise ValueError('不能翻到今天之后')
    service.load_today_market=unavailable
    assert client.get('/api/strategies/market?minute_offset=1').status_code==400


def test_page_route_cursor_and_owner_are_validated():
    class PageView(FakeView):
        def load_page(self, **kwargs):
            self.calls.append(kwargs)
            return dict(ok=True,records={},markets={},next_before=None)
        def load_live(self, **kwargs):
            self.calls.append(kwargs)
            return dict(ok=True,records={},market={})
    service=PageView()
    client=_app({'user_type':'member','user_id':'alice'},service).test_client()
    assert client.get('/api/strategies/page?before=not-a-date').status_code==400
    assert client.get('/api/strategies/page?before=2099-01-01').status_code==400
    assert not service.calls
    response=client.get('/api/strategies/page?user_id=imac&before=2026-09-11')
    assert response.status_code==200
    assert service.calls[-1]==dict(owner_user_id='alice',include_owners=False,before='2026-09-11')
    assert client.get('/api/strategies/live?user_id=imac').status_code==200
    assert service.calls[-1]==dict(owner_user_id='alice',include_daily=False)
    assert client.get('/api/strategies/live?daily=1').status_code==200
    assert service.calls[-1]['include_daily'] is True
    guest=_app(None,service).test_client()
    assert guest.get('/api/strategies/page').status_code==403
    assert guest.get('/api/strategies/live').status_code==403


def test_batch_history_queries_each_data_type_once_and_keeps_dates_separate(monkeypatch):
    @contextmanager
    def connection(_):yield object()
    calls=[]
    def query(conn,sql,args=()):
        calls.append((sql,args))
        if 'aiia_trade_calendar' in sql:
            return [dict(calendar_date=date(2026,10,d)) for d in [12,13,14,15,16]]
        if 'FROM kcrp_stock_price' in sql:
            return [dict(stk_code='603629.SH',trade_date=date(2026,10,d),preclose=10,open=10,high=12,low=9,close=d,volume=100,amount=1000) for d in [12,13,14,15,16]]
        if 'minute_snapshot' in sql:
            import json
            keys=json.loads(args[0])
            assert {day for day,end,code in keys}=={'2026-10-13','2026-10-14'}
            assert len(keys)==480
            assert keys==sorted(keys)
            return [dict(trade_date=date(2026,10,d),stk_code='603629',end=datetime(2026,10,d,9,31),open=10,high=15,low=9,close=d,volume=10,amount=100) for d in [13,14]]
        raise AssertionError(sql)
    monkeypatch.setattr(view_module,'kingdom_connection',connection);monkeypatch.setattr(view_module,'query',query)
    picks=[dict(signal_date=f'2026-10-{d}',stock_code='603629',stock_name='利通电子') for d in [13,14,16]]
    data=view_module.StrategyExecutionViewService().load_history_batch(picks,'2026-10-16')
    assert len(calls)==3
    assert data['2026-10-13']['stocks'][0]['bars'][0]['close']==13
    assert data['2026-10-14']['stocks'][0]['bars'][0]['close']==14
    assert data['2026-10-13']['stocks'][0]['daily_bars'][-1]['date']=='2026-10-13'
    assert data['2026-10-16']['stocks'][0]['daily_bars'][-1]['date']=='2026-10-15'
    assert data['2026-10-16']['stocks'][0]['bars']==[]
    assert data['2026-10-14']['minute_dates']==['2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16']

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

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


def test_today_market_only_reads_todays_selected_codes_and_bars(monkeypatch):
    @contextmanager
    def connection(_name):
        yield object()

    calls = []
    def query(_conn, sql, args=()):
        calls.append((sql, args))
        if "information_schema.tables" in sql:
            return [{"table_name": "automl_strategy_selection"}]
        if "FROM automl_strategy_selection" in sql:
            return [{"stock_code": "002636", "stock_name": "金安国纪"}]
        if "FROM aiia_stock_realtime_minute_snapshot" in sql:
            return [{"stk_code": "002636", "bar_end_time": datetime(2026, 10, 15, 9, 31),
                     "open_price": Decimal("70"), "high_price": Decimal("71"),
                     "low_price": Decimal("69"), "latest_price": Decimal("70.5"),
                     "volume": Decimal("100"), "amount": Decimal("7050")}]
        raise AssertionError(sql)

    monkeypatch.setattr(view_module, "kingdom_connection", connection)
    monkeypatch.setattr(view_module, "query", query)
    now = datetime(2026, 10, 15, 9, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = view_module.StrategyExecutionViewService().load_today_market(
        owner_user_id="alice", now=now,
        quote_loader=lambda args: ([{"code": "002636", "tradedate": "2026-10-15",
                                    "close": 70.5, "pct": 1.0, "snapshot_time": "2026-10-15 09:31:00"}], {}))
    assert result["requested_minutes"] == 1
    assert result["stocks"][0]["bars"][0]["time"] == "09:31"
    assert result["stocks"][0]["quote"]["close"] == 70.5
    assert all(args[0] == "alice" for sql, args in calls
               if "FROM automl_strategy_selection" in sql)
    assert any("trade_date=%s" in sql and "bar_end_time<=%s" in sql
               for sql, _ in calls)


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

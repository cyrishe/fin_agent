"""收盘开盘写库适配：中文版本身份、选股与次交易日09:40回填。"""
from __future__ import annotations

from decimal import Decimal
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests
from scripts.strategy_execution_store import record_daily_run, record_selection
from scripts.automl_strategy_execution import NEXT_0940_CLOSE

CLOSE_OPEN_ID = 'rolling20_7f_hgb_sklearn171_dbapi_amount_v2'
IDENTITIES = {
    CLOSE_OPEN_ID: ('收盘开盘', 'v1.0', '20日滚动·七因子四分类'),
    'B2': ('涨停冷静', 'v1.0', '四K形态后延后一根K模拟买入'),
}


def identity(strategy_id):
    name, version, description = IDENTITIES[strategy_id]
    return dict(strategy_name=name, model_version=version, version_desc=description)


def record_run(cursor, *, strategy_id, strategy_name, **kwargs):
    record_daily_run(cursor, **identity(strategy_id), **kwargs)


def record_decisions(cursor, *, user_id, strategy_id, strategy_name, signal_date,
                     picks, signal_at, model_sha256, environment,
                     exit_rule=NEXT_0940_CLOSE):
    for pick in picks:
        actionable = bool(pick.get('actionable', True))
        record_selection(cursor, user_id=user_id, **identity(strategy_id),
            signal_date=signal_date, selection_rank=int(pick['selection_rank']),
            stock_code=str(pick['symbol6']).zfill(6), stock_name=pick['name'],
            actionable=actionable, signal_at=signal_at, signal_price=pick.get('signal_price'),
            buy_at=signal_at if actionable else None,
            buy_price=pick.get('signal_price') if actionable else None,
            buy_price_basis='14:40_signal_quote_proxy' if actionable else None,
            details={**pick, 'legacy_strategy_id': strategy_id,
                     'exit_rule': exit_rule, 'simulation_only': True})


def api_0940_close(cursor, exit_date, stock_code, *, now=None):
    """Read the exact completed D1 09:40 bar from the minute API, never a later quote."""
    local = (now or datetime.now(ZoneInfo('Asia/Shanghai'))).astimezone(ZoneInfo('Asia/Shanghai'))
    target = datetime.fromisoformat(exit_date + ' 09:40:00').replace(tzinfo=local.tzinfo)
    if local < target:
        return None
    # Count history only when retrying an earlier trade day. Today's small
    # request includes one forming bar, discarded by the exact timestamp check.
    cursor.execute("""SELECT COUNT(*) AS days FROM aiia_trade_calendar
        WHERE market_code='CN_A' AND is_trade_day=1
          AND calendar_date>=%s AND calendar_date<%s""", (exit_date, local.date().isoformat()))
    history = int(cursor.fetchone()['days'])
    minute = local.hour * 60 + local.minute
    count = history * 240 + max(0, min(minute, 690)-570) + max(0, min(minute, 900)-780)
    forming = time(9,30) <= local.time() < time(11,30) or time(13) <= local.time() < time(15)
    market = 1 if stock_code.startswith('6') else 7 if stock_code.startswith(('9','4','8')) else 0
    response = requests.post('http://jzyzwup.upoem1.com/json/hq_marketdata/kLineData',
        json={'stReq': {'stHeader': {'shtMarket': market}, 'sCode': stock_code,
                       'eLineType': 1, 'shtStartxh': 0, 'shtWantNum': count + int(forming)}},
        timeout=(2,4))
    response.raise_for_status()
    prices = []
    for bar in response.json().get('stRsp', {}).get('vAnalyData', []):
        stamp = bar['sttDateTime']
        end = datetime.strptime(str(stamp['iDate']), '%Y%m%d') + timedelta(minutes=int(stamp['shtTime']))
        if end == target.replace(tzinfo=None):
            value = Decimal(str(bar['fClose']))
            if value.is_finite() and value > 0:
                prices.append(value)
    return prices[0] if len(prices) == 1 else None


def settle_previous(cursor, *, user_id, strategy_id, as_of_date, price_loader=None):
    spec = identity(strategy_id)
    cursor.execute("""SELECT e.signal_date,e.selection_rank,e.stock_code,e.buy_price,
        (SELECT MIN(c.calendar_date) FROM aiia_trade_calendar c
         WHERE c.market_code='CN_A' AND c.is_trade_day=1
           AND c.calendar_date>e.signal_date) AS exit_date
        FROM strategy_execution e WHERE e.user_id=%s AND e.strategy_name=%s
          AND e.model_version=%s AND e.actionable=1 AND e.buy_price IS NOT NULL
          AND e.sell_price IS NULL AND e.signal_date<%s
          AND JSON_UNQUOTE(JSON_EXTRACT(e.details_json,'$.exit_rule'))=%s
        ORDER BY e.signal_date,e.selection_rank FOR UPDATE""",
        (user_id, spec['strategy_name'], spec['model_version'], as_of_date, NEXT_0940_CLOSE))
    completed = []
    for row in cursor.fetchall():
        if row['exit_date'] is None or str(row['exit_date']) > as_of_date:
            continue
        day = str(row['exit_date'])
        price = (price_loader or api_0940_close)(cursor, day, row['stock_code'])
        if price is None:
            continue
        entry = Decimal(str(row['buy_price']))
        if entry <= 0:
            raise ValueError('买入代理价必须大于零')
        gross = (price / entry - 1) * 100
        cursor.execute("""UPDATE strategy_execution SET sell_at=%s,sell_price=%s,
            sell_price_basis=%s,gross_return_pct=%s WHERE user_id=%s
            AND strategy_name=%s AND model_version=%s AND signal_date=%s
            AND selection_rank=%s AND sell_price IS NULL""",
            (day+' 09:40:00', price, '09:40_minute_api_close_proxy', gross,
             user_id, spec['strategy_name'], spec['model_version'],
             row['signal_date'], row['selection_rank']))
        if cursor.rowcount != 1:
            raise ValueError('待回填交易被并发修改')
        completed.append(dict(signal_date=str(row['signal_date']),
            selection_rank=row['selection_rank'], stock_code=row['stock_code'],
            exit_date=day, entry_price=str(entry), exit_price=str(price),
            gross_return_pct=str(gross)))
    return completed

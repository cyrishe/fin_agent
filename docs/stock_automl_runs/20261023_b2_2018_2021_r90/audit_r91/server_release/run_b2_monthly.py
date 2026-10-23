"""Server B2 buy/sell scheduler; V1 stays running as V2's shadow account.

The monthly switch is stored in existing strategy_daily_run.environment_json.
No broker orders are sent. The existing completed-minute price proxy is kept.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pandas as pd

from scripts import run_automl_b2 as base
from scripts.automl_b2 import valid_bars
from scripts.automl_b2_data import calendar
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.run_automl_1440_strategy import write_connection
from scripts.b2_monthly_pause import (V2_ID,V2_SHA,V2_KEY,prior_month,digest,
                                      calculate_gate,check_gate)

TZ = ZoneInfo('Asia/Shanghai')


def decoded(x):
    return json.loads(x) if isinstance(x, str) else x or {}


def saved(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.next')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False)+'\n')
    tmp.replace(path)


def read_rows(db, sql, params):
    with db.cursor() as c:
        c.execute(sql, params)
        return c.fetchall()


def verify_definition(db, user):
    rows=read_rows(db,'SELECT definition_sha256 FROM automl_strategy_definition WHERE user_id=%s AND strategy_id=%s',(user,V2_ID))
    if len(rows)!=1 or rows[0]['definition_sha256']!=V2_SHA:
        raise ValueError('V2冻结定义未登记或哈希不同')


def load_gate(db, user, month):
    rows=read_rows(db,"""SELECT signal_date,environment_json FROM strategy_daily_run
        WHERE user_id=%s AND strategy_name=%s AND model_version=%s
        AND signal_date>=%s AND signal_date<DATE_ADD(%s,INTERVAL 1 MONTH)
        ORDER BY signal_date""",(user,*V2_KEY,month+'-01',month+'-01'))
    gates=[decoded(r['environment_json']).get('monthly_gate') for r in rows]
    if not gates:
        return None
    if any(g is None for g in gates) or len({g['sha256'] for g in gates})!=1:
        raise ValueError('月内已有V2记录的开关不完整或不一致')
    check_gate(gates[0],month+'-01')
    return gates[0]


def morning_complete(picks, bars, day, now):
    if any(p.get('actionable') for p in picks):
        return True
    for p in picks:
        if 'ordinary' not in p:
            return False
        if not p['ordinary']:
            continue
        if not p.get('shape_ready'):
            return False
        b=valid_bars(bars.get(p['symbol6'],pd.DataFrame()),day,now,p['cap'])
        expected={f'09:{n:02d}' for n in range(31,41 if p.get('shape_pass') else 35)}
        if not expected.issubset(set(b.time.dt.strftime('%H:%M'))):
            return False
    return True


def legacy_complete(args, d0, picks):
    folder=args.output/d0
    morning=folder/'morning.json'; observations=folder/'minute_observations.jsonl'
    if not morning.exists() or not observations.exists():
        return None
    snapshot=json.loads(morning.read_text())
    if snapshot.get('signal_date')!=d0:
        return None
    original={(p['symbol6'],int(p['selection_rank'])) for p in picks}
    if original!={(p['symbol6'],int(p['selection_rank'])) for p in snapshot['picks']}:
        raise ValueError('旧早盘回执与数据库候选不符')
    last=None
    with observations.open() as f:
        for line in f:
            if line.strip():
                item=json.loads(line)
                if item.get('observed_at')==snapshot['observed_at']:
                    last=item
    if not last or last.get('errors'):
        return None
    bars={k:pd.DataFrame(v) for k,v in last['bars'].items()}
    now=datetime.fromisoformat(snapshot['observed_at'])
    if morning_complete(snapshot['picks'],bars,snapshot['day'],now):
        return {'confirmed_at':now.isoformat(),'receipt_sha256':digest(snapshot)}
    return None


def compute_gate(args, month):
    now=datetime.now(TZ)
    with db_connection(args.env_file) as db:
        verify_definition(db,args.user_id)
        old=load_gate(db,args.user_id,month)
        if old:
            db.rollback(); return old
        cal=calendar(db)
        previous=prior_month(month)
        month_days=[d for d in cal if d.startswith(previous)]
        if not month_days:
            raise ValueError('上月交易日历缺失')
        # Include enough preceding days to prove a flat seed; never invent one.
        first=cal.index(month_days[0]); start=cal[max(0,first-30)]
        last=month_days[-1]
        # A rolling read horizon must not conceal an older unclosed lot.
        pending=read_rows(db,"""SELECT signal_date,details_json FROM strategy_execution
            WHERE user_id=%s AND strategy_name=%s AND model_version=%s
            AND actionable=1 AND sell_price IS NULL AND signal_date<=%s""",
            (args.user_id,*base.IDENTITY_KEY,last))
        for row in pending:
            d0=str(row['signal_date'])
            due=decoded(row['details_json']).get('planned_exit_date')
            if not due and d0 in cal:
                due=cal[cal.index(d0)+2]
            if not due or due<=last:
                raise ValueError('影子存在早期未结清持仓，不能使用空仓归一化: '+d0)
        final_d1=cal[cal.index(last)+1]
        if now < datetime.fromisoformat(final_d1+'T09:40:00').replace(tzinfo=TZ):
            raise ValueError('月末D0的D1判断尚未结束')
        runs=read_rows(db,"""SELECT * FROM strategy_daily_run WHERE user_id=%s
            AND strategy_name=%s AND model_version=%s AND signal_date BETWEEN %s AND %s
            ORDER BY signal_date""",(args.user_id,*base.IDENTITY_KEY,start,last))
        records=read_rows(db,"""SELECT * FROM strategy_execution WHERE user_id=%s
            AND strategy_name=%s AND model_version=%s AND signal_date BETWEEN %s AND %s
            ORDER BY signal_date,selection_rank""",(args.user_id,*base.IDENTITY_KEY,start,last))
        grouped={}
        for r in records:
            grouped.setdefault(str(r['signal_date']),[]).append(r)
        coverage={}; trades=[]; evidence=[]
        for run in runs:
            day=str(run['signal_date']); rows=grouped.get(day,[])
            if len(rows)!=int(run['selection_count']):
                continue
            picks=[{**decoded(r['details_json']),'actionable':int(r['actionable']),
                    'symbol6':r['stock_code'],'selection_rank':int(r['selection_rank'])} for r in rows]
            env=decoded(run['environment_json'])
            chosen=[r for r in rows if r['actionable']]
            if len(chosen)>1:
                raise ValueError('影子账户一天出现多个买入')
            proof=env.get('morning_complete')
            if not rows or chosen or env.get('source')=='historical_replay':
                proof=proof or {'source':env.get('source','stored_execution'),'recorded_at':str(run['created_at'])}
            if not proof:
                proof=legacy_complete(args,day,picks)
            if not proof:
                continue
            coverage[day]=proof
            evidence.append({'d0':day,'proof':proof,'run_created_at':str(run['created_at'])})
            for r in chosen:
                facts=decoded(r['details_json']); d1=cal[cal.index(day)+1];d2=cal[cal.index(day)+2]
                if not r['buy_price'] or not r['buy_at'] or str(r['buy_at'])[:10]!=d1:
                    raise ValueError('影子成交缺买价或D1日期不符')
                if facts.get('planned_exit_date',d2)!=d2:
                    raise ValueError('影子D2日期不符')
                sell=float(r['sell_price']) if r['sell_price'] is not None else None
                if d2<=last and (sell is None or str(r['sell_at'])[:10]!=d2):
                    raise ValueError('影子历史D2尚未结算: '+day)
                trades.append({'d0':day,'symbol':r['stock_code'],'buy_day':d1,
                               'buy_price':float(r['buy_price']),'exit_day':d2,'sell_price':sell})
        symbols=sorted({t['symbol'] for t in trades if t['buy_day']<=last})
        prices=[]
        if symbols:
            marks=','.join(['%s']*len(symbols))
            prices=read_rows(db,f"""SELECT trade_date,LEFT(stk_code,6) symbol,close
                FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s
                AND LEFT(stk_code,6) IN ({marks})""",(start,last,*symbols))
        closes={(str(r['symbol']),str(r['trade_date'])):float(r['close']) for r in prices if r['close'] is not None}
        db.rollback()
    source={'account':'B2/v1.0 always-on shadow','read_at':now.isoformat(),
            'coverage':evidence,'inputs_sha256':digest({'trades':trades,'prices':prices,'coverage':coverage}),
            'normalization':'Verified flat close; fractional shares and proportional fees preserve return ratios.',
            'historical_note':'历史回放记录可用于当前决策；不表示当年已实时运行V2。'}
    return calculate_gate(month,cal,trades,closes,coverage,
                          sum(t['d0'].startswith(previous) for t in trades),now.isoformat(),source)


def persist_morning_proof(args, snapshot, bars, now):
    if not args.write_db or not morning_complete(snapshot['picks'],bars,snapshot['day'],now):
        return
    proof={'confirmed_at':now.isoformat(),'receipt_sha256':digest(snapshot)}
    with write_connection(args.env_file) as db:
        with db.cursor() as c:
            c.execute("""UPDATE strategy_daily_run SET environment_json=JSON_SET(
                COALESCE(environment_json,JSON_OBJECT()),'$.morning_complete',CAST(%s AS JSON))
                WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date=%s
                AND JSON_EXTRACT(environment_json,'$.morning_complete') IS NULL""",
                (base.json_text(proof),args.user_id,*base.IDENTITY_KEY,snapshot['signal_date']))
        db.commit()


def buy(args):
    with db_connection(args.env_file) as db:
        cal=calendar(db)
        if args.day not in cal:
            db.rollback();return {'day':args.day,'note':'非交易日'}
        d0=cal[cal.index(args.day)-1]
        gate=load_gate(db,args.user_id,d0[:7])
        rows=read_rows(db,"""SELECT environment_json FROM strategy_daily_run WHERE user_id=%s
            AND strategy_name=%s AND model_version=%s AND signal_date=%s""",(args.user_id,*V2_KEY,d0))
        db.rollback()
    error=None
    def snapshot_callback(snapshot,bars,now):
        nonlocal error
        try:
            persist_morning_proof(args,snapshot,bars,now)
            if not rows:
                return  # Never synthesize a missing previous-close V2 signal.
            if gate is None:
                raise ValueError('V2缺月度开关，禁止新买入')
            paused=check_gate(gate,d0,before=datetime.fromisoformat(args.day+'T09:30:00').replace(tzinfo=TZ))
            if paused:
                return
            values=[{**p,'monthly_gate_sha256':gate['sha256'],'legacy_strategy_id':V2_ID} for p in snapshot['picks']]
            if args.write_db:
                base.persist_scan(args,d0,values,now,strategy_id=V2_ID)
            saved(args.v2_output/d0/'morning.json',{**snapshot,'picks':values,'monthly_gate_sha256':gate['sha256']})
        except Exception as exc:
            message=f'{type(exc).__name__}: {exc}'
            if message!=error:
                print('V2买入处理失败，影子继续：'+message,file=sys.stderr,flush=True)
            error=message
            saved(args.v2_output/d0/'buy_error.json',{'observed_at':now.isoformat(),'error':message})
    result=base.scan(args,on_snapshot=snapshot_callback)
    if error:
        raise ValueError(error)
    return {'day':args.day,'signal_date':d0,'v2_signal_present':bool(rows),
            'paused':gate['paused'] if gate else None,'shadow':base.concise(result)}


def save_v2_close(args, original, gate):
    paused=check_gate(gate,args.day)
    result={**original,'strategy_id':V2_ID,
            'picks':[] if paused else [{**p,'monthly_gate_sha256':gate['sha256']} for p in original['picks']],
            'environment':{**original['environment'],'legacy_strategy_id':V2_ID,
                'definition_sha256':V2_SHA,'model_version':V2_KEY[1],
                'version_desc':'月度停摆·四K形态延后一根K模拟买入',
                'monthly_gate':gate,'monthly_paused':paused,
                'decision_summary':'月度停摆，本月不新增买入' if paused else '月度放行，等待D1四K判断',
                'recorded_at':datetime.now(TZ).isoformat()}}
    if args.write_db:
        # Recheck persisted monthly gate before the first daily insert. The
        # scheduler's common flock serializes buy/sell and retries.
        with db_connection(args.env_file) as db:
            existing=load_gate(db,args.user_id,args.day[:7]);db.rollback()
        if existing and existing['sha256']!=gate['sha256']:
            raise ValueError('整月开关已冻结，禁止中途改变')
        base.save_close(args,result,strategy_id=V2_ID)
    saved(args.v2_output/args.day/'close.json',result)
    saved(args.v2_output/'months'/(args.day[:7]+'.json'),gate)
    return {'signal_date':args.day,'paused':paused,'r5':gate['r5'],'r10':gate['r10'],
            'candidate_count':original['candidate_count'],'selection_count':len(result['picks']),
            'gate_sha256':gate['sha256'],'write_db':args.write_db}


def sell(args):
    # Existing holdings are settled even during a paused month or when today's
    # selection/gate inputs are unavailable. Each operation remains retryable.
    outcome={}; errors=[]
    for name,key in [('shadow_sell',base.IDENTITY_KEY),('v2_sell',V2_KEY)]:
        try:
            outcome[name]=base.settle(args,strategy_key=key)
        except Exception as exc:
            errors.append(name+': '+str(exc))
    try:
        result=base.after_close(args)
        if 'picks' in result:
            gate=compute_gate(args,args.day[:7])
            outcome['v2_close']=save_v2_close(args,result,gate)
        else:
            outcome['close']=result
    except Exception as exc:
        errors.append('close/gate: '+str(exc))
    outcome.update(day=args.day,errors=errors,recorded_at=datetime.now(TZ).isoformat())
    saved(args.v2_output/args.day/'sell_receipt.json',outcome)
    if errors:
        raise ValueError(base.json_text(outcome))
    return outcome


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['buy','sell','gate'])
    p.add_argument('--env-file',type=Path,required=True)
    p.add_argument('--assets',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--v2-output',type=Path,required=True)
    p.add_argument('--user-id',default='imac')
    p.add_argument('--day',default=datetime.now(TZ).date().isoformat())
    p.add_argument('--write-db',action='store_true')
    p.add_argument('--watch',action='store_true')
    p.add_argument('--poll-seconds',type=float,default=3)
    p.add_argument('--release',default='B2_V2_SERVER_20261021')
    args=p.parse_args()
    if args.command in ('buy','sell') and args.day!=datetime.now(TZ).date().isoformat():
        raise ValueError('在线调度仅接受今天；不得回填为实时信号')
    if args.poll_seconds<1:
        raise ValueError('扫描间隔至少1秒')
    if args.command=='gate':
        result=compute_gate(args,args.day[:7])
        saved(args.v2_output/'gate_check.json',result)
        print(base.json_text({k:v for k,v in result.items() if k not in ('source','normalized_nav')}))
    else:
        print(base.json_text({'buy':buy,'sell':sell}[args.command](args)))


if __name__=='__main__':
    main()

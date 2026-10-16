"""Replay saved B2 monthly forecasts into the existing readable strategy tables.

prepare is read-only for DB; write requires a checked, hashed prepared payload.
Existing live rows are verified and preserved, never converted to hindsight signals.
"""
from __future__ import annotations
import argparse
from datetime import datetime
from decimal import Decimal
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

from scripts.automl_b2 import FEATURE_ORDER, STRATEGY_ID, EXIT_RULE, evaluate_morning, build_features, digest, definition
from scripts.automl_b2_data import calendar, refresh_daily, status_source, model_bundle, score, history_minutes
from scripts.experiment_limit_rebound_shape_r19 import facts
from scripts.run_automl_b2 import json_text, IDENTITY_KEY
from scripts.readable_strategy_execution import identity
from scripts.strategy_execution_store import record_daily_run, record_selection
from scripts.run_automl_1440_strategy import write_connection
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_limit_rebound_short_mixed_r12 import dump, sha


def decoded(x):
    return json.loads(x) if isinstance(x, str) else x or {}


def assert_json_matches(expected, actual):
    """MySQL JSON may round the last binary-float digit; identities stay exact."""
    if isinstance(expected,dict):
        assert isinstance(actual,dict) and expected.keys()==actual.keys()
        for key in expected:assert_json_matches(expected[key],actual[key])
    elif isinstance(expected,list):
        assert isinstance(actual,list) and len(expected)==len(actual)
        for a,b in zip(expected,actual):assert_json_matches(a,b)
    elif isinstance(expected,float):
        assert isinstance(actual,(int,float)) and math.isclose(expected,actual,rel_tol=1e-14,abs_tol=1e-12)
    else:assert expected==actual


def settlement(buy, d1, d2):
    """D2 close and original continuity guard; missing data stay unpriced."""
    if d1 is None or d2 is None or any(pd.isna(v) for v in (d1.close,d2.close,d2.preclose,d2.volume)):
        return {}
    if d1.close <= 0 or d2.close <= 0 or d2.volume <= 0 or abs(float(d2.preclose)-float(d1.close)) > .011:
        return {}
    entry, price = Decimal(str(buy)), Decimal(str(d2.close))
    return dict(sell_price=float(price), gross_return_pct=float((price/entry-1)*100),
                net_return_pct=float((price*Decimal('.999')/(entry*Decimal('1.001'))-1)*100))


def prepare(args):
    root=args.assets; out=args.output; out.mkdir(parents=True,exist_ok=True)
    now=datetime.now(ZoneInfo('Asia/Shanghai')); today=now.date().isoformat()
    with db_connection(args.env_file) as db:
        cal=calendar(db)
        complete=max(d for d in cal if d<today or (d==today and now.hour>=15))
        if args.end>complete:
            raise ValueError('回填信号日必须已经收盘')
        daily=refresh_daily(db,root,complete)
        # The server calendar currently contains 2026 only. Completed historical
        # sessions come from the frozen whole-market daily snapshot; keep the
        # official future calendar for pending D1/D2 dates.
        historical_days=sorted(daily.trade_date.unique())
        cal=sorted(set(cal).union(historical_days))
        hist=status_source(db,root)
        names=query(db,'SELECT LEFT(stk_code,6) AS symbol,stk_name FROM kcrp_stock_baseinfo')
        name_map=dict(zip(names.symbol,names.stk_name))
        existing=query(db,'SELECT * FROM strategy_daily_run WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date BETWEEN %s AND %s',(args.user_id,*IDENTITY_KEY,args.start,args.end))
        live={str(r.signal_date):r for r in existing.itertuples() if decoded(r.environment_json).get('source')!='historical_replay'}
        ref=pd.read_pickle(root/'reference/panel.pkl')
        cohort=pd.read_pickle(root/'reference/four_all_top3.pkl')
        cohort=cohort[cohort.part.eq('test')].set_index(['symbol','d0_date'])
        last=ref.d0_date.max()
        extension=pd.DataFrame()
        if args.end>last:
            extension=build_features(daily,hist,out/'extension_features',(pd.Timestamp(last)+pd.Timedelta(days=1)).date().isoformat())
            extension.to_pickle(out/'extension_panel.pkl')
        q=daily.set_index(['symbol','trade_date']); evidence=[]; payload=[]; checked_scores=0; max_diff=0.; checked_entries=[]
        for month in pd.period_range(args.start[:7],args.end[:7],freq='M'):
            month=str(month); bundle,rule,manifest=model_bundle(root,month)
            saved=pd.read_pickle(root/'months'/month/'forecast_scores.pkl')
            saved=saved[saved.d0_date.between(args.start,args.end)]
            original=ref.merge(saved[['symbol','d0_date']],on=['symbol','d0_date'],validate='one_to_one')
            rescored=score(bundle,original)
            cols=['symbol','d0_date','score','d0_rank']
            pd.testing.assert_frame_equal(rescored[cols].reset_index(drop=True),saved[cols].reset_index(drop=True),check_dtype=False,atol=1e-14,rtol=0)
            checked_scores+=len(saved)
            if len(saved):max_diff=max(max_diff,float((rescored.score.reset_index(drop=True)-saved.score.reset_index(drop=True)).abs().max()))
            new=score(bundle,extension[extension.d0_date.str.startswith(month)]) if len(extension) else extension
            frames=pd.concat([rescored,new],ignore_index=True) if len(new) else rescored
            for day in [d for d in cal if args.start<=d<=args.end and d.startswith(month)]:
                day_scores=frames[frames.d0_date.eq(day)]
                rows=day_scores.sort_values('d0_rank').head(3)
                d1=cal[cal.index(day)+1]; d2=cal[cal.index(day)+2]
                if day in live:
                    if live[day].candidate_count!=len(day_scores) or live[day].model_sha256!=manifest['files']['model.joblib']:
                        raise ValueError('已存实时模型或候选数不同: '+day)
                    old=query(db,'SELECT selection_rank,stock_code,details_json FROM strategy_execution WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date=%s ORDER BY selection_rank',(args.user_id,*IDENTITY_KEY,day))
                    assert old.stock_code.tolist()==rows.symbol.tolist()
                    for stored,r in zip(old.itertuples(),rows.itertuples()):
                        assert abs(decoded(stored.details_json)['score']-r.score)<1e-14
                    payload.append(dict(signal_date=day,preserve_live=True));continue
                picks=[]; bars={}
                for r in rows.to_dict('records'):
                    code=r['symbol']; key=(code,day)
                    cached=cohort.loc[key] if key in cohort.index else None
                    b=history_minutes(db,root,code,d1) if (root/'minute_history'/f'{d1}_{code}.pkl').exists() or cached is None else pd.DataFrame()
                    if cached is not None:
                        ordinary=bool(cached.ordinary);audit=bool(cached.audit_ok==True);reason=cached.reason if isinstance(cached.reason,str) else '历史状态过滤'
                        pre=None if pd.isna(cached.d1_preclose) else float(cached.d1_preclose)
                        cap=None if pd.isna(cached.d1_cap) else float(cached.d1_cap)
                        state0=str(cached.state0);state1=str(cached.state1)
                    else:
                        from scripts.automl_b2 import historical_states
                        from scripts.experiment_automl_limit_rebound_d0_open_r5 import cap_price
                        st=historical_states(pd.DataFrame([{'symbol':code,'asof_date':d} for d in (day,d1)]),hist).set_index('asof_date')
                        state0=st.loc[day,'st_type'];state1=st.loc[d1,'st_type'];ordinary=state0 in ('N','R') and state1 in ('N','R')
                        if (code,d1) not in q.index:raise ValueError('新增回放缺D1完整日线: '+code+' '+d1)
                        quote=q.loc[(code,d1)];pre=float(quote.preclose);cap=cap_price(pre,r['rate'])
                        audited=facts(b,pd.Series({**r,'d1_high':quote.high,'d1_low':quote.low,'d1_preclose':pre,'d1_cap':cap,'d1_close':quote.close,'d1_volume':quote.volume}))
                        audit=bool(audited['audit_ok']);reason=audited['reason']
                    if ordinary and audit and len(b)!=240:raise ValueError('历史合格分钟资产缺失: '+code+' '+d1)
                    features={k:None if pd.isna(r[k]) else float(r[k]) for k in FEATURE_ORDER}
                    picks.append(dict(symbol6=code,name=name_map.get(code,code),selection_rank=int(r['d0_rank']),score=float(r['score']),signal_price=float(r['close']),
                        preclose=pre,cap=cap,rate=float(r['rate']),state0=state0,state1=state1,ordinary=ordinary and audit,
                        features=features,model_month=month,rule=rule['selected'],simulation_only=True,historical_replay=True,
                        legacy_strategy_id=STRATEGY_ID,exit_rule=EXIT_RULE,d1_date=d1,planned_exit_date=d2,
                        audit_ok=audit,audit_reason=reason,historical_ordinary=ordinary,observation_bars=4,
                        name_reference='数据库当前名称',replayed_at=now.isoformat()))
                    bars[code]=b
                    if ordinary and not audit:evidence.append(dict(signal_date=day,stock_code=code,reason=reason))
                vals=evaluate_morning(picks,bars,d1,datetime.fromisoformat(d1+' 09:40:00'),rule['selected']) if picks else []
                for z in vals:
                    if z['historical_ordinary'] and not z['audit_ok']:z['reason']='原历史审计排除：'+z['audit_reason']
                    if z['actionable']:
                        checked_entries.append(dict(symbol=z['symbol6'],d0_date=day,entry_time=z['entry_bar_end'][11:16],entry=z['entry_price']))
                        sell=settlement(z['entry_price'],q.loc[(z['symbol6'],d1)] if (z['symbol6'],d1) in q.index else None,q.loc[(z['symbol6'],d2)] if d2<=complete and (z['symbol6'],d2) in q.index else None)
                        z.update(sell)
                        if sell:z.update(sell_at=d2+' 15:00:00',sell_price_basis='D2_daily_close_simulation')
                        z['buy_price_basis']='completed_minute_open_historical_simulation'
                env=dict(source='historical_replay',simulation_only=True,legacy_strategy_id=STRATEGY_ID,model_month=month,
                    model_sha256=manifest['files']['model.joblib'],rule_sha256=manifest['files']['rule.json'],definition_sha256=digest(definition()),
                    monthly_manifest_sha256=sha(root/'months'/month/'manifest.json'),replayed_at=now.isoformat(),
                    evidence_note='使用当月事先冻结的模型/规则及历史分钟回放；不具备当时实时到达与委托成交证据。',
                    training_window=manifest.get('window') or manifest.get('source_metrics',{}).get('window'),observation_bars=4)
                payload.append(dict(signal_date=day,candidate_count=len(day_scores),model_sha256=manifest['files']['model.joblib'],environment=env,picks=vals))
            print('prepared',month,flush=True)
        db.rollback()
    expected=pd.read_pickle(root/'reference/policy_picks.pkl');expected=expected[expected.policy.eq('four_fit_delay')&expected.d0_date.between(args.start,min(args.end,last))]
    actual=pd.DataFrame(checked_entries);actual=actual[actual.d0_date.le(last)]
    pd.testing.assert_frame_equal(actual.sort_values('d0_date').reset_index(drop=True),expected[actual.columns].sort_values('d0_date').reset_index(drop=True),check_dtype=False,atol=1e-12,rtol=0)
    dump(out/'payload.json',payload)
    summary=dict(start=args.start,end=args.end,days=len(payload),historical_days=sum(not x.get('preserve_live') for x in payload),preserved_live_days=sum(bool(x.get('preserve_live')) for x in payload),
        selections=sum(len(x.get('picks',[])) for x in payload),trades=sum(z['actionable'] for x in payload for z in x.get('picks',[])),
        closed=sum(bool(z.get('sell_price')) for x in payload for z in x.get('picks',[])),missing_minutes=evidence,
        reproduced_prediction_rows=checked_scores,max_score_difference=max_diff,reproduced_reference_entries=len(actual),
        payload_sha256=sha(out/'payload.json'),prepared_at=now.isoformat())
    summary['calendar_source']='已完成日期取全市场日K快照，并合并服务器交易日历的未来日期'
    dump(out/'prepared.json',summary);print(json_text(summary))


def write(args):
    receipt=json.loads((args.output/'prepared.json').read_text());assert sha(args.output/'payload.json')==receipt['payload_sha256']
    payload=json.loads((args.output/'payload.json').read_text());counts={'inserted_days':0,'existing_days':0,'preserved_live_days':0}
    with write_connection(args.env_file) as db:
        try:
            with db.cursor() as c:
                backup=args.output/'db_before.json'
                if not backup.exists():
                    before={}
                    for table,order in [('strategy_daily_run','strategy_name,model_version,signal_date'),
                                        ('strategy_execution','strategy_name,model_version,signal_date,selection_rank'),
                                        ('automl_strategy_definition','strategy_id')]:
                        c.execute('SELECT * FROM '+table+' WHERE user_id=%s ORDER BY '+order,(args.user_id,))
                        before[table]=list(c.fetchall())
                    backup.write_text(json_text(before)+'\n')
                for day in payload:
                    if day.get('preserve_live'):counts['preserved_live_days']+=1;continue
                    key=(args.user_id,*IDENTITY_KEY,day['signal_date'])
                    c.execute('SELECT environment_json FROM strategy_daily_run WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date=%s FOR UPDATE',key)
                    old=c.fetchone()
                    if old and decoded(old['environment_json']).get('source')!='historical_replay':raise ValueError('已有实时记录，禁止历史覆盖')
                    record_daily_run(c,user_id=args.user_id,**identity(STRATEGY_ID),signal_date=day['signal_date'],signal_at=day['signal_date']+' 15:00:00',candidate_count=day['candidate_count'],selection_count=len(day['picks']),model_sha256=day['model_sha256'],environment=day['environment'])
                    for z in day['picks']:
                        record_selection(c,user_id=args.user_id,**identity(STRATEGY_ID),signal_date=day['signal_date'],selection_rank=z['selection_rank'],stock_code=z['symbol6'],stock_name=z['name'],actionable=bool(z['actionable']),signal_at=day['signal_date']+' 15:00:00',signal_price=z['signal_price'],buy_at=z.get('entry_at'),buy_price=z.get('entry_price'),buy_price_basis=z.get('buy_price_basis'),details=z)
                        c.execute('SELECT actionable,buy_price,sell_price FROM strategy_execution WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date=%s AND selection_rank=%s',(*key,z['selection_rank']))
                        row=c.fetchone()
                        if row['actionable']!=z['actionable'] or (float(row['buy_price']) if row['buy_price'] is not None else None)!=z.get('entry_price'):raise ValueError('历史回放与既有交易不一致')
                        if z.get('sell_price') is not None:
                            if row['sell_price'] is not None and float(row['sell_price'])!=z['sell_price']:raise ValueError('既有卖价不一致')
                            c.execute('UPDATE strategy_execution SET sell_at=%s,sell_price=%s,sell_price_basis=%s,gross_return_pct=%s,net_return_pct=%s WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date=%s AND selection_rank=%s AND sell_price IS NULL',(z['sell_at'],z['sell_price'],z['sell_price_basis'],z['gross_return_pct'],z['net_return_pct'],*key,z['selection_rank']))
                    counts['existing_days' if old else 'inserted_days']+=1
            db.commit()
        except Exception:db.rollback();raise
    dump(args.output/'write_receipt.json',counts);print(json_text(counts))


def verify(args):
    """Independent database readback of every prepared identity and price."""
    prepared=json.loads((args.output/'prepared.json').read_text())
    assert sha(args.output/'payload.json')==prepared['payload_sha256']
    payload=json.loads((args.output/'payload.json').read_text())
    with db_connection(args.env_file) as db:
        tables={}
        with db.cursor() as cursor:
            for table,order in [('strategy_daily_run','strategy_name,model_version,signal_date'),
                                ('strategy_execution','strategy_name,model_version,signal_date,selection_rank'),
                                ('automl_strategy_definition','strategy_id')]:
                cursor.execute('SELECT * FROM '+table+' WHERE user_id=%s ORDER BY '+order,(args.user_id,))
                tables[table]=list(cursor.fetchall())
        db.rollback()
    # JSON roundtrip preserves the same date/decimal representation as the backup.
    tables=json.loads(json_text(tables))
    is_b2=lambda r: (r.get('strategy_name'),r.get('model_version'))==IDENTITY_KEY
    runs={r['signal_date']:r for r in tables['strategy_daily_run'] if is_b2(r)}
    rows={(r['signal_date'],r['selection_rank']):r for r in tables['strategy_execution'] if is_b2(r)}
    count=0
    for day in payload:
        date=day['signal_date'];run=runs[date]
        if day.get('preserve_live'):continue
        assert run['candidate_count']==day['candidate_count'] and run['selection_count']==len(day['picks'])
        assert run['model_sha256']==day['model_sha256']
        assert decoded(run['environment_json'])==day['environment']
        for z in day['picks']:
            row=rows[(date,z['selection_rank'])];details=decoded(row['details_json'])
            assert row['stock_code']==z['symbol6'] and row['actionable']==z['actionable']
            assert_json_matches(z,details)
            assert 'actionable_confirmed_at' not in details
            for column,source in [('signal_price','signal_price'),('buy_price','entry_price'),('sell_price','sell_price'),
                                  ('gross_return_pct','gross_return_pct'),('net_return_pct','net_return_pct')]:
                actual=row[column];expected=z.get(source)
                assert (actual is None)==(expected is None),(date,column)
                if expected is not None:assert abs(float(actual)-expected)<=5.1e-9,(date,column)
            for column,source in [('buy_at','entry_at'),('sell_at','sell_at')]:
                expected=z.get(source)
                assert row[column]==(expected.replace('T',' ') if expected else None),(date,column)
            count+=1
    before=json.loads((args.output/'db_before.json').read_text())
    for table,oldrows in before.items():
        for old in oldrows:
            # Older backups used pandas, which rendered SQL NULL datetimes NaT.
            for key in ('buy_at','sell_at','signal_at'):
                if old.get(key)=='NaT':old[key]=None
            if table=='automl_strategy_definition':
                current=next(r for r in tables[table] if r['strategy_id']==old['strategy_id'])
            else:
                key=['strategy_name','model_version','signal_date']+(['selection_rank'] if table=='strategy_execution' else [])
                current=next(r for r in tables[table] if all(r[k]==old[k] for k in key))
            assert current==old,('已有记录被修改',table)
    frame=pd.DataFrame([r for r in rows.values() if prepared['start']<=r['signal_date']<=prepared['end']])
    assert {d for d in runs if prepared['start']<=d<=prepared['end']}=={d['signal_date'] for d in payload}
    assert len(frame)==sum(runs[d['signal_date']]['selection_count'] for d in payload)
    assert frame.groupby('signal_date').actionable.sum().le(1).all()
    monthly=[]
    for month,g in frame.groupby(frame.signal_date.str[:7]):
        monthly.append(dict(month=month,days=g.signal_date.nunique(),selections=len(g),trades=int(g.actionable.sum()),closed=int(g.sell_price.notna().sum())))
    result=dict(verified_selections=count,existing_records_unchanged=True,definition_unchanged=True,
        total_days=frame.signal_date.nunique(),total_selections=len(frame),total_trades=int(frame.actionable.sum()),
        closed=int(frame.sell_price.notna().sum()),monthly=monthly,payload_sha256=prepared['payload_sha256'],
        verified_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
    dump(args.output/'readback.json',result);print(json_text(result))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['prepare','write','verify']);p.add_argument('--assets',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--env-file',type=Path,default=Path('.env'));p.add_argument('--user-id',default='imac');p.add_argument('--start',default='2025-10-16');p.add_argument('--end',default='2026-10-15');a=p.parse_args()
    {'prepare':prepare,'write':write,'verify':verify}[a.command](a)


if __name__=='__main__':main()

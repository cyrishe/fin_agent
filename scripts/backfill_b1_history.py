"""Rebuild B1 history from raw minute files/DB, refit daily, and settle D1 09:40.

No orders are placed. Each day is cached once; every fit uses only its previous
20 trading days. A new readable version preserves existing production records.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from decimal import Decimal
import hashlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import time
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
import sklearn
from threadpoolctl import threadpool_limits
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.build_automl_tail_standard import prior_profile
from scripts.automl_st_status import load_st_intervals
from scripts.prepare_automl_mk_1m_nonst import annotate
from scripts.audit_automl_mk_1m_entry_limit import mark_entry_limit
from scripts.run_automl_four_class_pipeline import (
    generate_training_features, generate_training_labels, train_model,
    infer, select_inference_candidates, select_top,
)
from scripts.strategy_execution_store import record_daily_run, record_selection

ZONE=ZoneInfo('Asia/Shanghai')
CLOCKS=tuple(pd.date_range('09:31',periods=120,freq='min').strftime('%H:%M:%S'))+tuple(pd.date_range('13:01',periods=100,freq='min').strftime('%H:%M:%S'))
PROBS=['p_lt0','p_0to1','p_1to3','p_ge3']
VERSION_DESC='B1七因子20日·分钟文件及数据库重建'


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def clean(value):
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    if isinstance(value,(float,np.floating)) and not np.isfinite(value):return None
    if isinstance(value,np.generic):return value.item()
    return value


def save_json(path,value):
    Path(path).write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,default=str,allow_nan=False)+'\n')


def read(path):return pd.read_csv(path,dtype={'symbol6':str},float_precision='round_trip')


def raw_summary(bars,day):
    """Aggregate the signal prefix and morning independently; never read future closes."""
    b=bars.copy();b['symbol6']=b.symbol.astype(str).str[:6];b['clock']=b.time.str[-8:]
    if not b.time.str[:10].eq(day).all() or b.duplicated(['symbol6','time']).any():
        raise ValueError(f'{day}: minute date/key mismatch')
    b=b[b.symbol6.str.startswith(('0','3','6'))]
    valid=(b[['open','high','low','close']].gt(0).all(axis=1)&b.volume.ge(0)&
           b.high.ge(b[['open','close']].max(axis=1))&b.low.le(b[['open','close']].min(axis=1)))
    b['valid']=valid
    cut=b[b.clock.isin(CLOCKS)]
    agg=cut.groupby('symbol6',sort=False).agg(bars=('clock','size'),exact=('valid','sum'),
        minute_volume_shares=('volume','sum'),min_low_so_far=('low','min'),amount=('turnover','sum'))
    amount_valid=cut.groupby('symbol6').turnover.agg(lambda x: x.notna().all() and x.ge(0).all())
    agg.loc[~amount_valid,'amount']=np.nan
    agg=agg[agg.bars.eq(220)&agg.exact.eq(220)].drop(columns=['bars','exact'])
    signal=cut[cut.clock.eq('14:40:00')].set_index('symbol6').close.rename('signal_price')
    agg=agg.join(signal).reset_index()
    morning=b[b.clock.isin(CLOCKS[:10])]
    coverage=morning.groupby('symbol6').agg(n=('clock','size'),valid=('valid','sum'),volume=('volume','sum'))
    volume5=morning[morning.clock.isin(CLOCKS[:5])].groupby('symbol6').volume.sum()
    keys=coverage.index[coverage.n.eq(10)&coverage.valid.eq(10)&volume5.reindex(coverage.index).gt(0)]
    good=morning[morning.symbol6.isin(keys)].sort_values(['symbol6','high'],ascending=[True,False])
    second=good.groupby('symbol6').nth(1).set_index('symbol6').high.rename('second_high')
    # A sale needs the exact 09:40 bar, even if another morning bar is missing.
    sell=morning[morning.clock.eq('09:40:00')&morning.valid&morning.volume.gt(0)].set_index('symbol6').close.rename('close_40')
    labels=pd.concat([second,sell],axis=1).reset_index()
    return agg,labels


def database_summary(db,day,signal=True):
    base="FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1"
    exact="is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time"
    morning=query(db,"SELECT stk_code AS symbol,bar_end_time AS time,open_price AS open,high_price AS high,low_price AS low,latest_price AS close,volume*100 AS volume,amount AS turnover "+base+" AND bar_end_time BETWEEN %s AND %s AND "+exact,(day,day+' 09:31:00',day+' 09:40:00'))
    if morning.empty:
        if not signal and day==datetime.now(ZONE).date().isoformat():
            # Today's DB batch may not be ingested yet. Only selected exits
            # are fetched from the minute API after all prediction is complete.
            return pd.DataFrame(),pd.DataFrame(columns=['symbol6','second_high','close_40'])
        raise ValueError(f'{day}: no morning data')
    morning['time']=morning.time.astype(str)
    for c in ['open','high','low','close','volume','turnover']:morning[c]=pd.to_numeric(morning[c])
    _,labels=raw_summary(morning,day)
    if not signal:return pd.DataFrame(),labels
    sql="""SELECT stk_code AS symbol6,COUNT(*) AS bars,
      SUM(is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time
          AND open_price>0 AND high_price>=GREATEST(open_price,latest_price)
          AND low_price<=LEAST(open_price,latest_price) AND low_price>0 AND latest_price>0
          AND volume>=0) AS exact,
      SUM(volume)*100 AS minute_volume_shares,MIN(low_price) AS min_low_so_far,CASE WHEN COUNT(amount)=220 AND MIN(amount)>=0 THEN SUM(amount) END AS amount,
      MAX(CASE WHEN bar_end_time=%s THEN latest_price END) AS signal_price """+base+" AND bar_end_time IN ("+','.join(['%s']*220)+") GROUP BY stk_code"
    data=query(db,sql,(day+' 14:40:00',day,*[day+' '+c for c in CLOCKS]))
    if data.empty:raise ValueError(f'{day}: no 14:40 data')
    data=data[data.bars.eq(220)&data.exact.eq(220)&data.symbol6.str.startswith(('0','3','6'))].drop(columns=['bars','exact'])
    for c in data.columns.difference(['symbol6']):data[c]=pd.to_numeric(data[c])
    return data,labels


def prepare_sources(args):
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    with db_connection(args.env_file) as db:
        if not (out/'calendar.csv').exists():
            cal=query(db,"SELECT calendar_date AS date FROM aiia_trade_calendar WHERE market_code='CN_A' AND is_trade_day=1 AND calendar_date BETWEEN %s AND %s ORDER BY calendar_date",('2025-10-01','2026-11-10'))
            cal['date']=cal.date.astype(str)
            # The maintained calendar starts in 2026; observed daily bars supply
            # the earlier warmup trading dates, as in the original B1 builder.
            earlier=query(db,"SELECT DISTINCT trade_date AS date FROM kcrp_stock_price WHERE trade_date >= %s AND trade_date < %s ORDER BY trade_date",('2025-10-01',cal.date.min()))
            earlier['date']=earlier.date.astype(str)
            cal=pd.concat([earlier,cal]).drop_duplicates().sort_values('date')
            cal.to_csv(out/'calendar.csv',index=False)
        calendar=read(out/'calendar.csv').date.tolist()
        now=datetime.now(ZONE);cutoff=min(args.end,now.date().isoformat())
        days=[d for d in calendar if args.start<=d<=cutoff and (d<now.date().isoformat() or now.hour*60+now.minute>=880)]
        if not days:raise ValueError('No completed decision date')
        first=calendar.index(days[0]);needed=calendar[first-20:calendar.index(days[-1])+2]
        needed=[d for d in needed if d<=now.date().isoformat() and (d<now.date().isoformat() or now.hour*60+now.minute>=580)]
        if len(calendar[:first])<20:raise ValueError('Missing warmup calendar')
        plan={'requested_start':args.start,'requested_end':args.end,'signal_days':days,'minute_days':needed,'as_of':now.isoformat()}
        save_json(out/'plan.json',plan)
        if not all((out/f).exists() for f in ('daily.csv.gz','names.csv','st.csv.gz')):
            raw=query(db,"SELECT trade_date AS date,LEFT(stk_code,6) AS symbol6,preclose,close,adjpreclose,adjclose,volume,turn_ratio FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s",('2025-10-01',days[-1]))
            raw['date']=raw.date.astype(str);raw.to_csv(out/'daily.csv.gz',index=False,float_format='%.17g')
            names=query(db,'SELECT LEFT(stk_code,6) AS symbol6,stk_name AS name FROM kcrp_stock_baseinfo')
            names.drop_duplicates('symbol6').to_csv(out/'names.csv',index=False)
            st=load_st_intervals(db);st.to_csv(out/'st.csv.gz',index=False)
        if not (out/'older_adjusted.csv.gz').exists():
            older=query(db,"SELECT trade_date AS date,LEFT(stk_code,6) AS symbol6,adjclose FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s",('2024-11-01','2025-09-30'))
            older.to_csv(out/'older_adjusted.csv.gz',index=False,float_format='%.17g')
    manifest=json.loads(args.minute_manifest.read_text()) if args.minute_manifest else []
    paths={Path(r['path']).name[:10]:r for r in manifest}
    tasks=[(args,day,paths.get(day),days[-1]) for day in needed]
    # File decoding/aggregation uses separate processes; cap DB readers at two.
    for from_file in (True,False):
        selected=[task for task in tasks if bool(task[2])==from_file]
        with ProcessPoolExecutor(max_workers=args.workers if from_file else min(args.workers,2)) as pool:
            list(pool.map(prepare_minute,selected))
    return plan


def prepare_minute(task):
    args,day,source_record,last_signal=task
    out=args.output
    dest=out/'minutes'/day;dest.mkdir(parents=True,exist_ok=True)
    if (dest/'complete.json').exists():return day,'cached'
    started=time.perf_counter();source=source_record
    if source:
        path=args.minute_root/source['path']
        if sha(path)!=source['sha256']:raise ValueError(f'{day}: uploaded minute hash differs')
        with subprocess.Popen(['zstd','-dc',str(path)],stdout=subprocess.PIPE) as proc:
            b=pd.read_csv(proc.stdout,dtype={'symbol':str})
            if proc.wait()!=0:raise ValueError('zstd decoding failed')
        summary,labels=raw_summary(b,day)
    else:
        with db_connection(args.env_file) as db:
            summary,labels=database_summary(db,day,signal=day<=last_signal)
        source={'path':'kingdomai minute table','source':'exact finalized 1m'}
    summary.to_csv(dest/'signal.csv.gz',index=False,float_format='%.17g')
    labels.to_csv(dest/'morning.csv.gz',index=False,float_format='%.17g')
    meta={'day':day,'signal_rows':len(summary),'morning_rows':len(labels),'source':source,
          'signal_sha256':sha(dest/'signal.csv.gz'),'morning_sha256':sha(dest/'morning.csv.gz'),
          'seconds':time.perf_counter()-started}
    save_json(dest/'complete.json',meta);print('minute',day,len(summary),round(meta['seconds'],2),flush=True)
    return day,'built'


def original_b1_profile(raw, older_adjusted):
    """Recreate CO7's extended history and the archived Jan-Jul builder window.

    Rolling MA sums retain floating-point state from their starting date. These
    input contexts are part of the frozen B1 recipe, even for equal-price ties.
    Other profile fields use at most 20 prior observations.
    """
    value=raw.loc[raw.turn_ratio.gt(0)&raw.volume.gt(0)&raw.close.gt(0)&
                  np.isfinite(raw.float_share)&np.isfinite(raw.float_mv),
                  ['date','symbol6','float_share','float_mv']].copy()
    profile,_=prior_profile(raw,value)
    older=older_adjusted.copy();older['date']=pd.to_datetime(older.date)
    adj=pd.concat([older,raw[['date','symbol6','adjclose']]],ignore_index=True)
    adj=adj[adj.symbol6.str.startswith(('0','3','6'))].sort_values(['symbol6','date'])
    if adj.duplicated(['date','symbol6']).any():raise ValueError('Overlapping adjusted history')
    for w in (5,10,20):
        adj[f'ma{w}_adj']=adj.groupby('symbol6').adjclose.transform(lambda x:x.rolling(w,min_periods=w).mean())
    cols=[f'ma{w}_adj' for w in (5,10,20)]
    profile=profile.drop(columns=cols).merge(adj[['date','symbol6',*cols]].rename(columns={'date':'prior_date'}),on=['prior_date','symbol6'],how='left',validate='one_to_one')
    # The original raw archive began 2026-01-05, and its builder loaded 70
    # calendar days of daily history. CO7 retained this Jan-Jul input context.
    legacy_start=pd.Timestamp('2026-01-05')-pd.Timedelta(days=70)
    legacy,_=prior_profile(raw[raw.date.ge(legacy_start)].copy(),value[value.date.ge(legacy_start)].copy())
    in_legacy=lambda f: pd.to_datetime(f.signal_date).between('2026-01-05','2026-07-31')
    return pd.concat([profile[~in_legacy(profile)],legacy[in_legacy(legacy)]],ignore_index=True)


def legacy_csv_floats(frame):
    """Preserve the two original default CSV parsing boundaries before fitting."""
    result=frame.copy();mask=result.signal_date.between('2026-01-05','2026-07-31')
    cols=[*FEATURES,'signal_price','second_high']
    values=result.loc[mask,cols]
    # trainable_candidates -> tradable_st_candidates -> training read. The
    # outer new snapshot is round-trip encoded only after these boundaries.
    for _ in range(2):values=pd.read_csv(StringIO(values.to_csv(index=False)))
    result.loc[mask,cols]=values.to_numpy()
    return result


def build_candidates(args,plan):
    out=args.output;raw=read(out/'daily.csv.gz');raw=raw[raw.symbol6.str.startswith(('0','3','6'))].copy()
    raw['date']=pd.to_datetime(raw.date)
    raw['float_share']=raw.volume/(raw.turn_ratio/100);raw['float_mv']=raw.float_share*raw.close
    profile=original_b1_profile(raw,read(out/'older_adjusted.csv.gz'))
    profile['signal_date']=pd.to_datetime(profile.signal_date).dt.strftime('%Y-%m-%d')
    calendar=read(out/'calendar.csv').date.tolist();nextday=dict(zip(calendar[:-1],calendar[1:]))
    names=read(out/'names.csv').set_index('symbol6').name
    parts=[];amounts=[]
    for day in plan['minute_days']:
        if day>plan['signal_days'][-1]:continue
        d=read(out/'minutes'/day/'signal.csv.gz');d['signal_date']=day
        amounts.append(d[['signal_date','symbol6','amount']])
        d=d.merge(raw[raw.date.eq(pd.Timestamp(day))][['symbol6','preclose']],on='symbol6',validate='one_to_one')
        d=d.merge(profile[profile.signal_date.eq(day)],on=['signal_date','symbol6'],how='left',validate='one_to_one')
        d['signal_return']=d.signal_price/d.preclose-1
        d['volume_ratio']=d.minute_volume_shares*240/(220*d.avg_volume5_shares)
        d['turnover_so_far_pct']=d.minute_volume_shares/d.float_share*100
        d['float_mv_100m_cny']=d.float_mv/1e8
        for w in (5,10,20):d[f'ma{w}']=d[f'ma{w}_adj']*d.preclose/d.prior_adjclose
        d['ma_bull_5_10_20']=(d.ma5>d.ma10)&(d.ma10>d.ma20)
        d['all_intraday_lows_above_ma']=d.min_low_so_far>d[['ma5','ma10','ma20']].max(axis=1)
        keep=(d.signal_return.between(.03,.06)&d.minute_volume_shares.gt(0)&d.min_low_so_far.gt(0)&d.history_complete.eq(True)&d.adj_price_break_20d.eq(False)&d.value_complete.eq(True)&d[list(FEATURES)].notna().all(axis=1))
        d=d[keep].copy();d['next_date']=nextday[day];d['name']=d.symbol6.map(names).fillna(d.symbol6)
        labelpath=out/'minutes'/nextday[day]/'morning.csv.gz'
        labels=read(labelpath) if labelpath.exists() else pd.DataFrame(columns=['symbol6','second_high','close_40'])
        # Preserve B1's archived training-label eligibility: its raw builder
        # included a morning label only when that stock's next-day prefix was
        # complete. The independently observed sale remains available.
        nextsignal=out/'minutes'/nextday[day]/'signal.csv.gz'
        if nextsignal.exists() and nextday[day]<=plan['signal_days'][-1]:
            labelkeys=read(nextsignal).symbol6
            labels.loc[~labels.symbol6.isin(labelkeys),'second_high']=np.nan
        else:
            labels['second_high']=np.nan
        d=d.merge(labels,on='symbol6',how='left',validate='one_to_one');parts.append(d)
    candidates=pd.concat(parts,ignore_index=True)
    st=read(out/'st.csv.gz')
    for col in ['begin_date','end_date','ann_date']:st[col]=pd.to_datetime(st[col])
    candidates=mark_entry_limit(annotate(candidates,st))
    if not candidates.entry_status_known.all():raise ValueError('Unknown historical ST status')
    candidates=candidates[~(candidates.historical_st&candidates.entry_at_limit)].copy()
    for col in FEATURES:candidates[col]=candidates[col].astype(float)
    candidates=candidates.sort_values(['signal_date','symbol6']).reset_index(drop=True)
    candidates=legacy_csv_floats(candidates)
    candidates.to_csv(out/'candidates.csv.gz',index=False,float_format='%.17g')
    pd.concat(amounts,ignore_index=True).to_csv(out/'amounts.csv.gz',index=False,float_format='%.17g')
    return candidates


def training_window(candidates,calendar,day):
    i=calendar.index(day);days=calendar[i-20:i]
    train=candidates[candidates.signal_date.isin(days)&candidates.second_high.notna()&candidates.next_date.le(day)].copy()
    if len(days)!=20 or set(train.signal_date)!=set(days):raise ValueError(f'{day}: incomplete previous20 training dates')
    return train,days


def fit_days(args,plan):
    if sklearn.__version__!='1.7.1':raise ValueError('B1 requires sklearn 1.7.1')
    out=args.output;c=read(out/'candidates.csv.gz');calendar=read(out/'calendar.csv').date.tolist()
    amounts=read(out/'amounts.csv.gz').set_index(['signal_date','symbol6']).amount
    result=[];folds=[]
    input_sha=sha(out/'candidates.csv.gz')
    with threadpool_limits(limits=1):
        for day in plan['signal_days']:
            dest=out/'models'/day;dest.mkdir(parents=True,exist_ok=True)
            train,days=training_window(c,calendar,day)
            stamp=dest/'complete.json'
            if stamp.exists():
                meta=json.loads(stamp.read_text())
                if meta['input_sha256']!=input_sha:raise ValueError('Cached model input differs')
                picked=read(dest/'picks.csv');folds.append(meta);result.append(picked);continue
            model=train_model(generate_training_features(train),generate_training_labels(train))
            scored=infer(model,select_inference_candidates(c,day));picked=select_top(scored)
            picked=picked.merge(c[c.signal_date.eq(day)][['symbol6','close_40']],on='symbol6',how='left',validate='one_to_one')
            gates=[]
            for p in picked.itertuples():
                past=[amounts.get((d,p.symbol6),np.nan) for d in days]
                amount=amounts.get((day,p.symbol6),np.nan)
                med=float(np.median(past)) if np.isfinite(past).all() else np.nan
                passed=bool(amount>=med) if np.isfinite(amount) and np.isfinite(med) and med>0 else None
                gates.append(dict(amount=amount,prior20_amount_median=med,prior20_amount_days=int(np.isfinite(past).sum()),
                                  amount_gate=passed,actionable=p.selection_rank==1 and passed is True))
            for col in ['amount','prior20_amount_median','prior20_amount_days','amount_gate','actionable']:
                picked[col]=[g[col] for g in gates]
            picked.to_csv(dest/'picks.csv',index=False,float_format='%.17g')
            scored.to_csv(dest/'scores.csv.gz',index=False,float_format='%.17g')
            train[['signal_date','symbol6','next_date','signal_price',*FEATURES,'second_high']].to_csv(dest/'training.csv.gz',index=False,float_format='%.17g')
            joblib.dump(model,dest/'model.joblib',compress=3)
            meta=dict(day=day,training_dates=days,training_rows=len(train),candidate_count=len(scored),selection_count=len(picked),
                      input_sha256=input_sha,model_sha256=sha(dest/'model.joblib'),training_sha256=sha(dest/'training.csv.gz'),sklearn=sklearn.__version__)
            save_json(stamp,meta);folds.append(meta);result.append(picked)
            print('fit',day,len(train),picked.symbol6.tolist(),flush=True)
    pd.concat(result,ignore_index=True).to_csv(out/'picks.csv',index=False,float_format='%.17g');save_json(out/'folds.json',folds)


def refresh_api_exits(args):
    from scripts.readable_strategy_execution import api_0940_close
    import requests
    path=args.output/'picks.csv';picks=read(path);now=datetime.now(ZONE)
    wanted=picks.actionable & picks.close_40.isna() & picks.next_date.eq(now.date().isoformat())
    if not wanted.any():return
    receipts=[]
    if 'exit_price_basis' not in picks:picks['exit_price_basis']='09:40_minute_close_proxy'
    with db_connection(args.env_file) as db:
        with db.cursor() as cur:
            for index,p in picks[wanted].iterrows():
                receipt={'signal_date':p.signal_date,'exit_date':p.next_date,'symbol6':p.symbol6,
                         'minute':'09:40:00','fetched_at':now.isoformat(),'source':'hq_marketdata/kLineData'}
                try:
                    value=api_0940_close(cur,p.next_date,p.symbol6,now=now)
                    receipt['price']=str(value) if value else None
                    if value is not None:
                        picks.loc[index,'close_40']=float(value)
                        picks.loc[index,'exit_price_basis']='09:40_minute_api_close_proxy'
                except requests.RequestException as exc:receipt['error']=str(exc)
                receipts.append(receipt)
    picks.to_csv(path,index=False,float_format='%.17g')
    for day in picks.loc[wanted,'signal_date'].unique():
        picks[picks.signal_date.eq(day)].to_csv(args.output/'models'/day/'picks.csv',index=False,float_format='%.17g')
    save_json(args.output/'api_exit_receipts.json',receipts)


def price(value):
    if value is None or pd.isna(value):return None
    return Decimal(str(value)).quantize(Decimal('.0001'))


def write_results(args):
    from scripts.run_automl_1440_strategy import write_connection
    out=args.output;picks=read(out/'picks.csv');folds=json.loads((out/'folds.json').read_text());complete=[]
    with write_connection(args.env_file) as db:
        for fold in folds:
            day=fold['day'];rows=picks[picks.signal_date.eq(day)]
            try:
                with db.cursor() as cur:
                    key=dict(user_id=args.user_id,strategy_name='收盘开盘',model_version=args.version,version_desc=VERSION_DESC,signal_date=day)
                    record_daily_run(cur,**key,signal_at=day+' 14:40:00',candidate_count=fold['candidate_count'],selection_count=len(rows),model_sha256=fold['model_sha256'],environment={'sklearn':'1.7.1','run':'B1_20261016_history_rebuild','input_sha256':fold['input_sha256'],'training_sha256':fold['training_sha256'],'simulation_only':True})
                    for p in rows.to_dict('records'):
                        actionable=bool(p['actionable']);buy=price(p['signal_price']) if actionable else None
                        record_selection(cur,**key,selection_rank=int(p['selection_rank']),stock_code=p['symbol6'],stock_name=p['name'],actionable=actionable,
                            signal_at=day+' 14:40:00',signal_price=price(p['signal_price']),buy_at=day+' 14:40:00' if actionable else None,buy_price=buy,
                            buy_price_basis='14:40_minute_close_proxy' if actionable else None,
                            details=clean({**p,'simulation_only':True,'exit_rule':'next_trade_day_0940_close','training_dates':fold['training_dates']}))
                        sell=price(p['close_40']) if actionable else None
                        if sell is not None:
                            sale_at=p['next_date']+' 09:40:00'
                            if sale_at>datetime.now(ZONE).strftime('%Y-%m-%d %H:%M:%S'):continue
                            identity=(args.user_id,'收盘开盘',args.version,day,int(p['selection_rank']))
                            cur.execute('SELECT actionable,buy_price,sell_price,sell_at FROM strategy_execution WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date=%s AND selection_rank=%s FOR UPDATE',identity)
                            existing=cur.fetchone()
                            if not existing['actionable'] or existing['buy_price']!=buy:raise ValueError('Existing entry differs')
                            if existing['sell_price'] is not None:
                                if existing['sell_price']!=sell or str(existing['sell_at'])!=sale_at:raise ValueError('Existing exit differs')
                            else:
                                cur.execute('UPDATE strategy_execution SET sell_at=%s,sell_price=%s,sell_price_basis=%s,gross_return_pct=%s WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date=%s AND selection_rank=%s AND sell_price IS NULL',
                                    (sale_at,sell,p.get('exit_price_basis','09:40_minute_close_proxy'),(sell/buy-1)*100,*identity))
                db.commit();complete.append(day)
            except Exception:db.rollback();raise
    save_json(out/'write_receipt.json',dict(user_id=args.user_id,strategy_name='收盘开盘',version=args.version,days=len(complete),picks=len(picks),actionable=int(picks.actionable.sum()),settled=int((picks.actionable&picks.close_40.notna()).sum()),written_at=datetime.now(ZONE).isoformat()))


def verify_results(args):
    """Read back every replayed row and compare persisted dates/prices to output."""
    picks=read(args.output/'picks.csv');folds=json.loads((args.output/'folds.json').read_text())
    with db_connection(args.env_file) as db:
        identity=(args.user_id,'收盘开盘',args.version,folds[0]['day'],folds[-1]['day'])
        where=' WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date BETWEEN %s AND %s'
        runs=query(db,'SELECT signal_date,candidate_count,selection_count,model_sha256 FROM strategy_daily_run'+where,identity)
        rows=query(db,'SELECT signal_date,selection_rank,stock_code,actionable,signal_price,buy_at,buy_price,sell_at,sell_price,gross_return_pct FROM strategy_execution'+where,identity)
    if len(runs)!=len(folds) or len(rows)!=len(picks):raise ValueError('DB row counts differ')
    runs['signal_date']=runs.signal_date.astype(str);rows['signal_date']=rows.signal_date.astype(str)
    runs=runs.set_index('signal_date');rows=rows.set_index(['signal_date','selection_rank'])
    for fold in folds:
        r=runs.loc[fold['day']]
        for col in ['candidate_count','selection_count','model_sha256']:
            if r[col]!=fold[col]:raise ValueError(f"DB daily fact differs: {fold['day']} {col}")
    for p in picks.to_dict('records'):
        r=rows.loc[(p['signal_date'],p['selection_rank'])]
        buy=price(p['signal_price']) if p['actionable'] else None
        sell=price(p['close_40']) if p['actionable'] else None
        expected={'stock_code':p['symbol6'],'actionable':int(p['actionable']),'signal_price':price(p['signal_price']),
                  'buy_price':buy,'sell_price':sell,
                  'buy_at':p['signal_date']+' 14:40:00' if buy else None,
                  'sell_at':p['next_date']+' 09:40:00' if sell else None}
        for col,val in expected.items():
            got=None if pd.isna(r[col]) else str(r[col]) if col.endswith('_at') else r[col]
            if got!=val:raise ValueError(f"DB execution differs: {p['signal_date']} {p['symbol6']} {col}")
        if sell and abs(Decimal(str(r.gross_return_pct))-(sell/buy-1)*100)>Decimal('0.00001'):
            raise ValueError('DB return differs')
        if not sell and pd.notna(r.gross_return_pct):raise ValueError('Unsettled record has a return')
    receipt={'days':len(runs),'selections':len(rows),'verified_at':datetime.now(ZONE).isoformat(),
             'picks_sha256':sha(args.output/'picks.csv'),'verified_fields':'identity, candidate/selection counts, model hash, actionable, signal/buy/sell prices, times, return'}
    save_json(args.output/'db_readback.json',receipt);print(json.dumps(receipt,ensure_ascii=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=['prepare','fit','write','verify','all']);p.add_argument('--start',default='2026-01-01');p.add_argument('--end',default='2026-10-31')
    p.add_argument('--env-file',type=Path,default=Path('.env'));p.add_argument('--output',type=Path,default=Path('outputs/stock_automl/b1_history_20261016'))
    p.add_argument('--minute-root',type=Path,default=Path('.'));p.add_argument('--minute-manifest',type=Path);p.add_argument('--workers',type=int,default=2)
    p.add_argument('--user-id',default='imac');p.add_argument('--version',default='v1.1');p.add_argument('--write-db',action='store_true')
    args=p.parse_args()
    if args.phase in ['prepare','all']:
        plan=prepare_sources(args);build_candidates(args,plan)
    else:plan=json.loads((args.output/'plan.json').read_text())
    if args.phase in ['fit','all']:fit_days(args,plan)
    if args.phase in ['fit','all','write']:refresh_api_exits(args)
    if args.phase=='write' or args.phase=='all' and args.write_db:
        if not args.write_db:raise ValueError('Use --write-db to persist the reviewed replay')
        write_results(args);verify_results(args)
    if args.phase=='verify':verify_results(args)

if __name__=='__main__':main()

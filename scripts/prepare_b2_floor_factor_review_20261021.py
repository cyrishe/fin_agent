"""Read-only enrichment of the frozen 306 V2 trades for human factor review."""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import io
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd
import zstandard

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/stock_automl/b2_2021_minute_r60_20261020'
OUT = ROOT / 'docs/stock_automl_runs/20261021_b2_floor_factor_review'
LEDGER = ROOT / 'docs/stock_automl_runs/20261020_b2_model_month_r61/模型归属.json'

def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda: f.read(4*1024*1024), b''): h.update(b)
    return h.hexdigest()

def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)+'\n')

def trades():
    ledger = pd.DataFrame(json.loads(LEDGER.read_text())['逐笔'])
    s = pd.read_pickle(BASE/'signals.pkl')
    s = s.merge(ledger, left_on=['symbol','d0_date'], right_on=['股票','D0'], validate='one_to_one')
    assert len(s)==306 and s.up.all()
    assert np.allclose(s.entry, s.买价) and np.allclose(s.d2_close,s.卖价)
    return s.sort_values(['d0_date','symbol']).reset_index(drop=True)

def sources():
    from scripts.benchmark_automl_1440_inference import db_connection,query
    f=trades(); OUT.mkdir(parents=True,exist_ok=True)
    with db_connection() as conn:
        for name,table,cols,keys in [
            ('valuation','kcrp_stock_pricevaluate','trade_date,stk_code,total_mv,float_mv,free_float_mv,total_share,float_share,pe_ttm,pb_mrq,update_time',list(zip(f.stk_code,f.d0_date))),
            ('prices','kcrp_stock_price','trade_date,stk_code,preclose,open,high,low,close,volume,amount,turn_ratio,is_limit_price,update_time',sorted(set(zip(f.stk_code,f.d0_date))|set(zip(f.stk_code,f.d1_date))|set(zip(f.stk_code,f.卖出日))))]:
            target=OUT/f'{name}.csv.gz'
            if target.exists(): continue
            frames=[]
            for i in range(0,len(keys),60):
                batch=keys[i:i+60]; marks=','.join(['(%s,%s)']*len(batch))
                frames.append(query(conn,f'SELECT {cols} FROM {table} WHERE (stk_code,trade_date) IN ({marks})',tuple(v for k in batch for v in k)))
            data=pd.concat(frames,ignore_index=True)
            assert not data.duplicated(['stk_code','trade_date']).any()
            data.to_csv(target,index=False,compression='gzip')
            print(name,len(data),flush=True)
        codes=sorted(f.stk_code.unique()); marks=','.join(['%s']*len(codes))
        target=OUT/'valuation_prior.csv.gz'
        if not target.exists():
            v=pd.read_csv(OUT/'valuation.csv.gz'); have=set(zip(v.stk_code,v.trade_date))
            missing=[(r.stk_code,r.d0_date) for r in f.itertuples() if (r.stk_code,r.d0_date) not in have]
            prior=[]
            for code,day in missing:
                q=query(conn,'SELECT trade_date,stk_code,total_share,float_share FROM kcrp_stock_pricevaluate WHERE stk_code=%s AND trade_date<=%s AND total_share>0 ORDER BY trade_date DESC LIMIT 1',(code,day))
                if len(q):q['D0']=day;prior.append(q)
            data=pd.concat(prior,ignore_index=True) if prior else pd.DataFrame(columns=['trade_date','stk_code','total_share','float_share','D0'])
            data.to_csv(target,index=False,compression='gzip'); print('prior shares',len(data),'of',len(missing),flush=True)
        target=OUT/'industry.csv.gz'
        if not target.exists():
            q=query(conn,f"SELECT stk_code,industry_name,industry_code,begin_date,end_date FROM kcrp_stock_industry WHERE industry_type='SW2021' AND level=2 AND stk_code IN ({marks})",tuple(codes))
            q.to_csv(target,index=False,compression='gzip'); print('industry',len(q),flush=True)
        target=OUT/'st.csv.gz'
        if not target.exists():
            q=query(conn,f'SELECT stk_code,st_type,begin_date,end_date FROM kcrp_stock_st WHERE stk_code IN ({marks})',tuple(codes))
            q.to_csv(target,index=False,compression='gzip')
        target=OUT/'names.csv.gz'
        if not target.exists():
            cols=query(conn,"SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='kcrp_stock_baseinfo'").COLUMN_NAME.tolist()
            name=next((c for c in ['stk_name','stk_short_name','short_name','name'] if c in cols),None)
            if name:
                q=query(conn,f'SELECT stk_code,{name} AS stock_name FROM kcrp_stock_baseinfo WHERE stk_code IN ({marks})',tuple(codes))
                q.to_csv(target,index=False,compression='gzip'); print('names',len(q),flush=True)
            else: print('name columns',cols,flush=True)
        conn.rollback()

def missing_minutes():
    from scripts.benchmark_automl_1440_inference import db_connection,query
    records=json.loads((OUT/'d0_minutes.json').read_text())
    wanted=[x for x in records if x['quality']=='缺分钟文件']
    (OUT/'minute_db').mkdir(exist_ok=True)
    with db_connection() as conn:
        for x in wanted:
            p=OUT/'minute_db'/f"{x['D0']}_{x['股票']}.csv.gz"
            if p.exists():
                try:
                    if len(pd.read_csv(p))==240:continue
                except pd.errors.EmptyDataError:pass
            code=x['股票']
            q=query(conn,"SELECT stk_code AS symbol,bar_end_time AS time,open_price AS open,high_price AS high,low_price AS low,latest_price AS close,volume,amount AS turnover FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s AND stk_code=%s AND kline_type='1m' AND period_minutes=1 AND is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time ORDER BY bar_end_time",(x['D0'],code))
            q.to_csv(p,index=False,compression='gzip')
            print('DB minutes',x['D0'],len(q),flush=True)
        conn.rollback()
    keep=[x for x in records if x['quality']!='缺分钟文件']
    dump(OUT/'d0_minutes.json',keep)
    minutes()

def minute_task(item):
    day,code,ohlc,cap=item
    raw=Path('/Volumes/ext/tushare_market_data')/day[:4]/f'{day.replace("-", "")}.csv.gz'
    if raw.exists(): path=raw; kind='tushare'
    else:
        root=ROOT/'outputs/stock_automl'/('mk_1m_compact_2025' if day[:4]=='2025' else 'mk_1m_compact')
        path=root/f'{day}.csv.zst';kind='mk'
    if not path.exists():
        path=OUT/'minute_db'/f'{day}_{code}.csv.gz';kind='db'
    if not path.exists(): return dict(D0=day,股票=code,quality='缺分钟文件',source=str(path))
    rows=[]
    opener=gzip.open if kind in ('tushare','db') else zstandard.open
    # Filter bytes before parsing; do not load all stocks into memory.
    with io.BufferedReader(opener(path,'rb')) as stream:
        header=stream.readline()
        for line in stream:
            symbol=line[:6] if kind in ('tushare','db') else line.split(b',',2)[1][:6]
            if symbol.decode('ascii')==code: rows.append(line)
    if not rows:return dict(D0=day,股票=code,quality='缺个股分钟',source=str(path))
    b=pd.read_csv(io.BytesIO(header+b''.join(rows)))
    if kind=='tushare':
        from scripts.import_tushare_minutes_2024 import convert
        try:b=convert(b,day)
        except ValueError as e:return dict(D0=day,股票=code,quality=str(e),source=str(path))
    b=b.sort_values('time').reset_index(drop=True)
    prices=[b.open.iloc[0],b.high.max(),b.low.min(),b.close.iloc[-1]]
    quality='完整且日K价一致' if len(b)==240 and not b.time.duplicated().any() and np.allclose(prices,ohlc,atol=.011,rtol=0) else '分钟与日K不一致'
    result=dict(D0=day,股票=code,quality=quality,source=str(path),source_sha256=sha(path),bars=len(b))
    if quality!='完整且日K价一致':return result
    touch=b.high.ge(cap-.001); below=b.low.lt(cap-.001); close_sealed=b.close.ge(cap-.001)
    prior_touch=touch.cummax().shift(fill_value=False)
    definite=(prior_touch & below) | (b.open.ge(cap-.001)&below) | (touch & b.close.lt(cap-.001))
    ambiguous=bool((touch & below & ~prior_touch & b.open.lt(cap-.001) & close_sealed).any())
    opened='是' if definite.any() else ('分钟内顺序不明' if ambiguous else '否')
    def first_clock(mask):return str(b.loc[mask,'time'].iloc[0])[11:16] if mask.any() else None
    last_below=int(np.flatnonzero(below)[-1]) if below.any() else -1
    final=str(b.time.iloc[last_below+1])[11:16] if last_below+1<len(b) else None
    result.update(opened_after_touch=opened,first_touch=first_clock(touch),first_close_sealed=first_clock(close_sealed),
        final_full_seal=final,close_sealed_minutes=int(close_sealed.sum()),full_sealed_minutes=int((~below).sum()),
        observed_reopens=int((close_sealed.shift(fill_value=False)&below).sum()),
        below_after_touch_minutes=int((prior_touch&below).sum()),
        amount_pm_fraction=float(b.loc[b.time.str[11:16].ge('13:01'),'turnover'].sum()/b.turnover.sum()) if b.turnover.sum()>0 else None,
        last30_amount_fraction=float(b.loc[b.time.str[11:16].ge('14:31'),'turnover'].sum()/b.turnover.sum()) if b.turnover.sum()>0 else None)
    return result

def minutes():
    OUT.mkdir(parents=True,exist_ok=True); f=trades(); cache=OUT/'d0_minutes.json'
    old=json.loads(cache.read_text()) if cache.exists() else []
    done={(x['D0'],x['股票']) for x in old}
    jobs=[(r.d0_date,r.symbol,[r.open,r.high,r.low,r.close],r.d0_cap) for r in f.itertuples() if (r.d0_date,r.symbol) not in done]
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(minute_task,x) for x in jobs]
        for future in as_completed(futures):
            old.append(future.result())
            if len(old)%20==0:
                dump(cache,old); print('D0 minutes',len(old),'/306',flush=True)
    dump(cache,sorted(old,key=lambda x:(x['D0'],x['股票'])))
    print('minute quality',pd.Series([x['quality'] for x in old]).value_counts().to_dict(),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['sources','minutes','missing_minutes']);a=p.parse_args()
    {'sources':sources,'minutes':minutes,'missing_minutes':missing_minutes}[a.stage]()

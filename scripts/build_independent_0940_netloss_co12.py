"""CO12: read two exact minute endpoints for the independent whole-market panel."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd
from scripts.experiment_b1_market_trade_gate_co8 import read, sha, save_json, net
from scripts.experiment_independent_open_drop_co10 import FEATURES

OUT=Path('outputs/stock_automl/independent_0940_netloss_co12')
REPORT=Path('docs/stock_automl_runs/20261015_independent_0940_netloss_co12')
CO10=Path('outputs/stock_automl/independent_open_drop_co10')
CO8=Path('outputs/stock_automl/b1_market_trade_gate_co8')
KEY=['signal_date','symbol6']


def valid_bars(b):
    x=b[['open','high','low','close','volume']].to_numpy(float)
    return (np.isfinite(x).all(axis=1)&(x[:,:4]>0).all(axis=1)&(x[:,4]>=0)
            &(x[:,1]>=np.maximum(x[:,0],x[:,3]))&(x[:,2]<=np.minimum(x[:,0],x[:,3])))


def extract(day):
    folder=OUT/'endpoints';folder.mkdir(parents=True,exist_ok=True)
    dest=folder/(day+'.csv.gz');receipt=folder/(day+'.json')
    if receipt.exists():
        old=json.loads(receipt.read_text());assert sha(dest)==old['endpoint_sha256'];return day,'cached'
    start=time.monotonic()
    source=json.loads((CO8/'days'/day/'complete.json').read_text())
    if source['kind']=='local_lossless_minutes':
        path=Path(source['path']);assert sha(path)==source['sha256']
        frames=[]
        for chunk in pd.read_csv(path,usecols=['time','symbol','open','high','low','close','volume'],
                                 dtype={'symbol':str},chunksize=250000):
            assert chunk.time.str[:10].eq(day).all()
            x=chunk[chunk.time.str[-8:].isin(['09:40:00','14:42:00'])].copy()
            x['symbol6']=x.symbol.str[:6];x['clock']=x.time.str[-8:]
            frames.append(x)
        b=pd.concat(frames,ignore_index=True)
        provenance=dict(kind=source['kind'],path=str(path),sha256=source['sha256'])
    else:
        from scripts.benchmark_automl_1440_inference import db_connection,query
        with db_connection() as db:
            b=query(db,"SELECT stk_code AS symbol6,bar_end_time,open_price AS open,high_price AS high,low_price AS low,latest_price AS close,volume,is_finalized,is_fallback,source_snapshot_time,fetch_time FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 AND bar_end_time IN (%s,%s)",
                    (day,day+' 09:40:00',day+' 14:42:00'))
        if b.empty:raise ValueError('No endpoint day '+day)
        exact=b.is_finalized.eq(1)&b.is_fallback.eq(0)&pd.to_datetime(b.source_snapshot_time).eq(pd.to_datetime(b.bar_end_time))
        provenance=dict(kind='database_finalized_exact_minutes',rejected_nonexact_rows=int((~exact).sum()),
            fetched_min=str(b.fetch_time.min()),fetched_max=str(b.fetch_time.max()))
        b=b[exact].copy();b['symbol6']=b.symbol6.astype(str).str[:6].str.zfill(6)
        b['clock']=pd.to_datetime(b.bar_end_time).dt.strftime('%H:%M:%S')
    b=b[b.symbol6.str.startswith(('0','3','6'))].copy()
    assert not b.duplicated(['symbol6','clock']).any()
    for c in ['open','high','low','close','volume']:b[c]=pd.to_numeric(b[c],errors='raise')
    b['valid']=valid_bars(b)
    b[['symbol6','clock','open','high','low','close','volume','valid']].to_csv(dest,index=False,float_format='%.17g')
    provenance.update(day=day,rows=len(b),invalid=int((~b.valid).sum()),endpoint_sha256=sha(dest),seconds=time.monotonic()-start)
    save_json(receipt,provenance)
    return day,len(b),round(provenance['seconds'],2)


def assemble():
    d=read(CO10/'dataset.csv.gz')
    d=d.rename(columns={'label_known':'opening_label_known','big_drop':'opening_big_drop'})
    source_hash=sha(CO10/'dataset.csv.gz');frames=[]
    for p in sorted((OUT/'endpoints').glob('*.csv.gz')):
        b=read(p);b['date']=p.name[:10];frames.append(b)
    b=pd.concat(frames,ignore_index=True);b=b[b.valid]
    entry=b[b.clock.eq('14:42:00')][['date','symbol6','open','volume']].rename(columns={'date':'signal_date','open':'entry_1442','volume':'entry_bar_volume'})
    exit_=b[b.clock.eq('09:40:00')][['date','symbol6','close','volume']].rename(columns={'date':'next_date','close':'exit_0940','volume':'exit_bar_volume'})
    d=d.merge(entry,on=KEY,how='left',validate='one_to_one').merge(exit_,on=['next_date','symbol6'],how='left',validate='many_to_one')
    d['label_known']=d.opening_label_known&d.entry_1442.gt(0)&d.exit_0940.gt(0)
    d['gross_1442']=(d.exit_0940/d.entry_1442-1).where(d.label_known)
    # Fixed 50,000 trade budget defines the model label; trading account applies exact size-dependent fees.
    d['net_0940']=d.gross_1442.map(lambda r:net(r) if np.isfinite(r) else np.nan)
    d['severe_loss']=d.net_0940.lt(-.02)
    d['severity']=(-d.net_0940).where(d.severe_loss&d.label_known)
    d['feature_cutoff']=d.signal_date+' 14:40:00'
    d['label_available_time']=d.next_date+' 09:40:00'
    assert not d.duplicated(KEY).any()
    assert np.isfinite(d.loc[d.label_known,'net_0940']).all()
    assert d.loc[d.label_known,'net_0940'].gt(-1).all()
    keep=['sample_id',*KEY,'next_date','feature_cutoff',*FEATURES,'corporate_action',
          'entry_1442','exit_0940','entry_bar_volume','exit_bar_volume','label_available_time',
          'label_known','gross_1442','net_0940','severe_loss','severity']
    d[keep].to_csv(OUT/'dataset.csv.gz',index=False,float_format='%.17g')
    d.groupby('signal_date').agg(rows=('sample_id','size'),known=('label_known','sum'),events=('severe_loss','sum')).to_csv(REPORT/'label_coverage.csv')
    b1=read(CO8/'dataset.csv.gz');m=b1[b1.selected].merge(d,on=KEY,validate='one_to_one',suffixes=('_b1',''))
    errors={c:float((m[c]-m[c+'_b1']).abs().max()) for c in ['entry_1442','exit_0940','gross_1442']}
    assert max(errors.values())<1e-10,errors
    save_json(REPORT/'input_audit.json',dict(rows=len(d),known=int(d.label_known.sum()),events=int(d.severe_loss.sum()),
        dates=int(d.signal_date.nunique()),B1_matched=len(m),B1_endpoint_max_errors=errors,
        original_features_sha256=source_hash,dataset_sha256=sha(OUT/'dataset.csv.gz'),
        unknown_labels_not_negative=True,post_decision_prices_only_used_in_labels=True,
        live_arrival_not_proven_by_final_historical_archive=True))
    print('assembled',len(d),'known',d.label_known.sum(),'events',d.severe_loss.sum(),flush=True)


def prepare(workers):
    d=read(CO10/'dataset.csv.gz',usecols=['signal_date','next_date'])
    days=sorted(set(d.signal_date)|set(d.next_date.dropna()))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(extract,day):day for day in days}
        for i,f in enumerate(as_completed(futures),1):
            result=f.result()
            if i%10==0 or i==1:print(i,len(days),result,flush=True)
    assemble()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','assemble','day']);p.add_argument('--workers',type=int,default=2);p.add_argument('--date')
    a=p.parse_args();OUT.mkdir(parents=True,exist_ok=True);REPORT.mkdir(parents=True,exist_ok=True)
    if a.phase=='prepare':prepare(a.workers)
    elif a.phase=='day':print(extract(a.date))
    else:assemble()

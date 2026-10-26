"""CO25: fixed low-base rules, causal daily features and monthly ridge forecasts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from scripts.research_gentle_rise_gr1 import source, sha, dump

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/stock_automl/low_base_co25_20261027'
REPORT = ROOT / 'docs/stock_automl_runs/20261027_low_base_co25'
FEATURES = ['position60','return5','return20','ma5_slope3','range5',
            'tr_contraction','volume_contraction','close_location','excess5',
            'log_amount20']
PRICE_COLS = ['open','high','low','close','preclose','volume','amount']


def calculate(p):
    """All matrices use a shared market calendar. Missing sessions are never skipped."""
    c, h, l, o, pc, v, a = [p[k] for k in ['close','high','low','open','preclose','volume','amount']]
    good = np.isfinite(c) & c.gt(0)
    for k in PRICE_COLS:
        good &= np.isfinite(p[k]) & p[k].gt(0)
    good &= h.ge(c) & h.ge(o) & l.le(c) & l.le(o) & h.ge(l)
    ratio = (c / pc).where(good)
    chain = np.exp(np.log(ratio).fillna(0).cumsum()).where(good)
    ah, al = h/c*chain, l/c*chain
    tr = pd.DataFrame(np.maximum.reduce([(h-l)/pc, abs(h/pc-1), abs(l/pc-1)]), index=c.index, columns=c.columns).where(good)
    lo60, hi60 = al.rolling(60).min(), ah.rolling(60).max()
    ma5 = chain.rolling(5).mean()
    market = (ratio-1).mean(axis=1)
    market5 = (1+market).rolling(5).apply(np.prod, raw=True)-1
    f = {'position60':(chain-lo60)/(hi60-lo60).where(hi60.gt(lo60)),
         'return5':chain/chain.shift(5)-1, 'return20':chain/chain.shift(20)-1,
         'ma5_slope3':ma5/ma5.shift(3)-1,
         'range5':ah.rolling(5).max()/al.rolling(5).min()-1,
         'tr_contraction':tr.rolling(5).mean()/tr.shift(5).rolling(20).mean(),
         'volume_contraction':v.where(good).rolling(5).mean()/v.where(good).shift(5).rolling(20).mean(),
         'close_location':((c-l)/(h-l).where(h.gt(l))).fillna(.5),
         'excess5':(chain/chain.shift(5)-1).sub(market5,axis=0),
         'log_amount20':np.log1p(a.where(good).shift(1).rolling(20).mean())}
    eligible = good.rolling(60).sum().eq(60) & a.shift(1).rolling(20).mean().ge(2e7) & a.ge(5e6)
    for x in f.values():
        eligible &= np.isfinite(x)
    low = eligible & f['position60'].le(.35) & f['return20'].le(.08)
    floor = al.rolling(3).min()/al.shift(3).rolling(10).min()
    stable = low & f['return5'].ge(-.02) & f['ma5_slope3'].ge(-.01) & floor.ge(.99)
    compact = stable & f['range5'].le(.08) & f['tr_contraction'].le(.85) & f['volume_contraction'].le(.90)
    masks = {'eligible':eligible,'low':low,'stable':stable,'compact':compact}
    values = {**f,'floor_ratio':floor,'signal_price':c,'entry_price':o.shift(-1)}
    for name in ['low','stable','compact']:
        values[name] = masks[name]
        values['first_'+name] = masks[name] & ~masks[name].shift(1,fill_value=False)
    for horizon in [2,3]:
        future = ratio.shift(-1).copy()
        for lag in range(2,horizon+1):
            future *= ratio.shift(-lag)
        entry = (o/pc).where(good).shift(-1)
        # Conservative daily-price execution proxy: one-price entry and suspect
        # one-price lower-limit exits cannot be assumed executable.
        entry_ok = good.shift(-1,fill_value=False) & h.shift(-1).gt(l.shift(-1))
        exit_lock = h.shift(-horizon).eq(l.shift(-horizon)) & ratio.shift(-horizon).lt(.955)
        known = entry_ok & ~exit_lock & future.notna()
        values[f'event{horizon}'] = future-1
        values[f'net{horizon}'] = ((future/entry)*(.999/1.001)-1).where(known)
        values[f'raw_net{horizon}'] = (c.shift(-horizon)/o.shift(-1)*(.999/1.001)-1).where(known)
        values[f'exit_price{horizon}'] = c.shift(-horizon)
    return values, masks, market


def build():
    OUT.mkdir(parents=True,exist_ok=True); REPORT.mkdir(parents=True,exist_ok=True)
    assert (REPORT/'DESIGN.md').exists()
    frames, sources = [], []
    for m in pd.period_range('2023-10','2026-10',freq='M'):
        path = source(str(m))
        d = pd.read_pickle(path) if '.pkl.' in path.name else pd.read_csv(path,dtype={'stk_code':str},float_precision='round_trip')
        d = d[['trade_date','stk_code',*PRICE_COLS]].copy()
        d['trade_date'] = pd.to_datetime(d.trade_date)
        d = d[d.stk_code.str.startswith(('00','30','60','688'))]
        for k in PRICE_COLS:d[k]=pd.to_numeric(d[k],errors='coerce')
        frames.append(d)
        sources.append({'month':str(m),'path':str(path),'sha256':sha(path),'rows':len(d)})
    raw = pd.concat(frames,ignore_index=True).sort_values(['trade_date','stk_code'])
    del frames
    assert not raw.duplicated(['trade_date','stk_code']).any()
    raw.to_parquet(OUT/'daily_inputs.parquet',index=False)
    p = {k:raw.pivot(index='trade_date',columns='stk_code',values=k).sort_index() for k in PRICE_COLS}
    del raw
    values,masks,market = calculate(p)
    dates=p['close'].index; codes=p['close'].columns
    period=(dates>='2024-01-01') & (dates<='2026-09-30')
    use=masks['eligible'].to_numpy() & period[:,None]
    i,j=np.where(use)
    panel=pd.DataFrame({'sample_id':np.arange(len(i)),'date':dates[i].strftime('%Y-%m-%d'),'stk_code':codes.to_numpy()[j]})
    for k,x in values.items():panel[k]=x.to_numpy()[i,j]
    for h in [2,3]:
        end=pd.Series(dates.strftime('%Y-%m-%d')).shift(-h)
        panel[f'label_end{h}']=end.to_numpy()[i]
    panel['month']=panel.date.str[:7]
    panel.to_parquet(OUT/'panel.parquet',index=False)
    daily=pd.DataFrame({'date':dates[period].strftime('%Y-%m-%d'),'market_return':market.loc[dates[period]].to_numpy()})
    for k,m in masks.items():daily[k]=m.loc[dates[period]].sum(axis=1).to_numpy()
    daily.to_csv(REPORT/'daily_counts.csv',index=False)
    pd.DataFrame({'date':dates.strftime('%Y-%m-%d')}).to_csv(OUT/'calendar.csv',index=False)
    dump(OUT/'build_manifest.json',{'experiment':'CO25_20261027_low_base','source_files':sources,
        'panel_rows':len(panel),'stable_rows':int(panel.stable.sum()),'compact_rows':int(panel.compact.sum()),
        'dates':[panel.date.min(),panel.date.max()],'code_sha256':sha(Path(__file__)),
        'design_sha256':sha(REPORT/'DESIGN.md'),'panel_sha256':sha(OUT/'panel.parquet'),
        'daily_inputs_sha256':sha(OUT/'daily_inputs.parquet'),'sklearn':sklearn.__version__})
    print(daily.groupby(daily.date.str[:4])[['eligible','low','stable','compact']].agg(['mean','min','max']).to_string(),flush=True)
    print('BUILD COMPLETE',len(panel),flush=True)


def fit_predict(train,test,h):
    a=train[FEATURES].to_numpy(float); b=test[FEATURES].to_numpy(float)
    bounds=np.quantile(a,[.01,.99],axis=0)
    a=np.clip(a,*bounds);b=np.clip(b,*bounds)
    counts=train.groupby('date').sample_id.transform('count').to_numpy()
    weights=1/counts;weights/=weights.mean()
    scaler=StandardScaler().fit(a,sample_weight=weights)
    model=Ridge(alpha=100).fit(scaler.transform(a),train[f'net{h}'],sample_weight=weights)
    bundle={'bounds':bounds,'scaler':scaler,'model':model,'features':FEATURES,'horizon':h}
    return bundle,model.predict(scaler.transform(b)),float(np.average(train[f'net{h}'],weights=weights))


def predict(bundle,test):
    a=np.clip(test[bundle['features']].to_numpy(float),*bundle['bounds'])
    return bundle['model'].predict(bundle['scaler'].transform(a))


def train_models():
    panel=pd.read_parquet(OUT/'panel.parquet'); panel=panel[panel.stable].copy()
    scores=[]; folds=[]
    with threadpool_limits(limits=1):
        for month in pd.period_range('2025-01','2026-09',freq='M'):
            start=str(month.start_time.date());since=str((month-6).start_time.date())
            test=panel[panel.month.eq(str(month))].copy()
            dest=OUT/'models'/str(month);dest.mkdir(parents=True,exist_ok=True)
            for h in [2,3]:
                tr=panel[panel.date.ge(since)&panel.date.lt(start)&panel[f'label_end{h}'].lt(start)&panel[f'net{h}'].notna()].copy()
                assert len(tr)>=500 and len(test)>0
                assert tr[f'label_end{h}'].max()<test.date.min()
                bundle,pr,constant=fit_predict(tr,test,h)
                joblib.dump(bundle,dest/f'd{h}.joblib',compress=3)
                np.testing.assert_array_equal(predict(joblib.load(dest/f'd{h}.joblib'),test),pr)
                members=tr[['sample_id','date',f'label_end{h}']].copy()
                members['sample_weight']=1/tr.groupby('date').sample_id.transform('count')
                members.sample_weight/=members.sample_weight.mean()
                members.to_csv(dest/f'members_d{h}.csv.gz',index=False,float_format='%.17g')
                z=test[['sample_id','date','stk_code','month','compact','first_stable',f'net{h}',f'event{h}','range5']].copy()
                z['horizon']=h;z['predicted_net']=pr;z['train_constant']=constant
                z=z.rename(columns={f'net{h}':'net',f'event{h}':'event'})
                scores.append(z)
                folds.append({'month':str(month),'horizon':h,'train_start':tr.date.min(),'train_end':tr.date.max(),
                              'label_end':tr[f'label_end{h}'].max(),'train_rows':len(tr),'test_rows':len(test),
                              'model_sha256':sha(dest/f'd{h}.joblib'),'members_sha256':sha(dest/f'members_d{h}.csv.gz')})
            print('TRAIN',month,len(test),flush=True)
    pd.concat(scores,ignore_index=True).to_parquet(OUT/'predictions.parquet',index=False)
    dump(OUT/'folds.json',folds)
    print('TRAIN COMPLETE',len(folds),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('stage',choices=['build','train']);args=ap.parse_args()
    build() if args.stage=='build' else train_models()

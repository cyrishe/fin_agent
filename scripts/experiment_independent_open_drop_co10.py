"""Independent market-wide opening risk classification. B1 is evaluation only."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import platform
import shutil
import subprocess

import joblib
import numpy as np
import pandas as pd
import scipy
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits
from scripts.experiment_b1_market_trade_gate_co8 import OUT as CO8, read,sha,save_json
from scripts.report_b1_market_trade_gate_co8 import account,metrics,random_reference

OUT=Path('outputs/stock_automl/independent_open_drop_co10')
REPORT=Path('docs/stock_automl_runs/20261015_independent_open_drop_co10')
FEATURES=['market_breadth','market_median','market_iqr','lag5_market_breadth',
          'signal_return','relative_amount20','lag_return','prior5_return',
          'prior5_vol','prior10_vol','prior5_downside','prior5_worst']
ARMS=['nonfall_logistic','nonfall_hist','extreme_logistic','extreme_hist']
KEY=['signal_date','symbol6']


def historical(d):
    d=d.sort_values(['symbol6','trade_date']).copy();dates=sorted(d.trade_date.unique())
    d['day_i']=d.trade_date.map(dict(zip(dates,range(len(dates)))))
    d['ret']=d.close/d.preclose-1;g=d.groupby('symbol6',sort=False)
    d['lag_return']=g.ret.shift();d['lag_i']=g.day_i.shift()
    d.loc[d.day_i.sub(d.lag_i).ne(1),'lag_return']=np.nan
    lag=g.ret.shift()
    def rolling(n,func):
        x=lag.groupby(d.symbol6,sort=False).transform(lambda s:func(s.rolling(n,min_periods=n)))
        contiguous=d.day_i.sub(g.day_i.shift(n)).eq(n)
        return x.where(contiguous)
    d['prior5_return']=rolling(5,lambda w:w.apply(lambda x:np.prod(1+x)-1,raw=True))
    d['prior5_vol']=rolling(5,lambda w:w.std(ddof=0));d['prior10_vol']=rolling(10,lambda w:w.std(ddof=0))
    d['prior5_downside']=rolling(5,lambda w:w.apply(lambda x:np.sqrt(np.mean(np.minimum(x,0)**2)),raw=True))
    d['prior5_worst']=rolling(5,lambda w:w.min())
    active=d[d.volume.gt(0)&d.preclose.gt(0)&d.close.gt(0)]
    breadth=active.assign(up=active.ret.gt(0)).groupby('trade_date').up.mean().reindex(dates)
    d['lag5_market_breadth']=d.trade_date.map(breadth.shift().rolling(5).mean())
    return d


def prepare():
    OUT.mkdir(parents=True,exist_ok=True);REPORT.mkdir(parents=True,exist_ok=True)
    columns=['trade_date','stk_code','preclose','open','close','volume']
    frozen=OUT/'daily_input.csv.gz';sources={}
    if frozen.exists():d=read(frozen)
    else:
        files=sorted(Path('outputs/stock_automl/limit_rebound_2y_r1/price_months').glob('price_*.csv.gz'))
        d=pd.concat([pd.read_csv(p,usecols=columns,float_precision='round_trip') for p in files],ignore_index=True)
        for p in files:sources[str(p)]=sha(p)
        if d.trade_date.max()<'2026-10-14':
            from scripts.benchmark_automl_1440_inference import db_connection,query
            with db_connection() as c:
                extra=query(c,'SELECT trade_date,stk_code,preclose,open,close,volume FROM kcrp_stock_price WHERE trade_date>%s AND trade_date<=%s',(d.trade_date.max(),'2026-10-14'))
            extra['trade_date']=pd.to_datetime(extra.trade_date).dt.strftime('%Y-%m-%d')
            d=pd.concat([d,extra],ignore_index=True)
        d.to_csv(frozen,index=False,float_format='%.17g')
    for col in columns[2:]:d[col]=pd.to_numeric(d[col],errors='coerce')
    d['symbol6']=d.stk_code.str[:6];d=d[d.symbol6.str.startswith(('0','3','6'))&d.trade_date.le('2026-10-14')]
    assert not d.duplicated(['trade_date','symbol6']).any()
    d=historical(d);calendar=sorted(d.trade_date.unique());nextmap=dict(zip(calendar[:-1],calendar[1:]))
    pastcols=['trade_date','symbol6','preclose','lag_return','prior5_return','prior5_vol','prior10_vol',
              'prior5_downside','prior5_worst','lag5_market_breadth']
    snapshots=[];amounts=[]
    days=sorted(p.name for p in (CO8/'days').iterdir() if p.is_dir() and p.name<='2026-10-13')
    for day in days:
        for name,collection in [('snapshot',snapshots),('amounts',amounts)]:
            p=CO8/'days'/day/(name+'.csv.gz');f=read(p);f['signal_date']=day;collection.append(f);sources[str(p)]=sha(p)
    snap=pd.concat(snapshots,ignore_index=True).rename(columns={'close':'signal_price'})
    snap=snap[snap.symbol6.str.startswith(('0','3','6'))&snap.signal_price.gt(0)].copy()
    snap=snap.merge(d[pastcols],left_on=KEY,right_on=['trade_date','symbol6'],how='left',validate='one_to_one')
    snap=snap[snap.preclose.gt(0)].copy();snap['signal_return']=snap.signal_price/snap.preclose-1
    market=snap.groupby('signal_date').signal_return.agg(market_breadth=lambda x:x.gt(0).mean(),
            market_median='median',market_iqr=lambda x:x.quantile(.75)-x.quantile(.25))
    snap=snap.merge(market,left_on='signal_date',right_index=True,validate='many_to_one')
    a=pd.concat(amounts,ignore_index=True).sort_values(['symbol6','signal_date'])
    a.loc[a['count'].ne(220),'amount']=np.nan
    # Reindex the complete date grid before rolling: a missing market day is never skipped.
    pivot=a.pivot(index='signal_date',columns='symbol6',values='amount').reindex(days)
    med=pivot.shift().rolling(20,min_periods=20).median()
    ratio=pivot/med;ratio.index.name='signal_date'
    relative=ratio.stack(future_stack=True).rename('relative_amount20').reset_index()
    snap=snap.merge(relative,on=KEY,how='left',validate='one_to_one')
    snap['next_date']=snap.signal_date.map(nextmap)
    labels=d[['trade_date','symbol6','open','preclose','volume']].rename(columns={
        'trade_date':'next_date','open':'next_open','preclose':'next_preclose','volume':'next_volume'})
    snap=snap.merge(labels,on=['next_date','symbol6'],how='left',validate='many_to_one')
    close=d[['trade_date','symbol6','close']].rename(columns={'trade_date':'signal_date','close':'label_day_close'})
    snap=snap.merge(close,on=KEY,how='left',validate='one_to_one')
    snap['corporate_action']=(snap.next_preclose/snap.label_day_close-1).abs().gt(.001)
    snap['label_known']=snap.next_open.gt(0)&snap.next_volume.gt(0)&~snap.corporate_action
    snap['open_return']=snap.next_open/snap.signal_price-1
    snap.loc[~snap.label_known,'open_return']=np.nan
    snap['big_drop']=snap.open_return.le(-.02+1e-12)
    snap['nonfall']=snap.open_return.ge(-1e-12);snap['big_rise']=snap.open_return.ge(.02-1e-12)
    snap['label_available_time']=snap.next_date+' 09:40:00'
    snap=snap.sort_values(KEY).reset_index(drop=True);snap['sample_id']=np.arange(len(snap))
    cols=['sample_id',*KEY,'next_date','signal_price',*FEATURES,'next_open','open_return',
          'label_known','corporate_action','big_drop','nonfall','big_rise','label_available_time']
    snap[cols].to_csv(OUT/'dataset.csv.gz',index=False,float_format='%.17g')
    save_json(OUT/'source_manifest.json',dict(source_hashes=sources,daily_input_sha256=sha(frozen),
        rows=len(snap),days=len(days),first=days[0],last=days[-1],label_unknown=int((~snap.label_known).sum()),
        note='Universe based on signal-time snapshots; unknown future outcomes retained as unknown, not zero.'))
    print('Prepared market rows',len(snap),'dates',len(days),flush=True)


def fit_model(t,arm):
    used=t[t.label_known&(t.big_drop|(t.nonfall if arm.startswith('nonfall') else t.big_rise))].copy()
    counts=used.big_drop.value_counts()
    if counts.get(True,0)<100 or counts.get(False,0)<100:raise ValueError('Insufficient independent training classes')
    x=used[FEATURES].replace([np.inf,-np.inf],np.nan)
    lo=x.quantile(.01);hi=x.quantile(.99);median=x.median().fillna(0.)
    x=x.clip(lo,hi,axis=1).fillna(median);mean=x.mean();std=x.std(ddof=0).replace(0,1).fillna(1.)
    weights=1/used.groupby('signal_date').sample_id.transform('count');weights=weights/weights.mean()
    weights=weights*np.where(used.big_drop,5.,1.)
    if arm.endswith('hist'):
        model=HistGradientBoostingClassifier(max_iter=60,learning_rate=.05,max_leaf_nodes=7,
            min_samples_leaf=100,l2_regularization=10,max_bins=63,early_stopping=False,random_state=1015)
    else:model=LogisticRegression(C=.1,solver='lbfgs',max_iter=1000)
    model.fit((x-mean)/std,used.big_drop.astype(int),sample_weight=weights)
    return dict(model=model,lo=lo,hi=hi,median=median,mean=mean,std=std,features=FEATURES),used.sample_id


def score(m,d):
    x=d[m['features']].replace([np.inf,-np.inf],np.nan).clip(m['lo'],m['hi'],axis=1).fillna(m['median'])
    return m['model'].predict_proba((x-m['mean'])/m['std'])[:,1]


def choose(v,retention):
    z=v[v.label_known];events=int(z.big_drop.sum());grid=[]
    for threshold in list(np.linspace(0,1,101))+[2.]:
        block=z.risk_score.ge(threshold);caught=int((block&z.big_drop).sum());keep=int((~block).sum())
        grid.append(dict(threshold=threshold,n=len(z),events=events,caught=caught,
                         recall=caught/events if events else 0.,kept=keep,allowed=keep>=retention*len(z)))
    if events<30:threshold=2.;reason='insufficient_validation_events'
    else:
        valid=[r for r in grid if r['allowed']];target=[r for r in valid if r['recall']>=.9]
        best=max(target,key=lambda r:(r['kept'],r['threshold'])) if target else max(valid,key=lambda r:(r['recall'],r['kept'],r['threshold']))
        threshold=best['threshold'];reason='target_met' if target else 'target_unmet'
    for r in grid:r.update(chosen=r['threshold']==threshold,reason=reason)
    return threshold,grid


def diagnostics(z,threshold):
    known=z[z.label_known];block=known.risk_score.ge(threshold);positive=known.big_drop
    n=len(known);events=int(positive.sum());caught=int((block&positive).sum());flagged=int(block.sum())
    negatives=n-events;kept=n-flagged
    return dict(n=len(z),known=n,unknown=len(z)-n,events=events,caught=caught,missed=events-caught,
        blocked=flagged,kept=kept,recall_pct=100*caught/events if events else np.nan,
        precision_pct=100*caught/flagged if flagged else np.nan,
        false_positive_pct=100*(flagged-caught)/negatives if negatives else np.nan,
        retention_pct=100*kept/n if n else np.nan,retained_event_pct=100*(events-caught)/kept if kept else np.nan,
        large_winners_blocked=int((block&known.big_rise).sum()))


def freeze():
    files=[REPORT/'DESIGN.md',OUT/'dataset.csv.gz',Path(__file__),
           Path('scripts/experiment_b1_market_trade_gate_co8.py'),Path('scripts/report_b1_market_trade_gate_co8.py')]
    p=REPORT/'pre_run_freeze.json'
    if p.exists():raise RuntimeError('Freeze exists')
    save_json(p,dict(created_utc=datetime.now(timezone.utc).isoformat(),files={str(p):sha(p) for p in files}))


def fit():
    freeze=json.loads((REPORT/'pre_run_freeze.json').read_text())
    for p,h in freeze['files'].items():assert sha(Path(p))==h,p
    d=read(OUT/'dataset.csv.gz');dates=sorted(d.signal_date.unique())
    months=sorted(set(s[:7] for s in dates if '2025-08-01'<=s<='2026-10-13'))
    folds=[];grids=[];results=[];daily_results=[];max_error=0.
    for month in months:
        test=d[d.signal_date.str.startswith(month)].copy();start=test.signal_date.min()
        prior=[s for s in dates if s<start][-100:];assert len(prior)==100
        train=d[d.signal_date.isin(prior[:60])];val=d[d.signal_date.isin(prior[60:])].copy()
        assert train.label_available_time.dropna().max()<prior[60]+' 14:40:00'
        assert val.label_available_time.dropna().max()<start+' 14:40:00'
        for arm in ARMS:
            m,membership=fit_model(train,arm);v=val.copy();v['risk_score']=score(m,v)
            z=test.copy();z['risk_score']=score(m,z)
            folder=OUT/'models'/month/arm;folder.mkdir(parents=True,exist_ok=True)
            joblib.dump(m,folder/'model.joblib',compress=3)
            pd.DataFrame({'sample_id':membership}).to_csv(folder/'training_membership.csv.gz',index=False)
            v[['sample_id',*KEY,'risk_score']].to_csv(folder/'validation.csv.gz',index=False,float_format='%.17g')
            z[['sample_id',*KEY,'risk_score']].to_csv(folder/'test.csv.gz',index=False,float_format='%.17g')
            # Independent model read-back; entire test scores checked, not just one sample.
            loaded=joblib.load(folder/'model.joblib')
            back=read(folder/'test.csv.gz');max_error=max(max_error,float(np.max(np.abs(score(loaded,z)-back.risk_score))))
            meta=dict(month=month,arm=arm,train_dates=prior[:60],validation_dates=prior[60:],test_dates=sorted(z.signal_date.unique()),
                training_rows=len(membership),training_events=int(train.loc[membership.index,'big_drop'].sum()),
                model_sha256=sha(folder/'model.joblib'),training_membership_sha256=sha(folder/'training_membership.csv.gz'))
            for mode,retention in [('main',.3),('aggressive',.1)]:
                threshold,grid=choose(v,retention)
                for r in grid:r.update(month=month,arm=arm,mode=mode)
                grids+=grid;name=arm+'_'+mode
                for scope,sub in [('market',z),('signal4to9',z[z.signal_return.between(.04,.09)])]:
                    results.append(dict(month=month,model=name,scope=scope,threshold=threshold,**diagnostics(sub,threshold)))
                for day,sub in z.groupby('signal_date'):
                    daily_results.append(dict(signal_date=day,model=name,**diagnostics(sub,threshold)))
                meta['threshold_'+mode]=threshold
            save_json(folder/'fold.json',meta);folds.append(meta)
            pd.DataFrame(results).to_csv(REPORT/'market_monthly.csv',index=False)
            print(month,arm,'training',len(membership),'test',len(test),flush=True)
    assert max_error<1e-12
    pd.DataFrame(daily_results).to_csv(REPORT/'market_daily.csv',index=False)
    pd.DataFrame(grids).to_csv(REPORT/'validation_thresholds.csv',index=False)
    save_json(OUT/'folds.json',folds)
    save_json(REPORT/'run_manifest.json',dict(python=platform.python_version(),sklearn=sklearn.__version__,numpy=np.__version__,
        scipy=scipy.__version__,pandas=pd.__version__,joblib=joblib.__version__,model_count=len(folds),features=FEATURES,
        max_reloaded_score_error=max_error,source_in_working_tree=True,frozen_files=freeze['files'],
        commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),B1_used_in_training_or_threshold=False))


def report():
    monthly=read(REPORT/'market_monthly.csv');summaries=[]
    for (model,scope),g in monthly.groupby(['model','scope']):
        sums=g[['n','known','unknown','events','caught','missed','blocked','kept','large_winners_blocked']].sum().to_dict()
        n=sums['known'];events=sums['events'];flagged=sums['blocked'];kept=sums['kept']
        sums.update(recall_pct=100*sums['caught']/events,precision_pct=100*sums['caught']/flagged if flagged else np.nan,
            retention_pct=100*kept/n,retained_event_pct=100*sums['missed']/kept if kept else np.nan)
        summaries.append(dict(model=model,scope=scope,**sums))
    summary=pd.DataFrame(summaries);summary.to_csv(REPORT/'market_summary.csv',index=False)
    # B1 identity is first accessed here, after independent models and thresholds are frozen.
    b=read(CO8/'dataset.csv.gz');b=b[b.signal_date.between('2025-08-01','2026-10-13')].copy()
    from scripts.experiment_b1_market_trade_gate_co8 import net
    b['baseline_trade']=b.selected&b.amount_gate.eq(1);b['net_main']=b.gross_1442.map(net);b['month']=b.signal_date.str[:7]
    marks={day:g for day,g in read(CO8/'valuation_paths.csv.gz').groupby('signal_date')}
    folds=json.loads((OUT/'folds.json').read_text());variants={'amount_baseline':b.assign(trade=b.baseline_trade)}
    for arm in ARMS:
        scored=[]
        for f in folds:
            if f['arm']!=arm:continue
            p=OUT/'models'/f['month']/arm/'test.csv.gz';s=read(p)
            z=b[b.month.eq(f['month'])].merge(s[KEY+['risk_score']],on=KEY,how='left',validate='one_to_one')
            z['missing_risk_score']=z.selected&z.risk_score.isna()
            for mode in ('main','aggressive'):z['threshold_'+mode]=f['threshold_'+mode]
            scored.append(z)
        z=pd.concat(scored,ignore_index=True)
        for mode in ('main','aggressive'):
            variants[arm+'_'+mode]=z.assign(trade=z.baseline_trade&(~z.risk_score.ge(z['threshold_'+mode])))
    daily=[];sm=[];mo=[];accounts={}
    for name,z in variants.items():
        z=z.sort_values('signal_date').reset_index(drop=True);a=account(z,marks);accounts[name]=a
        daily.append(a.drop(columns='intratrade_path').assign(model=name))
        for month,g in a.groupby('month'):mo.append(dict(model=name,month=month,**metrics(g)))
        for scope,g in [('all',a),('B1_Feb_Jul_2026',a[a.month.between('2026-02','2026-07')])]:
            sm.append(dict(model=name,scope=scope,**metrics(account(g,marks))))
    pd.DataFrame(sm).to_csv(REPORT/'b1_summary.csv',index=False);pd.DataFrame(mo).to_csv(REPORT/'b1_monthly.csv',index=False)
    pd.concat(daily,ignore_index=True).to_csv(REPORT/'b1_daily.csv.gz',index=False,float_format='%.17g')
    base=accounts['amount_baseline'];primary=accounts['nonfall_hist_main'];frac=primary.trade.sum()/base.trade.sum()
    rand=random_reference(base,primary);rand.to_csv(OUT/'random_same_month.csv.gz',index=False)
    save_json(REPORT/'b1_controls.json',dict(exposure_fraction=float(frac),
        constant_exposure=metrics(account(base,marks,exposure=frac)),random_median_nav_pct=float(rand.net_nav_pct.median()),
        random_fraction_at_least_primary=float(rand.net_nav_pct.ge(metrics(primary)['net_nav_pct']).mean()),
        B1_missing_scores=int(primary.missing_risk_score.sum())))
    # Whole-date block uncertainty, preserving within-day dependence of stock labels.
    md=read(REPORT/'market_daily.csv');md=md[md.model.eq('nonfall_hist_main')].sort_values('signal_date')
    rng=np.random.default_rng(1015);ci=[];n=len(md)
    for block in (5,10):
        recalls=[]
        for _ in range(2000):
            starts=rng.integers(0,n,size=int(np.ceil(n/block)));ids=np.concatenate([(s+np.arange(block))%n for s in starts])[:n]
            sample=md.iloc[ids];recalls.append(100*sample.caught.sum()/sample.events.sum())
        ci.append(dict(block_days=block,recall_ci95=np.quantile(recalls,[.025,.975]).tolist()))
    save_json(REPORT/'market_date_block_intervals.json',ci)
    print(summary[summary.scope.eq('market')][['model','known','events','caught','recall_pct','precision_pct','retention_pct']].to_string(index=False),flush=True)
    print(pd.DataFrame(sm).query("scope=='all'")[['model','trades','net_nav_pct','settled_max_drawdown_pct','losses_avoided','profits_missed']].to_string(index=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','freeze','fit','report']);a=p.parse_args()
    with threadpool_limits(limits=1):globals()[a.phase]()

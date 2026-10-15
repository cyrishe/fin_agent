"""CO8: immutable B1 picks, chronological market/stock trade gating.

prepare reads final historical archives; fit never changes the stock selection.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from threadpoolctl import threadpool_limits

OUT = Path('outputs/stock_automl/b1_market_trade_gate_co8')
REPORT = Path('docs/stock_automl_runs/20261015_b1_market_trade_gate')
OLD = Path('docs/stock_automl_runs/20261015_close_open_conditional_b1')
PRICES = Path('outputs/stock_automl/limit_rebound_2y_r1/price_months')
MARKET = ['market_breadth', 'market_median', 'market_iqr', 'lag5_market_breadth']
STOCK = ['relative_turnover5', 'last5_return', 'last30_position', 'prior5_return', 'open_gap']
ARMS = {'market': MARKET, 'stock': STOCK, 'combined': MARKET + STOCK}
CLOCKS = list(pd.date_range('09:31', periods=120, freq='min').strftime('%H:%M:%S')) + list(pd.date_range('13:01', periods=100, freq='min').strftime('%H:%M:%S'))
THRESHOLDS = [-999., -.005, 0., .005, 999.]


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()


def save_json(p, value):
    def clean(x):
        if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
        if isinstance(x,(list,tuple)):return [clean(v) for v in x]
        if isinstance(x,(float,np.floating)) and not np.isfinite(x):return None
        if isinstance(x,np.generic):return x.item()
        return x
    Path(p).write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, default=str, allow_nan=False)+'\n')


def read(p, **kwargs):
    return pd.read_csv(p, dtype={'symbol6': str}, float_precision='round_trip', **kwargs)


def load_daily():
    columns = ['trade_date','stk_code','preclose','close','volume','turn_ratio']
    frozen=OUT/'daily_input.csv.gz'
    files = [frozen] if frozen.exists() else sorted(PRICES.glob('price_*.csv.gz'))
    d = pd.concat([pd.read_csv(p, usecols=columns, float_precision='round_trip') for p in files], ignore_index=True)
    if d.trade_date.max()<'2026-10-14':
        supplement=OUT/'daily_supplement.csv.gz'
        if not supplement.exists():
            from scripts.benchmark_automl_1440_inference import db_connection, query
            OUT.mkdir(parents=True,exist_ok=True)
            with db_connection() as db:
                extra=query(db,'SELECT trade_date,stk_code,preclose,close,volume,turn_ratio FROM kcrp_stock_price WHERE trade_date>%s AND trade_date<=%s',(d.trade_date.max(),'2026-10-14'))
            extra['trade_date']=pd.to_datetime(extra.trade_date).dt.strftime('%Y-%m-%d')
            extra.to_csv(supplement,index=False,float_format='%.17g')
        d=pd.concat([d,pd.read_csv(supplement,usecols=columns,float_precision='round_trip')],ignore_index=True)
        files.append(supplement)
    if not frozen.exists():
        OUT.mkdir(parents=True,exist_ok=True)
        d[columns].to_csv(frozen,index=False,float_format='%.17g')
        save_json(OUT/'daily_source_files.json',[{'path':str(p),'sha256':sha(p)} for p in files])
    files=[frozen]
    d['symbol6'] = d.stk_code.str[:6]
    d = d[d.symbol6.str.startswith(('0','3','6')) & d.trade_date.le('2026-10-14')].copy()
    d = d.sort_values(['symbol6','trade_date']).reset_index(drop=True)
    assert not d.duplicated(['trade_date','symbol6']).any()
    for c in columns[2:]: d[c] = pd.to_numeric(d[c], errors='coerce')
    return d, files


def historical(d):
    """Only T preclose and rows before T can affect T features."""
    d = d.sort_values(['symbol6','trade_date']).copy()
    d['ret'] = d.close/d.preclose-1
    d['shares'] = d.volume/(d.turn_ratio/100)
    g = d.groupby('symbol6', sort=False)
    d['prior_shares'] = g.shares.shift()
    d['prior_turn5'] = g.turn_ratio.transform(lambda s: s.shift().rolling(5).mean())
    d['prior5_return'] = g.ret.transform(lambda s: (1+s.shift()).rolling(5).apply(np.prod, raw=True)-1)
    dates = sorted(d.trade_date.unique()); ix = dict(zip(dates, range(len(dates))))
    day_i = d.trade_date.map(ix)
    complete = day_i-sub_day(d, day_i, 5)
    d.loc[complete.ne(5), ['prior_turn5','prior5_return']] = np.nan
    d.loc[(day_i-sub_day(d, day_i, 1)).ne(1),'prior_shares'] = np.nan
    active = d[d.volume.gt(0)&d.preclose.gt(0)&d.close.gt(0)]
    breadth = active.assign(up=active.ret.gt(0)).groupby('trade_date').up.mean().reindex(dates)
    lag = breadth.shift().rolling(5).mean()
    d['lag5_market_breadth'] = d.trade_date.map(lag)
    return d


def sub_day(d, day_i, lag):
    return day_i.groupby(d.symbol6).shift(lag)


def frozen_picks(calendar):
    source = Path('docs/stock_automl_runs/20261011_mk_2025_mar_aug_backtest')
    top = read(source/'top2_verified.csv'); top = top[top.selection_rank.eq(1)].copy()
    days = read(source/'folds.csv').day.unique()
    early = pd.DataFrame({'signal_date': sorted(d for d in days if d<'2025-08-01')})
    early = early.merge(top[['signal_date','symbol6','actual_0940_return']], on='signal_date', how='left', validate='one_to_one')
    early = early.rename(columns={'actual_0940_return':'reference_gross'})
    early['source'] = 'official_frozen_2025'; early['amount_gate_original'] = np.nan
    late = read(OLD/'daily.csv'); late = late[late.model.eq('B1_171')].copy()
    late = late.rename(columns={'close40_return':'reference_gross','amount_gate':'amount_gate_original'})
    late['source'] = 'CO7_fixed_picks_with_extended_dates'
    d = pd.concat([early,late[['signal_date','symbol6','reference_gross','amount_gate_original','source']]], ignore_index=True)
    d['symbol6'] = d.symbol6.where(d.symbol6.notna(), '')
    d['selected'] = d.symbol6.ne('')
    d['next_date'] = d.signal_date.map(dict(zip(calendar[:-1],calendar[1:])))
    assert not d.signal_date.duplicated().any()
    d = d.sort_values('signal_date').reset_index(drop=True)
    # No labels or new factors enter the identity of the stock to be scored.
    return d


def extract_day(task):
    day, symbols, local = task
    folder = OUT/'days'/day; folder.mkdir(parents=True, exist_ok=True)
    if (folder/'complete.json').exists(): return day, 'cached'
    from scripts.build_automl_close_open_factor_panel import normalize_bars
    if local:
        b = normalize_bars(pd.read_csv(local, dtype={'symbol':str}))
        b = b[b.symbol6.str.startswith(('0','3','6'))].copy()
        cut = b[b.clock.isin(CLOCKS)&b.valid]
        amount = cut.groupby('symbol6').agg(amount=('turnover','sum'), count=('clock','nunique'))
        amount.loc[amount['count'].ne(220),'amount'] = np.nan
        snap = b[b.clock.eq('14:40:00')&b.valid][['symbol6','close']]
        paths = b[b.symbol6.isin(symbols)].copy()
        source = {'kind':'local_lossless_minutes','path':local,'sha256':sha(local)}
    else:
        from scripts.benchmark_automl_1440_inference import db_connection, query
        from scripts.build_automl_close_open_factor_panel import db_minutes
        with db_connection() as db:
            snap = query(db,"SELECT stk_code AS symbol6,latest_price AS close,fetch_time FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 AND bar_end_time=%s AND is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time",(day,day+' 14:40:00'))
            amount = query(db,"SELECT stk_code AS symbol6,SUM(amount) AS amount,COUNT(DISTINCT bar_end_time) AS count FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 AND bar_end_time BETWEEN %s AND %s AND is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time GROUP BY stk_code",(day,day+' 09:31:00',day+' 14:40:00'))
            paths = db_minutes(db, day, '15:00:00', symbols)
        for f in (snap,amount): f['symbol6'] = f.symbol6.astype(str).str[:6].str.zfill(6)
        amount.loc[amount['count'].ne(220),'amount'] = np.nan
        amount = amount.set_index('symbol6')
        fetched = pd.to_datetime(snap.fetch_time)
        source = {'kind':'database_finalized_exact_minutes','fetch_min':str(fetched.min()),'fetch_max':str(fetched.max()),
                  'snapshots_fetched_after_signal_minute':int((fetched>pd.Timestamp(day+' 14:40:00')).sum())}
    snap = snap[snap.symbol6.str.startswith(('0','3','6'))].copy()
    assert not snap.symbol6.duplicated().any()
    paths = paths[['symbol6','clock','open','high','low','close','volume','turnover','valid']].copy()
    paths.to_csv(folder/'paths.csv.gz', index=False, float_format='%.17g')
    snap.to_csv(folder/'snapshot.csv.gz', index=False, float_format='%.17g')
    amount.reset_index().to_csv(folder/'amounts.csv.gz', index=False, float_format='%.17g')
    source.update({'day':day,'market_rows':len(snap),'path_rows':len(paths),
                   'paths_sha256':sha(folder/'paths.csv.gz'),'snapshot_sha256':sha(folder/'snapshot.csv.gz'),
                   'amounts_sha256':sha(folder/'amounts.csv.gz')})
    save_json(folder/'complete.json',source)
    return day, len(snap), len(paths)


def stock_features(path, hist):
    p = path[path.clock.isin(CLOCKS)].sort_values('clock')
    if list(p.clock)!=CLOCKS or not p.valid.all(): return {}, False
    if not np.isfinite(p[['open','high','low','close','volume']].to_numpy(float)).all(): return {}, False
    w = p.tail(30); span = w.high.max()-w.low.min()
    return {'relative_turnover5':p.volume.sum()/hist.prior_shares*100/hist.prior_turn5,
            'last5_return':p.iloc[-1].close/p.iloc[-5].open-1,
            'last30_position':(p.iloc[-1].close-w.low.min())/span if span>0 else .5,
            'prior5_return':hist.prior5_return,'open_gap':p.iloc[0].open/hist.preclose-1}, True


def market_features(snap, history):
    m = snap.merge(history[['symbol6','preclose']],on='symbol6',how='inner',validate='one_to_one')
    m = m[m.preclose.gt(0)&m.close.gt(0)].copy(); r=m.close/m.preclose-1
    return {'market_breadth':float(r.gt(0).mean()),'market_median':float(r.median()),
            'market_iqr':float(r.quantile(.75)-r.quantile(.25)),
            'lag5_market_breadth':float(history.lag5_market_breadth.iloc[0]),'market_n':len(m)}


def prepare(workers=2):
    OUT.mkdir(parents=True,exist_ok=True); REPORT.mkdir(parents=True,exist_ok=True)
    raw, source_files = load_daily(); hist=historical(raw)
    calendar=sorted(raw.trade_date.unique()); picks=frozen_picks(calendar)
    picks.to_csv(OUT/'frozen_picks.csv',index=False,float_format='%.17g')
    start=calendar[max(0,calendar.index(picks.signal_date.min())-20)]
    days=[d for d in calendar if start<=d<=picks.next_date.max()]
    tasks=[]
    for day in days:
        codes=set(picks.loc[(picks.signal_date.eq(day)|picks.next_date.eq(day))&picks.selected,'symbol6'])
        root=Path('outputs/stock_automl/mk_1m_compact_2025' if day<'2026' else 'outputs/stock_automl/mk_1m_compact')
        path=root/(day+'.csv.zst')
        # CO7 used database bars from August 2026, even where another archive exists.
        local=str(path) if path.exists() and day<'2026-08-01' else ''
        tasks.append((day,sorted(codes),local))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i,f in enumerate(as_completed([pool.submit(extract_day,t) for t in tasks]),1):
            result=f.result()
            if i%10==0 or not isinstance(result[1],int): print('prepared',i,len(tasks),result,flush=True)
    amounts=pd.concat([read(OUT/'days'/d/'amounts.csv.gz').assign(signal_date=d) for d in days],ignore_index=True)
    pivot=amounts.pivot(index='signal_date',columns='symbol6',values='amount').reindex(days)
    med=pivot.shift().rolling(20,min_periods=20).median()
    rows=[]; minute_paths=[]; audits=[]
    hgroups={d:g for d,g in hist.groupby('trade_date')}
    for item in picks.to_dict('records'):
        day=item['signal_date']; h=hgroups[day]
        snap=read(OUT/'days'/day/'snapshot.csv.gz'); item.update(market_features(snap,h))
        if item['selected']:
            code=item['symbol6']; b=read(OUT/'days'/day/'paths.csv.gz'); b=b[b.symbol6.eq(code)].sort_values('clock')
            nxt=read(OUT/'days'/item['next_date']/'paths.csv.gz'); nxt=nxt[nxt.symbol6.eq(code)].sort_values('clock')
            hs=h[h.symbol6.eq(code)]
            features,valid=stock_features(b,hs.iloc[0]) if len(hs)==1 else ({},False)
            item.update(features);item['stock_path_complete']=valid
            def price(frame,clock,col):
                v=frame[frame.clock.eq(clock)&frame.valid][col]
                return float(v.iloc[0]) if len(v)==1 and v.iloc[0]>0 else np.nan
            item['signal_price']=price(b,'14:40:00','close')
            item['entry_1441']=price(b,'14:41:00','open');item['entry_1442']=price(b,'14:42:00','open')
            item['exit_0940']=price(nxt,'09:40:00','close')
            item['amount']=pivot.at[day,code] if code in pivot else np.nan
            item['prior20_amount_median']=med.at[day,code] if code in med else np.nan
            ratio=item['amount']/item['prior20_amount_median']
            item['computed_amount_gate']=float(ratio>=1) if np.isfinite(ratio) else np.nan
            item['amount_gate']=item['amount_gate_original'] if day>='2025-08-01' else item['computed_amount_gate']
            for kind,entry in [('reference',item['signal_price']),('1441',item['entry_1441']),('1442',item['entry_1442'])]:
                item['gross_'+kind]=item['exit_0940']/entry-1
            # Corporate-action flag is an outcome audit, never a feature or live gate.
            nh=hgroups[item['next_date']]; current_close=float(hs.iloc[0].close) if len(hs)==1 else np.nan
            nh=nh[nh.symbol6.eq(code)]
            item['corporate_action']=bool(abs(float(nh.iloc[0].preclose)/current_close-1)>.001) if len(nh)==1 and current_close>0 else False
            morning=nxt[nxt.clock.isin(CLOCKS[:10])&nxt.valid]
            second=morning.high.nlargest(2)
            item['second_high_return']=float(second.iloc[-1]/item['signal_price']-1) if morning.clock.tolist()==CLOCKS[:10] else np.nan
            hold=pd.concat([b[b.clock.ge('14:42:00')].assign(date=day),nxt[nxt.clock.le('09:40:00')].assign(date=item['next_date'])])
            hold=hold.assign(signal_date=day,timestamp=lambda d:d.date+' '+d.clock)
            minute_paths.append(hold[['signal_date','timestamp','close','valid']])
            expected=list(pd.date_range(day+' 14:42',day+' 15:00',freq='min').strftime('%Y-%m-%d %H:%M:%S'))+list(pd.date_range(item['next_date']+' 09:31',periods=10,freq='min').strftime('%Y-%m-%d %H:%M:%S'))
            item['valuation_path_complete']=hold.timestamp.tolist()==expected and bool(hold.valid.all())
            audits.append({'signal_date':day,'symbol6':code,'reference_gross':item['reference_gross'],'reconstructed_gross':item['gross_reference'],
                           'reference_difference':item['gross_reference']-item['reference_gross'],
                           'original_gate':item['amount_gate_original'],'computed_gate':item['computed_amount_gate'],
                           'valuation_path_complete':item['valuation_path_complete']})
        rows.append(item)
    frame=pd.DataFrame(rows).replace([np.inf,-np.inf],np.nan)
    frame['feature_complete']=frame[MARKET+STOCK].notna().all(axis=1)
    frame['decision_time']=frame.signal_date+' 14:40:00'
    frame['label_available_time']=frame.next_date+' 09:40:00'
    frame.to_csv(OUT/'dataset.csv.gz',index=False,float_format='%.17g')
    pd.concat(minute_paths,ignore_index=True).to_csv(OUT/'valuation_paths.csv.gz',index=False,float_format='%.17g')
    pd.DataFrame(audits).to_csv(REPORT/'input_reconciliation.csv',index=False,float_format='%.17g')
    pd.DataFrame([json.loads((OUT/'days'/d/'complete.json').read_text()) for d in days]).to_csv(REPORT/'source_availability.csv',index=False)
    save_json(OUT/'input_manifest.json',{'dataset_sha256':sha(OUT/'dataset.csv.gz'),'picks_sha256':sha(OUT/'frozen_picks.csv'),
              'design_sha256':sha(REPORT/'DESIGN.md'),'rows':len(frame),'selected':int(frame.selected.sum()),
              'daily_sources':[{'path':str(p),'sha256':sha(p)} for p in source_files],
              'selection_sources':[{'path':str(p),'sha256':sha(p)} for p in [OLD/'daily.csv',Path('docs/stock_automl_runs/20261011_mk_2025_mar_aug_backtest/top2_verified.csv')]],
              'days':[json.loads((OUT/'days'/d/'complete.json').read_text()) for d in days]})
    print('DATASET COMPLETE',len(frame),int(frame.selected.sum()),flush=True)


def settle(capital,gross,exposure=1.,extra_cost=0.):
    budget=capital*exposure
    if budget<=5:return capital
    buy=budget/(1.00026) if budget/1.00026*.00025>=5 else (budget-5)/1.00001
    sale=buy*(1+gross)
    return capital-budget+sale-max(sale*.00025,5)-sale*.00051-buy*extra_cost


def net(gross):
    return settle(50_000.,gross)/50_000.-1 if np.isfinite(gross) else np.nan


def fit_model(d,cols):
    x=d[cols].replace([np.inf,-np.inf],np.nan)
    lo=x.quantile(.01);hi=x.quantile(.99);median=x.median().fillna(0.)
    x=x.clip(lo,hi,axis=1).fillna(median);mean=x.mean();std=x.std(ddof=0).replace(0,1).fillna(1)
    model=Ridge(alpha=100.,solver='svd').fit((x-mean)/std,d.net_main.clip(-.1,.1))
    return {'model':model,'cols':cols,'lo':lo,'hi':hi,'median':median,'mean':mean,'std':std}


def predict(m,d):
    x=d[m['cols']].replace([np.inf,-np.inf],np.nan).clip(m['lo'],m['hi'],axis=1).fillna(m['median'])
    return m['model'].predict((x-m['mean'])/m['std'])


def path_stats(gross,trade):
    capital=100_000.;path=[capital]
    for r,t in zip(gross,trade):
        if t:
            if not np.isfinite(r):return np.nan,np.nan
            capital=settle(capital,r)
        path.append(capital)
    a=np.asarray(path)
    return float(a[-1]/a[0]-1),float(-(a/np.maximum.accumulate(a)-1).min())


def choose_gate(v,amount=True):
    if 'corporate_action' in v:
        v=v[~v.corporate_action.fillna(False).astype(bool)].copy()
    eligible=v.selected & (v.amount_gate.eq(1) if amount else True)
    valid=v.gross_1442.notna(); n=int((eligible&valid).sum())
    if (eligible&~valid).any(): raise ValueError('Unknown validation outcome: do not silently remove')
    base_ret,base_dd=path_stats(v.gross_1442,eligible)
    rows=[]
    for threshold in THRESHOLDS:
        trade=eligible & v.score.ge(threshold)
        if threshold==-999: trade=eligible.copy()
        gain,dd=path_stats(v.gross_1442,trade)
        enough=(threshold in (-999,999)) or (trade.sum()>=10 and trade.sum()>=.4*n)
        allowed=bool(enough and (threshold==-999 or dd<=base_dd+1e-12))
        rows.append({'threshold':threshold,'validation_trades':int(trade.sum()),'validation_baseline_trades':n,
                     'nav_return':gain,'drawdown':dd,'allowed':allowed})
    chosen=-999.
    if n>=15:
        best=max((r for r in rows if r['allowed']),key=lambda r:(r['nav_return'],r['validation_trades'],-r['threshold']))
        chosen=best['threshold']
    return chosen,rows


def chronological_split(d,month):
    test=d[d.signal_date.str.startswith(month)].copy()
    past=d[d.signal_date.lt(test.signal_date.min())].tail(100)
    if len(past)<100:return None
    train=past.iloc[:60].copy();validation=past.iloc[60:].copy()
    train=train[train.selected & train.net_main.notna() & train.label_available_time.lt(validation.decision_time.min()) & ~train.corporate_action.fillna(False).astype(bool)]
    assert train.signal_date.max()<validation.signal_date.min()<test.signal_date.min()
    assert train.label_available_time.max()<validation.decision_time.min()
    assert validation.label_available_time.max()<test.decision_time.min()
    return train,validation,test


def fit():
    import sklearn
    import scipy
    import platform
    import threadpoolctl
    d=read(OUT/'dataset.csv.gz');d['net_main']=d.gross_1442.map(net)
    d['corporate_action']=d.corporate_action.fillna(False).astype(bool)
    predictions=[];folds=[];grids=[]
    for month in sorted(d.signal_date.str[:7].unique()):
        split=chronological_split(d,month)
        if split is None:continue
        train,val,test=split
        assert len(train)>=25
        for arm,cols in ARMS.items():
            folder=OUT/'models'/month/arm;folder.mkdir(parents=True,exist_ok=True)
            model=fit_model(train,cols);val=val.copy();test=test.copy()
            val['score']=predict(model,val);test['score']=predict(model,test)
            threshold,grid=choose_gate(val,True)
            for r in grid:r.update(month=month,arm=arm,mode='overlay',chosen=r['threshold']==threshold)
            grids+=grid;test['threshold_overlay']=threshold
            if arm=='combined':
                t2,g2=choose_gate(val,False)
                for r in g2:r.update(month=month,arm=arm,mode='replacement',chosen=r['threshold']==t2)
                grids+=g2;test['threshold_replacement']=t2
            joblib.dump(model,folder/'model.joblib',compress=3)
            train.to_csv(folder/'training.csv.gz',index=False,float_format='%.17g')
            val.to_csv(folder/'validation.csv.gz',index=False,float_format='%.17g')
            test.to_csv(folder/'test.csv.gz',index=False,float_format='%.17g')
            meta={'month':month,'arm':arm,'training_days':train.signal_date.tolist(),
                  'validation_days':val.signal_date.tolist(),'test_days':test.signal_date.tolist(),
                  'training_label_max':train.label_available_time.max(),'validation_start':val.decision_time.min(),
                  'validation_label_max':val.label_available_time.max(),'test_start':test.decision_time.min(),
                  'model_sha256':sha(folder/'model.joblib'),'training_sha256':sha(folder/'training.csv.gz'),
                  'validation_sha256':sha(folder/'validation.csv.gz'),'test_sha256':sha(folder/'test.csv.gz'),
                  'threshold_overlay':threshold,'features':cols,'refit_on_validation':False}
            save_json(folder/'fold.json',meta);folds.append(meta)
            test['arm']=arm;predictions.append(test)
        print('fitted',month,'train',len(train),'validation',len(val),'test',len(test),flush=True)
    pd.concat(predictions,ignore_index=True).to_csv(OUT/'predictions.csv.gz',index=False,float_format='%.17g')
    pd.DataFrame(grids).to_csv(REPORT/'threshold_validation.csv',index=False,float_format='%.17g')
    save_json(OUT/'folds.json',folds)
    save_json(REPORT/'run_manifest.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                'script_sha256':sha(Path(__file__)),'design_sha256':sha(REPORT/'DESIGN.md'),
                'dataset_sha256':sha(OUT/'dataset.csv.gz'),'sklearn':sklearn.__version__,'alpha':100,
                'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__,'pandas':pd.__version__,
                'joblib':joblib.__version__,'threadpoolctl':threadpoolctl.__version__,
                'source_in_working_tree':True,'source_files':{str(p):sha(p) for p in [Path(__file__),Path('scripts/report_b1_market_trade_gate_co8.py'),Path('scripts/check_b1_market_trade_gate_co8.py')]},
                'folds':len(folds),'model_arms':ARMS,'thresholds':THRESHOLDS})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','fit']);p.add_argument('--workers',type=int,default=2);args=p.parse_args()
    with threadpool_limits(limits=1):
        prepare(args.workers) if args.phase=='prepare' else fit()

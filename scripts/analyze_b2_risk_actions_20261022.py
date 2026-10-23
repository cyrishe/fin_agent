"""Frozen risk warnings: opening-floor distributions, exit and cancel attribution."""
import argparse,gzip,io,json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np,pandas as pd,zstandard
from scripts import research_b2_floor_risk_20261021 as old
from scripts.replay_limit_rebound_cash_r23 import replay

ROOT=old.ROOT;OUT=ROOT/'docs/stock_automl_runs/20261022_b2_risk_actions';FEE=.001
MODELS=[('old15','上一轮买前模型',old.OUT/'test_predictions_exact.csv','id','logistic'),
        ('close34','四K收盘跌停模型',ROOT/'docs/stock_automl_runs/20261021_b2_d1_risk/test_predictions.csv','candidate','close_early_lr1'),
        ('union41','四K开收盘并集模型',ROOT/'docs/stock_automl_runs/20261021_b2_d1_risk/test_predictions.csv','candidate','union_early_lr01')]

def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [clean(v) for v in x]
    if isinstance(x,(np.integer,np.bool_)):return x.item()
    if isinstance(x,(float,np.floating)):return float(x) if np.isfinite(x) else None
    return x
def write(name,obj):(OUT/name).write_text(json.dumps(clean(obj),ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def net(price,entry):return price/entry*(1-FEE)/(1+FEE)-1
def account_replay(frame,calendar,end):
    summary,monthly,daily,ledger,excluded=replay(frame,calendar,start='2026-01-01',end=end,fee_rate=FEE)
    assert not excluded and summary['pending']==0 and summary['entries']==len(frame)
    return dict(summary=summary,monthly=monthly,daily=daily,ledger=ledger)

def local_bars(row):
    day=row['卖出日'];sym=row['symbol'];dest=OUT/'minutes'/f'{day}_{sym}.csv.gz'
    if dest.exists():return {'sequence':row['sequence'],'cache':str(dest),'sha256':old.sha(dest)}
    candidates=[(Path('/Volumes/ext/tushare_market_data')/day[:4]/f'{day.replace("-","")}.csv.gz','tushare'),
      (ROOT/'outputs/stock_automl'/('mk_1m_compact_2025' if day[:4]=='2025' else 'mk_1m_compact')/f'{day}.csv.zst','mk')]
    for path,kind in candidates:
        if not path.exists():continue
        opener=gzip.open if kind=='tushare' else zstandard.open;lines=[]
        with io.BufferedReader(opener(path,'rb')) as s:
            header=s.readline()
            for line in s:
                code=line[:6] if kind=='tushare' else line.split(b',',2)[1][:6]
                if code.decode()==sym:lines.append(line)
        if not lines:continue
        b=pd.read_csv(io.BytesIO(header+b''.join(lines)))
        if kind=='tushare':
            from scripts.import_tushare_minutes_2024 import convert
            b=convert(b,day)
        b.to_csv(dest,index=False,compression='gzip')
        return dict(sequence=row['sequence'],cache=str(dest),sha256=old.sha(dest),source=str(path),source_sha256=old.sha(path))
    for directory in ['limit_rebound_shape_top3_r20_20261015','limit_rebound_shape_r19_20261015']:
        path=ROOT/'outputs/stock_automl'/directory/'minute_days'/f'{day}_raw.pkl'
        if not path.exists():continue
        b=pd.read_pickle(path);b=b[b.symbol.astype(str).str[:6].eq(sym)]
        groups=b.groupby('source') if 'source' in b else [('all',b)]
        for _,g in groups:
            if len(g)==240:
                g.to_csv(dest,index=False,compression='gzip')
                return dict(sequence=row['sequence'],cache=str(dest),sha256=old.sha(dest),source=str(path),source_sha256=old.sha(path))
    return dict(sequence=row['sequence'],missing=True,day=day,symbol=sym)

def prepare():
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'minutes').mkdir(exist_ok=True)
    f=old.load();wanted=f[f.split.eq('test')|f.open_floor.eq('是')];audit=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        jobs=[pool.submit(local_bars,r) for r in wanted.to_dict('records')]
        for future in as_completed(jobs):
            audit.append(future.result())
            if len(audit)%25==0:print('local',len(audit),'/',len(wanted),flush=True)
    missing=[a for a in audit if a.get('missing')]
    if missing:
        from scripts.benchmark_automl_1440_inference import db_connection,query
        with db_connection() as conn:
            for x in missing:
                b=query(conn,"SELECT stk_code AS symbol,bar_end_time AS time,open_price AS open,high_price AS high,low_price AS low,latest_price AS close,volume FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s AND stk_code=%s AND kline_type='1m' AND period_minutes=1 AND is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time ORDER BY bar_end_time",(x['day'],x['symbol']))
                assert len(b)==240,(x,len(b));b.volume=pd.to_numeric(b.volume)*100
                dest=OUT/'minutes'/f"{x['day']}_{x['symbol']}.csv.gz";b.to_csv(dest,index=False,compression='gzip')
                x.update(missing=False,cache=str(dest),sha256=old.sha(dest),source='read-only finalized DB minute; volume lots*100')
                print('DB',x['day'],x['symbol'],flush=True)
            conn.rollback()
    write('minute_sources.json',audit)
    print('ready',len(audit))

def run():
    f=old.load();test=f[f.split=='test'];records=[]
    sources=json.loads((OUT/'minute_sources.json').read_text())
    for info in sources:
        row=f[f.sequence==info['sequence']].iloc[0];b=pd.read_csv(info['cache']).sort_values('time').reset_index(drop=True)
        assert old.sha(info['cache'])==info['sha256'] and len(b)==240 and b.time.is_unique
        assert b.time.iloc[0][11:16]=='09:31' and b.time.iloc[-1][11:16]=='15:00'
        assert np.allclose([b.open.iloc[0],b.high.max(),b.low.min(),b.close.iloc[-1]],[row.d2_open,row.d2_high,row.d2_low,row.d2_close],rtol=0,atol=.011)
        floor=row.d2_floor;q=b.iloc[0]
        touch=bool(q.low<=floor+.001);openfloor=bool(abs(q.open-floor)<.001);closefloor=bool(abs(q.close-floor)<.001)
        assert openfloor==(row.open_floor=='是')
        assert np.isclose(net(row.d2_close,row.entry),row.original_net,atol=1e-12)
        assert floor<=row.d2_close+.001
        # Decision at end of 09:31; earliest eligible execution proxy is 09:32.
        later=b.iloc[1:];ok=later[(later.open>floor+.001)&later.volume.gt(0)]
        conservative=later[(later.low>floor+.001)&later.volume.gt(0)]
        rec=dict(sequence=int(row.sequence),symbol=row.symbol,D0=row.d0_date,D2=row['卖出日'],split=row.split,
           open_floor=openfloor,first_touch=touch,first_close_floor=closefloor,first_all_locked=bool(q.high<=floor+.001),
           floor=floor,entry=row.entry,first_open=q.open,first_high=q.high,first_low=q.low,first_close=q.close,
           d2_close=row.d2_close,d2_return=row.d2_ret,original_net=row.original_net,
           floor_hyp_net=net(floor,row.entry),all_day_locked=bool(b.high.max()<=floor+.001),
           proxy_time=str(ok.time.iloc[0])[11:16] if len(ok) else None,proxy_price=float(ok.open.iloc[0]) if len(ok) else None,
           strict_time=str(conservative.time.iloc[0])[11:16] if len(conservative) else None,
           strict_price=float(conservative.open.iloc[0]) if len(conservative) else None)
        rec['proxy_net']=net(rec['proxy_price'],row.entry) if rec['proxy_price'] else None
        records.append(rec)
    minute=pd.DataFrame(records).sort_values('sequence');minute.to_csv(OUT/'D2首分钟与离板明细.csv',index=False,encoding='utf-8-sig')
    opening=f[f.open_floor.eq('是')].copy();rates=opening.d2_ret
    intervals=[('收盘仍跌停',opening.close_floor.eq('是')),
      ('已离板，跌幅≥8%',opening.close_floor.ne('是')&rates.le(-.08)),
      ('跌幅5%至不足8%',rates.gt(-.08)&rates.le(-.05)),
      ('下跌但不足5%',rates.gt(-.05)&rates.lt(0)),('平盘至不足5%',rates.ge(0)&rates.lt(.05)),('上涨≥5%',rates.ge(.05))]
    distribution=[dict(band=n,n=int(mask.sum()),fraction=float(mask.mean())) for n,mask in intervals]
    assert sum(x['n'] for x in distribution)==34
    pd.DataFrame(distribution).to_csv(OUT/'34笔收盘区间.csv',index=False,encoding='utf-8-sig')
    opening_summary=dict(n=34,loss_open=int(opening.open_net.lt(0).sum()),mean_open=float(opening.open_net.mean()),mean_close=float(opening.original_net.mean()),
      mean_delta=float((opening.open_net-opening.original_net).mean()),close_rebound=int(opening.d2_close.gt(opening.d2_open+.001).sum()),
      all_day_locked=int(opening.all_locked.eq('是').sum()))
    om=minute[minute.open_floor];of=om[om.proxy_net.notna()]
    opening_summary.update(ideal_delta_per_10000=float((opening.open_net-opening.original_net).sum()*10000),
      proxy_found=len(of),proxy_missing=int(om.proxy_net.isna().sum()),
      proxy_subset_delta_per_10000=float((of.proxy_net-of.original_net).sum()*10000))
    calendar=json.loads((ROOT/'outputs/stock_automl/b2_2021_minute_r60_20261020/calendar.json').read_text())
    end=test['卖出日'].max();baseline=account_replay(test,calendar,end)
    for n in ['daily','monthly','ledger']:pd.DataFrame(baseline[n]).to_csv(OUT/f'baseline_{n}.csv',index=False,encoding='utf-8-sig')
    actions=[];cancellations=[];account=[];detail=[]
    model_flags={}
    for model,label,path,col,id in MODELS:
        pred=pd.read_csv(path,dtype={'股票':str,'symbol':str});pred=pred[pred[col]==id]
        if 'sequence' in pred:
            flagged=set(pred.loc[pred.flagged.eq(1),'sequence'])
        else:
            keys=set(zip(pred.loc[pred.flagged.eq(1),'D0'],pred.loc[pred.flagged.eq(1),'股票']))
            flagged={r.sequence for r in test.itertuples() if (r.d0_date,r.symbol) in keys}
        model_flags[model]=flagged;h=test[test.sequence.isin(flagged)];v=h.original_net
        cancellations.append(dict(model=model,label=label,flagged=len(h),profits=int(v.gt(0).sum()),losses=int(v.lt(0).sum()),
          forfeited_profit_per_10000=float(v[v>0].sum()*10000),avoided_loss_per_10000=float(-v[v<0].sum()*10000),
          cancel_net_delta_per_10000=float(-v.sum()*10000),original_flagged_mean=float(v.mean()),
          original_sized_profit=float(h['盈亏元'].sum()),original_sized_gains=float(h.loc[v>0,'盈亏元'].sum()),original_sized_losses=float(-h.loc[v<0,'盈亏元'].sum())))
        filtered=test[~test.sequence.isin(flagged)]
        replayed=account_replay(filtered,calendar,end)
        account.append(dict(model=model,result=replayed['summary']))
        for n,data in [('daily',replayed['daily']),('monthly',replayed['monthly']),('ledger',replayed['ledger'])]:
            pd.DataFrame(data).to_csv(OUT/f'{model}_cancel_{n}.csv',index=False,encoding='utf-8-sig')
        group=minute[minute.sequence.isin(flagged)]
        assert len(group)==len(h)
        for trigger in ['open_floor','first_touch','first_close_floor']:
            z=group[group[trigger]];filled=z[z.proxy_net.notna()];unfilled=z[z.proxy_net.isna()]
            actions.append(dict(model=model,trigger=trigger,flagged=len(h),triggered=len(z),
              ideal_floor_delta_per_10000=float(((z.floor_hyp_net-z.original_net)*10000).sum()),
              ideal_floor_mean_delta=float((z.floor_hyp_net-z.original_net).mean()) if len(z) else None,
              proxy_filled=len(filled),proxy_unfilled=len(unfilled),all_day_locked=int(z.all_day_locked.sum()),
              proxy_realized_subset_delta_per_10000=float(((filled.proxy_net-filled.original_net)*10000).sum()),
              unresolved_close_gross_value_per_10000=float(((1+unfilled.original_net)/(1-FEE)*10000).sum()),
              unresolved_hypothetical_liquidation_value_per_10000=float(((1+unfilled.original_net)*10000).sum()),
              proxy_win_vs_close=int(filled.proxy_net.gt(filled.original_net+1e-12).sum()),proxy_loss_vs_close=int(filled.proxy_net.lt(filled.original_net-1e-12).sum())))
            for row in z.to_dict('records'):detail.append(dict(model=model,trigger=trigger,**row))
    # A comparison applying the rule to every test trade, independent of models.
    for trigger in ['open_floor','first_touch','first_close_floor']:
        z=minute[minute.split.eq('test')&minute[trigger]];fill=z[z.proxy_net.notna()]
        actions.append(dict(model='all98',trigger=trigger,flagged=98,triggered=len(z),ideal_floor_delta_per_10000=float(((z.floor_hyp_net-z.original_net)*10000).sum()),
          proxy_filled=len(fill),proxy_unfilled=int(z.proxy_net.isna().sum()),all_day_locked=int(z.all_day_locked.sum()),
          proxy_realized_subset_delta_per_10000=float(((fill.proxy_net-fill.original_net)*10000).sum())))
    pd.DataFrame(detail).to_csv(OUT/'模型触发退出明细.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(cancellations).to_csv(OUT/'取消买入损益归因.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(actions).to_csv(OUT/'首分钟退出对照.csv',index=False,encoding='utf-8-sig')
    write('results.json',dict(distribution=distribution,opening_summary=opening_summary,cancel=cancellations,actions=actions,
      baseline_account=baseline['summary'],cancel_accounts=account,account_period=dict(start='2026-01-01',end=end,initial=1000000),
      assumptions=['All models frozen pre-buy warnings, no D1 full-day features.','First-minute touch defined by 09:31 low; evaluated only at bar end.',
        'Floor exit counterfactual assumes unproven queue fill.','Open-above-floor execution proxy starts next bar; minute volume does not guarantee own fill.',
        'Unfilled are NOT sold; D2 close marked values only. No later-day recovery or true executable account replay.',
        'Cancellation accounts retain original unconditional D2 close fill, fractional shares, 0.1% each side, frozen V2 shadow gate and cohorts.',
        'Fixed 10000 per trade sums are monetary attribution, not continuous account returns.'],
      sources={str(old.SOURCE):old.sha(old.SOURCE),**{str(x[2]):old.sha(x[2]) for x in MODELS}},code_sha256=old.sha(__file__)))
    print(json.dumps(clean(dict(distribution=distribution,opening=opening_summary,cancel=cancellations,actions=actions,baseline=baseline['summary'],accounts=account)),ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run']);a=p.parse_args();{'prepare':prepare,'run':run}[a.stage]()

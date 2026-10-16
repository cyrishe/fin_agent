"""R20: frozen R19 shape discrimination and causal Top3 fallback transfer."""
from __future__ import annotations
import argparse,json,shutil
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np,pandas as pd
import scripts.experiment_limit_rebound_shape_r19 as r19
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha

OUT=Path('outputs/stock_automl/limit_rebound_shape_top3_r20_20261015')
PREV=Path('outputs/stock_automl/limit_rebound_shape_r19_20261015')
BASE=r19.BASE
POLICIES={'top1_base':'Top1原规则','top1_shape':'Top1加形态','top3_base':'前三顺延原规则','top3_shape':'前三顺延加形态'}


def specification():
    OUT.mkdir(exist_ok=True)
    s={'experiment':'R20_frozen_shape_gain_loss_and_top3_transfer',
       'source_rules_sha256':sha(PREV/'all_rules_frozen.json'),
       'stage1':'Saved R18 segments scores; Top1-3 original D0 ranks, no retrain or rerank.',
       'shape':'Apply same monthly R19 frozen rules to rank1,2,3; no optimization on rank2/3 or on test.',
       'split':'same six training months, two validation months, next test month; train D0 scores from saved causal OOF. Existing test period is not new untouched data.',
       'direction_labels':'Each D2/D3/D4 close and strategy exit relative to causal entry proxy: gain>=7%, loss<-5%, middle otherwise. No intraday-high label. Each horizon drops only unknown/discontinuous price outcomes, never imputes zero.',
       'entry':'Observe first five completed minute bars; chronological 09:36..09:40 bar-end labels. Positive-volume open below cap proxy, no cap queue fills.',
       'fallback':'Each minute choose highest D0 rank among shape-passing, ordinary, audited stocks with open below cap and positive volume AT THAT MINUTE; one per day, max rank3. Earlier eligible rank2/3 can precede rank1 opening later. No hindsight priority after seeing entire window.',
       'comparisons':'Individual ranks pass/reject plus all; four daily policies with same causal priority and entry. Expose rank2/3 fallback separately and displacement of later rank1.',
       'data':'Historical D0/D1 N/R only, minute full-day OHLC/volume reconciliation as R19. Missing data excluded and disclosed; fixed top3 ranks not expanded.',
       'returns':'D2/D3/D4 gross, strategy D2 sealed close else D3 sealed close else D4 close. No fees, latency/order-book fill or capital overlap model.'}
    p=OUT/'specification.json'
    if p.exists():assert json.loads(p.read_text())==s
    else:dump(p,s)
    if not (OUT/'rules_frozen.json').exists():shutil.copy2(PREV/'all_rules_frozen.json',OUT/'rules_frozen.json')
    return s


def prepare():
    specification();p=pd.read_pickle(BASE/'panel.pkl').set_index(['symbol','d0_date'])
    endpoint=pd.read_pickle(r19.OLD/'demand.pkl').set_index(['symbol','d0_date'])
    for c in ['d1_date','d1_preclose','d1_open','d1_close','d1_volume','d1_up','d1_cap','d1_st']:
        keys=p.index[p[c].isna()].intersection(endpoint.index);p.loc[keys,c]=endpoint.loc[keys,c]
    p=p.reset_index();parts=[];score_sources=[]
    for month in r19.TEST_MONTHS:
        for part,src in [('train','oof'),('valid','valid'),('test','forecast')]:
            path=BASE/month/f'segments_{src}_scores.pkl';s=pd.read_pickle(path)
            s=s[s.d0_rank.le(3)].merge(p,on=['symbol','d0_date'],validate='one_to_one')
            s['model_month']=month;s['part']=part;parts.append(s);score_sources.append({'path':str(path),'sha256':sha(path)})
    f=pd.concat(parts,ignore_index=True);d=f.drop_duplicates(['symbol','d0_date']).copy()
    hist=pd.read_csv(PREV/'historical_status.csv.gz',dtype=str);hist['symbol']=hist.stk_code.str[:6]
    hg={k:g for k,g in hist.groupby('symbol')}
    def state(sym,day):
        if pd.isna(day):return 'unknown'
        g=hg[sym];a=g[g.begin_date.le(day)&g.end_date.gt(day)]
        assert len(a)==1,(sym,day);return a.st_type.iloc[0]
    for k in (0,1):d[f'state{k}']=[state(r.symbol,getattr(r,f'd{k}_date')) for r in d.itertuples()]
    d['ordinary']=d.state0.isin(['N','R'])&d.state1.isin(['N','R'])
    f=f.merge(d[['symbol','d0_date','state0','state1','ordinary']],on=['symbol','d0_date'],validate='many_to_one')
    months=set(d.d1_date.dropna().str[:7]);wanted=set(d.symbol)
    sources=json.loads((BASE/'source_manifest.json').read_text())['raw_sources'];frames=[]
    for item in sources:
        path=Path(item['path'])
        if not any(m in path.name for m in months):continue
        assert sha(path)==item['sha256']
        a=pd.read_csv(path,dtype={'stk_code':str,'trade_date':str});a['symbol']=a.stk_code.str[:6]
        frames.append(a[a.symbol.isin(wanted)])
    ep=Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014/price_months/price_2026-10-14_extension.csv.gz')
    a=pd.read_csv(ep,dtype={'stk_code':str,'trade_date':str});a['symbol']=a.stk_code.str[:6];frames.append(a[a.symbol.isin(wanted)])
    daily=pd.concat(frames).drop_duplicates(['symbol','trade_date'],keep='last')
    q=daily[['symbol','trade_date','high','low']].rename(columns={'trade_date':'d1_date','high':'d1_high','low':'d1_low'})
    d=d.merge(q,on=['symbol','d1_date'],how='left',validate='one_to_one')
    daily.to_pickle(OUT/'daily_source_subset.pkl');d.to_pickle(OUT/'minute_demand.pkl');f.to_pickle(OUT/'all_top3.pkl')
    shutil.copy2(PREV/'historical_status.csv.gz',OUT/'historical_status.csv.gz')
    dump(OUT/'sources.json',{'score_files':score_sources,'panel':sha(BASE/'panel.pkl'),'daily':sources,'endpoint':sha(ep)})
    print('allrows',len(f),'unique',len(d),'days',d.d1_date.nunique(),flush=True)


def scan(day,dem):
    r19.OUT=OUT;root=OUT/'minute_days';root.mkdir(exist_ok=True);dest=root/f'{day}.json'
    if dest.exists():return json.loads(dest.read_text())
    found={};raws=[]
    # Reuse previously audited extracts, then fill only genuinely missing names.
    for p in [PREV/'minute_days'/f'{day}_raw.pkl',r19.OLD/'days'/f'{day}_raw.pkl']:
        if not p.exists():continue
        raw=pd.read_pickle(p);raw=raw[raw.symbol.isin(set(dem.symbol)-set(found))]
        for (sym,source),g in raw.groupby(['symbol','source']):
            z=r19.facts(g,dem[dem.symbol.eq(sym)].iloc[0])
            if z['audit_ok'] and sym not in found:
                z.update({'symbol':sym,'d1_date':day,'minute_source':str(source)});found[sym]=z;raws.append(g)
    missing=dem[~dem.symbol.isin(found)]
    if len(missing):
        res=r19.scan_day(day,missing)
        for z in res:found[z['symbol']]=z
        rp=root/f'{day}_raw.pkl'
        if rp.exists():raws.append(pd.read_pickle(rp))
    if raws:pd.concat(raws,ignore_index=True).drop_duplicates(['symbol','time','source']).to_pickle(root/f'{day}_raw.pkl')
    rows=[found[s] for s in dem.symbol];dump(dest,r19.clean(rows));return rows


def minutes():
    d=pd.read_pickle(OUT/'minute_demand.pkl');jobs=list(d.groupby('d1_date'));rows=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(scan,day,g) for day,g in jobs]
        for i,task in enumerate(as_completed(futures),1):
            rows.extend(task.result())
            if i%20==0 or i==len(jobs):print('minute days',i,'/',len(jobs),flush=True)
    m=pd.DataFrame(rows);m.to_pickle(OUT/'minute_facts.pkl')
    f=pd.read_pickle(OUT/'all_top3.pkl').merge(m,on=['symbol','d1_date'],how='left',validate='many_to_one')
    f['eligible']=f.ordinary&f.audit_ok.eq(True);f['trade']=f.eligible&f['fill'].eq(True)
    good=f.d1_close.gt(0)
    for k in (2,3,4):
        good &= f[f'd{k}_close'].gt(0)&f[f'd{k}_preclose'].sub(f[f'd{k-1}_close']).abs().le(.011)
        f[f'ret{k}']=np.where(f.trade&good,f[f'd{k}_close']/f.entry-1,np.nan)
    f['strategy']=np.where(f.d2_up.eq(True),f.ret2,np.where(f.d3_up.eq(True),f.ret3,f.ret4))
    f['complete']=f.trade&f.observed_3&f.ret4.notna();f['shape_pass']=False
    for s in json.loads((OUT/'rules_frozen.json').read_text()):
        ix=f.model_month.eq(s['month']);f.loc[ix,'shape_pass']=f.loc[ix,'eligible']&r19.mask(f.loc[ix],s['selected'])
    f.to_pickle(OUT/'enriched_top3.pkl')
    dump(OUT/'coverage.json',{'unique':len(d),'audited':int(m.audit_ok.sum()),
        'failures':m.loc[~m.audit_ok.eq(True),'reason'].value_counts().to_dict(),
        'fail_rows':r19.clean(f.loc[~f.audit_ok.eq(True),['symbol','d0_date','d1_date','d1_volume','reason']].drop_duplicates())})
    print('enriched',len(f),flush=True)


def select(f,top=3,shape=True):
    a=f[f.d0_rank.le(top)&f.trade&(f.shape_pass if shape else True)].copy()
    # Earliest valid first-open time, then D0 rank, equals chronological replay.
    return a.sort_values(['d0_date','entry_time','d0_rank']).drop_duplicates('d0_date')


def stats(f):
    g=f[f.trade];known=g[g.observed_3]
    z={'n':len(g),'known':len(known),'hit':float(known.label_3.mean()) if len(known) else None,
       'hits':int(known.label_3.sum()),'rank_counts':{str(int(k)):int(v) for k,v in g.d0_rank.value_counts().sort_index().items()},
       'whole_entry':int(g.entry_whole_below.eq(True).sum())}
    for c in ['ret2','ret3','ret4','strategy']:
        v=g[c].dropna();n=len(v);up=v.ge(.07-1e-12);down=v.lt(-.05-1e-12)
        z[c]={'n':n,'mean':float(v.mean()) if n else None,'up_n':int(up.sum()),'down_n':int(down.sum()),
          'up':float(up.mean()) if n else None,'down':float(down.mean()) if n else None,'median':float(v.median()) if n else None}
    return z


def report():
    specification();f=pd.read_pickle(OUT/'enriched_top3.pkl');monthly=[];discrimination=[];selected=[]
    for (month,part),g in f.groupby(['model_month','part']):
        for rank in [1,2,3]:
            for group in ['all','pass','reject']:
                q=g[g.d0_rank.eq(rank)&g.trade]
                if group!='all':q=q[q.shape_pass.eq(group=='pass')]
                discrimination.append({'month':month,'part':part,'rank':rank,'group':group,**stats(q)})
        for code in POLICIES:
            a=select(g,1 if code.startswith('top1') else 3,code.endswith('shape')).copy();a['policy']=code;selected.append(a)
            monthly.append({'month':month,'part':part,'policy':code,**stats(a)})
    picks=pd.concat(selected,ignore_index=True);picks.to_pickle(OUT/'daily_policy_picks.pkl')
    dump(OUT/'monthly_policy_metrics.json',monthly);dump(OUT/'monthly_discrimination.json',discrimination)
    test=f[f.part.eq('test')].copy();assert not test.duplicated(['d0_date','d0_rank']).any()
    result={'policies':{},'ranks':{},'test_days':int(test.d0_date.nunique()),'test_rows':len(test)}
    for code in POLICIES:
        a=picks[picks.part.eq('test')&picks.policy.eq(code)]
        result['policies'][code]=stats(a)
        keys=set(zip(a.symbol,a.d0_date));test[code]=[(s,d) in keys for s,d in zip(test.symbol,test.d0_date)]
    for rank in [1,2,3]:
        result['ranks'][str(rank)]={}
        a=test[test.d0_rank.eq(rank)&test.trade]
        for group in ['all','pass','reject']:
            g=a if group=='all' else a[a.shape_pass.eq(group=='pass')]
            result['ranks'][str(rank)][group]=stats(g)
    a=test[test.top3_shape];b=test[test.top1_shape];fallback=a[a.d0_rank.gt(1)]
    result['fallback']=stats(fallback)
    result['fallback_new_days']=stats(fallback[~fallback.d0_date.isin(b.d0_date)])
    result['fallback_displaced_later_top1']=stats(fallback[fallback.d0_date.isin(b.d0_date)])
    result['frozen_rules_sha256']=sha(OUT/'rules_frozen.json')
    test.to_pickle(OUT/'test_top3.pkl');dump(OUT/'result.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('stage',choices=['prepare','minutes','report']);args=a.parse_args();globals()[args.stage]()

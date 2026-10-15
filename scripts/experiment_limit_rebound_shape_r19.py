"""R19: frozen D0 Top1, first-five-minute simple rules, causal later entry."""
from __future__ import annotations
import argparse,json,math,shutil
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np,pandas as pd
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha
from scripts.experiment_limit_rebound_two_stage_r15 import TEST_MONTHS,window
from scripts.enrich_limit_rebound_segments_r18_0940 import DAY_CLOCK,COLS
from scripts.export_limit_rebound_short_mixed_r12 import filtered_local
from scripts.mk_minute_archive import day_file,COMPACT_ROOT,COMPACT_ROOT_2025
from scripts.benchmark_automl_1440_inference import db_connection,query

BASE=Path('outputs/stock_automl/limit_rebound_segments_r18_20261015')
OLD=BASE/'entry0940_r18a_20261015'
OUT=Path('outputs/stock_automl/limit_rebound_shape_r19_20261015')
FEATURE_NAMES={'high5':'五分最高涨幅','low5':'五分最低涨幅','body5':'五分实体涨幅',
 'pullback5':'高点回落','position5':'五分收价位置','below5':'板下分钟数',
 'whole5':'整分板下数','touch5':'触板分钟数','shape':'前五分形态'}

def clean(x):return json.loads(pd.DataFrame(x).to_json(orient='records',force_ascii=False,double_precision=15))

def rules():
    """Fixed small threshold vocabulary, declared before outcomes are evaluated."""
    atoms=[]
    limits={'high5':[0,.03,.06,.09],'low5':[-.03,0,.03,.06],
       'body5':[-.02,0,.02],'pullback5':[.01,.03,.05],'position5':[.3,.7],
       'below5':[1,3],'whole5':[1,2],'touch5':[1]}
    for f,vs in limits.items():
        for v in vs:
            for op in ('>=','<'):atoms.append((f,op,v))
    for v in ['前弱后强','前强后弱','两段走强','两段走弱','平走']:
        atoms.append(('shape','==',v))
    pairs=[]
    # Six predeclared interpretable families, never arbitrary multi-factor trees.
    for h in [.03,.06,.09]:
        for lo in [-.03,0,.03]:pairs.append([('high5','>=',h),('low5','>=',lo)])
    for body in [0,.02]:
        for pull in [.01,.03,.05]:pairs.append([('body5','>=',body),('pullback5','<',pull)])
    for pos in [.3,.7]:
        for lo in [-.03,0,.03]:pairs.append([('position5','>=',pos),('low5','>=',lo)])
    for n in [1,2]:pairs.append([('touch5','>=',1),('whole5','>=',n)])
    for n in [1,3]:pairs.append([('touch5','>=',1),('below5','>=',n)])
    for sh in ['前弱后强','两段走强']:
        for lo in [-.03,0,.03]:pairs.append([('shape','==',sh),('low5','>=',lo)])
    def label(a):
        f,op,v=a
        val=str(v) if isinstance(v,str) or f.endswith('5') and f in ['below5','whole5','touch5'] else f'{v:.0%}'
        return FEATURE_NAMES[f]+op+val
    out=[{'id':'all','atoms':[],'family':'none','text':'全部保留'}]
    for i,aa in enumerate([[a] for a in atoms]+pairs,1):
        fam='单条件' if len(aa)==1 else '+'.join(a[0] for a in aa)
        out.append({'id':f'r{i:03}','atoms':aa,'family':fam,'text':' 且 '.join(label(a) for a in aa)})
    return out

def specification():
    OUT.mkdir(exist_ok=True)
    spec={'experiment':'R19_first5_shape_top1','stage1':'Saved R18 segments51 RF; no retrain, no rerank. Legacy first-stage pool excluded S/Y only; this stage permits historical N/R only.',
      'monthly':'six train months, two validation months, next month test; first-stage train Top1 from saved causal OOF forecasts',
      'test_months':TEST_MONTHS,'features':'Only D1 bars ending 09:31..09:35 and known D1 price limits/reference; all D0 Top1, no cap-touch prefilter',
      'entry':'Freeze decision after 09:35 bar. Inspect 09:36..09:40 bar opens in chronological order; first positive-volume bar with open below cap by >=0.01 is entry proxy. No fill at cap, no fallback outside D0 Top1. Entire-bar-high-below-cap evidence reported separately, never a decision input.',
      'shape':'Compare 09:32 close with 09:30 open, and 09:35 close with 09:32 close. Sign pair names; zero/zero is flat. It is a coarse close-path description, not reconstructed ticks.',
      'target':'D1-D3 any sealed daily close, same label3. Main rule selection precision with >=40% train retention and >=15 train fills; gross strategy-return guard.',
      'selection':'Predeclared rules <=2 conditions. Train must improve precision by >=3pp, not lower strategy mean, appear in all three chronological 2-month blocks with >=3 fills each, and not reduce precision in >=2 blocks. Rank by Wilson(1.0) lower bound minus .02 per extra condition; shortlist <=3, best per feature-family. Validate >=6 fills and >=30% retention, no precision or strategy-mean deterioration. Choose highest mean(train,valid) Wilson lower bound, tie fewer conditions; otherwise no-filter fallback.',
      'stability':'Three chronological two-month blocks within training are stability checks, not independent heldout CV. D0 predictions are causal OOF; rule thresholds are selected on training.',
      'outcomes':'D2/D3/D4 close gross returns and first D2-D4 sealed close else D4 close. Each horizon checks price continuity. Known D4 cutoff for train/valid is inherited. Test final incomplete row stays unknown.',
      'audit':'Minute clock/full OHLC/internal consistency/positive prices/volume vs daily, historical status N/R. Data conflicts excluded and reported, missing not negative.',
      'rules':rules(),'prior_test_exposure':'Existing historical period already researched; new chronological replay is not fresh external holdout.'}
    spec=json.loads(json.dumps(spec,ensure_ascii=False))
    p=OUT/'specification.json'
    if p.exists():assert json.loads(p.read_text())==spec
    else:dump(p,spec)
    return spec

def prepare():
    specification();p=pd.read_pickle(BASE/'panel.pkl');parts=[]
    # Complete the known D1 endpoint only, from R18A's audited metadata.
    endpoint=pd.read_pickle(OLD/'demand.pkl').set_index(['symbol','d0_date'])
    p=p.set_index(['symbol','d0_date'])
    for k in ['d1_date','d1_preclose','d1_open','d1_close','d1_volume','d1_up','d1_cap','d1_st']:
        missing=p[k].isna();keys=p.index[missing].intersection(endpoint.index)
        p.loc[keys,k]=endpoint.loc[keys,k]
    p=p.reset_index()
    for month in TEST_MONTHS:
        for part,src in [('train','oof'),('valid','valid'),('test','forecast')]:
            f=pd.read_pickle(BASE/month/f'segments_{src}_scores.pkl')
            f=f[f.d0_rank.eq(1)].merge(p,on=['symbol','d0_date'],validate='one_to_one')
            f['model_month']=month;f['part']=part;parts.append(f)
    allrows=pd.concat(parts,ignore_index=True)
    hist=pd.read_csv(BASE/'top1_touch_r18b_20261015/historical_status_all.csv.gz',dtype=str)
    shutil.copy2(BASE/'top1_touch_r18b_20261015/historical_status_all.csv.gz',OUT/'historical_status.csv.gz')
    hist['symbol']=hist.stk_code.str[:6];hg={k:g for k,g in hist.groupby('symbol')}
    def state(sym,day):
        if pd.isna(day):return 'unknown'
        h=hg[sym];a=h[h.begin_date.le(day)&h.end_date.gt(day)]
        assert len(a)==1,(sym,day)
        return a.st_type.iloc[0]
    demands=allrows.drop_duplicates(['symbol','d0_date']).copy()
    for k in (0,1):demands[f'state{k}']=[state(r.symbol,getattr(r,f'd{k}_date')) for r in demands.itertuples()]
    demands['ordinary']=demands.state0.isin(['N','R'])&demands.state1.isin(['N','R'])
    allrows=allrows.merge(demands[['symbol','d0_date','state0','state1','ordinary']],on=['symbol','d0_date'],validate='many_to_one')
    # Daily OHLC is only used for data reconciliation, never to form 09:35 factors.
    months=set(demands.d1_date.dropna().str[:7]);sources=json.loads((BASE/'source_manifest.json').read_text())['raw_sources'];daily=[]
    wanted=set(demands.symbol)
    for item in sources:
        path=Path(item['path'])
        if not any(m in path.name for m in months):continue
        assert sha(path)==item['sha256']
        q=pd.read_csv(path,dtype={'stk_code':str,'trade_date':str});q['symbol']=q.stk_code.str[:6]
        daily.append(q[q.symbol.isin(wanted)])
    ep=Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014/price_months/price_2026-10-14_extension.csv.gz')
    q=pd.read_csv(ep,dtype={'stk_code':str,'trade_date':str});q['symbol']=q.stk_code.str[:6];daily.append(q[q.symbol.isin(wanted)])
    daily=pd.concat(daily).drop_duplicates(['symbol','trade_date'],keep='last')
    q=daily[['symbol','trade_date','high','low']].rename(columns={'trade_date':'d1_date','high':'d1_high','low':'d1_low'})
    demands=demands.merge(q,on=['symbol','d1_date'],how='left',validate='one_to_one')
    daily.to_pickle(OUT/'daily_source_subset.pkl');demands.to_pickle(OUT/'minute_demand.pkl');allrows.to_pickle(OUT/'all_top1.pkl')
    dump(OUT/'sources.json',{'panel':sha(BASE/'panel.pkl'),'daily':sources,'endpoint':sha(ep),'stage1_spec':sha(BASE/'specification.json')})
    print('rows',len(allrows),'unique stock-days',len(demands),'D1 days',demands.d1_date.nunique(),flush=True)

def features(b,pre,cap):
    """The function cannot read past the first five completed bars."""
    v=b.iloc[:5].copy();o=float(v.open.iloc[0]);c=float(v.close.iloc[-1]);h=float(v.high.max());l=float(v.low.min())
    first=float(v.close.iloc[1])-o;last=c-float(v.close.iloc[1])
    shape=('平走' if abs(first)<.005 and abs(last)<.005 else '前弱后强' if first<-.005 and last>.005
      else '前强后弱' if first>.005 and last<-.005 else '两段走强' if first>=-.005 and last>=-.005 else '两段走弱')
    return {'high5':h/pre-1,'low5':l/pre-1,'body5':c/o-1,'pullback5':(h-c)/pre,
      'position5':(c-l)/(h-l) if h-l>.005 else .5,
      'below5':int((v.volume.gt(0)&v.low.lt(cap-.005)).sum()),
      'whole5':int((v.volume.gt(0)&v.high.lt(cap-.005)).sum()),
      'touch5':int((v.volume.gt(0)&v.high.ge(cap-.005)).sum()),
      'shape':shape,'close5':c,'close5ret':c/pre-1,'open_gap':o/pre-1}

def facts(b,r):
    bad=lambda why:{'audit_ok':False,'reason':why}
    if b.empty:return bad('分钟缺失')
    b=b.sort_values('time').copy();clock=pd.to_datetime(b.time).dt.strftime('%H:%M')
    if clock.tolist()!=DAY_CLOCK:return bad('分钟时段不完整')
    v=b[COLS].apply(pd.to_numeric,errors='coerce')
    if not np.isfinite(v.to_numpy()).all() or v[COLS[:4]].le(0).any().any() or v.volume.lt(0).any():return bad('非法量价')
    if v.high.lt(v[['open','low','close']].max(axis=1)-.005).any() or v.low.gt(v[['open','high','close']].min(axis=1)+.005).any():return bad('OHLC冲突')
    for val,ref in [(v.open.iloc[0],r.d1_open),(v.high.max(),r.d1_high),(v.low.min(),r.d1_low),(v.close.iloc[-1],r.d1_close)]:
        if not np.isclose(val,ref,atol=.011,rtol=0):return bad('分钟日线价格冲突')
    if not np.isclose(v.volume.sum(),r.d1_volume,atol=1,rtol=.001):return bad('分钟日线量冲突')
    if v.high.max()>r.d1_cap+.011:return bad('分钟超过涨停价')
    z=features(v,r.d1_preclose,r.d1_cap)
    later=b.iloc[5:10];candidate=later[later.volume.gt(0)&later.open.lt(r.d1_cap-.005)]
    immediate=b.iloc[5]
    z.update({'audit_ok':True,'reason':'通过','fill':len(candidate)>0,'entry':None,'entry_time':None,
      'full_below_after5':int((later.volume.gt(0)&later.high.lt(r.d1_cap-.005)).sum()),
      'immediate_fill':bool(immediate.volume>0 and immediate.open<r.d1_cap-.005),
      'entry_whole_below':False})
    if len(candidate):
        x=candidate.iloc[0];z.update({'entry':float(x.open),'entry_time':str(x.time)[11:16],
           'entry_whole_below':bool(x.high<r.d1_cap-.005)})
    return z

def scan_day(day,demand):
    root=OUT/'minute_days';root.mkdir(exist_ok=True)
    dest=root/f'{day}.json'
    if dest.exists():return json.loads(dest.read_text())
    frames=[];found={}
    def collect(raw,source):
        if raw.empty:return
        raw=raw.copy();raw['symbol']=raw.symbol.astype(str).str[:6];raw['source']=source
        raw=raw[raw.symbol.isin(demand.symbol)];frames.append(raw)
        for sym,g in raw.groupby('symbol'):
            z=facts(g,demand[demand.symbol.eq(sym)].iloc[0]);z.update({'symbol':sym,'d1_date':day,'minute_source':source})
            if z['audit_ok'] or sym not in found:found[sym]=z
    old=OLD/'days'/f'{day}_raw.pkl'
    if old.exists():
        raw=pd.read_pickle(old)
        for source,g in raw.groupby('source'):collect(g,str(source))
    missing=set(demand.symbol)-{s for s,r in found.items() if r['audit_ok']}
    if missing:
        try:
            path=day_file(COMPACT_ROOT_2025 if day.startswith('2025') else COMPACT_ROOT,day)
            collect(filtered_local(path,missing),str(path))
        except FileNotFoundError:pass
    missing=set(demand.symbol)-{s for s,r in found.items() if r['audit_ok']}
    if missing and day>='2026-01-01':
        try:
            with db_connection() as conn:
                marks=','.join(['%s']*len(missing));symbols=sorted(missing)
                raw=query(conn,'SELECT stk_code AS symbol,bar_end_time AS time,open_price AS open,high_price AS high,low_price AS low,latest_price AS close,volume FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s AND kline_type=\'1m\' AND period_minutes=1 AND is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time AND stk_code IN ('+marks+')',(day,*symbols));conn.rollback()
            if len(raw):
                for c in COLS:raw[c]=pd.to_numeric(raw[c],errors='coerce')
                raw['volume']*=100
            collect(raw,'database_finalized')
        except Exception as exc:
            for s in missing:found.setdefault(s,{'symbol':s,'d1_date':day,'audit_ok':False,'reason':'读取失败:'+type(exc).__name__})
    result=[found.get(s,{'symbol':s,'d1_date':day,'audit_ok':False,'reason':'分钟缺失'}) for s in demand.symbol]
    if frames:pd.concat(frames,ignore_index=True).drop_duplicates(['symbol','time','source']).to_pickle(root/f'{day}_raw.pkl')
    dump(dest,clean(result));return result

def minutes():
    d=pd.read_pickle(OUT/'minute_demand.pkl');jobs=list(d.dropna(subset=['d1_date']).groupby('d1_date'));rows=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(scan_day,day,g) for day,g in jobs]
        for i,f in enumerate(as_completed(futures),1):
            rows.extend(f.result())
            if i%20==0 or i==len(jobs):print('minute days',i,'/',len(jobs),flush=True)
    m=pd.DataFrame(rows);m.to_pickle(OUT/'minute_facts.pkl')
    f=pd.read_pickle(OUT/'all_top1.pkl').merge(m,on=['symbol','d1_date'],how='left',validate='many_to_one')
    f['eligible']=f.ordinary&f.audit_ok.eq(True)
    f['trade']=f.eligible&f['fill'].eq(True)
    good=f.d1_close.gt(0)
    for k in (2,3,4):
        good &= f[f'd{k}_close'].gt(0)&f[f'd{k}_preclose'].sub(f[f'd{k-1}_close']).abs().le(.011)
        f[f'ret{k}']=np.where(f.trade&good,f[f'd{k}_close']/f.entry-1,np.nan)
    f['strategy']=np.where(f.d2_up.eq(True),f.ret2,np.where(f.d3_up.eq(True),f.ret3,f.ret4))
    f['complete']=f.trade&f.observed_3&f.ret4.notna()
    f.to_pickle(OUT/'enriched_top1.pkl')
    dump(OUT/'coverage.json',{'unique_demand':len(d),'minute_days':len(jobs),'minute_verified':int(m.audit_ok.eq(True).sum()),
       'failed_reasons':m.loc[~m.audit_ok.eq(True),'reason'].value_counts().to_dict(),
       'ordinary_unique':int(d.ordinary.sum()),'no_model_retraining':True})
    print('enriched',len(f),flush=True)

def mask(f,r):
    z=pd.Series(True,index=f.index)
    for feature,op,value in r['atoms']:
        s=f[feature]
        z &= s.ge(value) if op=='>=' else s.lt(value) if op=='<' else s.eq(value)
    return z

def metrics(f,r):
    signals=f[f.eligible&mask(f,r)];g=signals[signals.trade];known=g[g.observed_3]
    z={'signals':len(signals),'fills':len(g),'known':len(known),'hits':int(known.label_3.sum()),
       'hit':float(known.label_3.mean()) if len(known) else None,
       'no_fill':int((~signals.trade).sum()),'whole_entry':int(g.entry_whole_below.eq(True).sum())}
    for col in ['ret2','ret3','ret4','strategy']:
        v=g[col].dropna();z[col]={'n':len(v),'mean':float(v.mean()) if len(v) else None,
          'win':float(v.gt(0).mean()) if len(v) else None,'bad5':float(v.lt(-.05).mean()) if len(v) else None}
    return z

def wilson(p,n):
    if p is None or n==0:return -1.
    return (p+1/(2*n)-math.sqrt(p*(1-p)/n+1/(4*n*n)))/(1+1/n)

def choose():
    spec=specification();f=pd.read_pickle(OUT/'enriched_top1.pkl');rs=spec['rules'];selections=[]
    for month in TEST_MONTHS:
        root=OUT/month;root.mkdir(exist_ok=True)
        tr=f[f.model_month.eq(month)&f.part.eq('train')&f.complete].copy()
        va=f[f.model_month.eq(month)&f.part.eq('valid')&f.complete].copy()
        base=metrics(tr,rs[0]);vb=metrics(va,rs[0]);w=window(month)
        months=[str(m) for m in pd.period_range(*w['train'],freq='M')]
        chunks=[months[i:i+2] for i in range(0,6,2)];tests=[]
        for rule in rs[1:]:
            mt=metrics(tr,rule);n=mt['known']
            if n<max(15,math.ceil(.4*base['known'])):continue
            if mt['hit']<base['hit']+.03-1e-12 or mt['strategy']['mean']<base['strategy']['mean']-1e-12:continue
            blocks=[]
            for chunk in chunks:
                g=tr[tr.d0_date.str[:7].isin(chunk)];a=metrics(g,rs[0]);b=metrics(g,rule)
                blocks.append({'base':a,'rule':b})
            if min(x['rule']['known'] for x in blocks)<3:continue
            if sum(x['rule']['hit']>=x['base']['hit'] for x in blocks)<2:continue
            score=wilson(mt['hit'],n)-.02*(len(rule['atoms'])-1)
            tests.append({'rule':rule,'train':mt,'blocks':blocks,'train_score':score})
        tests.sort(key=lambda x:(-x['train_score'],len(x['rule']['atoms']),x['rule']['id']))
        shortlist=[];families=set()
        for x in tests:
            family=x['rule']['family'] if x['rule']['family']!='单条件' else x['rule']['atoms'][0][0]
            if family in families:continue
            shortlist.append(x);families.add(family)
            if len(shortlist)==3:break
        accepted=[]
        for x in shortlist:
            mv=metrics(va,x['rule']);x['valid']=mv
            ok=mv['known']>=max(6,math.ceil(.3*vb['known'])) and mv['hit']>=vb['hit']-1e-12 and mv['strategy']['mean']>=vb['strategy']['mean']-1e-12
            x['accepted']=ok
            if ok:
                x['joint_score']=(wilson(x['train']['hit'],x['train']['known'])+wilson(mv['hit'],mv['known']))/2
                accepted.append(x)
        selected=max(accepted,key=lambda x:(x['joint_score'],-len(x['rule']['atoms'])))['rule'] if accepted else rs[0]
        row={'month':month,'window':w,'selected':selected,'train_base':base,'valid_base':vb,
         'train_selected':metrics(tr,selected),'valid_selected':metrics(va,selected),
         'eligible_train_rules':len(tests),'shortlist':shortlist,'spec_sha256':sha(OUT/'specification.json'),
         'selection_keys':{'train':tr[['symbol','d0_date']].to_dict('records'),'valid':va[['symbol','d0_date']].to_dict('records')}}
        dump(root/'rule_frozen.json',row);selections.append(row)
        print('frozen',month,selected['text'],'train',base['known'],'valid',vb['known'],flush=True)
    dump(OUT/'all_rules_frozen.json',selections)

def report():
    # Report test only after every monthly decision was saved.
    frozen=json.loads((OUT/'all_rules_frozen.json').read_text());assert len(frozen)==len(TEST_MONTHS)
    f=pd.read_pickle(OUT/'enriched_top1.pkl');rows=[];details=[]
    for s in frozen:
        month=s['month'];rule=s['selected']
        for part in ['train','valid','test']:
            g=f[f.model_month.eq(month)&f.part.eq(part)].copy()
            if part!='test':g=g[g.complete]
            for name,r in [('全部Top1',{'atoms':[]}),('五分规则',rule)]:
                z=metrics(g,r);row={'月份':month,'集合':part,'方案':name,'规则':rule['text'],**z};rows.append(row)
            if part=='test':
                g['rule_pass']=g.eligible&mask(g,rule);g['selected_trade']=g.rule_pass&g.trade
                g['rule_id']=rule['id'];g['rule_text']=rule['text'];details.append(g)
    test=pd.concat(details).sort_values('d0_date');assert not test.d0_date.duplicated().any()
    test.to_pickle(OUT/'test_top1.pkl');dump(OUT/'monthly_metrics.json',rows)
    base=metrics(test,{'atoms':[]})
    chosen=test.copy();chosen['eligible']=chosen.rule_pass
    selected=metrics(chosen,{'atoms':[]})
    dump(OUT/'result.json',{'test_base':base,'test_selected':selected,
       'rules_used':test[['model_month','rule_id','rule_text']].drop_duplicates().to_dict('records'),
       'test_rows':len(test),'test_ordinary':int(test.ordinary.sum()),'test_audited':int(test.audit_ok.eq(True).sum()),
       'rules_frozen_sha256':sha(OUT/'all_rules_frozen.json'),'spec_sha256':sha(OUT/'specification.json')})
    print(json.dumps({'baseline':base,'selected':selected,'rule_months':sum(s['selected']['id']!='all' for s in frozen)},ensure_ascii=False,indent=2))

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('stage',choices=['prepare','minutes','choose','report']);args=a.parse_args();globals()[args.stage]()

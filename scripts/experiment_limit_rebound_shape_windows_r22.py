"""R22: shorter causal shape windows and frozen-rule out-of-period controls."""
from __future__ import annotations
import argparse,json,math,shutil
from pathlib import Path
import numpy as np,pandas as pd
import scripts.experiment_limit_rebound_shape_r19 as r19
from scripts.experiment_limit_rebound_shape_top3_r20 import OUT as R20,select,stats
from scripts.replay_limit_rebound_body8_r21 import OUT as R21,account,body_keep,EXITS
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha

OUT=Path('outputs/stock_automl/limit_rebound_shape_windows_r22_20261015')
BASE=r19.BASE
MONTHS=[str(x) for x in pd.period_range('2025-03','2026-10',freq='M')]
NAMES={'none':'不筛形态','fixed6':'固定最高6%','fixed6low':'固定6%和负3%',
       'original':'原6训2验','short2':'2训1验','short1':'1训1验','extended':'分段扩展'}
SCOPES={'新增3月':('2025-03-01','2025-03-31',['none','fixed6','fixed6low','short1']),
        '早期4—8月':('2025-04-01','2025-08-31',['none','fixed6','fixed6low','short1','short2']),
        '后段9月起':('2025-09-01','2026-10-31',['none','fixed6','fixed6low','original','short1','short2']),
        '扩展3月起':('2025-03-01','2026-10-31',['none','fixed6','fixed6low','extended'])}


def specification():
    OUT.mkdir(exist_ok=True)
    spec={'name':'R22_shorter_shape_windows_fixed_rule_controls',
      'stage1':'All R18 monthly RF models, rankings and score files unchanged; 6-month RF training/2-month validation. Only shape-selection windows shortened.',
      'data_start':'Complete audited minute archive begins Jan2025; 1train+1valid first test Mar2025; 2train+1valid first test Apr2025. Jan/Feb lack two prior calendar months for this monthly design.',
      'short_windows':{'short2':'2 train +1 validation, main short window','short1':'1 train +1 validation, small-sample sensitivity'},
      'training_scores':'Own historical-month saved RF forecast Top1 scores, genuinely causal OOF; never in-fit scores.',
      'validation_scores':'Target-month saved R18 validation scores, last validation month only. Same score source as R19 validation, shortened to1month.',
      'outcomes_purged':'Train D4 trading date strictly before validation month; validation D4 strictly before forecast month. Only complete valid-price returns used in selection.',
      'candidates':r19.rules(),
      'selection':'Same79 rules and target as R19, Top1 only. Train hit>=base+3pp and strategy mean>=base, >=40% retention, min8fills for2train or6for1train. Two training stability blocks: monthly for2train, chronological halves for1train. Each block>=3fills for2train or2for1train; hit noninferior in both blocks for2train or>=1 for1train. Wilson lower bound(z=1) minus .02 per extra condition; <=3 finalists, distinct families. Validation>=4fills and30% retention, hit and strategy mean noninferior; best mean train/validation Wilson, fewer conditions tie. If none, no filter.',
      'no_window_selection_on_test':'Both short windows fully reported, not monthwise choosing test winner. Extended schedule pre-fixed: Mar short1, Apr-Aug short2, Sep onward original6+2.',
      'controls':{'none':[], 'fixed6':[['high5','>=',.06]],'fixed6low':[['high5','>=',.06],['low5','>=',-.03]]},
      'entry':'Same R20 Top3 chronological first-open proxy09:36..09:40, first five minutes observed. Original shape gate can fall back inside Top3. Common -8% body gate AFTER daily choice: filtered day remains cash, never replaced.',
      'gate_comparison':'Main comparison adds fixed body>-8% to every scheme. Secondary un-gated comparison isolates shape-only contribution. Shape rule selection itself remains pre-body-gate like originalR19.',
      'portfolio':'Reuse audited R21B 25%priorNAV cash-funded ledger, four exits, end positions marked at known close, ex-post price-reference anomaly exclusion with no replacement; gross, no fees. Scope entry dates are D0 within range; follow each chosen trade up to D4 or last known date. Month tables distinguish signal month from holding-calendar account month.',
      'uncertainty':'Later14months: paired monthly account-return differences, 10000 bootstrap resamples of months, seed20261015, percentile95%; partial Oct remains labeled.',
      'historical_exposure':'Chronologically held out from each rule selection, but this historical range and fixed thresholds have already been examined; NOT fresh external blind test.',
      'source_hashes':{'R21_candidates':sha(R21/'all_candidates.pkl'),'R20_enriched':sha(R20/'enriched_top3.pkl'),'R19_rules':sha(r19.OUT/'all_rules_frozen.json')}}
    dest=OUT/'specification.json';spec=json.loads(json.dumps(spec))
    if dest.exists():assert json.loads(dest.read_text())==spec
    else:dump(dest,spec)
    return spec


def prepare():
    specification();f=pd.read_pickle(R21/'all_candidates.pkl');f.to_pickle(OUT/'forecast_top3.pkl')
    meta=pd.read_pickle(R20/'enriched_top3.pkl').drop_duplicates(['symbol','d0_date']).drop(columns=['score','d0_rank','model_month','part','shape_pass'])
    frames=[];sources=[]
    for month in MONTHS:
        path=BASE/month/'segments_valid_scores.pkl';s=pd.read_pickle(path)
        s=s[s.d0_rank.eq(1)&s.d0_date.str.startswith(str(pd.Period(month)-1))].copy();s['target_month']=month;frames.append(s)
        sources.append({'path':str(path),'sha256':sha(path)})
    v=pd.concat(frames,ignore_index=True);existing=set(zip(meta.symbol,meta.d0_date))
    missing=v[['symbol','d0_date']].drop_duplicates();missing=missing[[tuple(x) not in existing for x in missing.values]]
    if len(missing):
        panel=pd.read_pickle(BASE/'panel.pkl');d=missing.merge(panel,on=['symbol','d0_date'],validate='one_to_one')
        hist=pd.read_csv(R20/'historical_status.csv.gz',dtype=str);hist['symbol']=hist.stk_code.str[:6];hgroups=dict(tuple(hist.groupby('symbol')))
        def state(sym,day):
            h=hgroups[sym];a=h[h.begin_date.le(day)&h.end_date.gt(day)];assert len(a)==1;return a.st_type.iloc[0]
        for k in [0,1]:d[f'state{k}']=[state(r.symbol,getattr(r,f'd{k}_date')) for r in d.itertuples()]
        d['ordinary']=d.state0.isin(['N','R'])&d.state1.isin(['N','R'])
        months=set(d.d1_date.str[:7]);daily=[]
        for src in json.loads((BASE/'source_manifest.json').read_text())['raw_sources']:
            path=Path(src['path'])
            if not any(m in path.name for m in months):continue
            assert sha(path)==src['sha256'];a=pd.read_csv(path,dtype={'stk_code':str,'trade_date':str});a['symbol']=a.stk_code.str[:6]
            daily.append(a[a.symbol.isin(d.symbol)])
        q=pd.concat(daily).drop_duplicates(['symbol','trade_date'])
        d=d.merge(q[['symbol','trade_date','high','low']].rename(columns={'trade_date':'d1_date','high':'d1_high','low':'d1_low'}),on=['symbol','d1_date'],validate='one_to_one')
        d.to_pickle(OUT/'extra_minute_demand.pkl');rows=[];prior=r19.OUT;r19.OUT=OUT
        try:
            for day,g in d.groupby('d1_date'):
                rows.extend(r19.scan_day(day,g));print('extra minute',day,len(g),flush=True)
        finally:r19.OUT=prior
        d=d.merge(pd.DataFrame(rows),on=['symbol','d1_date'],validate='one_to_one');d['eligible']=d.ordinary&d.audit_ok.eq(True);d['trade']=d.eligible&d['fill'].eq(True)
        good=d.d1_close.gt(0)
        for k in [2,3,4]:
            good &= d[f'd{k}_close'].gt(0)&d[f'd{k}_preclose'].sub(d[f'd{k-1}_close']).abs().le(.011)
            d[f'ret{k}']=np.where(d.trade&good,d[f'd{k}_close']/d.entry-1,np.nan)
        d['strategy']=np.where(d.d2_up.eq(True),d.ret2,np.where(d.d3_up.eq(True),d.ret3,d.ret4));d['complete']=d.trade&d.observed_3&d.ret4.notna()
        meta=pd.concat([meta,d],ignore_index=True)
    meta.to_pickle(OUT/'metadata.pkl');v=v.merge(meta,on=['symbol','d0_date'],validate='many_to_one');v.to_pickle(OUT/'validation_top1.pkl')
    dump(OUT/'sources.json',{'validation':sources,'models_manifest':str(R21/'models.json'),'models_manifest_sha256':sha(R21/'models.json'),
      'minute_archive_start':'2025-01-02','forecast_days':int(f.d0_date.nunique()),'extra_minute_stock_days':len(missing),'extra_verified':int(meta.tail(len(missing)).audit_ok.eq(True).sum()) if len(missing) else 0})
    print('prepared',len(f),'forecast rows;',len(v),'validation rows; extra',len(missing),flush=True)


def window_data(f,v,month,ntrain,calendar):
    m=pd.Period(month);valid=str(m-1);first=str(m-ntrain-1)
    tr=f[f.d0_rank.eq(1)&f.d0_date.ge(first+'-01')&f.d0_date.lt(valid+'-01')].copy()
    va=v[v.target_month.eq(month)].copy()
    for data,boundary in [(tr,valid+'-01'),(va,month+'-01')]:
        data['mature']=[int(i)+4<len(calendar) and calendar[int(i)+4]<boundary for i in data.date_i]
    tr=tr[tr.complete&tr.mature].copy();va=va[va.complete&va.mature].copy()
    return tr,va,{'train':[first,str(m-2)],'valid':[valid,valid],'test':month}


def choose_rule(tr,va,ntrain,rs):
    base=r19.metrics(tr,rs[0]);vb=r19.metrics(va,rs[0]);candidates=[]
    if ntrain==2:blocks=[g for _,g in tr.groupby(tr.d0_date.str[:7])]
    else:
        dates=sorted(tr.d0_date.unique());cut=len(dates)//2
        blocks=[tr[tr.d0_date.isin(dates[:cut])],tr[tr.d0_date.isin(dates[cut:])]]
    for rule in rs[1:]:
        mt=r19.metrics(tr,rule);n=mt['known']
        if n<max(8 if ntrain==2 else 6,math.ceil(.4*base['known'])):continue
        if mt['hit']<base['hit']+.03-1e-12 or mt['strategy']['mean']<base['strategy']['mean']-1e-12:continue
        bm=[{'base':r19.metrics(b,rs[0]),'rule':r19.metrics(b,rule)} for b in blocks]
        if len(bm)!=2 or min(x['rule']['known'] for x in bm)<(3 if ntrain==2 else 2):continue
        if sum(x['rule']['hit']>=x['base']['hit'] for x in bm)<(2 if ntrain==2 else 1):continue
        candidates.append({'rule':rule,'train':mt,'blocks':bm,'score':r19.wilson(mt['hit'],n)-.02*(len(rule['atoms'])-1)})
    candidates.sort(key=lambda x:(-x['score'],len(x['rule']['atoms']),x['rule']['id']));short=[];families=set()
    for x in candidates:
        fam=x['rule']['family'] if x['rule']['family']!='单条件' else x['rule']['atoms'][0][0]
        if fam in families:continue
        short.append(x);families.add(fam)
        if len(short)==3:break
    accepted=[]
    for x in short:
        z=r19.metrics(va,x['rule']);x['valid']=z
        ok=z['known']>=max(4,math.ceil(.3*vb['known'])) and z['hit']>=vb['hit']-1e-12 and z['strategy']['mean']>=vb['strategy']['mean']-1e-12
        x['accepted']=ok
        if ok:
            x['joint']=(r19.wilson(x['train']['hit'],x['train']['known'])+r19.wilson(z['hit'],z['known']))/2;accepted.append(x)
    rule=max(accepted,key=lambda x:(x['joint'],-len(x['rule']['atoms'])))['rule'] if accepted else rs[0]
    return {'selected':rule,'train_base':base,'valid_base':vb,'train_selected':r19.metrics(tr,rule),'valid_selected':r19.metrics(va,rule),
      'eligible_rules':len(candidates),'shortlist':short,'keys':{'train':tr[['symbol','d0_date']].to_dict('records'),'valid':va[['symbol','d0_date']].to_dict('records')}}


def choose():
    spec=specification();f=pd.read_pickle(OUT/'forecast_top3.pkl');v=pd.read_pickle(OUT/'validation_top1.pkl');calendar=json.loads((BASE/'calendar.json').read_text());frozen=[]
    for ntrain in [2,1]:
        for month in MONTHS:
            if ntrain==2 and month<'2025-04':continue
            tr,va,w=window_data(f,v,month,ntrain,calendar);choice=choose_rule(tr,va,ntrain,spec['candidates'])
            row={'scheme':f'short{ntrain}','month':month,'window':w,**choice};frozen.append(row)
            print('frozen',ntrain,month,choice['selected']['text'],'n',choice['train_base']['known'],choice['valid_base']['known'],flush=True)
    dump(OUT/'short_rules_frozen.json',frozen)
    shutil.copy2(r19.OUT/'all_rules_frozen.json',OUT/'original_rules_frozen.json')
    dump(OUT/'selection_receipt.json',{'spec_sha256':sha(OUT/'specification.json'),'short_rules_sha256':sha(OUT/'short_rules_frozen.json'),
      'original_rules_sha256':sha(OUT/'original_rules_frozen.json'),'status':'all39short_rules_saved_before_test_evaluation'})


def policy_picks(f,rule,gate):
    q=f.copy();q['shape_pass']=q.eligible&r19.mask(q,rule);a=select(q,3,True)
    return a[body_keep(a.body5)].copy() if gate else a.copy()


def evaluate():
    spec=specification();f=pd.read_pickle(OUT/'forecast_top3.pkl');calendar=json.loads((BASE/'calendar.json').read_text())
    short=json.loads((OUT/'short_rules_frozen.json').read_text());old=json.loads((OUT/'original_rules_frozen.json').read_text())
    dynamic={(s['scheme'],s['month']):s['selected'] for s in short};dynamic.update({('original',s['month']):s['selected'] for s in old})
    rs={r['id']:r for r in spec['candidates']};fixed={'none':rs['all'],'fixed6':rs['r005'],'fixed6low':rs['r051']}
    chosen=[];month_metrics=[];assignments=[]
    for month in MONTHS:
        g=f[f.model_month.eq(month)]
        for scheme in NAMES:
            if scheme=='extended':rule=dynamic[('short1' if month=='2025-03' else 'short2' if month<'2025-09' else 'original',month)]
            elif scheme in fixed:rule=fixed[scheme]
            elif (scheme,month) in dynamic:rule=dynamic[(scheme,month)]
            else:continue
            assignments.append({'scheme':scheme,'month':month,'rule':rule})
            for gate in [False,True]:
                a=policy_picks(g,rule,gate);a['scheme']=scheme;a['body_gate']=gate;chosen.append(a)
                month_metrics.append({'scheme':scheme,'month':month,'body_gate':gate,**stats(a)})
    p=pd.concat(chosen,ignore_index=True);p.to_pickle(OUT/'policy_picks.pkl');dump(OUT/'rule_assignments.json',assignments);dump(OUT/'signal_month_metrics.json',month_metrics)
    overall=[];accounts=[];navs=[];ledger=[];exclusions=[];month_account=[]
    for scope,(start,end,schemes) in SCOPES.items():
        # Include each scope's selected trades to their known D4 endpoint, without new later signals.
        ds=f[f.d0_date.ge(start)&f.d0_date.le(end)].date_i
        closing=calendar[min(int(ds.max())+4,len(calendar)-1)]
        for scheme in schemes:
            for gate in [False,True]:
                a=p[p.scheme.eq(scheme)&p.body_gate.eq(gate)&p.d0_date.ge(start)&p.d0_date.le(end)]
                ctx={'scope':scope,'scheme':scheme,'body_gate':gate}
                overall.append({**ctx,**stats(a)})
                for ex in EXITS:
                    result,m,n,l,e=account(a,ex,calendar,start,closing);c={**ctx,'exit':ex}
                    accounts.append({**c,**result});navs.extend([{**c,**x} for x in n]);ledger.extend([{**c,**x} for x in l]);exclusions.extend([{**c,**x} for x in e]);month_account.extend([{**c,**x} for x in m])
    dump(OUT/'overall.json',overall);dump(OUT/'accounts.json',accounts);dump(OUT/'account_months.json',month_account)
    pd.DataFrame(navs).to_pickle(OUT/'daily_nav.pkl');pd.DataFrame(ledger).to_pickle(OUT/'cash_ledger.pkl');dump(OUT/'account_exclusions.json',exclusions)
    rng=np.random.default_rng(20261015);comparisons=[]
    later=[x for x in month_account if x['scope']=='后段9月起' and x['body_gate']]
    for scheme in ['original','short1','short2']:
        for base in ['none','fixed6','fixed6low']:
            for ex in EXITS:
                a={x['月份']:x['账户月收益'] for x in later if x['scheme']==scheme and x['exit']==ex}
                b={x['月份']:x['账户月收益'] for x in later if x['scheme']==base and x['exit']==ex}
                assert set(a)==set(b);diff=np.array([a[m]-b[m] for m in sorted(a)])
                boot=rng.choice(diff,size=(10000,len(diff)),replace=True).mean(axis=1)
                comparisons.append({'scheme':scheme,'baseline':base,'exit':ex,'months':len(diff),'win_months':int((diff>1e-12).sum()),
                  'tie_months':int((np.abs(diff)<=1e-12).sum()),'mean_month_difference':float(diff.mean()),'ci_low':float(np.quantile(boot,.025)),'ci_high':float(np.quantile(boot,.975))})
    dump(OUT/'paired_month_comparison.json',comparisons)
    print('evaluated',len(overall),'scope/policy/gate rows',len(accounts),'accounts',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','choose','evaluate']);a=p.parse_args();globals()[a.stage]()

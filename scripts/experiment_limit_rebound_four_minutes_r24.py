"""R24: four completed bars vs five, saved D0 models, causal Top3 execution."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import scripts.experiment_limit_rebound_shape_r19 as shape
from scripts.experiment_limit_rebound_shape_top3_r20 import OUT as R20, select, stats
from scripts.replay_limit_rebound_cash_r23 import replay, dump, sha, BASE

OUT = Path('outputs/stock_automl/limit_rebound_four_minutes_r24_20261015')
DOC = Path('docs/stock_automl_runs/20261015_limit_rebound_four_minutes_r24')
FEATURES = list(shape.FEATURE_NAMES) + ['close5', 'close5ret', 'open_gap']
NAMES = {'five': '五分原方案', 'four_old': '四分沿用门槛', 'four_fit': '四分重选规则'}


def first_features(b, pre, cap, n):
    # Retain old internal column names for the frozen rule evaluator. n is saved explicitly.
    assert n in (4, 5)
    result=shape.features(b.iloc[:n], pre, cap)
    # Normalize arithmetic noise at exact thresholds, matching R20's 15-place cache.
    return {k:round(v,15) if isinstance(v,float) else v for k,v in result.items()}


def entry_facts(b, cap, n):
    later = b.iloc[n:10]
    a = later[later.volume.gt(0) & later.open.lt(cap-.005)]
    result = dict(fill=len(a)>0, entry=np.nan, entry_time=None, entry_whole_below=False)
    if len(a):
        r = a.iloc[0]
        result.update(entry=float(r.open), entry_time=pd.Timestamp(r.time).strftime('%H:%M'),
                      entry_whole_below=bool(r.high<cap-.005))
    return result


def reprice(f):
    f = f.copy()
    f['trade'] = f.eligible & f['fill'].eq(True)
    good = f.d1_close.gt(0)
    for k in (2,3,4):
        good &= f[f'd{k}_close'].gt(0) & f[f'd{k}_preclose'].sub(f[f'd{k-1}_close']).abs().le(.011)
        f[f'ret{k}'] = np.where(f.trade & good, f[f'd{k}_close']/f.entry-1, np.nan)
    f['strategy'] = np.where(f.d2_up.eq(True), f.ret2, np.where(f.d3_up.eq(True), f.ret3, f.ret4))
    f['complete'] = f.trade & f.observed_3 & f.ret4.notna()
    return f


def choose_rule(tr, va, month, rs):
    """Exact R19 six-train/two-validation selection, independent of test frame."""
    base=shape.metrics(tr,rs[0]); vb=shape.metrics(va,rs[0]); window=shape.window(month)
    months=[str(m) for m in pd.period_range(*window['train'],freq='M')]
    chunks=[months[i:i+2] for i in range(0,6,2)]; tests=[]
    for rule in rs[1:]:
        mt=shape.metrics(tr,rule); n=mt['known']
        if n<max(15,math.ceil(.4*base['known'])): continue
        if mt['hit']<base['hit']+.03-1e-12 or mt['strategy']['mean']<base['strategy']['mean']-1e-12: continue
        blocks=[]
        for chunk in chunks:
            g=tr[tr.d0_date.str[:7].isin(chunk)]
            blocks.append({'base':shape.metrics(g,rs[0]),'rule':shape.metrics(g,rule)})
        if min(x['rule']['known'] for x in blocks)<3: continue
        if sum(x['rule']['hit']>=x['base']['hit'] for x in blocks)<2: continue
        tests.append({'rule':rule,'train':mt,'blocks':blocks,
                      'train_score':shape.wilson(mt['hit'],n)-.02*(len(rule['atoms'])-1)})
    tests.sort(key=lambda x:(-x['train_score'],len(x['rule']['atoms']),x['rule']['id']))
    shortlist=[]; families=set()
    for x in tests:
        fam=x['rule']['family'] if x['rule']['family']!='单条件' else x['rule']['atoms'][0][0]
        if fam in families: continue
        shortlist.append(x); families.add(fam)
        if len(shortlist)==3: break
    accepted=[]
    for x in shortlist:
        mv=shape.metrics(va,x['rule']); x['valid']=mv
        ok=(mv['known']>=max(6,math.ceil(.3*vb['known'])) and mv['hit']>=vb['hit']-1e-12
            and mv['strategy']['mean']>=vb['strategy']['mean']-1e-12)
        x['accepted']=ok
        if ok:
            x['joint_score']=(shape.wilson(x['train']['hit'],x['train']['known'])+shape.wilson(mv['hit'],mv['known']))/2
            accepted.append(x)
    rule=max(accepted,key=lambda x:(x['joint_score'],-len(x['rule']['atoms'])))['rule'] if accepted else rs[0]
    return {'month':month,'window':window,'selected':rule,'train_base':base,'valid_base':vb,
            'train_selected':shape.metrics(tr,rule),'valid_selected':shape.metrics(va,rule),
            'shortlist':shortlist,'eligible_train_rules':len(tests),
            'selection_keys':{p:q[['symbol','d0_date']].to_dict('records') for p,q in [('train',tr),('valid',va)]}}


def choose_all(f, old, calendar):
    results=[]
    for s in old:
        month=s['month']; parts=[]
        for part in ('train','valid'):
            a=f[f.model_month.eq(month)&f.part.eq(part)&f.d0_rank.eq(1)].copy()
            cutoff=str(pd.Period(s['window'][part][1])+1)+'-01'
            assert all(calendar[int(i)+4]<cutoff for i in a.date_i)
            parts.append(a[a.complete].copy())
        results.append(choose_rule(*parts,month,shape.rules()))
    return results


def chosen(f, rule):
    q=f.copy(); q['shape_pass']=q.eligible & shape.mask(q,rule)
    a=select(q,3,True)
    # User's extra body gate rejects the entire selected day, no replacement.
    return a[a.body5.gt(-.08+1e-12)].copy()


def delay_same_stock(p, bars):
    rows=[]
    for r in p.to_dict('records'):
        b=bars[(r['symbol'],r['d0_date'])]
        clock=pd.to_datetime(b.time).dt.strftime('%H:%M')
        a=b[clock.gt(r['entry_time'])&clock.le('09:40')&b.volume.gt(0)&b.open.lt(r['d1_cap']-.005)]
        if len(a):
            x=a.iloc[0]; r.update(entry=float(x.open),entry_time=pd.Timestamp(x.time).strftime('%H:%M'),
                                 entry_whole_below=bool(x.high<r['d1_cap']-.005))
            rows.append(r)
    return reprice(pd.DataFrame(rows,columns=p.columns))


def main():
    OUT.mkdir(exist_ok=True); DOC.mkdir(exist_ok=True)
    source=R20/'enriched_top3.pkl'; old=json.loads((shape.OUT/'all_rules_frozen.json').read_text())
    calendar=json.loads((BASE/'calendar.json').read_text())
    specification=dict(name='R24_four_completed_minutes_vs_five', source_sha256=sha(source),
        old_rules_sha256=sha(shape.OUT/'all_rules_frozen.json'),
        scope='Saved R18 Top3; Jan-Oct14 2026 accounts. Four-minute rule train/validation/test use original fourteen rolling windows. No daily model retrain.',
        observation='Four bars end09:31..09:34; five bars end09:31..09:35. high/low relative D1preclose, body relative D1open; path split at09:32 in both.',
        internal_columns='Four-minute frame reuses high5/low5/body5 etc as evaluator aliases, observation_bars=4. These contain only first FOUR bars. Fractional features rounded to15 decimals, matching R20 cache precision and normalizing exact-threshold floating error. Original five-minute selections/prices remain unchanged.',
        selection='Exact original79 candidates, Top1 six train months/two validation, purged D4, original shortlist/stability/retention/precision/return guards. Save all rules before evaluating tests. Transfer to Top3.',
        execution='Monitor shape-passing originalTop3 chronologically; first below-cap positive-volume minute OPEN wins; tie D0rank. Four starts end-label09:35, five09:36; cutoff09:40. Apply body>-8% after choosing; failed day not replaced.',
        delayed='Same selected stock, at least one bar after its activation; first qualifying open by09:40. No substitution. This differs from imposing a fixed one-minute delay only after feature completion.',
        controls='Existing five baseline and its known delayed result must reproduce. Four old thresholds and four reselected rules both reported. Shape-only discrimination uses common original-five entry prices/universe.',
        account='100w; flat half available cash, carrying one lot all cash; D2 close; buy/sell each0.1%; fractional shares; end pending marked; no fill/capacity guarantee.',
        exposure='Previously examined historical period. Monthly test not used for each rule selection, but this comparison is not a new blind test.')
    dump(OUT/'specification.json',specification)
    f5=pd.read_pickle(source); f5['observation_bars']=5
    unique=f5.drop_duplicates(['symbol','d0_date']); records=[]; bars={}; raw_sources=[]
    for day,g in unique[unique.audit_ok.eq(True)].groupby('d1_date'):
        path=R20/'minute_days'/f'{day}_raw.pkl'; raw=pd.read_pickle(path)
        raw_sources.append({'path':str(path),'sha256':sha(path)})
        for r in g.itertuples():
            b=raw[raw.symbol.eq(r.symbol)&raw.source.eq(r.minute_source)].sort_values('time').reset_index(drop=True)
            assert len(b)==240 and b.time.is_unique
            assert pd.to_datetime(b.time.iloc[:10]).dt.strftime('%H:%M').tolist()==[f'09:{i:02}' for i in range(31,41)]
            assert np.isclose(b.volume.sum(),r.d1_volume,rtol=.001,atol=1)
            for k,v in first_features(b,r.d1_preclose,r.d1_cap,5).items():
                z=getattr(r,k)
                assert v==z if isinstance(v,str) else np.isclose(v,z,rtol=0,atol=1e-12),(day,r.symbol,k)
            z=first_features(b,r.d1_preclose,r.d1_cap,4)
            # Changing bar5 onward cannot change any four-minute feature.
            altered=b.copy(); altered.loc[4:,['open','high','low','close']]=9999.
            assert z==first_features(altered,r.d1_preclose,r.d1_cap,4)
            records.append({'symbol':r.symbol,'d0_date':r.d0_date,**z,**entry_facts(b,r.d1_cap,4)})
            bars[(r.symbol,r.d0_date)]=b.iloc[:10].copy()
    four=pd.DataFrame(records); cols=[c for c in four if c not in ('symbol','d0_date')]
    f4=f5.drop(columns=cols).merge(four,on=['symbol','d0_date'],how='left',validate='many_to_one')
    f4['observation_bars']=4; f4=reprice(f4); f4.to_pickle(OUT/'four_all_top3.pkl')
    pd.concat([b.assign(d0_date=k[1]) for k,b in bars.items()]).to_pickle(OUT/'first10_evidence.pkl')
    dump(OUT/'raw_sources.json',raw_sources)
    # Reproduction of the original selector is required before selecting the new rules.
    # R20 serialized minute facts to 15 decimal places. Reproduce rule fitting
    # from its exact R19 training table, not a later rounded transfer table.
    reproduced=choose_all(pd.read_pickle(shape.OUT/'enriched_top1.pkl'),old,calendar)
    assert json.loads(json.dumps(reproduced))==[{k:v for k,v in s.items() if k!='spec_sha256'} for s in old]
    new=choose_all(f4,old,calendar); dump(OUT/'four_rules_frozen.json',new)
    dump(OUT/'selection_receipt.json',{'rules_sha256':sha(OUT/'four_rules_frozen.json'),'all_saved_before_tests':True})
    print('Frozen four-minute rules',[(s['month'],s['selected']['text'].replace('五分','四分')) for s in new],flush=True)
    # Mutate test inputs/outcomes; no effect on rule selection or train/valid metrics.
    mutated=f4.copy(); te=mutated.part.eq('test')
    mutated.loc[te,['high5','low5','body5','ret2','ret3','ret4','strategy']]=999.
    mutated.loc[te,'label_3']=1-mutated.loc[te,'label_3']
    assert choose_all(mutated,old,calendar)==new
    rulemap={s['month']:s['selected'] for s in old}; newmap={s['month']:s['selected'] for s in new}
    allp=[]; selection_metrics=[]; discrimination=[]; monitor=[]
    for code,frame,rules in [('five',f5,rulemap),('four_old',f4,rulemap),('four_fit',f4,newmap)]:
        for (month,part),g in frame.groupby(['model_month','part']):
            rule=rules[month]; a=chosen(g,rule); a['policy']=code
            selection_metrics.append({'policy':code,'month':month,'part':part,**stats(a)})
            if part=='test':
                allp.append(a)
                for day,h in g.groupby('d1_date'):
                    monitor.append({'policy':code,'date':day,'ranked':len(h),
                        'shape_pass':int((h.eligible&shape.mask(h,rule)).sum())})
        # Isolate shape at a common fifth-minute entry window/price, not early buying.
        common=f5[f5.part.eq('test')&f5.d1_date.ge('2026-01-01')].copy()
        if code!='five':
            keys=f4[f4.part.eq('test')].set_index(['symbol','d0_date'])
            for c in FEATURES: common[c]=[keys.loc[(r.symbol,r.d0_date),c] for r in common.itertuples()]
        common['pass_shape']=False
        for month,g in common.groupby('model_month'):
            common.loc[g.index,'pass_shape']=shape.mask(g,rules[month])
        for group in ['all','pass','reject']:
            q=common if group=='all' else common[common.pass_shape.eq(group=='pass')]
            discrimination.append({'policy':code,'group':group,**stats(q)})
    picks=pd.concat(allp,ignore_index=True); accounts=[]; monthly=[]; navs=[]; ledgers=[]; exclusions=[]; decisions=[]
    for code in NAMES:
        a=picks[picks.policy.eq(code)].copy()
        for delayed,q in [(False,a),(True,delay_same_stock(a,bars))]:
            p=code+('_delay' if delayed else '')
            s,m,n,l,e=replay(q,calendar,fee_rate=.001)
            accounts.append({'policy':p,**s, 'trade_stats':stats(q[q.d1_date.ge('2026-01-01')])})
            monthly.extend([{'policy':p,**x} for x in m]); navs.append(n.assign(policy=p));ledgers.append(l.assign(policy=p))
            exclusions.extend([{'policy':p,**x} for x in e]); decisions.append(q.assign(policy=p))
    p=pd.concat(decisions,ignore_index=True); p.to_pickle(OUT/'policy_picks.pkl')
    nav=pd.concat(navs,ignore_index=True); ledger=pd.concat(ledgers,ignore_index=True)
    nav.to_pickle(OUT/'daily_nav.pkl'); ledger.to_pickle(OUT/'ledger.pkl')
    dump(OUT/'accounts.json',accounts); dump(OUT/'monthly.json',monthly); dump(OUT/'exclusions.json',exclusions)
    dump(OUT/'selection_metrics.json',selection_metrics); dump(OUT/'shape_discrimination.json',discrimination)
    dump(OUT/'monitor_counts.json',monitor)
    original=BASE.parent/'limit_rebound_fee001_r23c_20261015'; delayed=BASE.parent/'limit_rebound_windows_r23d_20261015'
    for policy,root,filename in [('five',original,'ledger.pkl'),('five_delay',delayed,'delay_ledger.pkl')]:
        a=ledger[ledger.policy.eq(policy)].reset_index(drop=True); b=pd.read_pickle(root/filename).reset_index(drop=True)
        assert a[['symbol','d0_date','entry_time']].equals(b[['symbol','d0_date','entry_time']])
        for c in ['buy','cost','shares','proceeds','return_']:
            assert np.allclose(a[c].astype(float),b[c].astype(float),rtol=0,atol=1e-7,equal_nan=True)
    # Independent cash/mark reconciliation for every scenario/day.
    for policy,g in nav.groupby('policy'):
        l=ledger[ledger.policy.eq(policy)]
        for r in g.itertuples():
            bought=l[l.entry_date.le(r.date)]; sold=l[l.exit_date.fillna('9999').le(r.date)]
            held=bought[bought.exit_date.fillna('9999').gt(r.date)]
            cash=1e6-bought.cost.sum()+sold.proceeds.sum(); mv=(held.shares*held.d1_close).sum()
            assert np.isclose(r.nav,cash+mv,rtol=0,atol=1e-7)
    baseline=ledger[ledger.policy.eq('five')]; late=ledger[ledger.policy.eq('five_delay')]
    missed=baseline[~baseline.entry_date.isin(late.entry_date)]
    recovery=[]
    for code in ['four_old','four_old_delay','four_fit','four_fit_delay']:
        q=ledger[ledger.policy.eq(code)]; keys=set(zip(q.symbol,q.entry_date))
        same=missed[[ (r.symbol,r.entry_date) in keys for r in missed.itertuples() ]]
        recovery.append({'policy':code,'missed8_recovered_same_stock':len(same),'profitable_recovered':int(same.return_.gt(0).sum()),
                         'dates':same.entry_date.tolist()})
    dump(OUT/'recovery.json',recovery)
    dump(OUT/'verification.json',{'status':'PASS','raw_unique_stock_days':len(records),
        'original14_rule_selections_exact':True,'original106_trades_and_delayed98_exact':True,
        'four_features_immune_to_bar5_and_later':True,'test_mutation_does_not_change_selection':True,
        'purged_train_valid_D4':True,'independent_daily_cash_checks':len(nav)})
    print(pd.DataFrame(accounts)[['policy','entries','total_return','max_drawdown','sharpe']].to_string(index=False),flush=True)
    print(json.dumps(recovery,ensure_ascii=False),flush=True)


if __name__=='__main__': main()

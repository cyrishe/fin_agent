"""R32 full-history B2 six-train/one-valid versus frozen six/two.
Reuses R31 fitted models byte-for-byte; independent output, no production writes.
"""
from __future__ import annotations
import argparse
import json
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
from scripts import experiment_b2_validation_r31 as r
from scripts.mk_minute_archive import COMPACT_ROOT_2024, day_file
from scripts.export_limit_rebound_short_mixed_r12 import filtered_local

OUT=Path('outputs/stock_automl/b2_validation_full_r32_20261019')
OLD31=r.OUT
EARLY=Path('outputs/stock_automl/b2_fourk_2024_extension_20261018')
CONT=Path('outputs/stock_automl/b2_continuous_20261018')
MONTHS=[str(m) for m in pd.period_range('2025-03','2026-10',freq='M')]
HISTORY=[str(m) for m in pd.period_range('2024-08','2026-10',freq='M')]
COMMON=MONTHS[2:]
EP=Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014/price_months/price_2026-10-14_extension.csv.gz')
BASE=r.BASE
KEY=r.KEY
r.OUT=OUT; r.MONTHS=MONTHS; r.HISTORY=HISTORY; r.VARIANTS={'D1S1':(1,1)}
_original_assemble=r.assemble
_original_enrich=r.enrich_day
_original_hilo=r.extension.daily_high_low

def spec():
    OUT.mkdir(parents=True,exist_ok=True)
    value=dict(experiment='R32 full-history two-stage6+1 vs frozen6+2',
        new_months=MONTHS,common_months=COMMON,history_months=HISTORY,
        train_months=6,validation_months=1,reference_validation_months=2,
        end='2026-10-14',start_new='2025-03-01',start_common='2025-05-01',
        parent_spec_sha256=r.sha(OLD31/'specification.json'),
        panel_sha256=r.sha(BASE/'panel.pkl'),calendar_sha256=r.sha(BASE/'calendar.json'),
        endpoint_sha256=r.sha(EP),
        change='Only extend R31 D1S1 over all available months. Same RF51 factors/seed, mature label and board-event purge,79fourK rules,minimum6validation,Top3 selection,postlock body>-8% no replacement,next qualifying bar open,D2close exit,0.1% each side.',
        account='1000000 initial,flat half cash,holding one lot all remaining cash; common and new-full each reset at stated start; partial Oct14 marked pending.',
        controls='R31 Apr-Sep2026 exact scores/rules/picks and reset-account; frozen baseline full account exact.',
        early='Mar-Apr2025 only new scheme; no fabricated baseline. First daily model Aug2024, first shape Mar2025.',
        endpoint='Patch only known D1 Oct14 metadata from audited endpoint; features and labels unchanged. Missing labels remain unknown.',
        exposure='Historical chronological holdouts previously researched, not a fresh external blind test; no test-winner switching.',
        production_changed=False)
    p=OUT/'specification.json'
    if p.exists():assert json.loads(p.read_text())==json.loads(json.dumps(value))
    else:r.dump(p,value)
    return value
r.spec=spec

def train():
    spec()
    parent=json.loads((OLD31/'specification.json').read_text())
    assert parent['panel_sha256']==r.sha(BASE/'panel.pkl')
    assert parent['calendar_sha256']==r.sha(BASE/'calendar.json')
    reuse=[]
    for month in HISTORY:
        src=OLD31/'daily1'/month
        if not (src/'metrics.json').exists():continue
        dest=OUT/'daily1'/month
        dest.mkdir(parents=True,exist_ok=True)
        for name in ['segments.joblib','training_samples.pkl','metrics.json',*['segments_'+p+'_scores.pkl' for p in ['train','valid','forecast']]]:
            if not (dest/name).exists():shutil.copy2(src/name,dest/name)
            assert r.sha(src/name)==r.sha(dest/name)
        reuse.append(dict(month=month,source=str(src),model_sha256=r.sha(src/'segments.joblib')))
    r.dump(OUT/'reused_models.json',reuse)
    r.train()

def assemble():
    f=_original_assemble()
    endpoint=pd.read_pickle(r.shape.OLD/'demand.pkl').set_index(KEY)
    f=f.set_index(KEY)
    for k in ['d1_date','d1_preclose','d1_open','d1_close','d1_volume','d1_up','d1_cap','d1_st']:
        fill=f.index.isin(endpoint.index)&f[k].isna()&(f.index.get_level_values('d0_date')=='2026-10-13')
        f.loc[fill,k]=endpoint[k].reindex(f.index[fill]).to_numpy()
    f=f.reset_index()
    # An unseen final-day candidate requires explicit data handling, not silence.
    last=f[f.d0_date.eq('2026-10-13')]
    assert last.d1_date.notna().all(),last[KEY].to_dict('records')
    f.to_pickle(OUT/'stage_samples.pkl')
    return f
r.assemble=assemble

def hilo(d):
    a=_original_hilo(d)
    ep=pd.read_csv(EP,dtype={'stk_code':str,'trade_date':str})
    ep['symbol']=ep.stk_code.str[:6]
    b=ep[['symbol','trade_date','high','low']].rename(columns={'trade_date':'d1_date','high':'d1_high','low':'d1_low'})
    return pd.concat([a,b]).drop_duplicates(['symbol','d1_date'],keep='last')
r.extension.daily_high_low=hilo

def enrich_day(day,demand):
    if not day.startswith('2024'):return _original_enrich(day,demand)
    try:
        path=day_file(COMPACT_ROOT_2024,day)
        raw=filtered_local(path,set(demand.symbol))
    except FileNotFoundError:
        path=None;raw=pd.DataFrame()
    groups={s[:6]:g.assign(symbol=s[:6]).sort_values('time').reset_index(drop=True) for s,g in raw.groupby('symbol')} if len(raw) else {}
    records=[];extracts=[]
    for row in demand.itertuples():
        b=groups.get(row.symbol,pd.DataFrame())
        z=dict(symbol=row.symbol,d1_date=day,minute_source=str(path),**r.shape.facts(b,row))
        if z['audit_ok']:
            z.update(r.four.first_features(b,row.d1_preclose,row.d1_cap,4))
            z.update(r.four.entry_facts(b,row.d1_cap,4))
            extracts.append(b.iloc[:10].assign(d0_date=row.d0_date))
        records.append(z)
    return records,extracts
r.enrich_day=enrich_day

def select():
    r.select_rules()
    old={x['month']:x for x in json.loads((OLD31/'rules_frozen.json').read_text()) if x['variant']=='D1S1'}
    selected=json.loads((OUT/'rules_frozen.json').read_text())
    for a in selected:
        if a['month'] in old:
            for k in ['selected','train_base','valid_base','train_selected','valid_selected']:
                assert a[k]==old[a['month']][k],(a['month'],k)
    receipt=json.loads((OUT/'selection_receipt.json').read_text())
    receipt.pop('baseline_six_rules_exact',None)
    receipt['r31_six_rules_exact']=True
    r.dump(OUT/'selection_receipt.json',receipt)

def baseline():
    old=pd.read_pickle(r.four.OUT/'policy_picks.pkl')
    old=old[old.policy.eq('four_fit_delay')]
    old=pd.concat([pd.read_pickle(EARLY/'signals.pkl'),old],ignore_index=True)
    rules=json.loads((EARLY/'four_rules_frozen.json').read_text())
    rules=[x for x in rules if x['month']<'2025-09']+json.loads((r.four.OUT/'four_rules_frozen.json').read_text())
    mapping={x['month']:x['selected'] for x in rules}
    old['variant']='D2S2'
    old['rule_id']=old.d0_date.str[:7].map(lambda m:mapping[m]['id'])
    old['rule_text']=old.d0_date.str[:7].map(lambda m:mapping[m]['text'].replace('五分','四分'))
    for rule in rules:rule['variant']='D2S2'
    return old,rules

def evaluate():
    rules=json.loads((OUT/'rules_frozen.json').read_text())
    f=pd.read_pickle(OUT/'enriched.pkl')
    first=pd.read_pickle(OUT/'first10.pkl')
    bars={(s,d):g.sort_values('time') for (s,d),g in first.groupby(KEY)}
    cal=json.loads((BASE/'calendar.json').read_text())
    picks=[];classes=[]
    for rule in rules:
        q=f[f.model_month.eq(rule['month'])&f.part.eq('test')]
        a=r.four.delay_same_stock(r.four.chosen(q,rule['selected']),bars)
        a['rule_id']=rule['selected']['id'];a['rule_text']=rule['selected']['text'].replace('五分','四分')
        picks.append(a)
        for group,z in [('形态前Top3',q[q.eligible]),('形态通过Top3',q[q.eligible&r.shape.mask(q,rule['selected'])]),('最终交易',a)]:
            known=z[z.observed_3]
            classes.append(dict(variant='D1S1',month=rule['month'],group=group,n=len(known),hits=int(known.label_3.sum()),hit=float(known.label_3.mean()) if len(known) else None))
    new=pd.concat(picks,ignore_index=True)
    ref=pd.read_pickle(OLD31/'signals.pkl')
    ref=ref[ref.variant.eq('D1S1')]
    cols=KEY+['d1_date','entry_time','entry']
    pd.testing.assert_frame_equal(new[new.d0_date.str[:7].between('2026-04','2026-09')][cols].sort_values('d0_date').reset_index(drop=True),ref[cols].sort_values('d0_date').reset_index(drop=True),check_dtype=False)
    old,oldrules=baseline()
    signals=pd.concat([old,new],ignore_index=True);signals.to_pickle(OUT/'signals.pkl')
    r.dump(OUT/'all_rules.json',oldrules+rules)
    r.dump(OUT/'shape_test_metrics.json',classes)
    accounts=[];months=[];navs=[];ledgers=[];excludes=[]
    for scope,name,start,end in [('共同区间','D2S2','2025-05-01',cal[-1]),('共同区间','D1S1','2025-05-01',cal[-1]),('一月全期','D1S1','2025-03-01',cal[-1]),('新增早期','D1S1','2025-03-01','2025-04-30'),('六个月复核','D1S1','2026-04-01','2026-09-30')]:
        # No previous-month picks may enter a reset-start account, matching R31/reference.
        p=signals[signals.variant.eq(name)&signals.d0_date.ge(start)&signals.d0_date.le(end)]
        s,m,n,l,e=r.replay(p,cal,start=start,end=end,fee_rate=.001)
        tag=dict(scope=scope,variant=name)
        accounts.append(dict(**tag,**s));months.extend([dict(**tag,**x) for x in m])
        navs.append(n.assign(**tag));ledgers.append(l.assign(**tag));excludes.extend([dict(**tag,**x) for x in e])
    reference=json.loads((CONT/'receipt.json').read_text())['summary']
    for k in ['entries','closed','pending','final_nav','total_return','max_drawdown','sharpe']:
        if k in reference:assert np.isclose(accounts[0][k],reference[k],atol=1e-8,rtol=0),(k,accounts[0][k],reference[k])
    r31a=[x for x in json.loads((OLD31/'accounts.json').read_text()) if x['variant']=='D1S1'][0]
    for k in ['entries','closed','total_return','max_drawdown','sharpe']:
        assert np.isclose(accounts[-1][k],r31a[k],atol=1e-10,rtol=0),(k,accounts[-1][k],r31a[k])
    r.dump(OUT/'accounts.json',accounts);r.dump(OUT/'monthly.json',months);r.dump(OUT/'exclusions.json',excludes)
    pd.concat(navs).to_pickle(OUT/'nav.pkl');pd.concat(ledgers).to_pickle(OUT/'ledger.pkl')
    daily=[];panel=pd.read_pickle(BASE/'panel.pkl')
    for dv,ms in [(1,MONTHS),(2,COMMON)]:
        for month in ms:
            for part in ['train','valid','forecast']:
                scored=pd.read_pickle(r.source(dv,month,part)).merge(panel,on=KEY,validate='one_to_one')
                daily.append(dict(daily_validation=dv,month=month,part=part,**r.classify(scored)))
    r.dump(OUT/'daily_metrics.json',daily)
    checks=0
    for n,l in zip(navs,ledgers):
        for row in n.itertuples():
            bought=l[l.entry_date.le(row.date)];sold=l[l.exit_date.fillna('9999').le(row.date)];held=bought[bought.exit_date.fillna('9999').gt(row.date)]
            cash=1e6-bought.cost.sum()+sold.proceeds.sum()
            assert np.isclose(row.cash,cash,atol=1e-7,rtol=0)
            assert np.isclose(row.nav,cash+(held.shares*held.d1_close).sum(),atol=1e-7,rtol=0)
            checks+=1
        closed=l[l.exit_date.notna()]
        assert closed.exit_date.eq(closed.entry_date.map(lambda d:cal[cal.index(d)+1])).all()
        assert closed.sell.eq(closed.d2_close).all()
        assert np.allclose(closed.return_,closed.sell/closed.buy*.999/1.001-1,atol=1e-12,rtol=0)
    r.dump(OUT/'verification.json',dict(baseline_full_account_exact=True,r31_six_rules_exact=True,r31_six_picks_exact=True,r31_six_reset_account_exact=True,independent_cash_rows=checks,d2_price_and_fees_exact=True))
    print(pd.DataFrame(accounts)[['scope','variant','entries','closed','total_return','max_drawdown','sharpe']].to_string(index=False),flush=True)

def audit():
    # R31 auditor expects only new variants; isolate input without editing saved files.
    from datetime import datetime
    from scripts.automl_b2 import evaluate_morning
    panel=pd.read_pickle(BASE/'panel.pkl');reloads=[]
    for dv,months in [(1,HISTORY),(2,COMMON)]:
        for month in months:
            root=OUT/'daily1' if dv==1 else BASE
            bundle=r.joblib.load(root/month/'segments.joblib');q=panel[panel.d0_date.str.startswith(month)]
            scored=r.rank(q,bundle['model'].predict_proba(r.xmatrix(q,r.FEATURES['segments']))[:,1])
            expected=pd.read_pickle(r.source(dv,month,'forecast'))
            joined=scored[KEY+['score','d0_rank']].merge(expected,on=KEY,validate='one_to_one',suffixes=('_new','_old'))
            diff=float((joined.score_new-joined.score_old).abs().max())
            assert diff<1e-12 and joined.d0_rank_new.eq(joined.d0_rank_old).all()
            if dv==1:
                fit=pd.read_pickle(root/month/'training_samples.pkl')
                assert fit[KEY+['first_limit_date']].to_dict('records')==bundle['training_keys']
                assert fit.activated.all() and fit.observed_3.all()
                expected_fit=r.block(panel,json.loads((BASE/'calendar.json').read_text()),*r.window(month,1)['train'])
                expected_fit=expected_fit[expected_fit.activated&expected_fit.observed_3]
                pd.testing.assert_frame_equal(fit[KEY].reset_index(drop=True),expected_fit[KEY].reset_index(drop=True))
            reloads.append(dict(validation=dv,month=month,rows=len(joined),max_diff=diff))
    r.dump(OUT/'model_reload_verification.json',reloads)
    f=pd.read_pickle(OUT/'enriched.pkl');first=pd.read_pickle(OUT/'first10.pkl')
    # Compact 2024 symbols include exchange suffixes; canonical panel keys do not.
    if first.symbol.str.len().gt(6).any():
        first['symbol']=first.symbol.str[:6]
        assert not first.duplicated(KEY+['time']).any()
        first.to_pickle(OUT/'first10.pkl')
    bars={(s,d):g.sort_values('time') for (s,d),g in first.groupby(KEY)}
    demand=pd.read_pickle(OUT/'minute_demand.pkl').set_index(KEY)
    for key,b in bars.items():
        row=demand.loc[key]
        changed=b.copy();changed.loc[changed.index[4:],['open','high','low','close']]=9999.
        assert r.four.first_features(b,row.d1_preclose,row.d1_cap,4)==r.four.first_features(changed,row.d1_preclose,row.d1_cap,4)
    rules={x['month']:x['selected'] for x in json.loads((OUT/'rules_frozen.json').read_text())}
    decisions=[];selected=[]
    for day,g in f[f.part.eq('test')].groupby('d1_date'):
        d0=g.d0_date.iloc[0]
        picks=[dict(symbol6=z.symbol,selection_rank=int(z.d0_rank),preclose=float(z.d1_preclose),cap=float(z.d1_cap),ordinary=bool(z.eligible)) for z in g.itertuples()]
        sources={z.symbol:bars.get((z.symbol,d0),pd.DataFrame()) for z in g.itertuples()}
        states=evaluate_morning(picks,sources,day,datetime.fromisoformat(day+' 09:40:00'),rules[d0[:7]])
        for s in states:
            decisions.append(dict(d0_date=d0,d1_date=day,**s))
            if s['actionable']:selected.append((d0,s['symbol6'],s['entry_bar_end'][11:16],float(s['entry_price'])))
    p=pd.read_pickle(OUT/'signals.pkl');p=p[p.variant.eq('D1S1')]
    assert sorted(selected)==sorted((z.d0_date,z.symbol,z.entry_time,float(z.entry)) for z in p.itertuples())
    pd.DataFrame(decisions).to_pickle(OUT/'execution_decisions.pkl')
    checks=json.loads((OUT/'verification.json').read_text());checks.update(model_reload_count=len(reloads),model_score_max_diff=max(x['max_diff'] for x in reloads),production_evaluator_exact=len(selected),candidate_decisions=len(decisions),training_sample_windows_exact=True,four_bar_no_future_feature_checks=len(bars))
    r.dump(OUT/'verification.json',checks);print(json.dumps(checks,indent=2),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['train','prepare','select','evaluate','audit'])
    arg=parser.parse_args();{'train':train,'prepare':r.prepare,'select':select,'evaluate':evaluate,'audit':audit}[arg.stage]()

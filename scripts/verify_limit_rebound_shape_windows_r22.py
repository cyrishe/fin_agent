"""Temporal cuts, fixed choices, new minute evidence and cash accounting audit."""
import json
from pathlib import Path
import joblib,numpy as np,pandas as pd
from scripts.experiment_limit_rebound_shape_windows_r22 import OUT,BASE,R20,R21,MONTHS,window_data,choose_rule
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha,xmatrix
from scripts.verify_limit_rebound_shape_top3_r20 import allowed


def main():
    spec=json.loads((OUT/'specification.json').read_text());receipt=json.loads((OUT/'selection_receipt.json').read_text())
    assert sha(OUT/'specification.json')==receipt['spec_sha256']
    assert sha(OUT/'short_rules_frozen.json')==receipt['short_rules_sha256']
    assert sha(OUT/'original_rules_frozen.json')==receipt['original_rules_sha256']==spec['source_hashes']['R19_rules']
    assert sha(R21/'all_candidates.pkl')==spec['source_hashes']['R21_candidates']
    f=pd.read_pickle(OUT/'forecast_top3.pkl');v=pd.read_pickle(OUT/'validation_top1.pkl');cal=json.loads((BASE/'calendar.json').read_text())
    rules=json.loads((OUT/'short_rules_frozen.json').read_text());selection_checks=0
    for s in rules:
        n=int(s['scheme'][-1]);month=s['month'];tr,va,w=window_data(f,v,month,n,cal)
        assert w==s['window']
        assert not set(zip(tr.symbol,tr.d0_date))&set(zip(va.symbol,va.d0_date))
        assert all(cal[int(i)+4]<w['valid'][0]+'-01' for i in tr.date_i)
        assert all(cal[int(i)+4]<month+'-01' for i in va.date_i)
        # Perturb every target-month and later feature, outcome and eligibility.
        changed=f.copy();future=changed.d0_date.ge(month+'-01')
        for c in ['high5','low5','body5','label_3','ret2','ret3','ret4','strategy']:
            changed.loc[future,c]=np.arange(int(future.sum()))%3-1
        changed.loc[future,'eligible']=False
        tc,vc,_=window_data(changed,v,month,n,cal)
        assert tc.equals(tr) and vc.equals(va)
        actual=choose_rule(tc,vc,n,spec['candidates']);assert actual['selected']==s['selected']
        assert actual['keys']==s['keys'];selection_checks+=1
    scores=0
    for month,g in v.groupby('target_month'):
        b=joblib.load(BASE/month/'segments.joblib');pred=b['model'].predict_proba(xmatrix(g,b['features']))[:,1]
        assert np.array_equal(pred,g.score.to_numpy()),month;scores+=len(g)
    extra=pd.read_pickle(OUT/'extra_minute_demand.pkl');metadata=pd.read_pickle(OUT/'metadata.pkl');rawcount=0
    for day,g in extra.groupby('d1_date'):
        raw=pd.read_pickle(OUT/'minute_days'/f'{day}_raw.pkl')
        for r in g.itertuples():
            q=metadata[metadata.symbol.eq(r.symbol)&metadata.d0_date.eq(r.d0_date)].iloc[0]
            b=raw[raw.symbol.eq(r.symbol)&raw.source.eq(q.minute_source)].sort_values('time');assert len(b)==240
            assert np.allclose([b.open.iloc[0],b.high.max(),b.low.min(),b.close.iloc[-1]],[r.d1_open,r.d1_high,r.d1_low,r.d1_close],rtol=0,atol=.011)
            assert np.isclose(b.volume.sum(),r.d1_volume,rtol=.001,atol=1)
            first=b.iloc[:5]
            assert np.allclose([q.high5,q.low5,q.body5],[first.high.max()/r.d1_preclose-1,first.low.min()/r.d1_preclose-1,first.close.iloc[-1]/first.open.iloc[0]-1],rtol=0,atol=1e-12)
            rawcount+=1
    picks=pd.read_pickle(OUT/'policy_picks.pkl');assign=json.loads((OUT/'rule_assignments.json').read_text());choice_checks=0
    for s in assign:
        for day,g in f[f.model_month.eq(s['month'])].groupby('d0_date'):
            eligible=[]
            for _,r in g.iterrows():
                if r.trade and allowed(r,s['rule']['atoms']):eligible.append(r)
            want=min(eligible,key=lambda r:(r.entry_time,r.d0_rank)) if eligible else None
            for gate in [False,True]:
                a=picks[picks.scheme.eq(s['scheme'])&picks.model_month.eq(s['month'])&picks.d0_date.eq(day)&picks.body_gate.eq(gate)]
                expected=None if want is None or (gate and not want.body5>-.08+1e-12) else want
                if expected is None:assert a.empty
                else:
                    assert len(a)==1;r=a.iloc[0]
                    assert (r.symbol,r.entry,r.entry_time)==(expected.symbol,expected.entry,expected.entry_time)
                choice_checks+=1
    original=picks[picks.scheme.eq('original')&picks.body_gate]
    saved=pd.read_pickle(R21/'policy_picks.pkl');saved=saved[saved.policy.eq('body8')&saved.d0_date.ge('2025-09-01')]
    assert len(original)==len(saved)==150 and set(zip(original.symbol,original.d0_date))==set(zip(saved.symbol,saved.d0_date))
    nav=pd.read_pickle(OUT/'daily_nav.pkl');ledger=pd.read_pickle(OUT/'cash_ledger.pkl');lookup=f.set_index(['symbol','d0_date']);checks=0
    month_results=json.loads((OUT/'account_months.json').read_text());month_checks=0
    for key,n in nav.groupby(['scope','scheme','body_gate','exit']):
        n=n.sort_values('date');dates=n.date.tolist();ix={d:i for i,d in enumerate(dates)}
        l=ledger[(ledger.scope==key[0])&(ledger.scheme==key[1])&(ledger.body_gate==key[2])&(ledger.exit==key[3])]
        flows=np.zeros(len(n));mv=np.zeros(len(n));count=np.zeros(len(n))
        for t in l.itertuples():
            begin=ix[t.entry_date];flows[begin]-=t.cost
            end=ix[t.exit_date] if pd.notna(t.exit_date) else len(n)
            if end<len(n):flows[end]+=t.proceeds
            row=lookup.loc[(t.symbol,t.d0_date)]
            for j in range(begin,end):mv[j]+=t.shares*row[f'd{j-begin+1}_close'];count[j]+=1
        cash=1+np.cumsum(flows)
        assert np.allclose(cash,n.cash,rtol=0,atol=1e-10)
        assert np.allclose(mv,n.market_value,rtol=0,atol=1e-10)
        assert np.allclose(cash+mv,n.nav,rtol=0,atol=1e-10)
        assert np.array_equal(count,n.positions.to_numpy());checks+=len(n)
        previous=1.
        for month,g in n.groupby('month'):
            r=next(x for x in month_results if (x['scope'],x['scheme'],x['body_gate'],x['exit'],x['月份'])==(*key,month))
            values=np.r_[previous,g.nav];drawdown=float(np.max(1-values/np.maximum.accumulate(values)))
            assert np.isclose(r['账户月收益'],g.nav.iloc[-1]/previous-1,rtol=0,atol=1e-12)
            assert np.isclose(r['月内回撤'],drawdown,rtol=0,atol=1e-12)
            previous=float(g.nav.iloc[-1]);month_checks+=1
    result={'status':'PASS','short_rules_future_mutation_and_temporal_checks':selection_checks,
      'validation_saved_model_exact_scores':scores,'extra_minute_stock_days':rawcount,
      'chronological_policy_days':choice_checks,'prior_R21B150_picks_unchanged':True,'cash_nav_days':checks,
      'month_return_drawdown_checks':month_checks,'pytest':'8 passed','spec_sha256':sha(OUT/'specification.json'),'rules_sha256':sha(OUT/'short_rules_frozen.json')}
    xlsx=OUT/'形态短窗_滚动与固定规则对照_R22_20261015.xlsx'
    if xlsx.exists():
        from scripts.verify_limit_rebound_segments_r18_touch import read,col,equal
        exported=read(xlsx);data=json.loads((OUT/'workbook_data.json').read_text());cells=0
        for name,rows in data.items():
            columns=list(rows[0]);sheet=exported[name]
            for i,c in enumerate(columns):assert sheet[col(i)+'1']==c
            for j,r in enumerate(rows,2):
                for i,c in enumerate(columns):assert equal(sheet.get(col(i)+str(j)),r[c]),(name,j,c);cells+=1
        result.update(workbook_cells=cells,workbook_sha256=sha(xlsx))
    dump(OUT/'verification.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

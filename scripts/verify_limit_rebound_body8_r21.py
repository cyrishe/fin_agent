"""Independent raw-minute, chronological-choice and cash-ledger audit for R21."""
import json
from datetime import datetime
from pathlib import Path
import numpy as np,pandas as pd
from scripts.replay_limit_rebound_body8_r21 import OUT,SOURCE,BASE
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha
from scripts.verify_limit_rebound_shape_top3_r20 import allowed


def main():
    f=pd.read_pickle(OUT/'all_candidates.pkl');p=pd.read_pickle(OUT/'policy_picks.pkl')
    rules={r['month']:r for r in json.loads((OUT/'rules.json').read_text())}
    original={r['month']:r['selected'] for r in json.loads((SOURCE/'rules_frozen.json').read_text())}
    assert f.d0_date.nunique()==428 and len(f)==1284
    models=json.loads((OUT/'models.json').read_text());assert sum(m['reproduced_top3_scores'] for m in models)==len(f)
    for m in models:assert sha(Path(m['model_path']))==m['model_sha256'] and sha(Path(m['scores_path']))==m['scores_sha256']
    for month,r in rules.items():assert r['rule']==original.get(month,original['2025-09'])
    old=pd.read_pickle(SOURCE/'test_top3.pkl');old=old[old.top3_shape]
    new=p[p.policy.eq('old')&p.d0_date.ge('2025-09-01')]
    assert set(zip(old.symbol,old.d0_date))==set(zip(new.symbol,new.d0_date)) and len(new)==156
    rawcount=0;lookup={}
    for day,g in f[f.audit_ok.eq(True)].groupby('d1_date'):
        raw=pd.read_pickle(SOURCE/'minute_days'/f'{day}_raw.pkl')
        for r in g.itertuples():
            b=raw[raw.symbol.eq(r.symbol)&raw.source.eq(r.minute_source)].sort_values('time')
            assert len(b)==240 and b.time.nunique()==240
            first=b.iloc[:5];bar=b.iloc[5];lookup[(r.symbol,r.d0_date)]=b.iloc[5:10]
            assert pd.Timestamp(bar.time).strftime('%H:%M')=='09:36'
            for actual,want in [(r.high5,first.high.max()/r.d1_preclose-1),(r.low5,first.low.min()/r.d1_preclose-1),
                                (r.body5,first.close.iloc[-1]/first.open.iloc[0]-1),
                                (r.m36_high,bar.high),(r.m36_low,bar.low),(r.m36_open,bar.open),
                                (r.m36_high_ret,bar.high/r.d1_preclose-1),(r.m36_low_ret,bar.low/r.d1_preclose-1)]:
                assert np.isclose(actual,want,atol=1e-12,rtol=0)
            if r.trade:assert np.isclose(r.entry_ret,r.entry/r.d1_preclose-1,atol=1e-12,rtol=0)
            rawcount+=1
    choices=0
    for day,g in f.groupby('d0_date'):
        atoms=rules[day[:7]]['rule']['atoms']
        for _,r in g.iterrows():
            assert r.shape_pass==bool(r.eligible and allowed(r,atoms))
            assert r.new_pass==bool(r.shape_pass and r.body5>-.08+1e-12)
        for policy in ['old','body8']:
            expected=None
            for j in range(5):
                for _,r in g[g.eligible].sort_values('d0_rank').iterrows():
                    if not allowed(r,atoms):continue
                    bar=lookup[(r.symbol,r.d0_date)].iloc[j]
                    if bar.volume>0 and bar.open<r.d1_cap-.005:
                        expected=(r.symbol,pd.Timestamp(bar.time).strftime('%H:%M'),float(bar.open));picked_body=r.body5;break
                if expected is not None:break
            if expected is not None and policy=='body8' and not picked_body>-.08+1e-12:expected=None
            a=p[p.policy.eq(policy)&p.d0_date.eq(day)]
            if expected is None:assert a.empty
            else:
                assert len(a)==1
                r=a.iloc[0];assert (r.symbol,r.entry_time,float(r.entry))==expected
            choices+=1
    nav=pd.read_pickle(OUT/'daily_nav.pkl');ledger=pd.read_pickle(OUT/'cash_ledger.pkl')
    cal=json.loads((BASE/'calendar.json').read_text());idx={d:i for i,d in enumerate(cal)}
    rows={(r.symbol,r.d0_date):r for r in f.itertuples()};checks=0
    results=json.loads((OUT/'account_results.json').read_text())
    for (policy,scope,exit_name),g in nav.groupby(['policy','scope','exit']):
        l=ledger[ledger.policy.eq(policy)&ledger.scope.eq(scope)&ledger.exit.eq(exit_name)]
        peak=1.;worst=0.
        for n in g.itertuples():
            bought=l[l.entry_date.le(n.date)]
            sold=bought[bought.exit_date.notna()&bought.exit_date.le(n.date)]
            live=bought[bought.exit_date.isna()|bought.exit_date.gt(n.date)]
            cash=1-bought.cost.sum()+sold.proceeds.sum();value=0.
            for t in live.itertuples():
                r=rows[(t.symbol,t.d0_date)];k=idx[n.date]-idx[t.entry_date]+1
                value+=t.shares*getattr(r,f'd{k}_close')
            assert np.allclose([n.cash,n.market_value,n.nav],[cash,value,cash+value],rtol=0,atol=1e-10)
            assert n.positions==len(live)
            peak=max(peak,n.nav);worst=max(worst,1-n.nav/peak);checks+=1
        result=next(x for x in results if (x['policy'],x['scope'],x['exit'])==(policy,scope,exit_name))
        assert np.isclose(result['total_return'],g.nav.iloc[-1]-1,atol=1e-12,rtol=0)
        assert np.isclose(result['max_drawdown'],worst,atol=1e-12,rtol=0)
    # Each monthly NAV result must compound exactly back to full-period account result.
    monthly=json.loads((OUT/'monthly_results.json').read_text())
    for r in results:
        if r['scope']!='全期含早期回看':continue
        name='原形态' if r['policy']=='old' else '原形态＋实体>-8%'
        m=[x for x in monthly if x['方案']==name and x['退出']==r['exit']]
        assert len(m)==22 and np.isclose(np.prod([1+x['账户月收益'] for x in m])-1,r['total_return'],atol=1e-12)
    verification={'status':'PASS','saved_model_score_matches':len(f),'models_hash_checked':len(models),
      'raw_minute_stock_days':rawcount,'chronological_policy_days':choices,'daily_cash_nav_checks':checks,
      'R20_original_test_picks_unchanged':len(new),'strict_body_boundary_test':'see pytest log',
      'monthly_compounding_accounts':8}
    assert set(zip(p[p.policy.eq('body8')].symbol,p[p.policy.eq('body8')].d0_date)).issubset(set(zip(p[p.policy.eq('old')].symbol,p[p.policy.eq('old')].d0_date)))
    verification['new_gate_never_replaces_original_choice']=True
    xlsx=OUT/'前三原选_实体过滤不顺延_买价与月度回撤_R21B_20261015.xlsx'
    if xlsx.exists():
        from scripts.verify_limit_rebound_segments_r18_touch import read,col,equal
        exported=read(xlsx);data=json.loads((OUT/'workbook_data.json').read_text());cells=0
        for name,items in data.items():
            columns=list(items[0]);sheet=exported[name]
            for i,c in enumerate(columns):assert sheet[col(i)+'1']==c
            for j,r in enumerate(items,2):
                for i,c in enumerate(columns):
                    val=r[c]
                    if c in ['日期','D1日期'] and val:val=(datetime.fromisoformat(val)-datetime(1899,12,30)).days
                    assert equal(sheet.get(col(i)+str(j)),val),(name,j,c)
                    cells+=1
        verification.update(workbook_cells=cells,workbook_sha256=sha(xlsx))
    dump(OUT/'verification.json',verification);print(json.dumps(verification,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

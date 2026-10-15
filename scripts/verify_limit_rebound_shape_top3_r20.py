"""Raw minute and causal daily selection replay for R20."""
import json
from pathlib import Path
from datetime import datetime
import numpy as np,pandas as pd
from scripts.experiment_limit_rebound_shape_top3_r20 import OUT,PREV,BASE,POLICIES,dump,sha


def allowed(row,atoms):
    for col,op,value in atoms:
        x=row[col]
        if not (x>=value if op=='>=' else x<value if op=='<' else x==value):return False
    return True


def main():
    f=pd.read_pickle(OUT/'enriched_top3.pkl');m=pd.read_pickle(OUT/'minute_facts.pkl')
    picks=pd.read_pickle(OUT/'daily_policy_picks.pkl')
    ss=json.loads((OUT/'rules_frozen.json').read_text());rules={s['month']:s['selected']['atoms'] for s in ss}
    frozen=sha(PREV/'all_rules_frozen.json');assert sha(OUT/'rules_frozen.json')==frozen
    old=pd.read_pickle(PREV/'enriched_top1.pkl');a=f[f.d0_rank.eq(1)]
    key=['model_month','part','symbol','d0_date'];assert set(map(tuple,a[key].values))==set(map(tuple,old[key].values))
    cols=['score','high5','low5','body5','pullback5','position5','below5','whole5','touch5','entry','ret2','ret3','ret4','strategy']
    both=a.merge(old,on=key,suffixes=('_new','_old'),validate='one_to_one')
    for c in cols:assert np.allclose(both[c+'_new'].astype(float),both[c+'_old'].astype(float),rtol=0,atol=1e-12,equal_nan=True),c
    lookup={};rawcount=0
    for day,zs in m[m.audit_ok.eq(True)].groupby('d1_date'):
        raw=pd.read_pickle(OUT/'minute_days'/f'{day}_raw.pkl')
        for z in zs.itertuples():
            b=raw[raw.symbol.eq(z.symbol)&raw.source.eq(z.minute_source)].sort_values('time')
            assert len(b)==240 and b.time.nunique()==240
            lookup[(z.symbol,day)]=b.iloc[5:10]
            row=f[f.symbol.eq(z.symbol)&f.d1_date.eq(day)].iloc[0];first=b.iloc[:5]
            assert np.isclose(first.high.max()/row.d1_preclose-1,row.high5,atol=1e-12,rtol=0)
            assert np.isclose(first.low.min()/row.d1_preclose-1,row.low5,atol=1e-12,rtol=0)
            rawcount+=1
    cut=json.loads(Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014/calendar_through_20261014.json').read_text())
    for s in ss:
        for part in ['train','valid']:
            g=f[f.model_month.eq(s['month'])&f.part.eq(part)]
            end=str(pd.Period(s['window'][part][1],'M')+1)+'-01'
            assert all(cut[int(i)+4]<end for i in g.date_i)
    valid_count=0
    for (month,part,day),g in f.groupby(['model_month','part','d0_date']):
        for _,r in g.iterrows():assert r.shape_pass==(r.eligible and allowed(r,rules[month]))
        for policy in POLICIES:
            expected=None;limit=1 if policy.startswith('top1') else 3;shape=policy.endswith('shape')
            candidates=g[g.d0_rank.le(limit)&g.eligible].sort_values('d0_rank')
            for j in range(5):
                for _,r in candidates.iterrows():
                    if shape and not allowed(r,rules[month]):continue
                    bar=lookup[(r.symbol,r.d1_date)].iloc[j]
                    if bar.volume>0 and bar.open<r.d1_cap-.005:
                        expected=(r.symbol,pd.Timestamp(bar.time).strftime('%H:%M'),float(bar.open));break
                if expected is not None:break
            actual=picks[picks.model_month.eq(month)&picks.part.eq(part)&picks.d0_date.eq(day)&picks.policy.eq(policy)]
            if expected is None:assert actual.empty
            else:
                assert len(actual)==1
                r=actual.iloc[0];assert (r.symbol,r.entry_time,float(r.entry))==expected
            valid_count+=1
    nreturns=0
    for r in f.itertuples():
        good=r.d1_close>0;vals={}
        for k in [2,3,4]:
            good=good and getattr(r,f'd{k}_close')>0 and abs(getattr(r,f'd{k}_preclose')-getattr(r,f'd{k-1}_close'))<=.011
            v=getattr(r,f'd{k}_close')/r.entry-1 if r.trade and good else np.nan
            actual=getattr(r,f'ret{k}');assert (pd.isna(v) and pd.isna(actual)) or np.isclose(v,actual,atol=1e-12,rtol=0)
            vals[k]=v;nreturns+=1
        strat=vals[2] if r.d2_up==True else vals[3] if r.d3_up==True else vals[4]
        assert (pd.isna(strat) and pd.isna(r.strategy)) or np.isclose(strat,r.strategy,atol=1e-12,rtol=0)
    result={'status':'PASS','rules_unchanged':frozen,'R19_Top1_rows_unchanged':len(both),
      'raw_minute_stock_days':rawcount,'chronological_policy_day_checks':valid_count,
      'independent_return_checks':nreturns,'train_valid_D4_cutoffs':True}
    xlsx=OUT/'前五分形态_涨跌区分与前三顺延_R20_20261015.xlsx'
    if xlsx.exists():
        from scripts.verify_limit_rebound_segments_r18_touch import read,col,equal
        exported=read(xlsx);data=json.loads((OUT/'workbook_data.json').read_text());cells=0
        for name,rows in data.items():
            columns=list(rows[0]);sheet=exported[name]
            for i,c in enumerate(columns):assert sheet[col(i)+'1']==c
            for j,r in enumerate(rows,2):
                for i,c in enumerate(columns):
                    val=r[c]
                    if c in ['日期','D1日期'] and val:val=(datetime.fromisoformat(val)-datetime(1899,12,30)).days
                    assert equal(sheet.get(col(i)+str(j)),val),(name,j,c)
                    cells+=1
        result.update({'workbook_cells':cells,'workbook_sha256':sha(xlsx)})
    dump(OUT/'verification.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

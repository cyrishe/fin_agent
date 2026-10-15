"""Independent R19 raw-price replay and frozen-selection isolation checks."""
from __future__ import annotations
import contextlib,io,json,tempfile,shutil
from datetime import datetime
from pathlib import Path
import numpy as np,pandas as pd
import scripts.experiment_limit_rebound_shape_r19 as exp


def main():
    out=exp.OUT; f=pd.read_pickle(out/'enriched_top1.pkl')
    d=pd.read_pickle(out/'minute_demand.pkl');m=pd.read_pickle(out/'minute_facts.pkl')
    frozen=json.loads((out/'all_rules_frozen.json').read_text())
    checked=0;maxdiff=0.
    for day,dem in d.groupby('d1_date'):
        p=out/'minute_days'/f'{day}_raw.pkl'
        raw=pd.read_pickle(p) if p.exists() else pd.DataFrame()
        for r in dem.itertuples():
            z=m[m.symbol.eq(r.symbol)&m.d1_date.eq(day)].iloc[0]
            if z.audit_ok!=True:
                assert r.d1_volume==0,(r.symbol,day,'unexplained missing data')
                continue
            b=raw[raw.symbol.eq(r.symbol)&raw.source.eq(z.minute_source)].sort_values('time')
            assert len(b)==240 and b.time.nunique()==240
            a=b.iloc[:5];o=a.open.iloc[0];c=a.close.iloc[-1];h=a.high.max();l=a.low.min()
            manual={'high5':h/r.d1_preclose-1,'low5':l/r.d1_preclose-1,
                'body5':c/o-1,'pullback5':(h-c)/r.d1_preclose,
                'position5':(c-l)/(h-l) if h-l>.005 else .5,
                'below5':sum((a.volume>0)&(a.low<r.d1_cap-.005)),
                'whole5':sum((a.volume>0)&(a.high<r.d1_cap-.005)),
                'touch5':sum((a.volume>0)&(a.high>=r.d1_cap-.005))}
            for k,v in manual.items():
                diff=abs(v-z[k]);assert diff<1e-12,(r.symbol,day,k);maxdiff=max(maxdiff,diff)
            eligible=[j for j in range(5,10) if b.iloc[j].volume>0 and b.iloc[j].open<r.d1_cap-.005]
            assert bool(eligible)==bool(z['fill'])
            if eligible:
                j=eligible[0];assert b.iloc[j].open==z.entry
                assert pd.Timestamp(b.iloc[j].time).strftime('%H:%M')==z.entry_time
                assert bool(b.iloc[j].high<r.d1_cap-.005)==bool(z.entry_whole_below)
            else:assert pd.isna(z.entry)
            checked+=1
    cal=json.loads(Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014/calendar_through_20261014.json').read_text())
    scorechecks=0
    for s in frozen:
        month=s['month']
        for part,src in [('train','oof'),('valid','valid'),('test','forecast')]:
            a=f[f.model_month.eq(month)&f.part.eq(part)].sort_values(['d0_date','symbol'])
            b=pd.read_pickle(exp.BASE/month/f'segments_{src}_scores.pkl')
            b=b[b.d0_rank.eq(1)].sort_values(['d0_date','symbol'])
            assert a[['symbol','d0_date']].values.tolist()==b[['symbol','d0_date']].values.tolist()
            assert np.array_equal(a.score.to_numpy(),b.score.to_numpy());scorechecks+=len(a)
            if part!='test':
                start,end=s['window'][part];cutoff=str(pd.Period(end,'M')+1)+'-01'
                assert a.d0_date.ge(start+'-01').all()
                assert all(cal[int(i)+4]<cutoff for i in a.date_i)
                keys=a[a.complete][['symbol','d0_date']].to_dict('records')
                assert keys==s['selection_keys'][part]
    # Recompute all per-horizon returns without using the experiment's return code.
    returns=0
    for r in f.itertuples():
        assert r.eligible==(r.ordinary and r.audit_ok==True)
        assert r.trade==(r.eligible and r.fill==True)
        assert r.ordinary==(r.state0 in ('N','R') and r.state1 in ('N','R'))
        continuity=True
        expected={}
        for k in (2,3,4):
            close=getattr(r,f'd{k}_close');previous=getattr(r,f'd{k-1}_close');pre=getattr(r,f'd{k}_preclose')
            continuity &= close>0 and previous>0 and abs(pre-previous)<=.011
            value=close/r.entry-1 if r.trade and continuity else np.nan
            expected[k]=value;actual=getattr(r,f'ret{k}')
            assert (pd.isna(value) and pd.isna(actual)) or np.isclose(value,actual,rtol=0,atol=1e-12)
            returns+=1
        strategy=expected[2] if r.d2_up==True else expected[3] if r.d3_up==True else expected[4]
        assert (pd.isna(strategy) and pd.isna(r.strategy)) or np.isclose(strategy,r.strategy,rtol=0,atol=1e-12)
        if r.observed_3:
            assert bool(r.label_3)==any(getattr(r,f'd{k}_up')==True for k in (1,2,3))
    # Mutating every test outcome and rule feature cannot affect selected rules.
    with tempfile.TemporaryDirectory(prefix='r19_selection_') as temp:
        tmp=Path(temp);shutil.copy2(out/'specification.json',tmp/'specification.json')
        altered=f.copy();q=altered.part.eq('test')
        altered.loc[q,'label_3']=1-altered.loc[q,'label_3']
        altered.loc[q,['ret2','ret3','ret4','strategy']]=999.
        altered.loc[q,['high5','low5','body5','pullback5','position5']]=-999.
        altered.to_pickle(tmp/'enriched_top1.pkl')
        exp.OUT=tmp
        try:
            with contextlib.redirect_stdout(io.StringIO()):exp.choose()
            assert json.loads((tmp/'all_rules_frozen.json').read_text())==frozen
        finally:exp.OUT=out
    result={'status':'PASS','unique_raw_days_replayed':checked,'feature_max_abs_difference':maxdiff,
      'saved_top1_scores_exact':scorechecks,'independent_horizon_return_checks':returns,
      'label_replay':'D1-D3 daily flags match','train_valid_D4_before_cutoff':True,
      'test_outcome_and_feature_mutation_does_not_change_selection':True,
      'missing_minute_cases':2,'missing_cases_daily_volume_zero':True,
      'rules_sha256':exp.sha(out/'all_rules_frozen.json'),'spec_sha256':exp.sha(out/'specification.json')}
    xlsx=out/'前五分钟形态_Top1简单规则_R19_20261015.xlsx'
    if xlsx.exists():
        from scripts.verify_limit_rebound_segments_r18_touch import read,equal,col
        exported=read(xlsx);payload=json.loads((out/'workbook_data.json').read_text());cells=0
        assert list(exported)==list(payload)
        for name,rows in payload.items():
            columns=list(rows[0]);sheet=exported[name]
            for i,c in enumerate(columns):assert sheet[col(i)+'1']==c
            for j,row in enumerate(rows,2):
                for i,c in enumerate(columns):
                    value=row[c]
                    if c in ('日期','D1日期') and value:value=(datetime.fromisoformat(value)-datetime(1899,12,30)).days
                    assert equal(sheet.get(col(i)+str(j)),value),(name,j,c)
                    cells+=1
        result.update({'export_cells_reconciled':cells,'workbook_sha256':exp.sha(xlsx),'workbook_sheets':list(payload)})
    exp.dump(out/'verification.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

"""Independent chronology, baseline and saved-model audit plus CO18 comparisons."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import joblib
from threadpoolctl import threadpool_limits
from scripts.backfill_b1_history import read
from scripts.run_automl_four_class_pipeline import select_inference_candidates, infer, select_top

ROOT=Path('outputs/stock_automl/b1_validation_co18_20261026')
SOURCE=Path('outputs/stock_automl/b1_history_20261016/run')

def metrics(df):
    known=df[~df.unknown]
    ret=known.return_net.to_numpy(dtype=float)
    equity=np.r_[1.,np.cumprod(1+ret)]
    trades=known[known.executed]
    return dict(days=len(df),unknown=int(df.unknown.sum()),trades=len(trades),
        win_rate=float((trades.return_net>0).mean()) if len(trades) else None,
        mean_trade_net=float(trades.return_net.mean()) if len(trades) else None,
        compound_net=float(equity[-1]-1),compound_gross=float((1+known.return_gross).prod()-1),
        max_drawdown=float((equity/np.maximum.accumulate(equity)-1).min()))

def main():
    meta=json.loads((ROOT/'source.json').read_text());days=meta['days']
    cal=read(SOURCE/'calendar.csv').date.tolist();old=read(SOURCE/'picks.csv')
    old=old[old.selection_rank.eq(1)].set_index('signal_date')
    records=[];validations=[];audits=[];differences=[];no_eligible=[]
    with threadpool_limits(limits=1):
        for day in days:
            folder=ROOT/'folds'/day;x=json.loads((folder/'result.json').read_text())
            i=cal.index(day);assert x['train_dates']==cal[i-20:i-2] and x['validation_dates']==cal[i-2:i]
            train=pd.read_csv(folder/'training.csv.gz',usecols=['signal_date','next_date']);assert train.next_date.max()<=x['validation_dates'][0]
            assert set(train.signal_date)==set(x['train_dates'])
            val=pd.DataFrame(x['validation']);assert len(val)==6 and val.day.max()<day
            vsource=pd.read_csv(folder/'validation.csv.gz',usecols=['signal_date','next_date']);assert vsource.next_date.max()<=day
            assert set(vsource.signal_date)==set(x['validation_dates'])
            stats=[]
            for name in ['原HGB','强约束HGB','逻辑回归']:
                v=val[val.model.eq(name)].sort_values('day');known=v[~v.unknown]
                n=int(known.actionable.eq(True).sum());score=float(v.net.fillna(0).sum()/2)
                stats.append((n,int(v.unknown.sum()),score,v))
            eligible=[k for k,(n,u,_,_) in enumerate(stats) if n>=1 and u==0]
            if not eligible:no_eligible.append(day)
            k=max(eligible,key=lambda k:(stats[k][2],-k)) if eligible else 0
            passed=bool(eligible) and stats[k][2]>0
            v=stats[k][3];stable=passed and all(h.actionable.eq(True).sum()>=1 and h.net.sum()>0 for h in [v.iloc[:1],v.iloc[1:]])
            rr={r['variant'][0]:r for r in x['records']}
            assert rr['C']['chosen']==['原HGB','强约束HGB','逻辑回归'][k]
            assert rr['D']['executed']==bool(rr['C']['actionable'] and passed)
            assert rr['E']['executed']==bool(rr['C']['actionable'] and stable)
            for r in x['records']:
                if r['executed'] and not r['unknown']:
                    assert np.isclose(r['return_net'],r['sell_price']*.999/(r['signal_price']*1.001)-1,rtol=0,atol=1e-12)
            base=rr['A'];previous=old.loc[day] if day in old.index else None
            if previous is None:
                if base['stock']:differences.append(dict(day=day,reason='new top1'))
            elif not (base['stock']==previous.symbol6 and bool(base['actionable'])==bool(previous.actionable) and np.isclose(base['signal_price'],previous.signal_price) and np.isclose(base['sell_price'] if base['sell_price'] is not None else np.nan,previous.close_40,equal_nan=True)):
                differences.append(dict(day=day,reason='baseline mismatch',new=base['stock'],old=previous.symbol6))
            original_scores=read(SOURCE/'models'/day/'scores.csv.gz')
            baseline_scores=read(folder/'baseline_scores.csv.gz')
            assert original_scores.symbol6.tolist()==baseline_scores.symbol6.tolist()
            probabilities=['p_lt0','p_0to1','p_1to3','p_ge3']
            assert np.allclose(original_scores[probabilities],baseline_scores[probabilities],atol=1e-14,rtol=0)
            frame=select_inference_candidates(read(folder/'test.csv.gz'),day)
            # Every saved next-day scoring result must reproduce from its fitted model.
            for model,scored in [('baseline.joblib','baseline_scores.csv.gz')]+[(f'model{j}.joblib',f'test_scores{j}.csv.gz') for j in range(3)]:
                actual=infer(joblib.load(folder/model),frame);saved=read(folder/scored)
                assert actual.symbol6.tolist()==saved.symbol6.tolist()
                cols=['p_lt0','p_0to1','p_1to3','p_ge3']
                assert np.allclose(actual[cols],saved[cols],atol=1e-14,rtol=0)
            records.extend(x['records']);validations.extend(x['validation']);audits.append(day)
    df=pd.DataFrame(records);df['month']=df.day.str[:7];df.to_csv(ROOT/'daily.csv',index=False)
    pd.DataFrame(validations).to_csv(ROOT/'validation_picks.csv.gz',index=False)
    overall={v:metrics(g) for v,g in df.groupby('variant')}
    monthly=[dict(month=m,variant=v,**metrics(g)) for (m,v),g in df.groupby(['month','variant'])]
    pd.DataFrame(monthly).to_csv(ROOT/'monthly.csv',index=False)
    c=df[df.variant.str.startswith('C')]
    reliability={f'{field}={value}':metrics(g) for field in ['reliable','stable'] for value,g in c.groupby(field)}
    correlation=float(c.loc[~c.unknown,['validation_score','return_net']].corr().iloc[0,1])
    pairs={};rng=np.random.default_rng(42)
    pivot=df.pivot(index='day',columns='variant',values='return_net');pivot.columns=pivot.columns.str[0]
    for a,b in [('C','B'),('D','C'),('E','D'),('C','A'),('D','A'),('E','A')]:
        delta=pivot[a]-pivot[b]
        unknown=df.pivot(index='day',columns='variant',values='unknown');unknown.columns=unknown.columns.str[0]
        delta=delta[~(unknown[a]|unknown[b])]
        month=delta.groupby(delta.index.str[:7]).agg(['sum','count'])
        idx=rng.integers(0,len(month),size=(10000,len(month)))
        values=month['sum'].to_numpy()[idx].sum(axis=1)/month['count'].to_numpy()[idx].sum(axis=1)
        pairs[a+'-'+b]=dict(mean_daily_delta=float(delta.mean()),month_bootstrap_95=np.quantile(values,[.025,.975]).tolist(),positive_months=int((month['sum']>0).sum()),months=len(month))
    labels={v:dict(count=int(g.label.notna().sum()),ge3=int(g.label.ge(.03).sum()),between1and3=int((g.label.ge(.01)&g.label.lt(.03)).sum())) for v,g in df.groupby('variant')}
    previous_summary=json.loads((ROOT.parent/'b1_validation_co17_20261026/analysis.json').read_text())
    comparison={key:dict(co17=previous_summary['overall'][old_name],co18=overall[new_name]) for key,old_name,new_name in zip('ABCDE',previous_summary['overall'],overall)}
    result=dict(co17_comparison=comparison,validation_no_eligible_days=no_eligible,days=len(days),first=days[0],last=days[-1],baseline_differences=differences,chronology_and_model_checks=len(audits),overall=overall,monthly=monthly,reliability=reliability,validation_nextday_correlation=correlation,paired_differences=pairs,top1_labels_before_gate=labels,selected_models=c.chosen.value_counts().to_dict())
    (ROOT/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    previous=pd.read_csv(ROOT.parent/'b1_validation_co17_20261026/daily.csv')
    common=previous[previous.variant.str.startswith('A')].sort_values('day')
    current=df[df.variant.str.startswith('A')].sort_values('day')
    assert common.day.tolist()==current.day.tolist()
    assert np.allclose(common.return_net,current.return_net,equal_nan=True)
    assert not differences,differences
    (ROOT/'complete.json').write_text(json.dumps(dict(days=len(days),input_hashes=meta['hashes'],verified=True),indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='monthly'},ensure_ascii=False,indent=2))
if __name__=='__main__':main()

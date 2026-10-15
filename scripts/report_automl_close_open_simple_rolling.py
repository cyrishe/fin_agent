"""CO5 monthly, tail-loss, outlier-removal and perturbation audit."""
from __future__ import annotations
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.experiment_automl_close_open_simple_rolling import OUT, REPORT, SOURCE, net_return


def attach_amounts(picks,amounts,calendar):
    if amounts.duplicated(['date','symbol6']).any(): raise ValueError('Duplicate amount key')
    wide=amounts.pivot(index='date',columns='symbol6',values='amount').reindex(calendar)
    median=wide.shift(1).rolling(20,min_periods=20).median()
    counts=wide.shift(1).rolling(20,min_periods=1).count()
    rows=[]
    for r in picks.itertuples():
        if pd.isna(r.symbol6):
            rows.append((np.nan,np.nan,0,np.nan));continue
        current=wide.at[r.signal_date,r.symbol6] if r.symbol6 in wide else np.nan
        prior=median.at[r.signal_date,r.symbol6] if r.symbol6 in wide else np.nan
        n=counts.at[r.signal_date,r.symbol6] if r.symbol6 in wide else 0
        passed=float(current>=prior) if np.isfinite(current) and np.isfinite(prior) and prior>0 else np.nan
        rows.append((current,prior,n,passed))
    out=picks.copy()
    out[['amount','prior20_amount_median','prior20_amount_days','amount_gate']]=rows
    out['month']=out.signal_date.str[:7]
    out['selected']=out.symbol6.notna()
    out['net_return']=net_return(out.close40_return,50_000,.00025)
    out['second_return']=out.second_high/out.signal_price-1
    return out


def execution(frame,gate):
    d=frame.copy()
    allowed=d.amount_gate.eq(1) if gate else pd.Series(True,index=d.index)
    unknown=d.selected & d.amount_gate.isna() if gate else pd.Series(False,index=d.index)
    d['trade']=d.selected & allowed
    d['unknown_gate']=unknown
    d['daily_net']=np.where(unknown,np.nan,np.where(d.trade,d.net_return,0.))
    d['daily_gross']=np.where(unknown,np.nan,np.where(d.trade,d.close40_return,0.))
    return d


def safe_mean(x): return float(x.mean()*100) if len(x.dropna()) else np.nan


def describe(d):
    t=d[d.trade & d.net_return.notna()]
    before=d[d.selected & d.strong.notna()]
    tagged=t[t.strong.notna() & t.weak.notna()]
    equity=d.sort_values('signal_date').daily_net.dropna().cumsum()
    peaks=np.maximum.accumulate(np.r_[0,equity.to_numpy()])[1:]
    tail=t.net_return.nsmallest(max(1,math.ceil(len(t)*.05)))
    second=before.second_return
    return {'days':len(d),'selected':int(d.selected.sum()),'trades':len(t),
            'unknown_trade_outcomes':int((d.trade & d.net_return.isna()).sum()),
            'unknown_gates':int(d.unknown_gate.sum()),
            'pre_gate_strong_pct':safe_mean(before.strong),
            'pre_gate_acceptable_pct':float(second.ge(.01-1e-10).mul(second.lt(.03-1e-10)).mean()*100) if len(second) else np.nan,
            'pre_gate_error_pct':float(second.lt(.01-1e-10).mean()*100) if len(second) else np.nan,
            'strong_pct':safe_mean(tagged.strong),'weak_pct':safe_mean(tagged.weak),
            'gross_trade_mean_pct':safe_mean(t.close40_return),'net_trade_mean_pct':safe_mean(t.net_return),
            'net_daily_mean_pct':safe_mean(d.daily_net),'net_sum_pct':float(d.daily_net.sum()*100),
            'net_win_pct':float(t.net_return.gt(0).mean()*100) if len(t) else np.nan,
            'loss2_pct':float(t.net_return.le(-.02).mean()*100) if len(t) else np.nan,
            'loss5_pct':float(t.net_return.le(-.05).mean()*100) if len(t) else np.nan,
            'worst_trade_pct':float(t.net_return.min()*100),'cvar5_pct':safe_mean(tail),
            'max_drawdown_fixed_pct':float(np.min(equity.to_numpy()-peaks)*100) if len(equity) else np.nan,
            'net_trade_mean_slippage10bp_pct':safe_mean(t.net_return-.001)}


def robustness(d):
    t=d[d.trade & d.net_return.notna()]
    sums=d.groupby('month').daily_net.sum(min_count=1)
    monthmean=d.groupby('month').daily_net.mean()
    best=sums.idxmax()
    without_month=d[d.month.ne(best)]
    best_ids=t.groupby('month').net_return.idxmax() if len(t) else []
    rest=t.drop(best_ids)
    zeroed=d.daily_net.copy();zeroed.loc[best_ids]=0.
    both=rest[rest.month.ne(best)]
    counts=d.groupby('month').size()
    return {'months':len(sums),'positive_months':int(sums.gt(0).sum()),
            'month_equal_daily_mean_pct':safe_mean(monthmean),'worst_month_sum_pct':float(sums.min()*100),
            'month_sum_std_pp':float(sums.std()*100),'best_month':best,
            'best_month_sum_pct':float(sums.max()*100),
            'ex_best_month_trade_mean_pct':safe_mean(without_month.loc[without_month.trade,'net_return']),
            'ex_best_month_daily_mean_pct':safe_mean(without_month.daily_net),
            'ex_month_top_trade_mean_pct':safe_mean(rest.net_return),
            'ex_month_top_daily_mean_pct':safe_mean(zeroed),
            'ex_month_top_sum_pct':float(zeroed.sum()*100),
            'ex_month_top_positive_months':int(zeroed.groupby(d.month).sum(min_count=1).gt(0).sum()),
            'ex_both_trade_mean_pct':safe_mean(both.net_return),
            'ex_both_daily_mean_pct':safe_mean(zeroed[d.month.ne(best)]),
            'ex_top_month_equal_daily_mean_pct':safe_mean(zeroed.groupby(d.month).mean()),
            'smallest_month_days':int(counts.min())}


def block_ci(x):
    x=np.asarray(x,float);x=x[np.isfinite(x)]
    rng=np.random.default_rng(20261014)
    starts=rng.integers(0,len(x),size=(2000,math.ceil(len(x)/5)))
    ids=((starts[:,:,None]+np.arange(5))%len(x)).reshape(2000,-1)[:,:len(x)]
    low,high=np.quantile(x[ids].mean(axis=1),[.025,.975])
    return low*100,high*100


def run():
    REPORT.mkdir(parents=True,exist_ok=True)
    picks=pd.read_csv(OUT/'picks_before_amount_gate.csv',dtype={'symbol6':str},float_precision='round_trip')
    amounts=pd.concat([pd.read_csv(p,dtype={'symbol6':str},float_precision='round_trip') for p in sorted((OUT/'amounts').glob('*.csv.gz'))],ignore_index=True)
    calendar=pd.read_csv(SOURCE/'availability.csv').date.tolist()
    days={r.signal_date for r in picks.itertuples()}
    required={date for day in days for date in calendar[calendar.index(day)-20:calendar.index(day)+1]}
    if not required.issubset(set(amounts.date)): raise ValueError('Amount date build incomplete')
    picks=attach_amounts(picks,amounts,calendar)
    picks.to_csv(OUT/'daily_picks_with_gate.csv',index=False,float_format='%.17g')
    daily=[];monthly=[];summary=[]
    common=sorted(picks.loc[picks.model.eq('B1_frozen_original171'),'signal_date'].unique())
    for model,g in picks.groupby('model',sort=True):
        for gate in (False,True):
            d=execution(g,gate)
            d['rule']='amount_gate' if gate else 'no_gate'
            daily.append(d)
            for month,m in d.groupby('month'):
                record={'model':model,'rule':d.rule.iloc[0],'month':month,**describe(m)}
                t=m[m.trade & m.net_return.notna()]
                drop=t.net_return.idxmax() if len(t) else None
                remaining=t.drop(index=drop) if drop is not None else t
                record['ex_top_trade_mean_pct']=safe_mean(remaining.net_return)
                record['ex_top_sum_pct']=record['net_sum_pct']-(t.loc[drop,'net_return']*100 if drop is not None else 0)
                record['ex_top_daily_mean_pct']=record['ex_top_sum_pct']/m.daily_net.notna().sum()
                monthly.append(record)
            for scope,part in [('all',d),('full_months',d[d.month<'2026-10']),('B1_common_dates',d[d.signal_date.isin(common)])]:
                if part.empty: continue
                summary.append({'model':model,'rule':d.rule.iloc[0],'scope':scope,**describe(part),**robustness(part)})
    daily=pd.concat(daily,ignore_index=True)
    daily.to_csv(OUT/'daily_execution.csv.gz',index=False,float_format='%.17g')
    pd.DataFrame(monthly).to_csv(REPORT/'monthly.csv',index=False)
    summary=pd.DataFrame(summary);summary.to_csv(REPORT/'summary.csv',index=False)
    stable=[]
    for model in sorted(picks.model.unique()):
        if model.endswith('_drop2pct') or model=='B1_frozen_original171': continue
        a=picks[picks.model.eq(model)].set_index('signal_date')
        b=picks[picks.model.eq(model+'_drop2pct')].set_index('signal_date').reindex(a.index)
        for period,dates in [('all',a.index),('B1_common_dates',a.index.intersection(common))]:
            different=a.loc[dates,'symbol6'].fillna('CASH').ne(b.loc[dates,'symbol6'].fillna('CASH'))
            da=execution(a.loc[dates],True);db=execution(b.loc[dates],True)
            stable.append({'model':model,'scope':period,'days':len(dates),'changed_top1_days':int(different.sum()),
                           'top1_change_pct':float(different.mean()*100),'base_net_daily_pct':safe_mean(da.daily_net),
                           'perturbed_net_daily_pct':safe_mean(db.daily_net),
                           'paired_daily_delta_pp':safe_mean(db.daily_net-da.daily_net)})
    pd.DataFrame(stable).to_csv(REPORT/'perturbation.csv',index=False)
    paired=[]
    for rule in ('no_gate','amount_gate'):
        ref=daily[daily.model.eq('B1_frozen_original171')&daily.rule.eq(rule)].set_index('signal_date').daily_net
        for model,g in daily[daily.rule.eq(rule)&~daily.model.str.endswith('_drop2pct')].groupby('model'):
            if model=='B1_frozen_original171': continue
            x=g.set_index('signal_date').daily_net.reindex(ref.index)-ref
            low,high=block_ci(x)
            paired.append({'model':model,'rule':rule,'days':int(x.notna().sum()),'daily_delta_pp':safe_mean(x),
                           'block5_ci_low_pp':low,'block5_ci_high_pp':high})
    pd.DataFrame(paired).to_csv(REPORT/'paired_B1.csv',index=False)
    # Keep the explicit original daily ledger, including its coverage in the new candidate pool.
    daily[daily.model.eq('B1_frozen_original171')].to_csv(REPORT/'B1_original_daily.csv',index=False)
    daily[~daily.model.str.endswith('_drop2pct')].to_csv(REPORT/'daily_main.csv.gz',index=False)
    factors=[];validation=[];folds=[]
    for f in sorted((OUT/'folds').glob('*/complete.json')):
        meta=json.loads(f.read_text());month=meta['month']
        factors.append(pd.read_csv(f.parent/'factor_selection.csv').assign(month=month))
        validation.append(pd.read_csv(f.parent/'validation.csv').assign(month=month))
        folds.append({k:v for k,v in meta.items() if k!='models'})
    pd.concat(factors,ignore_index=True).to_csv(REPORT/'factor_selection_by_month.csv',index=False)
    pd.concat(validation,ignore_index=True).to_csv(REPORT/'validation_by_month.csv',index=False)
    (REPORT/'folds.json').write_text(json.dumps(folds,ensure_ascii=False,indent=2)+'\n')
    (REPORT/'run_manifest.json').write_text((OUT/'run_manifest.json').read_text())
    # Compact printable output, no perturbation versions in the main table.
    main=summary[summary.rule.eq('amount_gate')&summary.scope.eq('all')&~summary.model.str.endswith('_drop2pct')]
    print(main[['model','days','trades','positive_months','months','net_trade_mean_pct','strong_pct','weak_pct','loss2_pct','ex_best_month_trade_mean_pct','ex_month_top_trade_mean_pct','unknown_gates']].to_string(index=False))

if __name__=='__main__': run()

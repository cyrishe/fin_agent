"""CO4: freeze discovery directions, then evaluate temporal holdouts by month."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from scripts.build_automl_close_open_factor_panel import DATA, REPORT, FACTORS, BINARY, sha


def phase(day: str) -> str:
    return 'discovery' if day < '2025-07-01' else 'validation' if day < '2026-01-01' else 'forward'


def sample_split(frame: pd.DataFrame) -> pd.DataFrame:
    d = frame.copy()
    d['phase'] = d.signal_date.map(phase)
    # A boundary signal cannot contribute its later label to the previous phase.
    keep = (~d.phase.eq('discovery') | d.next_date.lt('2025-07-01')) & (
        ~d.phase.eq('validation') | d.next_date.lt('2026-01-01'))
    return d[keep & d.strong.notna() & d.weak.notna()].copy()


def auc(y: np.ndarray, x: np.ndarray) -> float:
    positive = y == 1
    n, m = positive.sum(), (~positive).sum()
    if min(n,m) < 2:
        return np.nan
    return float((rankdata(x)[positive].sum()-n*(n+1)/2)/(n*m))


def tail_masks(x: np.ndarray, binary: bool = False):
    if binary:
        return x == 1, x == 0
    r = (rankdata(x)-.5)/len(x)
    return r >= .8, r <= .2


def daily_associations(frame: pd.DataFrame, factors=FACTORS) -> pd.DataFrame:
    records = []
    for day, group in frame.groupby('signal_date',sort=True):
        y, bad = group.strong.to_numpy(), group.weak.to_numpy()
        returns = group.close40_return.to_numpy()
        for name in factors:
            original = group[name].to_numpy(float)
            known = np.isfinite(original)
            x, strong, weak, ret = original[known],y[known],bad[known],returns[known]
            if len(x) < 20:
                continue
            high,low = tail_masks(x,name in BINARY)
            extremes = (strong == 1) | (weak == 1)
            row = {'date':day,'phase':phase(day),'factor':name,'n':len(x),
                   'high_n':int(high.sum()),'low_n':int(low.sum()),
                   'auc_strong':auc(strong,x),'auc_weak':auc(weak,x),
                   'auc_extremes':auc(strong[extremes],x[extremes])}
            for target,values in [('strong',strong),('weak',weak),('quality',strong-weak),
                                  ('close40',ret),('clipped_close40',np.clip(ret,-.05,.05))]:
                row['delta_'+target] = float(np.mean(values[high])-np.mean(values[low])) if min(high.sum(),low.sum()) >= 5 else np.nan
            records.append(row)
    return pd.DataFrame(records)


def block_inference(values, repetitions: int = 2000) -> dict:
    x = np.asarray(values,float)
    x = x[np.isfinite(x)]
    if len(x) < 20:
        return {'mean':float(np.mean(x)) if len(x) else np.nan,'low':np.nan,'high':np.nan,'p':np.nan}
    rng = np.random.default_rng(20261014)
    starts = rng.integers(0,len(x),size=(repetitions,math.ceil(len(x)/5)))
    indices = (starts[:,:,None]+np.arange(5)) % len(x)
    boot = x[indices.reshape(repetitions,-1)[:,:len(x)]].mean(axis=1)
    mean = float(x.mean())
    p = (1+np.count_nonzero(np.abs(boot-mean) >= abs(mean)))/(repetitions+1)
    return {'mean':mean,'low':float(np.quantile(boot,.025)),
            'high':float(np.quantile(boot,.975)),'p':float(p)}


def bh(values: pd.Series) -> pd.Series:
    result = pd.Series(np.nan,index=values.index)
    valid = values.dropna().sort_values()
    q = valid.to_numpy()*len(valid)/np.arange(1,len(valid)+1)
    result.loc[valid.index] = np.minimum(1,np.minimum.accumulate(q[::-1])[::-1])
    return result


def freeze_discovery(daily: pd.DataFrame, train: pd.DataFrame) -> tuple[pd.DataFrame,dict]:
    rows, rules = [], {}
    for name,group in daily[daily.phase.eq('discovery')].groupby('factor',sort=False):
        stats = block_inference(group.delta_quality)
        direction = 1 if stats['mean'] >= 0 else -1
        months = group.groupby(group.date.str[:7]).delta_quality.mean()*direction
        rows.append({'factor':name,'direction':direction,'discovery_days':int(group.delta_quality.notna().sum()),
                     'discovery_effect_pp':stats['mean']*direction*100,
                     'discovery_p':stats['p'],'discovery_same_months':int(months.gt(0).sum()),
                     'discovery_months':int(months.notna().sum())})
        x = train[name].to_numpy(float); x = x[np.isfinite(x)]
        cuts = [.5] if name in BINARY else np.unique(np.quantile(x,[.2,.4,.6,.8])).tolist()
        rules[name] = {'direction':direction,'numeric_cuts':cuts}
    summary = pd.DataFrame(rows)
    summary['discovery_q'] = bh(summary.discovery_p)
    summary['discovery_selected'] = (summary.discovery_q.le(.10) & summary.discovery_days.ge(40) &
                                      summary.discovery_same_months.ge(4) &
                                      summary.discovery_same_months.ge(summary.discovery_months*2/3))
    # Representatives are fixed on discovery data, before validation is read.
    candidates = summary[summary.discovery_selected].sort_values('discovery_effect_pp',ascending=False).factor.tolist()
    rank_frame = train.groupby('signal_date')[list(FACTORS)].rank(pct=True)
    corr = rank_frame[candidates].corr(method='pearson')
    kept = []
    for name in candidates:
        owner = next((other for other in kept if abs(corr.loc[name,other]) >= .85),None)
        rules[name]['representative'] = owner or name
        if owner is None:
            kept.append(name)
    summary['representative'] = summary.factor.map(lambda name: rules[name].get('representative',''))
    summary['discovery_representative'] = summary.factor.eq(summary.representative)
    return summary,rules


def oriented_summary(daily: pd.DataFrame, rules: dict) -> tuple[pd.DataFrame,pd.DataFrame]:
    d = daily.copy()
    d['direction'] = d.factor.map(lambda x:rules[x]['direction'])
    for c in [x for x in d if x.startswith('delta_')]:
        d[c] *= d.direction
    for c in ('auc_strong','auc_weak','auc_extremes'):
        d[c] = np.where(d.direction.eq(1),d[c],1-d[c])
    d['month'] = d.date.str[:7]
    monthly = d.groupby(['phase','month','factor']).agg(
        days=('delta_quality','count'),strong_lift=('delta_strong','mean'),
        weak_lift=('delta_weak','mean'),quality_lift=('delta_quality','mean'),
        close40_spread=('delta_close40','mean'),auc_strong=('auc_strong','mean'),
        auc_weak=('auc_weak','mean'),auc_extremes=('auc_extremes','mean')).reset_index()
    rows = []
    for (part,name),group in d.groupby(['phase','factor']):
        stats = block_inference(group.delta_quality)
        m = monthly[(monthly.phase == part)&(monthly.factor == name)].quality_lift.dropna()
        rows.append({'phase':part,'factor':name,'days':int(group.delta_quality.notna().sum()),
                     'quality_lift_pp':stats['mean']*100,'ci_low_pp':stats['low']*100,
                     'ci_high_pp':stats['high']*100,'p':stats['p'],
                     'strong_lift_pp':group.delta_strong.mean()*100,'weak_lift_pp':group.delta_weak.mean()*100,
                     'auc_strong':group.auc_strong.mean(),'auc_extremes':group.auc_extremes.mean(),
                     'positive_months':int(m.gt(0).sum()),'months':len(m),
                     'monthly_equal_lift_pp':m.mean()*100,'worst_month_lift_pp':m.min()*100,
                     'drop_best_month_lift_pp':m.drop(m.idxmax()).mean()*100 if len(m)>1 else np.nan})
    return pd.DataFrame(rows),monthly


def fixed_bins(frame: pd.DataFrame, rules: dict) -> pd.DataFrame:
    rows = []
    for name,rule in rules.items():
        bins = np.r_[-np.inf,rule['numeric_cuts'],np.inf]
        x = frame[['phase','signal_date','strong','weak','close40_return']].copy()
        x['bin'] = pd.cut(frame[name],bins=bins,labels=False)
        for (part,b),group in x.groupby(['phase','bin']):
            daily = group.groupby('signal_date')[['strong','weak','close40_return']].mean()
            rows.append({'phase':part,'factor':name,'bin':int(b),'rows':len(group),'days':len(daily),
                         'lower':bins[int(b)],'upper':bins[int(b)+1],
                         'strong_rate':daily.strong.mean(),'weak_rate':daily.weak.mean(),
                         'close40_mean':daily.close40_return.mean()})
    return pd.DataFrame(rows)


def rolling(daily: pd.DataFrame, panel: pd.DataFrame, rules: dict) -> tuple[pd.DataFrame,pd.DataFrame]:
    availability = pd.read_csv(DATA/'availability.csv')
    calendar = availability.date.tolist()
    complete_days = set(pd.read_csv(REPORT/'minute_coverage.csv').query('complete_candidates > 0').date)
    nextday = dict(zip(calendar[:-1],calendar[1:]))
    records,folds = [],[]
    for month in sorted(panel.loc[panel.phase.eq('forward'),'signal_date'].str[:7].unique()):
        first = min(d for d in calendar if d.startswith(month))
        eligible = [d for d in calendar if d < first and nextday.get(d,'9999') < first]
        past = eligible[-60:]
        missing = sorted(set(past)-complete_days)
        folds.append({'month':month,'train_start':past[0] if past else '',
                      'train_end':past[-1] if past else '', 'train_days':len(past),
                      'missing_days':','.join(missing),'used':len(past)==60 and not missing})
        if len(past) != 60 or missing:
            continue
        # All 76 factors are reported. No search or return-dependent threshold.
        history = daily[daily.date.isin(past)].groupby('factor').delta_quality.mean()
        test = daily[daily.date.str.startswith(month)].copy()
        test['direction'] = test.factor.map(lambda n:1 if history.get(n,np.nan) >= 0 else -1)
        for name,group in test.groupby('factor'):
            direction = int(group.direction.iloc[0])
            records.append({'month':month,'factor':name,'direction':direction,
                            'same_as_discovery':direction == rules[name]['direction'],
                            'days':int(group.delta_quality.notna().sum()),
                            'quality_lift_pp':group.delta_quality.mean()*direction*100,
                            'strong_lift_pp':group.delta_strong.mean()*direction*100,
                            'weak_lift_pp':group.delta_weak.mean()*direction*100})
    return pd.DataFrame(records),pd.DataFrame(folds)


def run() -> None:
    raw = pd.read_csv(DATA/'factor_panel.csv.gz',dtype={'symbol6':str,'signal_date':str,'next_date':str})
    panel = sample_split(raw)
    train = panel[panel.phase.eq('discovery')]
    training_daily = daily_associations(train)
    screening,rules = freeze_discovery(training_daily,train)
    screening.to_csv(REPORT/'discovery_screen.csv',index=False)
    (REPORT/'discovery_rules.json').write_text(json.dumps(rules,ensure_ascii=False,indent=2)+'\n')
    print('Frozen discovery directions and selection; now opening temporal holdouts',flush=True)
    future_daily = daily_associations(panel[~panel.phase.eq('discovery')])
    daily = pd.concat([training_daily,future_daily],ignore_index=True)
    daily.to_csv(DATA/'daily_associations.csv.gz',index=False)
    summary,monthly = oriented_summary(daily,rules)
    summary = summary.merge(screening[['factor','discovery_selected','discovery_representative']],on='factor',validate='many_to_one')
    summary['holdout_q'] = np.nan
    for part in ('validation','forward'):
        ix = summary.phase.eq(part) & summary.discovery_selected
        summary.loc[ix,'holdout_q'] = bh(summary.loc[ix,'p'])
    summary['supported'] = (summary.discovery_selected & summary.holdout_q.le(.10) & summary.ci_low_pp.gt(0) &
                             summary.strong_lift_pp.ge(0) & summary.weak_lift_pp.le(0) &
                             summary.positive_months.ge(summary.months*2/3))
    summary.to_csv(REPORT/'factor_summary.csv',index=False)
    monthly.to_csv(REPORT/'factor_monthly.csv',index=False)
    fixed_bins(panel,rules).to_csv(REPORT/'fixed_numeric_bins.csv',index=False)
    roll,folds = rolling(daily,panel,rules)
    roll.to_csv(REPORT/'rolling60_monthly.csv',index=False); folds.to_csv(REPORT/'rolling60_folds.csv',index=False)
    strata = []
    selected = summary[summary.phase.eq('validation') & summary.supported].factor.tolist()
    for label,mask in [('non_st',~panel.historical_st),('mainboard',~panel.symbol6.str.startswith(('3','68'))),
                       ('growth_star',panel.symbol6.str.startswith(('3','68'))),
                       ('no_next_corporate_action',panel.next_corporate_action.eq(0))]:
        if selected:
            sub = daily_associations(panel[mask],selected)
            result,_ = oriented_summary(sub[sub.factor.isin(selected)],rules)
            strata.append(result.assign(stratum=label))
    if strata:
        pd.concat(strata,ignore_index=True).to_csv(REPORT/'validation_supported_strata.csv',index=False)
    counts = panel.groupby(['phase',panel.signal_date.str[:7].rename('month')]).agg(
        rows=('symbol6','size'),days=('signal_date','nunique'),strong=('strong','sum'),weak=('weak','sum'),
        strong_rate=('strong','mean'),weak_rate=('weak','mean'))
    counts.to_csv(REPORT/'label_monthly.csv')
    (REPORT/'analysis_manifest.json').write_text(json.dumps({
        'panel_sha256':sha(DATA/'factor_panel.csv.gz'),'script_sha256':sha(Path(__file__)),
        'design_sha256':sha(REPORT/'DESIGN.md'),'frozen_rules_sha256':sha(REPORT/'discovery_rules.json'),
        'bootstrap_repetitions':2000,'block_days':5,'seed':20261014,
        'discovery_candidates':int(screening.discovery_selected.sum()),
        'discovery_representatives':int(screening.discovery_representative.sum()),
        'validation_supported':selected,
        'forward_supported':summary[summary.phase.eq('forward') & summary.supported].factor.tolist(),
    },ensure_ascii=False,indent=2)+'\n')
    print(summary[summary.discovery_selected][['phase','factor','quality_lift_pp','positive_months','months','supported']].to_string(index=False),flush=True)


if __name__ == '__main__':
    run()

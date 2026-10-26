"""Report CO25 event studies; no compounding of overlapping trade labels."""
import numpy as np
import pandas as pd
from pathlib import Path
from scripts.research_low_base_co25 import OUT,REPORT,dump,sha


def daily_stats(z,value):
    x=z[['date',value]].rename(columns={value:'value'}).copy()
    x['known']=x.value.notna()
    cls=x.value.round(10)  # Price-chain floating noise is not a positive return.
    for name,mask in [('up',cls.gt(0)),('strong',cls.ge(.03)),('loss2',cls.lt(-.02))]:
        x[name]=mask.where(x.known).astype(float)
    return x.groupby('date').agg(count=('value','size'),known=('value','count'),mean=('value','mean'),
        up=('up','mean'),strong=('strong','mean'),loss2=('loss2','mean')).reset_index()


def main():
    p=pd.read_parquet(OUT/'panel.parquet');pred=pd.read_parquet(OUT/'predictions.parquet')
    results=[];daily=[];baselines={};picks=[]
    calendar=sorted(p.date.unique())
    period_dates={'all':set(calendar)}
    for prefix in ['2024','2025','2026',*sorted(p.month.unique())]:
        period_dates[prefix]={d for d in calendar if d.startswith(prefix)}
    for h in [2,3]:
        for label in ['event','net']:
            value=f'{label}{h}'
            for scope in ['eligible','low','stable']:
                z=p if scope=='eligible' else p[p[scope]]
                baselines[(h,label,scope)]=daily_stats(z,value).set_index('date')['mean']
        groups=[('eligible',p),*[(n,p[p[n]]) for n in ['low','stable','compact','first_low','first_stable','first_compact']]]
        sc=pred[pred.horizon.eq(h)]
        for name,sort in [('ridge_top3',['predicted_net','stk_code']),('narrow_top3',['range5','stk_code'])]:
            ascending=[name!='ridge_top3',True]
            top=sc.sort_values(sort,ascending=ascending).groupby('date',sort=False).head(3).copy()
            top['method']=name;picks.append(top)
            ids=top.sample_id
            groups.append((name,p[p.sample_id.isin(ids)]))
        for method,z in groups:
            for label in ['event','net']:
                value=f'{label}{h}';dd=daily_stats(z,value)
                dd['method']=method;dd['horizon']=h;dd['target']=label
                dd['month']=dd.date.str[:7];dd['year']=dd.date.str[:4]
                for scope in ['eligible','low','stable']:
                    dd['excess_'+scope]=dd['mean']-dd.date.map(baselines[(h,label,scope)])
                daily.append(dd)
                # A full calendar is reported separately; no-candidate months
                # remain explicit rows here with zero count and unknown means.
                for period,dates in period_dates.items():
                    e=z[z.date.isin(dates)];d=dd[dd.date.isin(dates)]
                    v=e[value].dropna();n=len(v);cls=v.round(10)
                    row={'period':period,'method':method,'horizon':h,'target':label,
                         'calendar_days':len(dates),'signal_days':len(d),'rows':len(e),'known':n,'unknown':len(e)-n,
                         'mean':v.mean(),'median':v.median(),'up':cls.gt(0).mean() if n else np.nan,
                         'strong':cls.ge(.03).mean() if n else np.nan,'loss2':cls.lt(-.02).mean() if n else np.nan,
                         'tail5':v.nsmallest(max(1,int(np.ceil(.05*n)))).mean(),'daily_mean':d['mean'].mean()}
                    for scope in ['eligible','low','stable']:row['excess_'+scope]=d['excess_'+scope].mean()
                    results.append(row)
    pd.DataFrame(results).to_csv(REPORT/'summary.csv',index=False)
    dd=pd.concat(daily,ignore_index=True);dd.to_csv(REPORT/'daily_metrics.csv.gz',index=False)
    pd.concat(picks,ignore_index=True).to_csv(REPORT/'model_top3.csv.gz',index=False,float_format='%.17g')
    # Model prediction quality, against the SAME training-only constant.
    diagnostics=[]
    for (h,month),g in pred.groupby(['horizon','month']):
        z=g.dropna(subset=['net']);err=z.predicted_net-z.net;const=z.train_constant-z.net
        ranks=[]
        for date,b in z.groupby('date'):
            if len(b)>2 and b.predicted_net.nunique()>1 and b.net.nunique()>1:
                ranks.append(b.predicted_net.corr(b.net,method='spearman'))
        errors=pd.DataFrame({'date':z.date,'ae':abs(err),'ce':abs(const),'se':err**2,'cs':const**2})
        means=errors.groupby('date').mean().mean()
        diagnostics.append({'horizon':h,'month':month,'known':len(z),'unknown':len(g)-len(z),
                            'predicted_mean':z.predicted_net.mean(),'actual_mean':z.net.mean(),
                            'mae':means.ae,'constant_mae':means.ce,'mse':means.se,'constant_mse':means.cs,
                            'daily_rank_ic':np.mean(ranks)})
    pd.DataFrame(diagnostics).to_csv(REPORT/'model_quality.csv',index=False)
    # Five-market-day moving-block bootstrap of paired daily excess. Intervals
    # are descriptive and do not correct for multiple tested rule variants.
    rng=np.random.default_rng(20261027);bootstrap=[]
    cal=pd.read_csv(REPORT/'daily_counts.csv').date.tolist()
    for (method,h,label,year),g in dd.groupby(['method','horizon','target','year']):
        if method not in ['compact','first_compact','ridge_top3','narrow_top3'] or label!='net':continue
        days=[d for d in cal if d.startswith(year)]
        for reference in ['eligible','stable']:
            x=g.set_index('date')['excess_'+reference].reindex(days).to_numpy()
            if np.isfinite(x).sum()<10:continue
            length=len(x);starts=rng.integers(0,max(1,length-4),size=(2000,int(np.ceil(length/5))))
            ix=(starts[:,:,None]+np.arange(5)).reshape(2000,-1)[:,:length]
            means=np.nanmean(x[ix],axis=1)
            bootstrap.append({'method':method,'horizon':h,'year':year,'reference':reference,
                              'daily_excess':np.nanmean(x),'ci_low':np.nanquantile(means,.025),'ci_high':np.nanquantile(means,.975)})
    pd.DataFrame(bootstrap).to_csv(REPORT/'paired_block_intervals.csv',index=False)
    # Adjustment sensitivity is reported, not removed after seeing outcomes.
    adjustment=[]
    for h in [2,3]:
        for name in ['low','stable','compact']:
            z=p[p[name]];valid=z[f'net{h}'].notna() & z[f'raw_net{h}'].notna()
            adjustment.append({'horizon':h,'method':name,'known':int(valid.sum()),
                               'different_over_1bp':int((z.loc[valid,f'net{h}']-z.loc[valid,f'raw_net{h}']).abs().gt(.0001).sum())})
    pd.DataFrame(adjustment).to_csv(REPORT/'adjustment_sensitivity.csv',index=False)
    dump(REPORT/'analysis_manifest.json',{'panel_sha256':sha(OUT/'panel.parquet'),
        'predictions_sha256':sha(OUT/'predictions.parquet'),'script_sha256':sha(Path(__file__)),
        'summary_rows':len(results),'daily_rows':len(dd),'models':len(diagnostics)})
    s=pd.DataFrame(results)
    print(s[(s.period.isin(['2024','2025','2026']))&(s.target=='net')&s.method.isin(['eligible','low','stable','compact','ridge_top3','narrow_top3'])][
        ['period','horizon','method','rows','unknown','mean','daily_mean','excess_eligible','excess_stable','strong','loss2']].to_string(index=False),flush=True)


if __name__=='__main__':main()

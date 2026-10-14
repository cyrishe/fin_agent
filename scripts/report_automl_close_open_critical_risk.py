"""CO6 risk recall, rejected winners, monthly cost and false optimism audit."""
import json
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from scripts.experiment_automl_close_open_critical_risk import OUT,REPORT,CO5,SOURCE,net_classes,CLASSES
from scripts.report_automl_close_open_simple_rolling import attach_amounts,execution,describe,robustness

BASES=('B1_frozen_original171','tree171_seven7_samepool','logit_broad76','ridge_broad76')


def combined_gate(a,b):
    return np.where((a==0)|(b==0),0.,np.where((a==1)&(b==1),1.,np.nan))


def risk_stats(base,threshold):
    known=base[base.selected & base.amount_gate.eq(1)&base.net_return.notna()].copy()
    available=known[known.p_critical.notna()].copy()
    blocked=available.p_critical.gt(threshold)
    critical=available.net_return.lt(-.02)
    loss5=available.net_return.lt(-.05)
    win=available.net_return.gt(0)
    strong=available.strong.eq(1)
    random_critical=0.;random_wins=0.
    for _,g in available.groupby('month'):
        n=g.p_critical.gt(threshold).sum()
        random_critical+=n*g.net_return.lt(-.02).mean()
        random_wins+=n*g.net_return.gt(0).mean()
    def fraction(n,den):return n/den*100 if den else np.nan
    return {'original_trades':len(known),'risk_unknown':len(known)-len(available),'scorable_trades':len(available),
            'original_critical':int(critical.sum()),'blocked':int(blocked.sum()),
            'blocked_critical':int((blocked&critical).sum()),
            'critical_recall_pct':fraction((blocked&critical).sum(),critical.sum()),
            'blocked_critical_precision_pct':fraction((blocked&critical).sum(),blocked.sum()),
            'blocked_loss5':int((blocked&loss5).sum()),'blocked_profitable':int((blocked&win).sum()),
            'profitable_rejected_pct':fraction((blocked&win).sum(),win.sum()),
            'blocked_strong':int((blocked&strong).sum()),'blocked_net_ge3':int((blocked&available.net_return.ge(.03)).sum()),
            'remaining_critical':int((~blocked&critical).sum()),
            'remaining_critical_pct':fraction((~blocked&critical).sum(),(~blocked).sum()),
            'blocked_net_sum_pct':float(available.loc[blocked,'net_return'].sum()*100),
            'random_same_month_expected_critical_blocked':float(random_critical),
            'excess_critical_blocked_vs_random':float((blocked&critical).sum()-random_critical),
            'random_same_month_expected_profitable_blocked':float(random_wins),
            'critical_auc_selected':float(roc_auc_score(critical,available.p_critical)) if critical.nunique()==2 else np.nan}


def run():
    REPORT.mkdir(exist_ok=True,parents=True)
    risk=pd.read_csv(OUT/'risk_scores.csv.gz',dtype={'symbol6':str},float_precision='round_trip')
    base=pd.read_csv(CO5/'daily_picks_with_gate.csv',dtype={'symbol6':str},float_precision='round_trip')
    base=base[base.model.isin(BASES)].copy()
    base=base.merge(risk,on=['signal_date','symbol6'],how='left',validate='many_to_one')
    calendar=pd.read_csv(SOURCE/'availability.csv').date.tolist()
    amounts=pd.concat([pd.read_csv(p,dtype={'symbol6':str},float_precision='round_trip') for p in sorted((CO5/'amounts').glob('*.csv.gz'))],ignore_index=True)
    new=pd.read_csv(OUT/'new_picks.csv',dtype={'symbol6':str},float_precision='round_trip')
    new=attach_amounts(new,amounts,calendar)
    original=base.copy();original['original_model']=original.model
    pieces=[original,new];gate_summaries=[]
    common=base.loc[base.model.eq(BASES[0]),'signal_date'].unique()
    for model,g in base.groupby('model'):
        for threshold in (.20,.10):
            v=g.copy();v['original_model']=model
            v['risk_pass']=np.where(v.p_critical.notna(),v.p_critical.le(threshold).astype(float),np.nan)
            v['raw_amount_gate']=v.amount_gate
            v['amount_gate']=combined_gate(v.amount_gate,v.risk_pass)
            v['model']=model+f'_risk{int(threshold*100)}'
            pieces.append(v)
            for scope,t in [('all',g),('B1_common_dates',g[g.signal_date.isin(common)])]:
                gate_summaries.append({'base_model':model,'model':v.model.iloc[0],'scope':scope,'threshold':threshold,**risk_stats(t,threshold)})
            for month,t in g.groupby('month'):
                gate_summaries.append({'base_model':model,'model':v.model.iloc[0],'scope':'monthly','month':month,'threshold':threshold,**risk_stats(t,threshold)})
    picks=pd.concat(pieces,ignore_index=True)
    pd.DataFrame(gate_summaries).to_csv(REPORT/'risk_interception.csv',index=False)
    daily=[];monthly=[];summaries=[]
    for model,g in picks.groupby('model'):
        d=execution(g,True);daily.append(d)
        for month,m in d.groupby('month'):
            t=m[m.trade & m.net_return.notna()]
            net=t.net_return
            remaining=net.drop(net.idxmax()) if len(net) else net
            monthly.append({'model':model,'month':month,**describe(m),
                            'critical_count':int(net.lt(-.02).sum()),
                            'critical_pct':float(net.lt(-.02).mean()*100) if len(net) else np.nan,
                            'ex_top_trade_mean_pct':float(remaining.mean()*100),
                            'ex_top_sum_pct':float(remaining.sum()*100)})
        for scope,part in [('all',d),('full_months',d[d.month<'2026-10']),('B1_common_dates',d[d.signal_date.isin(common)])]:
            metrics=describe(part);stress=robustness(part)
            if metrics['trades']==0:
                stress['best_month']=None
            t=part[part.trade&part.net_return.notna()]
            summaries.append({'model':model,'scope':scope,**metrics,**stress,
                              'critical_count':int(t.net_return.lt(-.02).sum()),
                              'critical_pct':float(t.net_return.lt(-.02).mean()*100) if len(t) else np.nan})
    daily=pd.concat(daily,ignore_index=True)
    daily.to_csv(OUT/'daily_execution.csv.gz',index=False,float_format='%.17g')
    pd.DataFrame(monthly).to_csv(REPORT/'monthly.csv',index=False)
    summary=pd.DataFrame(summaries);summary.to_csv(REPORT/'summary.csv',index=False)
    daily[daily.model.str.startswith('B1_')].to_csv(REPORT/'B1_filter_daily.csv',index=False)
    diags=[row for f in sorted((OUT/'folds').glob('*/diagnostics.json')) for row in json.loads(f.read_text())]
    (REPORT/'classification_diagnostics.json').write_text(json.dumps(diags,indent=2)+'\n')
    pd.DataFrame([{k:v for k,v in r.items() if k!='confusion'} for r in diags]).to_csv(REPORT/'classification_by_month.csv',index=False)
    # Fixed probability bands expose calibration and non-discriminating market shifts.
    panel=pd.read_csv(SOURCE/'factor_panel.csv.gz',dtype={'symbol6':str},float_precision='round_trip')
    rp=risk.merge(panel[['signal_date','symbol6','close40_return']],on=['signal_date','symbol6'],validate='one_to_one').dropna(subset=['close40_return'])
    from scripts.analyze_automl_cost_robustness import net_return
    rp['critical']=net_return(rp.close40_return,50_000,.00025).lt(-.02)
    rp['band']=pd.cut(rp.p_critical,[0,.1,.2,.3,.5,1.],include_lowest=True)
    cal=rp.groupby('band',observed=True).agg(rows=('critical','size'),predicted=('p_critical','mean'),observed=('critical','mean')).reset_index()
    cal.to_csv(REPORT/'risk_calibration_test.csv',index=False)
    # Audit original optimistic labels against actual cash-loss outcomes.
    old=pd.read_csv('docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top1_verified.csv',dtype={'symbol6':str},float_precision='round_trip')
    b=base[base.model.eq(BASES[0])&base.selected].drop(columns=['predicted_class'],errors='ignore')
    b=b.merge(old[['signal_date','symbol6','predicted_class']],on=['signal_date','symbol6'],validate='one_to_one')
    b['critical']=b.net_return.lt(-.02)
    b['old_peak_severe']=b.second_return.lt(-.01)
    b['actual_net_class']=[CLASSES[k] for k in net_classes(b.net_return)]
    b.to_csv(REPORT/'B1_critical_cases.csv',index=False)
    cases=[]
    for scope,g in [('before_amount_gate',b),('after_amount_gate',b[b.amount_gate.eq(1)])]:
        for predicted,t in g.groupby('predicted_class'):
            cases.append({'scope':scope,'predicted_class':predicted,'trades':len(t),
                          'actual_net_critical':int(t.critical.sum()),
                          'critical_but_old_peak_not_severe':int((t.critical&~t.old_peak_severe).sum())})
    pd.DataFrame(cases).to_csv(REPORT/'B1_false_optimism.csv',index=False)
    (REPORT/'run_manifest.json').write_text((OUT/'run_manifest.json').read_text())
    print(summary[summary.scope.eq('all')][['model','trades','critical_count','critical_pct','net_trade_mean_pct','positive_months','months','ex_month_top_trade_mean_pct']].round(4).to_string(index=False))

if __name__=='__main__':run()

"""CO7 interception, monthly settled NAV, true peak drawdown and stress tables."""
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from scripts.experiment_automl_close_open_conditional_b1 import OUT, REPORT, KEY, ARMS, read


def settle_cash(capital,gross,slippage=0.):
    """Spend at most available cash, including entry fees; fractional shares proxy."""
    if not np.isfinite(gross) or not np.isfinite(capital):return np.nan,np.nan
    proportional=capital/(1+.00025+.00001)
    buy=proportional if proportional*.00025>=5 else (capital-5)/(1+.00001)
    if buy<=0:return capital,0.
    sale=buy*(1+gross)
    if sale<0:raise ValueError('Price return below -100%')
    end=sale-max(sale*.00025,5)-sale*(.0005+.00001)-buy*slippage
    return end,buy


def account(frame,slippage=0.):
    d=frame.sort_values('signal_date').copy();capital=100_000.;before=[];after=[];invested=[]
    for r in d.itertuples():
        before.append(capital)
        if r.trade:
            capital,buy=settle_cash(capital,r.close40_return,slippage)
        else:buy=0.
        after.append(capital);invested.append(buy)
    d['nav_before']=before;d['nav_after']=after;d['invested']=invested
    d['account_return']=d.nav_after/d.nav_before-1
    return d


def account_stats(d):
    if len(d)==0:return {'nav_return_pct':np.nan,'max_drawdown_pct':np.nan,'nav_complete':False}
    complete=d.nav_after.notna().all()
    if not complete:return {'nav_return_pct':np.nan,'max_drawdown_pct':np.nan,'nav_complete':False}
    path=np.r_[d.nav_before.iloc[0],d.nav_after]
    peak=np.maximum.accumulate(path)
    dd=path/peak-1
    return {'nav_return_pct':float((path[-1]/path[0]-1)*100),
            'max_drawdown_pct':float(-dd.min()*100),'nav_complete':True}


def interception(d):
    b=d[d.baseline_trade&d.net_return.notna()]
    blocked=~b.trade;critical=b.net_return.lt(-.02);win=b.net_return.gt(0)
    def pct(n,den):return float(n/den*100) if den else np.nan
    random_crit=sum((~g.trade).sum()*g.net_return.lt(-.02).mean() for _,g in b.groupby('month'))
    risk=b[b.risk.notna()]
    return {'original_trades':len(b),'blocked':int(blocked.sum()),'original_critical':int(critical.sum()),
            'blocked_critical':int((blocked&critical).sum()),'critical_recall_pct':pct((blocked&critical).sum(),critical.sum()),
            'blocked_precision_pct':pct((blocked&critical).sum(),blocked.sum()),
            'original_profitable':int(win.sum()),'blocked_profitable':int((blocked&win).sum()),
            'profitable_rejected_pct':pct((blocked&win).sum(),win.sum()),
            'retained_trade_pct':pct((~blocked).sum(),len(b)),
            'blocked_net_sum_pct':float(b.loc[blocked,'net_return'].sum()*100),
            'random_matched_expected_critical_blocked':float(random_crit),
            'critical_blocked_above_random':float((blocked&critical).sum()-random_crit),
            'selected_critical_auc':float(roc_auc_score(risk.net_return.lt(-.02),risk.risk)) if risk.net_return.lt(-.02).nunique()==2 else np.nan}


def metrics(d):
    t=d[d.trade&d.net_return.notna()]
    n=len(t);crit=t.net_return.lt(-.02)
    def avg(s):return float(s.mean()*100) if len(s.dropna()) else np.nan
    return {'days':len(d),'selected':int(d.selected.sum()),'trades':n,
            'unknown_outcome_trades':int((d.trade&d.net_return.isna()).sum()),
            'unknown_amount_skipped':int((d.selected&d.amount_gate.isna()).sum()),
            'critical_count':int(crit.sum()),'critical_pct':float(crit.mean()*100) if n else np.nan,
            'loss5_count':int(t.net_return.lt(-.05).sum()),'net_trade_mean_pct':avg(t.net_return),
            'net_daily_mean_pct':avg(d.daily_net),'net_sum_pct':float(t.net_return.sum()*100),
            'strong_pct':avg(t.strong),'worst_trade_pct':float(t.net_return.min()*100),
            'cvar5_pct':avg(t.net_return.nsmallest(max(1,math.ceil(n*.05)))),
            'corporate_action_trades':int(t.next_corporate_action.eq(1).sum()),
            **account_stats(d),**interception(d)}


def make_daily(base,risk=None,mode=None):
    d=base.copy();d['selected']=d.symbol6.notna();d['month']=d.signal_date.str[:7]
    d['baseline_trade']=d.selected&d.amount_gate.eq(1)
    if risk is None:d['risk']=np.nan;d['threshold']=1.01;d['trade']=d.baseline_trade
    else:
        d=d.merge(risk[KEY+['risk','adaptive_threshold']],on=KEY,how='left',validate='one_to_one')
        d['threshold']=d.adaptive_threshold if mode=='adaptive' else float(mode)
        d['trade']=d.baseline_trade&d.risk.notna()&d.risk.le(d.threshold)
    d['daily_net']=np.where(d.trade,d.net_return,0.)
    return account(d)


def stress(d):
    traded=d[d.trade&d.net_return.notna()]
    sums=d.groupby('month').daily_net.sum(min_count=1)
    best=sums.idxmax()
    top_ids=traded.groupby('month').net_return.idxmax().tolist() if len(traded) else []
    rows=[]
    for name,mask in [('exclude_best_month',d.month.eq(best)),('exclude_monthly_top',d.index.isin(top_ids)),
                      ('exclude_both',d.month.eq(best)|d.index.isin(top_ids))]:
        s=d.copy();s.loc[mask,'trade']=False;s.loc[mask,'daily_net']=0
        s=account(s);t=s[s.trade&s.net_return.notna()]
        rows.append({'stress':name,'best_month':best,'remaining_trades':len(t),
                     'net_trade_mean_pct':float(t.net_return.mean()*100),**account_stats(s)})
    return rows


def run():
    top=read(OUT/'oof_with_context.csv.gz')
    pred=read(OUT/'stage2_predictions.csv.gz')
    folds=json.loads((OUT/'stage2_folds.json').read_text())
    days=sorted({d for f in folds for d in f['test_dates']})
    b=top[top.selection_rank.eq(1)&top.signal_date.isin(days)]
    base=pd.DataFrame({'signal_date':days}).merge(b,on='signal_date',how='left',validate='one_to_one')
    choices={'B1_171':make_daily(base)}
    for arm in ARMS:
        risks=pred[pred.arm.eq(arm)&pred.selection_rank.eq(1)]
        for name,mode in [('fixed35',.35),('fixed50',.50),('adaptive','adaptive')]:
            choices[arm+'_'+name]=make_daily(base,risks,mode)
    original_dates=[f['test_date'] for f in json.loads(Path('docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/folds.json').read_text())]
    summaries=[];months=[];stress_rows=[];daily=[]
    for model,d in choices.items():
        d['model']=model;daily.append(d)
        for month,g in d.groupby('month'):
            months.append({'model':model,'month':month,**metrics(g)})
        for scope,mask in [('all',np.ones(len(d),bool)),('full_months',d.month.lt('2026-10')),
                           ('2025',d.month.str.startswith('2025')),('2026',d.month.str.startswith('2026')),
                           ('B1_frozen_dates',d.signal_date.isin(original_dates))]:
            part=account(d[mask]);r=metrics(part)
            ms=[account_stats(g)['nav_return_pct'] for _,g in part.groupby('month')]
            r.update({'months':len(ms),'positive_months':int(sum(x>0 for x in ms)),
                      'active_months':int(part.groupby('month').trade.sum().gt(0).sum()),
                      'worst_month_nav_pct':float(np.nanmin(ms)),'monthly_nav_std_pp':float(np.nanstd(ms,ddof=1))})
            r.update({'nav_return_extra10bp_pct':account_stats(account(part,.001))['nav_return_pct'],
                      'drawdown_extra10bp_pct':account_stats(account(part,.001))['max_drawdown_pct']})
            summaries.append({'model':model,'scope':scope,**r})
            stress_rows += [{'model':model,'scope':scope,**row} for row in stress(part)]
    pd.DataFrame(summaries).to_csv(REPORT/'summary.csv',index=False)
    pd.DataFrame(months).to_csv(REPORT/'monthly.csv',index=False)
    pd.DataFrame(stress_rows).to_csv(REPORT/'stress.csv',index=False)
    all_daily=pd.concat(daily,ignore_index=True)
    all_daily.to_csv(OUT/'daily_execution.csv.gz',index=False,float_format='%.17g')
    cols=['model','signal_date','next_date','symbol6','selected','amount_gate','risk','threshold','trade','close40_return','net_return','daily_net','nav_before','nav_after','strong','next_corporate_action']
    all_daily[cols].to_csv(REPORT/'daily.csv',index=False,float_format='%.17g')
    top[top.selection_rank.eq(1)&top.next_corporate_action.eq(1)].to_csv(REPORT/'corporate_action_cases.csv',index=False)
    unknown=all_daily[(all_daily.trade&all_daily.net_return.isna())| (all_daily.selected&all_daily.amount_gate.isna())]
    unknown[cols].to_csv(REPORT/'unknown_cases.csv',index=False)
    (REPORT/'run_manifest.json').write_text((OUT/'run_manifest.json').read_text())
    print(pd.DataFrame(summaries).query("scope=='all'")[['model','trades','blocked_critical','blocked_profitable','critical_pct','net_trade_mean_pct','nav_return_pct','max_drawdown_pct','positive_months','months','unknown_outcome_trades']].round(4).to_string(index=False))


if __name__=='__main__':run()

"""Explicit post-initial-ranking coverage diagnostic: K=10/25/50, without retraining."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.rank_gate_daily20_risk_co13 import risk_ranks
from scripts.experiment_daily20_risk_co13 import OUT,REPORT,CO12,REPORT12,CO8,KEY,read,sha,save_json,check_freeze
from scripts.report_independent_0940_netloss_co12 import policy_metrics
from scripts.experiment_b1_calibrated_risk_co11 import sized_account
from scripts.report_b1_market_trade_gate_co8 import bootstrap_pair

KS=(10,25,50)


def freeze():
    p=REPORT/'smallk_pre_run_freeze.json'
    if p.exists():raise RuntimeError('Already frozen')
    files=[Path('scripts/smallk_gate_daily20_risk_co13.py'),Path('scripts/rank_gate_daily20_risk_co13.py'),
           REPORT/'rank_gate_summary.csv',REPORT/'rank_gate_pre_run_freeze.json',OUT/'predictions.csv.gz']
    files+=sorted((CO12/'models').glob('*/test.csv.gz'))
    save_json(p,dict(created_utc=datetime.now(timezone.utc).isoformat(),K=list(KS),files={str(p):sha(p) for p in files},
        initial_K_results_already_seen=True,reason='K100 blocks 74/222 B1 trades with daily20; user wants only a few dangerous operations removed',
        status='additional exploratory coverage diagnostic, not untouched validation',threshold_selection_by_profit=False))


def run():
    check_freeze()
    for p,h in json.loads((REPORT/'smallk_pre_run_freeze.json').read_text())['files'].items():assert sha(Path(p))==h,p
    source={'60d':pd.concat([read(p) for p in sorted((CO12/'models').glob('*/test.csv.gz'))],ignore_index=True),
            '20d':read(OUT/'predictions.csv.gz')}
    assert set(source['60d'].sample_id)==set(source['20d'].sample_id)
    old=read(REPORT12/'daily.csv.gz');baseline=old[old.model.eq('baseline')].copy()
    marks={day:g for day,g in read(CO8/'valuation_paths.csv.gz').groupby('signal_date')}
    sums=[];months=[];days=[];cases=[];accounts={};intervals=[];costs=[];exclusions=[]
    for window,scores in source.items():
        ranked=risk_ranks(scores);cols=[*KEY,'p_severe','conditional_severity','tail_expected_loss','risk_rank_max','risk_pool_size']
        base=baseline.drop(columns=[c for c in cols if c not in KEY and c in baseline]).merge(ranked[cols],on=KEY,how='left',validate='one_to_one').sort_values('signal_date').reset_index(drop=True)
        assert base.loc[base.baseline_trade,'risk_rank_max'].notna().all()
        for k in KS:
            name=f'{window}_rank_top{k}';blocked=base.baseline_trade&base.risk_rank_max.le(k)
            a=sized_account(base.assign(exposure=(base.baseline_trade&~blocked).astype(float)),marks);accounts[name]=a
            for scope,g in [('all',a),('B1_Feb_Jul_2026',a[a.month.between('2026-02','2026-07')])]:
                sums.append(dict(model=name,scope=scope,**policy_metrics(sized_account(g,marks))))
            for month,g in a.groupby('month'):months.append(dict(model=name,month=month,**policy_metrics(g)))
            days.append(a.assign(model=name).drop(columns='intratrade_path'))
            cases.append(a.loc[blocked,[*KEY,'net_main','p_severe','risk_rank_max','risk_pool_size']].assign(model=name))
            costs.append(dict(model=name,extra_bp=10,**policy_metrics(sized_account(a,marks,extra=.001))))
            exclusions.append(dict(model=name,excluded_day='2025-11-07',**policy_metrics(sized_account(a[a.signal_date.ne('2025-11-07')],marks))))
    for k in KS:intervals.append(bootstrap_pair(accounts[f'60d_rank_top{k}'],accounts[f'20d_rank_top{k}']).assign(K=k))
    summary=pd.DataFrame(sums);monthly=pd.DataFrame(months)
    summary.to_csv(REPORT/'smallk_summary.csv',index=False);monthly.to_csv(REPORT/'smallk_monthly.csv',index=False)
    pd.concat(days).to_csv(REPORT/'smallk_daily.csv.gz',index=False,float_format='%.17g')
    pd.concat(cases).to_csv(REPORT/'smallk_blocked_trades.csv',index=False)
    pd.concat(intervals).to_csv(REPORT/'smallk_paired_intervals.csv',index=False)
    pd.DataFrame(costs).to_csv(REPORT/'smallk_cost_sensitivity.csv',index=False)
    pd.DataFrame(exclusions).to_csv(REPORT/'smallk_zero_volume_exclusion.csv',index=False)
    pd.concat([read(REPORT/'summary.csv'),read(REPORT/'rank_gate_summary.csv'),summary],ignore_index=True).to_csv(REPORT/'all_schemes_summary.csv',index=False)
    pd.concat([read(REPORT/'monthly.csv'),read(REPORT/'rank_gate_monthly.csv'),monthly],ignore_index=True).to_csv(REPORT/'all_schemes_monthly.csv',index=False)
    lines=['# CO13追加：更小K，仅拦少数高风险交易','',
        '看到预设K100仍拦掉20日方案74/222笔交易后，根据用户“只需去掉少数危险操作”的目标，固定追加K=10、25、50；属于事后覆盖率诊断，不是新的盲测。模型和概率完全复用，无重训。排序与并列边界处理同前100/250/500。', '',
        '## 全期','', '|方案|净收益%|最大回撤%|笔数|拦截笔数|避亏/其中大亏|误拦盈利|','|---|---:|---:|---:|---:|---:|---:|']
    for r in summary[summary.scope.eq('all')].itertuples():
        lines.append(f'|{r.model}|{r.net_nav_pct:+.2f}|{r.settled_max_drawdown_pct:.2f}|{r.trades}|{r.baseline_trades-r.trades}|{r.losses_avoided}/{r.large_losses_avoided}|{r.profits_missed}|')
    lines+=['','每格为 **净收益% / 最大回撤% / 交易笔数 / 避亏笔数 / 误拦盈利笔数**，10月截至13日。']
    for k in KS:
        lines+=['',f'## 前{k}名','','|月份|60日/月更|20日/日更|','|---|---:|---:|']
        for month,g in monthly.groupby('month'):
            r=g.set_index('model');cells=[]
            for w in ['60d','20d']:
                a=r.loc[f'{w}_rank_top{k}'];cells.append(f'{a.net_nav_pct:+.2f} / {a.settled_max_drawdown_pct:.2f} / {int(a.trades)} / {int(a.losses_avoided)} / {int(a.profits_missed)}')
            lines.append('|'+month+'|'+'|'.join(cells)+'|')
    lines+=['','全部拦截交易见`smallk_blocked_trades.csv`；所有22个方案的统一月表见`all_schemes_monthly.csv`。','']
    (REPORT/'SMALL_K_RESULTS.md').write_text('\n'.join(lines))
    print(summary[summary.scope.eq('all')][['model','net_nav_pct','settled_max_drawdown_pct','trades','losses_avoided','large_losses_avoided','profits_missed']].to_string(index=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['freeze','run']);globals()[p.parse_args().phase]()

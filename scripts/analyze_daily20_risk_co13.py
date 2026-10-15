"""Paired risk calibration and decision changes; thresholds remain frozen."""
import json
import numpy as np
import pandas as pd
from scripts.experiment_daily20_risk_co13 import REPORT,OUT,read
from scripts.report_daily20_risk_co13 import POLICIES


def run():
    daily=read(REPORT/'daily.csv.gz');rows=[];changes=[]
    for label,name in [('60d','60d_tail_budget_005'),('20d','20d_tail_budget_005')]:
        a=daily[daily.model.eq(name)&daily.baseline_trade].copy()
        a['actual_severe']=a.net_main.lt(-.02)
        for month,g in [('all',a),*a.groupby('month')]:
            tail=g[g.actual_severe]
            rows.append(dict(window=label,month=month,rows=len(g),events=int(g.actual_severe.sum()),
                mean_p_pct=g.p_severe.mean()*100,actual_pct=g.actual_severe.mean()*100,
                brier=float(((g.p_severe-g.actual_severe.astype(float))**2).mean()),
                past_frequency_brier=float(((g.probability_reference-g.actual_severe.astype(float))**2).mean()),
                severity_mae_pp=float((tail.conditional_severity+tail.net_main).abs().mean()*100),
                mean_tail_pct=g.tail_expected_loss.mean()*100))
    quality=pd.DataFrame(rows);quality.to_csv(REPORT/'B1_probability_comparison.csv',index=False)
    calibrators=[]
    for p in sorted((OUT/'models').glob('*/calibration.json')):
        c=json.loads(p.read_text())
        for scope in ['global','strong']:
            prob=c[scope+'_probability'];sev=c[scope+'_severity']
            calibrators.append(dict(day=p.parent.name,scope=scope,prior=prob['prior'],slope=prob['slope'],
                intercept=prob['intercept'],fallback=prob['fallback'],severity_factor=sev['factor'],
                severity_factor_capped=bool(sev['factor']!=sev['raw_ratio'])))
    calibrators=pd.DataFrame(calibrators);calibrators.to_csv(REPORT/'calibration_parameters_daily.csv',index=False)
    for policy in POLICIES:
        old=daily[daily.model.eq('60d_'+policy)].set_index('signal_date')
        new=daily[daily.model.eq('20d_'+policy)].set_index('signal_date')
        for day in new.index:
            a=old.loc[day];b=new.loc[day]
            if abs(b.exposure-a.exposure)<1e-12:continue
            changes.append(dict(policy=policy,signal_date=day,symbol6=b.symbol6,net_main=b.net_main,
                old_probability=a.p_severe,new_probability=b.p_severe,
                old_tail_loss=a.tail_expected_loss,new_tail_loss=b.tail_expected_loss,
                old_exposure=a.exposure,new_exposure=b.exposure,
                daily_account_return_delta_pp=(b.account_return-a.account_return)*100))
    changes=pd.DataFrame(changes).sort_values('daily_account_return_delta_pp')
    changes.to_csv(REPORT/'decision_changes.csv',index=False)
    monthly=read(REPORT/'monthly_pair_changes.csv');summary=read(REPORT/'summary.csv').query("scope=='all'")
    lines=['# CO13：20日与60日风险模型的差异诊断','','## 月度方向','',
           '|固定交易规则|20日收益更高月份|20日回撤更小月份|收益更低月份|','|---|---:|---:|---:|']
    for p,g in monthly.groupby('policy'):
        lines.append(f'|{POLICIES[p]}|{int(g.net_nav_pct_delta.gt(1e-10).sum())}/15|{int(g.settled_max_drawdown_pct_delta.lt(-1e-10).sum())}/15|{int(g.net_nav_pct_delta.lt(-1e-10).sum())}/15|')
    lines+=['','## 原B1成交222笔：同一批股票的概率与幅度误差','',
        '|训练方案|平均预测大亏率%|实际大亏率%|Brier|历史基率Brier|条件幅度MAE百分点|','|---|---:|---:|---:|---:|---:|']
    for r in quality[quality.month.eq('all')].itertuples():
        lines.append(f'|{r.window}|{r.mean_p_pct:.2f}|{r.actual_pct:.2f}|{r.brier:.4f}|{r.past_frequency_brier:.4f}|{r.severity_mae_pp:.3f}|')
    lines+=['','Brier越低越好；条件幅度误差只在实际大亏时衡量。历史基率由各自过去校准样本计算，二者基率参考不同。完整月度概率差异见B1_probability_comparison.csv。','',
            '## 每日校准稳定性','', '|人群|校准日数|回退至过去基率日数|幅度倍率触及封顶日数|','|---|---:|---:|---:|']
    for scope,g in calibrators.groupby('scope'):
        lines.append(f'|{scope}|{len(g)}|{int(g.fallback.notna().sum())}|{int(g.severity_factor_capped.sum())}|')
    lines+=['','当校准样本两类不足或拟合斜率为负时，按运行前规则回退至过去人群频率。完整逐日参数已保存。','',
            '## 预设主减仓方案：变化最大的交易','',
            '|日期|股票|实际全额净收益%|旧概率→新概率%|旧仓位→新仓位%|当日账户收益差百分点|','|---|---|---:|---:|---:|---:|']
    g=changes[changes.policy.eq('tail_budget_005')]
    for r in pd.concat([g.head(5),g.tail(5)]).itertuples():
        lines.append(f'|{r.signal_date}|{r.symbol6}|{r.net_main*100:+.2f}|{r.old_probability*100:.1f}→{r.new_probability*100:.1f}|{r.old_exposure*100:.1f}→{r.new_exposure*100:.1f}|{r.daily_account_return_delta_pp:+.2f}|')
    lines+=['','这是事后解释变化来源，不用于重新选择阈值或新增过滤规则。每日预测同时改变训练样本长度、更新频率、校准时效，无法把差异单独归因于分箱或窗口长度。','']
    (REPORT/'DIAGNOSTICS.md').write_text('\n'.join(lines))
    print(quality[quality.month.eq('all')].to_string(index=False))
    print(monthly.groupby('policy').net_nav_pct_delta.agg(['mean','min','max']).to_string())


if __name__=='__main__':run()

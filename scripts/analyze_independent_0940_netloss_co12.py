"""Summarize probability and conditional severity quality; no policy selection."""
import pandas as pd
from scripts.experiment_independent_0940_netloss_co12 import REPORT,read


def run():
    market=read(REPORT/'market_metrics_monthly.csv')
    b1=read(REPORT/'B1_calibration_monthly.csv');b1['phase']='test';b1['method']='calibrated'
    cols=['rows','events','probability_sum','brier_sum','reference_brier_sum','severity_actual_sum',
          'severity_pred_sum','severity_abs_error_sum','reference_severity_abs_error_sum',
          'tail_loss_actual_sum','tail_loss_pred_sum']
    all_=pd.concat([market,b1],ignore_index=True)
    d=all_.groupby(['phase','scope','method'],as_index=False)[cols].sum()
    d['predicted_severe_pct']=100*d.probability_sum/d.rows
    d['observed_severe_pct']=100*d.events/d.rows
    d['brier']=d.brier_sum/d.rows;d['reference_brier']=d.reference_brier_sum/d.rows
    d['predicted_conditional_loss_pct']=100*d.severity_pred_sum/d.events
    d['observed_conditional_loss_pct']=100*d.severity_actual_sum/d.events
    d['conditional_loss_mae_pp']=100*d.severity_abs_error_sum/d.events
    d['reference_conditional_loss_mae_pp']=100*d.reference_severity_abs_error_sum/d.events
    d['predicted_tail_loss_pct']=100*d.tail_loss_pred_sum/d.rows
    d['observed_tail_loss_pct']=100*d.tail_loss_actual_sum/d.rows
    d.to_csv(REPORT/'probability_and_severity_summary.csv',index=False)
    columns=['phase','scope','rows','events','predicted_severe_pct','observed_severe_pct','brier','reference_brier',
             'predicted_conditional_loss_pct','observed_conditional_loss_pct','conditional_loss_mae_pp',
             'reference_conditional_loss_mae_pp','predicted_tail_loss_pct','observed_tail_loss_pct']
    z=d[d.method.eq('calibrated')]
    lines=['# CO12：概率、条件亏损幅度与尾部损失的验证','',
        '参考概率是校准期对应人群的固定事件频率；参考幅度是校准期对应人群大亏的固定加权均值。均为过去数据，不用测试期事后均值。幅度误差只在实际发生净亏>2%的样本中计算。市场/强势子群与B1人群有重叠，不相加。', '',
        '|阶段/人群|样本/大亏数|估计/实际大亏率%|Brier/过去频率Brier|预计/实际条件大亏%|幅度MAE/过去均值MAE（百分点）|预计/实际尾部损失%|',
        '|---|---:|---:|---:|---:|---:|---:|']
    for r in z.itertuples():lines.append(
        f'|{r.phase}/{r.scope}|{r.rows}/{r.events}|{r.predicted_severe_pct:.2f}/{r.observed_severe_pct:.2f}|'
        f'{r.brier:.4f}/{r.reference_brier:.4f}|{r.predicted_conditional_loss_pct:.2f}/{r.observed_conditional_loss_pct:.2f}|'
        f'{r.conditional_loss_mae_pp:.2f}/{r.reference_conditional_loss_mae_pp:.2f}|{r.predicted_tail_loss_pct:.2f}/{r.observed_tail_loss_pct:.2f}|')
    lines += ['', '原始分类分数与校准后的对照见probability_and_severity_summary.csv；逐月漂移见market_metrics_monthly.csv、B1_calibration_monthly.csv。不能仅凭总体平均接近就认定逐股风险排序可靠。', '']
    (REPORT/'MODEL_QUALITY.md').write_text('\n'.join(lines))
    # Added after discovering a zero-volume entry proxy. Common exclusion is a diagnostic,
    # not a new deployable trading rule (that volume is unknown at the decision time).
    from scripts.report_independent_0940_netloss_co12 import policy_metrics
    from scripts.experiment_b1_calibrated_risk_co11 import sized_account
    from scripts.experiment_independent_0940_netloss_co12 import CO8
    cases=read(REPORT/'zero_entry_volume_B1_cases.csv')
    daily=read(REPORT/'daily.csv.gz');marks={day:g for day,g in read(CO8/'valuation_paths.csv.gz').groupby('signal_date')}
    exclusion=[]
    for name,g in daily.groupby('model'):
        a=sized_account(g[~g.signal_date.isin(cases.signal_date)].sort_values('signal_date'),marks)
        exclusion.append(dict(model=name,excluded_dates=','.join(cases.signal_date),**policy_metrics(a)))
    pd.DataFrame(exclusion).to_csv(REPORT/'zero_volume_common_exclusion.csv',index=False)
    print(z[columns].round(4).to_string(index=False))


if __name__=='__main__':run()

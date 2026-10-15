"""Audit and summarize CO11 without choosing a policy using test returns."""
import json
import numpy as np
import pandas as pd
from scripts.experiment_b1_calibrated_risk_co11 import (
    OUT, REPORT, CO10, ARMS, NAMES, BINS, read, sha, save_json, probability, strong, verify_freeze,
)


def run():
    verify_freeze()
    pred=read(OUT/'b1_probabilities.csv.gz'); audit=[]; maxerr=0.
    for path in sorted((OUT/'calibration').glob('*/*/model.json')):
        m=json.loads(path.read_text()); samples=read(path.with_name('samples.csv.gz'))
        assert set(samples.signal_date)==set(m['calibration_dates'])
        assert samples[samples.label_known].label_available_time.max()<min(m['check_dates'])+' 14:40:00'
        assert max(m['check_dates'])<min(m['test_dates'])
        d=pred[pred.arm.eq(m['arm'])&pred.month.eq(m['month'])]
        pg=probability(m['global_calibration'],d.risk_score)
        pc=np.where(strong(d),probability(m['strong_calibration'],d.risk_score),pg)
        err=float(max(np.max(abs(pg-d.p_global)),np.max(abs(pc-d.p_conditional))))
        maxerr=max(maxerr,err)
        audit.append(dict(month=m['month'],arm=m['arm'],model_sha256=sha(path),samples_sha256=sha(path.with_name('samples.csv.gz')),
            rows=len(samples), reload_max_error=err, chronology_pass=True,
            global_fallback=m['global_calibration']['fallback'],strong_fallback=m['strong_calibration']['fallback']))
    assert maxerr<1e-12
    save_json(REPORT/'audit.json',dict(models=len(audit),max_probability_reload_error=maxerr,folds=audit,
        original_B1_account_reproduced=True,base_models_independent=True,calibration_contains_boundary_labels=True,
        new_forward_holdout=False,top5_replacement_not_evaluated=True))
    stats=read(REPORT/'probability_metrics_monthly.csv')
    group=['arm','phase','scope','prediction']
    overall=stats.groupby(group,as_index=False)[['rows','events','sum_probability','squared_error_sum','constant_prior_error_sum']].sum()
    overall['predicted_pct']=overall.sum_probability/overall.rows*100
    overall['observed_pct']=overall.events/overall.rows*100
    overall['brier']=overall.squared_error_sum/overall.rows
    overall['global_prior_brier']=overall.constant_prior_error_sum/overall.rows
    overall.to_csv(REPORT/'probability_summary.csv',index=False)
    bins=read(REPORT/'reliability_bins_monthly.csv').groupby(group+['bin'],as_index=False)[['rows','events','sum_probability']].sum()
    bins['predicted_pct']=bins.sum_probability/bins.rows*100;bins['observed_pct']=bins.events/bins.rows*100
    n=bins.rows;phat=bins.events/n;den=1+1.96**2/n
    center=(phat+1.96**2/(2*n))/den
    delta=1.96*np.sqrt(phat*(1-phat)/n+1.96**2/(4*n*n))/den
    bins['descriptive_wilson_low_pct']=(center-delta)*100
    bins['descriptive_wilson_high_pct']=(center+delta)*100
    bins.to_csv(REPORT/'reliability_bins.csv',index=False)
    daily=read(REPORT/'daily.csv.gz')
    traded=daily[daily.model.eq('baseline')&daily.baseline_trade].copy()
    assert traded[['big_drop','big_rise']].notna().all().all()
    for col in ('big_drop','big_rise'):traded[col]=traded[col].astype(bool)
    traded['bin']=pd.cut(traded.p_conditional,BINS,right=False)
    joint=traded.groupby('bin',observed=True).agg(rows=('signal_date','size'),
        estimated_open_drop=('p_conditional','mean'),opening_drops=('big_drop','sum'),
        opening_big_winners=('big_rise','sum'),actual_open_drop=('big_drop','mean'),
        net0940_mean=('net_main','mean'),loss_rate=('net_main',lambda x:(x<0).mean()),
        net0940_large_losses=('net_main',lambda x:(x<-.02).sum()))
    joint.to_csv(REPORT/'b1_traded_risk_bins.csv')
    traded[traded.p_conditional.ge(.2)].sort_values('net_main')[
        ['signal_date','symbol6','p_conditional','p_global','p_extreme','open_return','net_main']
    ].to_csv(REPORT/'p20_blocked_cases.csv',index=False)
    summary=read(REPORT/'summary.csv');monthly=read(REPORT/'monthly.csv');controls=read(REPORT/'exposure_random_controls.csv')
    def fmt(v):return '—' if pd.isna(v) else f'{v:.2f}'
    lines=['# CO11：校准独立下跌概率后，与 B1 结合','',
        '结论与具体案例见 [INTERPRETATION.md](INTERPRETATION.md)。全部方案如下，不据测试结果改写预设主方案。', '',
        '独立全市场风险树不重训。每月前60日训练树，后20日校准、再20日检查，然后下一月测试。校准包含中间样本，不加大跌权重。主模型是CO10 nonfall_hist，extreme_hist仅用于预设双模型确认。全部使用sklearn 1.7.1。', '',
        '正式比较2025-08至2026-10-13，共288个信号日。B1名单和原成交额轮空固定，14:42 open买入代理、次晨09:40 close卖出；10万元现金复利，按实际金额计算既定交易费用。历史已被反复研究，属于时间外探索，不是新盲测。', '',
        '“下跌概率”特指次晨正式开盘/前日14:40价−1≤−2%的校准估计，与实际09:40退出净亏不是同一个目标。校准不保证其在未来仍准确，尤其B1只有少量事件。', '',
        '## 总体：所有预设方案','',
        '|方案|交易数|净值收益%|结算最大回撤%|分钟回撤%|每笔净收益均值%|避亏/避≥2%净亏/误拦盈利|平均仓位%|',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in summary[summary.scope.eq('all')].itertuples():
        lines.append(f'|{NAMES[r.model]}|{r.trades}|{fmt(r.net_nav_pct)}|{fmt(r.settled_max_drawdown_pct)}|{fmt(r.minute_close_max_drawdown_pct)}|{fmt(r.net_trade_mean_pct)}|{r.losses_avoided}/{r.large_losses_avoided}/{r.profits_missed}|{fmt(r.mean_exposure_on_baseline*100)}|')
    lines += ['', '每笔净收益均值按每次实际出手股票、全额成交口径统计；减仓不会改变该股票收益率。分档仓位对账户的单次影响另见 daily.csv.gz、summary.csv 的 mean_account_return_on_trades_pct。平均仓位以原B1成交日为分母。避亏与误拦只统计完全轮空，半仓不算避开。', '',
        '## 原2026年2–7月区间','', '|方案|笔数|净值收益%|结算最大回撤%|','|---|---:|---:|---:|']
    for r in summary[summary.scope.eq('B1_Feb_Jul_2026')].itertuples():
        lines.append(f'|{NAMES[r.model]}|{r.trades}|{fmt(r.net_nav_pct)}|{fmt(r.settled_max_drawdown_pct)}|')
    lines += ['', '## 逐月净值收益（%）','',
        '按信号日月份归属，10月截至13日；每个方案逐月最大回撤、交易数、避亏及误拦见 monthly.csv。', '',
        '|月份|'+'|'.join(NAMES[k] for k in NAMES)+'|', '|---|'+'---:|'*len(NAMES)]
    pivot=monthly.pivot(index='month',columns='model',values='net_nav_pct')
    for month,row in pivot.iterrows():lines.append('|'+month+'|'+'|'.join(fmt(row[k]) for k in NAMES)+'|')
    lines += ['', '## 概率检验','', '|主模型测试人群|概率类型|样本数|估计大跌率%|实际大跌率%|Brier（越低越好）|', '|---|---|---:|---:|---:|---:|']
    for r in overall[overall.arm.eq('nonfall_hist')&overall.phase.eq('test')].itertuples():
        lines.append(f'|{r.scope}|{r.prediction}|{r.rows}|{fmt(r.predicted_pct)}|{fmt(r.observed_pct)}|{r.brier:.4f}|')
    lines += ['', 'risk_score是未校准加权分数；p_global是全市场校准；p_conditional对当日上涨4%–9%股票采用该子群校准，其余采用全市场校准。Brier的常数对照使用校准期全市场频率，不能把它当作B1最优常数基准。', '',
        '固定概率分箱详见 reliability_bins.csv（含实际发生率）。Wilson区间仅是描述性行级区间，未处理同日股票相关性；不能当独立证据。月度漂移见 probability_metrics_monthly.csv，校准后第二段20日检查结果也完整保留。', '',
        '## 仓位与随机对照','', '|方案|同平均仓位B1收益%|同平均仓位回撤%|同月同仓位分布随机中位收益%|随机收益≥实际比例|', '|---|---:|---:|---:|---:|']
    for r in controls.itertuples():
        lines.append(f'|{NAMES[r.model]}|{fmt(r.constant_nav_pct)}|{fmt(r.constant_dd_pct)}|{fmt(r.random_median_nav_pct)}|{r.fraction_random_at_least_actual:.3f}|')
    lines += ['', '上述是事后覆盖率诊断，不是可部署方案，也不是经过多重尝试修正的p值。每个方案1000次随机置换；配对5/10日块区间见 paired_block_intervals.csv。额外10bp成本见 cost_sensitivity.csv；剔除各方案最大改善贡献日后重算见 largest_contribution_exclusion.csv。', '',
        '## 结合方式与限制','',
        '- 当前可审核方式：原B1选股→原成交额规则→独立风险概率→轮空或减仓。概率模型仍面向全市场，B1身份不进入训练或校准。',
        '- 未来Top5递补必须用同一B1身份的完整候选概率、每个候选当时可见特征及成交价格单独回测；当前本机没有这份完整归档，因此本轮未声称验证递补收益。',
        '- 不能直接以B1达标概率减去开盘下跌概率来排序，两者并不互补，收益幅度也不同。若做收益风险联合排序，需要同一09:40退出口径的收益及损失幅度估计。',
        '- 下一步判断依据应包括概率是否分层、是否优于同仓位对照、是否跨月稳定，以及是否预测到真正的09:40大亏。不能只看轮空后最大回撤下降。',
        '- 原Top1达标/可接受/错误率不随过滤变化，见 top1_classification.json；线上B1未修改。', '',
        '## 文件、复算与归档','',
        'experiment_b1_calibrated_risk_co11.py freeze/run/report；report_b1_calibrated_risk_co11.py 生成本报告并回读校准参数。calibration目录保存30组全市场/子群参数及实际校准样本。所有源文件哈希见 pre_run_freeze.json。原树、全市场原始测试分数及源标签以已冻结CO10包作为复算依赖。',
        'audit.json记录全部30组参数回读误差和时间边界。归档回执另存archive_receipt.json；未核验云端时保持待同步。', '']
    (REPORT/'README.md').write_text('\n'.join(lines))
    print(overall[overall.arm.eq('nonfall_hist')&overall.phase.eq('test')][['scope','prediction','rows','predicted_pct','observed_pct','brier']].to_string(index=False))


if __name__=='__main__':run()

"""Fixed B1 comparison, independent target diagnostics and temporal audit for CO12."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from scripts.experiment_independent_0940_netloss_co12 import (
    OUT,REPORT,CO8,REPORT11,KEY,FEATURES,read,sha,save_json,check_freeze,predict_bundle,
)
from scripts.experiment_b1_calibrated_risk_co11 import sized_account,random_positions
from scripts.experiment_b1_market_trade_gate_co8 import net
from scripts.report_b1_market_trade_gate_co8 import metrics,bootstrap_pair

NAMES={'baseline':'原B1','old_open_p20':'CO11开盘概率20%轮空','new_net_p20':'新09:40概率20%轮空',
       'new_net_size20_40':'新09:40概率20%半仓/40%轮空','new_tail_veto01':'新尾部期望损失≥1%轮空',
       'tail_budget_005':'新尾部损失预算0.5%减仓（主）'}


def policy_exposures(d):
    p=d.p_severe;r=d.tail_expected_loss
    return {
        'baseline':d.baseline_trade.astype(float),
        'old_open_p20':d.old_exposure,
        'new_net_p20':(~p.ge(.2)).astype(float)*d.baseline_trade,
        'new_net_size20_40':np.where(p.ge(.4),0.,np.where(p.ge(.2),.5,1.))*d.baseline_trade,
        'new_tail_veto01':(~r.ge(.01)).astype(float)*d.baseline_trade,
        'tail_budget_005':np.where(r.notna(),np.minimum(1.,.005/r.clip(lower=1e-12)),1.)*d.baseline_trade}


def policy_metrics(d):
    m=metrics(d);b=d.baseline_trade
    partial=b&d.exposure.gt(0)&d.exposure.lt(1-1e-12)
    removed=(1-d.exposure)*b
    m.update(mean_exposure_on_baseline=float(d.loc[b,'exposure'].mean()),
        mean_account_return_per_trade_pct=float(d.loc[d.trade,'account_return'].mean()*100),
        partial_winning_trades=int((partial&d.net_main.gt(0)).sum()),
        partial_losing_trades=int((partial&d.net_main.lt(0)).sum()),
        partial_severe_trades=int((partial&d.net_main.lt(-.02)).sum()),
        positive_return_exposure_removed_pp=float((removed*d.net_main.clip(lower=0)).sum()*100),
        negative_return_exposure_removed_pp=float(-(removed*d.net_main.clip(upper=0)).sum()*100),
        severe_return_exposure_removed_pp=float(-(removed*d.net_main.where(d.net_main.lt(-.02),0)).sum()*100),
        missing_risk_on_baseline=int((b&d.p_severe.isna()).sum()))
    return m


def calibration_stats(d,scope,month):
    known=d[d.label_known].copy();tail=known[known.severe_loss]
    return dict(scope=scope,month=month,rows=len(known),events=int(known.severe_loss.sum()),
        probability_sum=float(known.p_severe.sum()),brier_sum=float(((known.p_severe-known.severe_loss.astype(float))**2).sum()),
        reference_brier_sum=float(((known.probability_reference-known.severe_loss.astype(float))**2).sum()),
        severity_actual_sum=float(tail.severity.sum()),severity_pred_sum=float(tail.conditional_severity.sum()),
        severity_abs_error_sum=float((tail.severity-tail.conditional_severity).abs().sum()),
        reference_severity_abs_error_sum=float((tail.severity-tail.severity_reference).abs().sum()),
        tail_loss_actual_sum=float((-known.net_0940).where(known.severe_loss,0).sum()),
        tail_loss_pred_sum=float(known.tail_expected_loss.sum()))


def run():
    check_freeze()
    dataset=read(OUT/'dataset.csv.gz');b1=read(CO8/'dataset.csv.gz')
    old=read(REPORT11/'daily.csv.gz');old=old[old.model.eq('conditional_p20')]
    scores=[];audits=[];maxerr=0.
    for f in json.loads((OUT/'folds.json').read_text()):
        root=OUT/'models'/f['month'];m=joblib.load(root/'model.joblib')
        assert sha(root/'model.joblib')==f['model_sha256']
        members=read(root/'training_membership.csv.gz')
        expected=dataset[dataset.signal_date.isin(f['train_dates'])&dataset.label_known]
        assert members.sample_id.tolist()==expected.sample_id.tolist()
        assert members.severe_loss.tolist()==expected.severe_loss.tolist()
        weights=1/expected.groupby('signal_date').sample_id.transform('size');weights=weights/weights.mean()
        np.testing.assert_allclose(members.probability_weight,weights,rtol=0,atol=1e-12)
        tailweights=weights[expected.severe_loss];tailweights=tailweights/tailweights.mean()
        np.testing.assert_allclose(members.loc[members.severe_loss,'severity_weight'],tailweights,rtol=0,atol=1e-12)
        assert f['train_label_max']<min(f['calibration_dates'])+' 14:40:00'
        assert f['calibration_label_max']<min(f['check_dates'])+' 14:40:00'
        assert f['check_label_max']<min(f['test_dates'])+' 14:40:00'
        z=read(root/'test.csv.gz');full=dataset[dataset.signal_date.isin(f['test_dates'])]
        assert z.sample_id.tolist()==full.sample_id.tolist()
        back=predict_bundle(m,full)
        error=float(np.max(np.abs(back.to_numpy()-z[back.columns].to_numpy())));maxerr=max(maxerr,error);assert error<1e-12
        scores.append(z)
        audits.append(dict(month=f['month'],training_rows=len(members),events=int(members.severe_loss.sum()),
                           test_rows=len(z),model_sha256=sha(root/'model.joblib'),reload_error=error,chronology_pass=True))
    z=pd.concat(scores,ignore_index=True)
    labels=dataset[['sample_id','label_known','severe_loss','severity','net_0940']]
    joined=z.merge(labels,on='sample_id',validate='one_to_one')
    base=b1[b1.signal_date.isin(old.signal_date)].merge(z.drop(columns='sample_id'),on=KEY,how='left',validate='one_to_one')
    base=base.merge(old[[*KEY,'exposure','nav_after']].rename(columns={'exposure':'old_exposure','nav_after':'old_nav_after'}),
                    on=KEY,how='left',validate='one_to_one').sort_values('signal_date').reset_index(drop=True)
    base['baseline_trade']=base.selected&base.amount_gate.eq(1);base['month']=base.signal_date.str[:7]
    base['net_main']=base.gross_1442.map(net)
    assert base.old_exposure.notna().all()
    matches=base.merge(dataset[[*KEY,'net_0940']],on=KEY,validate='one_to_one')
    assert matches.loc[matches.baseline_trade,'net_0940'].notna().all()
    assert np.max(np.abs(matches.loc[matches.baseline_trade,'net_main']-matches.loc[matches.baseline_trade,'net_0940']))<1e-12
    marks={day:g for day,g in read(CO8/'valuation_paths.csv.gz').groupby('signal_date')}
    summaries=[];monthly=[];daily=[];controls=[];intervals=[];cost=[];excluded=[];accounts={}
    for name,exposure in policy_exposures(base).items():
        d=sized_account(base.assign(exposure=exposure),marks);accounts[name]=d
        if name=='baseline':assert abs(metrics(d)['net_nav_pct']+43.37174055687569)<1e-9
        if name=='old_open_p20':assert np.max(abs(d.nav_after-d.old_nav_after))<1e-8
        for scope,subset in [('all',d),('2025',d[d.month.str.startswith('2025')]),('2026',d[d.month.str.startswith('2026')]),
                             ('B1_Feb_Jul_2026',d[d.month.between('2026-02','2026-07')])]:
            a=sized_account(subset,marks);summaries.append(dict(model=name,scope=scope,**policy_metrics(a)))
        for month,g in d.groupby('month'):monthly.append(dict(model=name,month=month,**policy_metrics(g)))
        daily.append(d.assign(model=name).drop(columns='intratrade_path'))
        changed=sized_account(d,marks,extra=.001)
        cost.append(dict(model=name,extra_cost_bp=10,**policy_metrics(changed)))
        if name!='baseline':
            avg=float(d.loc[d.baseline_trade,'exposure'].mean())
            constant=sized_account(base.assign(exposure=base.baseline_trade*avg),marks)
            random=random_positions(base,d)
            controls.append(dict(model=name,constant_exposure=avg,constant_nav_pct=metrics(constant)['net_nav_pct'],
                constant_dd_pct=metrics(constant)['settled_max_drawdown_pct'],random_median_nav_pct=float(np.median(random[:,0])),
                random_low_nav_pct=float(np.quantile(random[:,0],.025)),random_high_nav_pct=float(np.quantile(random[:,0],.975)),
                fraction_random_at_least_actual=float((random[:,0]>=metrics(d)['net_nav_pct']).mean())))
            for label,comp in [('baseline',accounts['baseline']),('old_open_p20',accounts['old_open_p20']),('constant_exposure',constant)]:
                v=bootstrap_pair(comp,d);v['model']=name;v['comparison']=label;intervals.append(v)
            delta=d.account_return-accounts['baseline'].account_return;day=d.loc[delta.idxmax(),'signal_date']
            for label,other in [('baseline',accounts['baseline']),('overlay',d)]:
                a=sized_account(other[other.signal_date.ne(day)],marks)
                excluded.append(dict(model=name,excluded_day=day,policy=label,**policy_metrics(a)))
    pd.DataFrame(summaries).to_csv(REPORT/'summary.csv',index=False)
    pd.DataFrame(monthly).to_csv(REPORT/'monthly.csv',index=False)
    pd.concat(daily).to_csv(REPORT/'daily.csv.gz',index=False,float_format='%.17g')
    pd.DataFrame(controls).to_csv(REPORT/'exposure_random_controls.csv',index=False)
    pd.concat(intervals).to_csv(REPORT/'paired_block_intervals.csv',index=False)
    pd.DataFrame(cost).to_csv(REPORT/'cost_sensitivity.csv',index=False)
    pd.DataFrame(excluded).to_csv(REPORT/'largest_contribution_exclusion.csv',index=False)
    # Evaluation join only: B1 membership never enters model fitting/calibration.
    selected=joined.merge(base[[*KEY,'baseline_trade']],on=KEY,validate='one_to_one')
    stats=[]
    for scope,subset in [('B1_selected',selected),('B1_traded',selected[selected.baseline_trade])]:
        for month,g in subset.groupby(subset.signal_date.str[:7]):stats.append(calibration_stats(g,scope,month))
    pd.DataFrame(stats).to_csv(REPORT/'B1_calibration_monthly.csv',index=False)
    rows=[]
    for var,edges in [('p_severe',[0,.05,.1,.2,.3,.4,.5,.75,1.000001]),('tail_expected_loss',[0,.0025,.005,.01,.02,.05,1.000001])]:
        for bin_,g in selected[selected.baseline_trade].groupby(pd.cut(selected[selected.baseline_trade][var],edges,right=False),observed=True):
            rows.append(dict(variable=var,bin=str(bin_),**calibration_stats(g,'B1_traded','all')))
    pd.DataFrame(rows).to_csv(REPORT/'B1_risk_bins.csv',index=False)
    baseline=accounts['baseline']
    diagnostic=baseline[baseline.baseline_trade][[*KEY,'net_main','p_severe','conditional_severity','tail_expected_loss']].copy()
    for name,d in accounts.items():diagnostic=diagnostic.merge(d[[*KEY,'exposure']].rename(columns={'exposure':name+'_exposure'}),on=KEY,validate='one_to_one')
    diagnostic.sort_values('net_main').to_csv(REPORT/'B1_trade_cases.csv',index=False)
    known=base[base.selected&base.second_high_return.notna()].second_high_return
    save_json(REPORT/'top1_classification.json',dict(known=len(known),target_pct=float(known.ge(.03-1e-10).mean()*100),
        acceptable_pct=float((known.ge(.01-1e-10)&known.lt(.03-1e-10)).mean()*100),error_pct=float(known.lt(.01-1e-10).mean()*100),same_fixed_picks=True))
    save_json(REPORT/'audit.json',dict(folds=audits,max_reloaded_prediction_error=maxerr,
        B1_baseline_reproduced=True,CO11_p20_daily_nav_reproduced=True,feature_whitelist=FEATURES,
        unknown_future_labels_not_used_to_filter_predictions=True,chronology_all_passed=True,
        baseline_missing_risk=int((base.baseline_trade&base.p_severe.isna()).sum())))
    make_report(pd.DataFrame(summaries),pd.DataFrame(monthly),pd.DataFrame(controls))
    print(pd.DataFrame(summaries).query("scope=='all'")[[ 'model','trades','net_nav_pct','settled_max_drawdown_pct','losses_avoided','large_losses_avoided','profits_missed','partial_winning_trades']].to_string(index=False))


def make_report(summary,monthly,controls):
    def fmt(v):return '—' if pd.isna(v) else f'{v:.2f}'
    lines=['# CO12：独立预测09:40大亏概率及条件幅度，再叠加B1','',
        '结论、局限和具体案例另见 INTERPRETATION.md；全部预设方案如下。', '',
        '测试2025-08至2026-10-13，按整日严格向前：60日独立全市场训练→20日校准→20日检查→下一月测试。B1身份不参加训练/校准，无同日随机划分；模型在校准后不重训。全部sklearn1.7.1；历史已多次研究，仍是探索，不称新盲测。', '',
        '概率目标：14:42一分钟open买入代理，下一交易日09:40一分钟close退出，固定5万元预算扣既定费用后净亏超过2%。幅度为发生这种大亏时预计亏多少；两者相乘是尾部期望损失，不是净收益预测。特征只用14:40及以前，买卖价及未来标签有效性不进入模型。', '',
        '交易账户初始10万元、现金复利、不借款；按实际仓位与资金逐笔计算费用。减仓0.5%是模型预测的大亏贡献预算，不是保证损失上限。后补历史分钟可核对事件时间，不能证明历史当时数据已实时送达或必能成交。', '',
        '分钟标签为结束时间：14:42条目的open是该分钟起始价代理（约14:41）；预测不使用该分钟任何字段。因子事件截止14:40，真实送达及计算延迟仍需前瞻记录。', '',
        '## 总体结果','',
        '|方案|笔数|净值收益%|结算回撤%|分钟回撤%|每笔全额净均值%|完全避亏/其中大亏|完全误拦盈利|减仓盈利笔数|平均仓位%|',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in summary[summary.scope.eq('all')].itertuples():
        lines.append(f'|{NAMES[r.model]}|{r.trades}|{fmt(r.net_nav_pct)}|{fmt(r.settled_max_drawdown_pct)}|{fmt(r.minute_close_max_drawdown_pct)}|{fmt(r.net_trade_mean_pct)}|{r.losses_avoided}/{r.large_losses_avoided}|{r.profits_missed}|{r.partial_winning_trades}|{fmt(r.mean_exposure_on_baseline*100)}|')
    lines += ['', '每笔全额净均值用于比较出手股票，不随减仓机械变化；实际账户每次收益在mean_account_return_per_trade_pct。误杀拆成完全轮空与部分减仓，收益敞口减少见monthly.csv的positive_return_exposure_removed_pp；敞口指标是净收益率×减少仓位的简单和，不冒充复利账户金额。', '',
        '## 原2026年2–7月区间','', '|方案|笔数|净值收益%|结算回撤%|','|---|---:|---:|---:|']
    for r in summary[summary.scope.eq('B1_Feb_Jul_2026')].itertuples():
        lines.append(f'|{NAMES[r.model]}|{r.trades}|{fmt(r.net_nav_pct)}|{fmt(r.settled_max_drawdown_pct)}|')
    lines += ['', '## 逐月：净收益 / 结算最大回撤 / 笔数','',
        '按信号日月份归属，10月仅截至13日；每月回撤在该月持仓路径内计算。', '',
        '|月份|'+'|'.join(NAMES.values())+'|','|---|'+'---:|'*len(NAMES)]
    for month,g in monthly.groupby('month'):
        rows=g.set_index('model');cells=[f'{rows.loc[k,"net_nav_pct"]:.2f}% / {rows.loc[k,"settled_max_drawdown_pct"]:.2f}% / {int(rows.loc[k,"trades"])}' for k in NAMES]
        lines.append('|'+month+'|'+'|'.join(cells)+'|')
    lines += ['', '## 逐月误杀：完全轮空盈利 / 部分减仓盈利','',
        '|月份|'+'|'.join(NAMES.values())+'|','|---|'+'---:|'*len(NAMES)]
    for month,g in monthly.groupby('month'):
        rows=g.set_index('model');lines.append('|'+month+'|'+'|'.join(f'{int(rows.loc[k,"profits_missed"])} / {int(rows.loc[k,"partial_winning_trades"])}' for k in NAMES)+'|')
    lines += ['', '## 同仓位及随机对照','',
        '|方案|同平均仓位B1收益%|回撤%|同月同仓位随机中位收益%|随机不低于实际比例|','|---|---:|---:|---:|---:|']
    for r in controls.itertuples():lines.append(f'|{NAMES[r.model]}|{fmt(r.constant_nav_pct)}|{fmt(r.constant_dd_pct)}|{fmt(r.random_median_nav_pct)}|{r.fraction_random_at_least_actual:.3f}|')
    lines += ['', '随机1000次是事后覆盖率诊断，不是经过多重研究修正的p值。配对5/10日块区间见paired_block_intervals.csv，额外10bp成本见cost_sensitivity.csv，剔除最大改善日见largest_contribution_exclusion.csv。', '',
        '## 模型与文件','',
        '12项因子白名单见DESIGN.md，训练/校准/检查/测试日期及标签成熟时间见models各月fold.json。概率树使用完整分布、无大亏额外权重；幅度树仅拟合大亏，保留原日权重比例。输入标准化/截尾仅在训练段拟合；真实标签不截尾。Platt校准及幅度倍率只在独立20日校准段拟合；上涨4%–9%子群独立校准不使用B1身份。', '',
        '- market_metrics_monthly.csv / market_risk_bins_monthly.csv：全市场、强势子群的概率与幅度检验，含检查期与测试期。',
        '- B1_calibration_monthly.csv / B1_risk_bins.csv：B1的概率及幅度实际表现，不能只看交易收益。',
        '- monthly.csv / daily.csv.gz：全部逐月与逐笔结果，包含回撤、误杀、减仓损失与收益敞口。',
        '- B1_trade_cases.csv：所有原B1交易及各方案仓位，便于核对避开大亏与错过盈利。',
        '- top1_classification.json：原B1名单的达标/可接受/错误率，各方案相同。',
        '- input_audit.json / audit.json：独立全市场端点与B1逐笔价格对齐、全部模型回读、两条冻结对照复现和时间边界。',
        '- pre_run_freeze.json / run_manifest.json：方案、输入代码哈希、计算环境和运行证据。归档状态以archive_receipt.json为准，未云端回读即待同步。', '']
    (REPORT/'README.md').write_text('\n'.join(lines))


if __name__=='__main__':run()

"""Paired monthly comparison of frozen monthly-60 and daily-20 risk overlays."""
import json
import numpy as np
import pandas as pd
from scripts.experiment_daily20_risk_co13 import OUT,REPORT,CO12,REPORT12,CO8,KEY,FEATURES,read,sha,save_json,check_freeze,previous20
from scripts.report_independent_0940_netloss_co12 import policy_exposures,policy_metrics,calibration_stats
from scripts.experiment_b1_calibrated_risk_co11 import sized_account
from scripts.report_b1_market_trade_gate_co8 import bootstrap_pair

POLICIES={'new_net_p20':'概率20%轮空','new_net_size20_40':'概率分档仓位',
          'new_tail_veto01':'尾损1%轮空','tail_budget_005':'尾损预算0.5%减仓（预设主）'}


def run():
    check_freeze();dataset=read(CO12/'dataset.csv.gz');dates=sorted(dataset.signal_date.unique())
    folds=json.loads((OUT/'folds.json').read_text());scores=[];audit=[]
    for f in folds:
        day=f['day'];root=OUT/'models'/day
        for name,h in f['hashes'].items():assert sha(root/name)==h,(day,name)
        assert f['train_dates']==previous20(dates,day)
        assert f['train_label_max']<day+' 14:40:00'
        members=read(root/'training_membership.csv.gz')
        expected=dataset[dataset.signal_date.isin(f['train_dates'])&dataset.label_known&dataset.label_available_time.lt(day+' 14:40:00')]
        assert members.sample_id.tolist()==expected.sample_id.tolist()
        weights=1/expected.groupby('signal_date').sample_id.transform('size');weights/=weights.mean()
        np.testing.assert_allclose(weights,members.probability_weight,atol=1e-12,rtol=0)
        tw=weights[expected.severe_loss];tw/=tw.mean()
        np.testing.assert_allclose(tw,members.loc[members.severe_loss,'severity_weight'],atol=1e-12,rtol=0)
        if not f['formal_test']:continue
        assert f['calibration_dates']==previous20(dates,day)
        assert f['calibration_label_max']<day+' 14:40:00'
        raw=[]
        for source,h in f['calibration_source_hashes'].items():
            path=OUT/'models'/source/'raw_oos.csv.gz';assert sha(path)==h
            # Source model was fitted strictly before each calibration observation.
            sf=json.loads((OUT/'models'/source/'fold.json').read_text())
            assert max(sf['train_dates'])<source<day
            raw.append(read(path))
        cm=read(root/'calibration_membership.csv.gz');expectedraw=pd.concat(raw,ignore_index=True).set_index('sample_id')
        np.testing.assert_allclose(cm[['raw_probability','raw_severity']],expectedraw.loc[cm.sample_id,['raw_probability','raw_severity']],atol=1e-12,rtol=0)
        expectedcal=dataset[dataset.signal_date.isin(f['calibration_dates'])&dataset.label_known&dataset.label_available_time.lt(day+' 14:40:00')]
        assert set(cm.sample_id)==set(expectedcal.sample_id)
        z=read(root/'test.csv.gz');assert z.sample_id.tolist()==dataset.loc[dataset.signal_date.eq(day),'sample_id'].tolist()
        scores.append(z);audit.append({'day':day,'training_rows':len(members),'calibration_rows':len(cm),'chronology_pass':True})
    z=pd.concat(scores,ignore_index=True);z.to_csv(OUT/'predictions.csv.gz',index=False,float_format='%.17g')
    old=read(REPORT12/'daily.csv.gz');base=old[old.model.eq('baseline')].copy()
    riskcols=[c for c in z.columns if c not in KEY]
    base=base.drop(columns=[c for c in riskcols if c in base]).merge(z,on=KEY,how='left',validate='one_to_one').sort_values('signal_date').reset_index(drop=True)
    assert not base.loc[base.baseline_trade,'p_severe'].isna().any()
    marks={day:g for day,g in read(CO8/'valuation_paths.csv.gz').groupby('signal_date')}
    summaries=[];monthly=[];daily=[];accounts={};intervals=[];costs=[];controls=[]
    for name,g in old.groupby('model',sort=False):
        name2=name if name in ['baseline','old_open_p20'] else '60d_'+name
        a=sized_account(g.sort_values('signal_date'),marks)
        np.testing.assert_allclose(a.nav_after,g.sort_values('signal_date').nav_after,atol=1e-8,rtol=0)
        accounts[name2]=a
    for name,exposure in policy_exposures(base).items():
        if name not in POLICIES:continue
        accounts['20d_'+name]=sized_account(base.assign(exposure=exposure),marks)
    for name,a in accounts.items():
        assert a[KEY].reset_index(drop=True).equals(base[KEY])
        for scope,g in [('all',a),('B1_Feb_Jul_2026',a[a.month.between('2026-02','2026-07')])]:
            summaries.append(dict(model=name,scope=scope,**policy_metrics(sized_account(g,marks))))
        for month,g in a.groupby('month'):monthly.append(dict(model=name,month=month,**policy_metrics(g)))
        daily.append(a.assign(model=name).drop(columns='intratrade_path'))
        costs.append(dict(model=name,extra_bp=10,**policy_metrics(sized_account(a,marks,extra=.001))))
        if name.startswith('20d_'):
            other=accounts['60d_'+name[4:]]
            intervals.append(bootstrap_pair(other,a).assign(model=name,comparison='60d_'+name[4:]))
            avg=a.loc[a.baseline_trade,'exposure'].mean()
            constant=sized_account(base.assign(exposure=base.baseline_trade*avg),marks)
            controls.append(dict(model=name,constant_exposure=avg,**policy_metrics(constant)))
            intervals.append(bootstrap_pair(constant,a).assign(model=name,comparison='same_exposure_B1'))
    summary=pd.DataFrame(summaries);months=pd.DataFrame(monthly)
    summary.to_csv(REPORT/'summary.csv',index=False);months.to_csv(REPORT/'monthly.csv',index=False)
    pd.concat(daily).to_csv(REPORT/'daily.csv.gz',index=False,float_format='%.17g')
    pd.concat(intervals).to_csv(REPORT/'paired_block_intervals.csv',index=False)
    pd.DataFrame(costs).to_csv(REPORT/'cost_sensitivity.csv',index=False)
    pd.DataFrame(controls).to_csv(REPORT/'same_exposure_controls.csv',index=False)
    # Counterfactual winners/losses share the unchanged B1 full-size return labels.
    cases=base.loc[base.baseline_trade,[*KEY,'net_main','p_severe','conditional_severity','tail_expected_loss']].copy()
    for name,a in accounts.items():cases=cases.merge(a[[*KEY,'exposure']].rename(columns={'exposure':name}),on=KEY,validate='one_to_one')
    cases.to_csv(REPORT/'B1_trade_cases.csv',index=False)
    joined=z.merge(dataset[['sample_id','label_known','severe_loss','severity','net_0940','signal_return']],on='sample_id',validate='one_to_one')
    selected=joined.merge(base[[*KEY,'baseline_trade']],on=KEY,validate='one_to_one')
    quality=[]
    for scope,g in [('market',joined),('B1_selected',selected),('B1_traded',selected[selected.baseline_trade])]:
        for month,m in g.groupby(g.signal_date.str[:7]):quality.append(calibration_stats(m,scope,month))
        quality.append(calibration_stats(g,scope,'all'))
    pd.DataFrame(quality).to_csv(REPORT/'probability_quality.csv',index=False)
    delta=months[months.model.str.startswith('20d_')].copy();delta['policy']=delta.model.str[4:]
    oldmonths=months[months.model.str.startswith('60d_')].copy();oldmonths['policy']=oldmonths.model.str[4:]
    delta=delta.merge(oldmonths,on=['month','policy'],suffixes=('_20','_60'),validate='one_to_one')
    for c in ['net_nav_pct','settled_max_drawdown_pct','trades','profits_missed','partial_winning_trades','losses_avoided']:
        delta[c+'_delta']=delta[c+'_20']-delta[c+'_60']
    delta.to_csv(REPORT/'monthly_pair_changes.csv',index=False)
    # Preserve the prior execution-proxy diagnostic consistently across all arms.
    exclusions=[]
    for name,a in accounts.items():
        exclusions.append(dict(model=name,excluded_day='2025-11-07',**policy_metrics(sized_account(a[a.signal_date.ne('2025-11-07')],marks))))
    pd.DataFrame(exclusions).to_csv(REPORT/'zero_volume_common_exclusion.csv',index=False)
    save_json(REPORT/'audit.json',dict(days=audit,formal_days=len(audit),warmup_days=len(folds)-len(audit),
        all_chronology_pass=True,all_calibration_scores_historical_OOS=True,all_training_weights_verified=True,
        original_B1_and_all_CO12_daily_NAV_reproduced=True,baseline_missing_risk=0,
        top1_classification=json.loads((REPORT12/'top1_classification.json').read_text())))
    make_report(summary,months,pd.DataFrame(quality))
    print(summary[summary.scope.eq('all')][['model','net_nav_pct','settled_max_drawdown_pct','trades','losses_avoided','profits_missed','partial_winning_trades']].to_string(index=False))


def make_report(summary,months,quality):
    lines=['# CO13：每日20日风险模型与原60日方案逐月对照','',
        'B1选股、原成交额轮空、因子、两预测目标、树参数、sklearn1.7.1及交易费用保持一致。测试2025-08-01至2026-10-13；10月不完整。', '',
        '20日方案每天重训，概率/幅度校准使用此前20日各自当时生成的样本外预测，标签必须早于预测日14:40成熟；60日方案仍为60训练/20校准/20检查后预测下月。比较的是更新方案整体，包含窗口、更新频率和校准时效差别。', '',
        '收益是10万元现金账户扣费复利；14:42结束分钟open作买入代理、次晨09:40 close卖出。回撤为结算净值口径，分钟回撤另存CSV。误杀以原B1全额交易盈利为准，分完全错过与部分减仓。历史重复研究，属于探索。', '',
        '## 全期汇总','', '|方案|净收益%|最大回撤%|笔数|避亏/其中大亏|完全错过盈利|减仓盈利|','|---|---:|---:|---:|---:|---:|---:|']
    names={'baseline':'原B1','old_open_p20':'旧开盘20%轮空'}
    names.update({prefix+k:label+' '+v for prefix,label in [('60d_','60日/月更'),('20d_','20日/日更')] for k,v in POLICIES.items()})
    for r in summary[summary.scope.eq('all')].itertuples():
        lines.append(f'|{names[r.model]}|{r.net_nav_pct:+.2f}|{r.settled_max_drawdown_pct:.2f}|{r.trades}|{r.losses_avoided}/{r.large_losses_avoided}|{r.profits_missed}|{r.partial_winning_trades}|')
    lines+=['','## 按月逐项配对','', '每格为 **净收益% / 最大回撤% / 交易笔数 / 完全错过盈利笔数 / 减仓盈利笔数**。逐月收益由全期账户连续净值计算，不重置每月资金。']
    def cell(r):return f'{r.net_nav_pct:+.2f} / {r.settled_max_drawdown_pct:.2f} / {int(r.trades)} / {int(r.profits_missed)} / {int(r.partial_winning_trades)}'
    for policy,label in POLICIES.items():
        lines+=['',f'### {label}','','|月份|原B1|60日/月更|20日/日更|','|---|---:|---:|---:|']
        for month,g in months.groupby('month'):
            rows=g.set_index('model');lines.append('|'+month+'|'+'|'.join(cell(rows.loc[k]) for k in ['baseline','60d_'+policy,'20d_'+policy])+'|')
    lines+=['','## 原2026年2–7月区间','','|方案|净收益%|最大回撤%|笔数|','|---|---:|---:|---:|']
    for r in summary[summary.scope.eq('B1_Feb_Jul_2026')].itertuples():lines.append(f'|{names[r.model]}|{r.net_nav_pct:+.2f}|{r.settled_max_drawdown_pct:.2f}|{r.trades}|')
    lines+=['','## 20日方案概率与幅度检查','','|样本范围|标签数|预测大亏率%|实际大亏率%|Brier|过去基率Brier|条件幅度MAE百分点|','|---|---:|---:|---:|---:|---:|---:|']
    for r in quality[quality.month.eq('all')].itertuples():
        lines.append(f'|{r.scope}|{r.rows}|{r.probability_sum/r.rows*100:.2f}|{r.events/r.rows*100:.2f}|{r.brier_sum/r.rows:.4f}|{r.reference_brier_sum/r.rows:.4f}|{r.severity_abs_error_sum/r.events*100:.3f}|')
    lines+=['','## 复核与材料','',
        '- `monthly.csv`：全部10个方案逐月收益、回撤、笔数、避亏与误杀。`monthly_pair_changes.csv`：20日减60日逐项变化。',
        '- `daily.csv.gz`、`B1_trade_cases.csv`：B1固定股票、风险概率、条件幅度及各方案仓位。',
        '- `paired_block_intervals.csv`、`same_exposure_controls.csv`、`cost_sensitivity.csv`：日期块配对、同仓位及费用诊断，不用于重新选择阈值。',
        '- `audit.json`、每一天`fold.json`：训练/校准日期、标签成熟时刻、样本名单/权重、源样本外分数哈希；所有模型回读误差见`run_manifest.json`。',
        '- `DESIGN.md`、`pre_run_freeze.json`：运行前冻结；原B1分类指标不变，见审计。训练数据复用CO12冻结文件，不重扫分钟K。',
        '- 完整训练包、DB登记及本地镜像见`archive_receipt.json`；未云端独立回读保持待同步。','']
    (REPORT/'README.md').write_text('\n'.join(lines))


if __name__=='__main__':run()

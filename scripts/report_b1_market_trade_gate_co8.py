"""Account, paired comparisons and read-back audit for the frozen CO8 experiment."""
import json
import math
import platform
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from scripts.experiment_b1_market_trade_gate_co8 import (
    OUT, REPORT, OLD, MARKET, STOCK, read, sha, save_json, settle, net, predict,
)

NAMES = {'amount_baseline':'原成交额轮空','market_overlay':'成交额＋市场',
         'stock_overlay':'成交额＋个股','combined_overlay':'成交额＋市场及个股（主方案）',
         'combined_replacement':'市场及个股替代成交额','recent_pnl_control':'过去40日盈亏开关','cash':'现金'}


def drawdown(values):
    a=np.asarray(values,float)
    return float(-(a/np.maximum.accumulate(a)-1).min()*100)


def account(d,marks,entry='1442',extra=0.,exposure=1.):
    capital=100_000.;rows=[]
    for r in d.to_dict('records'):
        before=capital; path=[capital]; complete=True
        if exposure<=0:r['trade']=False
        if r['trade']:
            gross=r['gross_'+entry]
            if not np.isfinite(gross):raise ValueError(f"Unknown traded exit or entry: {r['signal_date']} {entry}")
            budget=capital*exposure
            buy=budget/1.00026 if budget/1.00026*.00025>=5 else (budget-5)/1.00001
            capital=settle(capital,gross,exposure,extra)
            if entry=='1442':
                m=marks.get(r['signal_date'],pd.DataFrame())
                complete=bool(r.get('valuation_path_complete',False)) and not m.empty
                if complete:
                    sale=buy*m.close.to_numpy()/r['entry_1442']
                    marked=before-budget+sale-np.maximum(sale*.00025,5)-sale*.00051-buy*extra
                    path.extend(marked.tolist())
            path.append(capital)
        r.update(nav_before=before,nav_after=capital,account_return=capital/before-1,
                 intratrade_path=path,mark_complete=complete)
        rows.append(r)
    return pd.DataFrame(rows)


def metrics(d):
    traded=d[d.trade];baseline=d[d.baseline_trade]
    blocked=baseline[~baseline.trade];added=d[d.trade&~d.baseline_trade]
    settled=np.r_[d.nav_before.iloc[0],d.nav_after]
    intra=[d.nav_before.iloc[0]]
    for p in d.intratrade_path:intra.extend(p)
    return {'days':len(d),'trades':int(d.trade.sum()),'baseline_trades':int(d.baseline_trade.sum()),
        'net_nav_pct':float((d.nav_after.iloc[-1]/d.nav_before.iloc[0]-1)*100),
        'settled_max_drawdown_pct':drawdown(settled),
        'minute_close_max_drawdown_pct':drawdown(intra) if d.mark_complete.all() else np.nan,
        'minute_marks_complete':bool(d.mark_complete.all()),
        'net_trade_mean_pct':float(traded.net_main.mean()*100),
        'net_daily_mean_pct':float(d.account_return.mean()*100),
        'losses_avoided':int(blocked.net_main.lt(0).sum()),'large_losses_avoided':int(blocked.net_main.lt(-.02).sum()),
        'profits_missed':int(blocked.net_main.gt(0).sum()),
        'avoided_loss_sum_pp':float(-blocked.loc[blocked.net_main.lt(0),'net_main'].sum()*100),
        'missed_profit_sum_pp':float(blocked.loc[blocked.net_main.gt(0),'net_main'].sum()*100),
        'added_trades':len(added),'added_losses':int(added.net_main.lt(0).sum()),'added_profits':int(added.net_main.gt(0).sum()),
        'retained_large_losses':int(traded.net_main.lt(-.02).sum()),
        'corporate_action_trades':int(traded.corporate_action.fillna(False).astype(bool).sum()),
        'worst_trade_pct':float(traded.net_main.min()*100),
        'worst5pct_mean_pct':float(traded.net_main.nsmallest(max(1,math.ceil(len(traded)*.05))).mean()*100)}


def audit():
    ds=read(OUT/'dataset.csv.gz');frozen=read(OUT/'frozen_picks.csv')
    pd.testing.assert_frame_equal(ds[['signal_date','symbol6']],frozen[['signal_date','symbol6']])
    folds=json.loads((OUT/'folds.json').read_text());maximum=0.;coefficients=[]
    for f in folds:
        folder=OUT/'models'/f['month']/f['arm']
        assert f['training_label_max']<f['validation_start']
        assert f['validation_label_max']<f['test_start']
        assert not set(f['training_days'])&set(f['validation_days'])
        assert not set(f['validation_days'])&set(f['test_days'])
        model=joblib.load(folder/'model.joblib');assert sha(folder/'model.joblib')==f['model_sha256']
        for col,coef in zip(model['cols'],model['model'].coef_):
            coefficients.append({'month':f['month'],'arm':f['arm'],'feature':col,
                                 'coefficient_per_training_std':float(coef),'training_mean':float(model['mean'][col]),
                                 'training_std':float(model['std'][col]),'clip_low':float(model['lo'][col]),
                                 'clip_high':float(model['hi'][col]),'missing_fill':float(model['median'][col]),
                                 'model_intercept':float(model['model'].intercept_)})
        for phase in ('training','validation','test'):
            p=folder/(phase+'.csv.gz');assert sha(p)==f[phase+'_sha256']
            frame=read(p)
            if phase!='training':maximum=max(maximum,float(np.max(np.abs(predict(model,frame)-frame.score))))
            else:assert not frame.corporate_action.fillna(False).astype(bool).any()
            original=ds.set_index('signal_date').reindex(frame.signal_date)
            assert (original.symbol6.fillna('').to_numpy()==frame.symbol6.fillna('').to_numpy()).all()
            assert np.allclose(frame.net_main,frame.gross_1442.map(net),equal_nan=True)
    assert maximum<1e-12
    late=read(OLD/'daily.csv');late=late[late.model.eq('B1_171')]
    matched=ds.merge(late,on='signal_date',suffixes=('','_old'),validate='one_to_one')
    assert (matched.symbol6.fillna('')==matched.symbol6_old.fillna('')).all()
    assert np.allclose(matched.amount_gate,matched.amount_gate_old,equal_nan=True)
    reconciliation=read(REPORT/'input_reconciliation.csv')
    dif=reconciliation.reference_difference.dropna()
    sources=read(REPORT/'source_availability.csv')
    result={'fixed_B1_days_matched':len(matched),'model_bundles':len(folds),'max_reloaded_prediction_error':maximum,
            'chronology_all_passed':True,'fixed_model_after_validation':True,
            'source_reference_return_max_difference':float(dif.abs().max()),
            'source_return_differences_over_1bp':int(dif.abs().gt(.0001).sum()),
            'incomplete_selected_feature_rows':int((ds.selected&~ds.feature_complete).sum()),
            'incomplete_selected_valuation_paths':int((ds.selected&~ds.valuation_path_complete.fillna(False).astype(bool)).sum()),
            'corporate_action_selected_rows':int(ds.corporate_action.fillna(False).sum()),
            'database_days':int(sources.kind.eq('database_finalized_exact_minutes').sum()),
            'final_archive_does_not_prove_live_arrival':True}
    save_json(REPORT/'audit.json',result)
    pd.DataFrame(coefficients).to_csv(REPORT/'model_coefficients.csv',index=False)
    return result


def bootstrap_pair(base,primary):
    a=primary.account_return.to_numpy()-base.account_return.to_numpy();n=len(a)
    rng=np.random.default_rng(81015);rows=[]
    for block in (5,10):
        means=[]
        for _ in range(4000):
            starts=rng.integers(0,n,size=math.ceil(n/block))
            ids=np.concatenate([(s+np.arange(block))%n for s in starts])[:n]
            means.append(a[ids].mean()*100)
        rows.append({'block_days':block,'paired_daily_delta_pp':float(a.mean()*100),
                     'ci95_low_pp':float(np.quantile(means,.025)),'ci95_high_pp':float(np.quantile(means,.975)),
                     'bootstrap_replicates':4000,'note':'exploratory paired block interval; repeated research not corrected'})
    return pd.DataFrame(rows)


def random_reference(base,primary):
    rng=np.random.default_rng(81016);rows=[]
    indices={m:g.index.to_numpy() for m,g in base[base.trade].groupby('month')}
    counts=primary.groupby('month').trade.sum()
    gross=base.gross_1442.to_numpy()
    for repetition in range(2000):
        mask=np.zeros(len(base),bool)
        for month,ids in indices.items():
            n=int(counts.get(month,0));mask[rng.choice(ids,size=n,replace=False)]=True
        cap=100_000.;path=[cap]
        for r,t in zip(gross,mask):
            if t:cap=settle(cap,r)
            path.append(cap)
        blocked=base.trade.to_numpy()&~mask
        rows.append({'iteration':repetition,'net_nav_pct':(cap/100_000-1)*100,'settled_max_drawdown_pct':drawdown(path),
                     'losses_avoided':int((blocked&base.net_main.lt(0).to_numpy()).sum()),
                     'profits_missed':int((blocked&base.net_main.gt(0).to_numpy()).sum())})
    return pd.DataFrame(rows)


def run():
    pred=read(OUT/'predictions.csv.gz');pred['month']=pred.signal_date.str[:7]
    marks={d:g for d,g in read(OUT/'valuation_paths.csv.gz').groupby('signal_date')}
    base=pred[pred.arm.eq('combined')].sort_values('signal_date').reset_index(drop=True)
    base['baseline_trade']=base.selected&base.amount_gate.eq(1)
    variants={}
    variants['amount_baseline']=base.assign(trade=base.baseline_trade)
    for arm in ('market','stock','combined'):
        d=pred[pred.arm.eq(arm)].sort_values('signal_date').reset_index(drop=True)
        d['baseline_trade']=d.selected&d.amount_gate.eq(1)
        d['trade']=d.baseline_trade&d.score.ge(d.threshold_overlay)
        d.loc[d.threshold_overlay.eq(-999),'trade']=d.baseline_trade
        variants[arm+'_overlay']=d
    variants['combined_replacement']=base.assign(trade=base.selected&base.score.ge(base.threshold_replacement))
    grid=read(REPORT/'threshold_validation.csv')
    plain=grid[grid.arm.eq('combined')&grid['mode'].eq('overlay')&grid.threshold.eq(-999)].set_index('month').nav_return
    variants['recent_pnl_control']=base.assign(trade=base.baseline_trade&base.month.map(plain).gt(0))
    variants['cash']=base.assign(trade=False)
    summaries=[];monthly=[];sensitivity=[];daily=[];accounts={}
    for name,d in variants.items():
        d=account(d,marks);d['model']=name;accounts[name]=d;daily.append(d.drop(columns='intratrade_path'))
        for month,g in d.groupby('month'):
            monthly.append(dict(model=name,month=month,**metrics(g)))
        for scope,sub in [('all',d),('2025',d[d.month.str.startswith('2025')]),('2026',d[d.month.str.startswith('2026')]),
                          ('full_months',d[d.month.lt('2026-10')]),('B1_Feb_Jul_2026',d[d.month.between('2026-02','2026-07')])]:
            sub=account(sub,marks)
            ms=[metrics(g)['net_nav_pct'] for _,g in sub.groupby('month')]
            summaries.append(dict(model=name,scope=scope,months=len(ms),positive_months=sum(v>0 for v in ms),**metrics(sub)))
        for entry,cost in [('reference',0.),('1441',0.),('1442',0.),('1442',.001)]:
            s=account(d,marks,entry,cost)
            s['net_main']=s['gross_'+entry].map(lambda r:settle(50_000.,r,extra_cost=cost)/50_000.-1 if np.isfinite(r) else np.nan)
            sensitivity.append(dict(model=name,entry=entry,extra_cost_bp=cost*10000,**metrics(s)))
    all_daily=pd.concat(daily,ignore_index=True);all_daily.to_csv(REPORT/'daily.csv.gz',index=False,float_format='%.17g')
    summary=pd.DataFrame(summaries);summary.to_csv(REPORT/'summary.csv',index=False)
    months=pd.DataFrame(monthly);months.to_csv(REPORT/'monthly.csv',index=False)
    pd.DataFrame(sensitivity).to_csv(REPORT/'execution_sensitivity.csv',index=False)
    b=accounts['amount_baseline'];p=accounts['combined_overlay']
    boot=bootstrap_pair(b,p);boot.to_csv(REPORT/'paired_block_intervals.csv',index=False)
    rand=random_reference(b,p);rand.to_csv(OUT/'random_monthly_matched.csv.gz',index=False)
    frac=float(p.trade.sum()/b.trade.sum());reduced=account(b,marks,exposure=frac)
    save_json(REPORT/'exposure_and_random_controls.json',{
        'primary_exposure_fraction':frac,'constant_fraction_baseline':metrics(reduced),
        'random_same_month_same_trade_counts':{
            'replicates':len(rand),'median_nav_pct':float(rand.net_nav_pct.median()),
            'nav_95_range_pct':rand.net_nav_pct.quantile([.025,.975]).tolist(),
            'fraction_random_nav_at_least_primary':float(rand.net_nav_pct.ge(metrics(p)['net_nav_pct']).mean()),
            'mean_losses_avoided':float(rand.losses_avoided.mean()),'mean_profits_missed':float(rand.profits_missed.mean()),
            'note':'ex-post coverage-matched diagnostic, not a deployable policy or corrected significance test'}})
    result=audit()
    base.groupby('month').agg(days=('signal_date','size'),market_min=('market_n','min'),
                              market_median=('market_n','median'),market_max=('market_n','max'),
                              complete_factor_rows=('feature_complete','sum')).to_csv(REPORT/'coverage_monthly.csv')
    definitions={
        'market_breadth':('14:40','沪深有效14:40价/当日前收−1大于0的比例'),
        'market_median':('14:40','上述横截面收益中位数'),
        'market_iqr':('14:40','上述横截面收益75%分位−25%分位'),
        'lag5_market_breadth':('T−1收盘','前5个市场日有效成交股票的上涨比例均值'),
        'relative_turnover5':('14:40','截至14:40分钟股数/昨日股本代理×100/前5日平均换手率'),
        'last5_return':('14:40','14:40 close/14:36 open−1'),
        'last30_position':('14:40','(14:40 close−末30根最低low)/(末30根最高high−最低low)，平盘0.5'),
        'prior5_return':('T−1收盘','此前5个市场日close/preclose连乘−1'),
        'open_gap':('T开盘','09:31分钟open/当日前收−1')}
    pd.DataFrame([{'feature':k,'latest_input':v[0],'formula':v[1]} for k,v in definitions.items()]).to_csv(REPORT/'feature_dictionary.csv',index=False)
    # Fixed Top1 classification is independent of all trade gates.
    known=base[base.selected&base.second_high_return.notna()].second_high_return
    save_json(REPORT/'top1_classification.json',{'known_top1':len(known),
        'target_pct':float(known.ge(.03-1e-10).mean()*100),
        'acceptable_pct':float((known.ge(.01-1e-10)&known.lt(.03-1e-10)).mean()*100),
        'error_pct':float(known.lt(.01-1e-10).mean()*100),'same_picks_for_every_gate':True,
        'label_reference':'second morning high relative to frozen 14:40 signal price; before any gate'})
    make_report(summary,months,pd.DataFrame(sensitivity),boot,result)
    print(summary[summary.scope.eq('all')][['model','trades','net_nav_pct','settled_max_drawdown_pct','losses_avoided','profits_missed']].to_string(index=False))


def make_report(summary,months,sensitivity,boot,audit_result):
    def fmt(x):return '—' if pd.isna(x) else f'{x:.2f}'
    main=summary[summary.scope.eq('all')]
    lines=['# CO8：固定 B1 的市场环境与个股状态交易判断','',
        '本轮固定 B1 名单，不重训第一层、不换股、不递补。主方案为原成交额门槛再叠加市场＋个股净收益评分；全部预设对照公开。历史已反复研究，本轮结果是时间外探索。','',
        '主口径：14:40 完结分钟生成输入，14:42 open 买入代理，次晨09:40 close 退出；初始10万元、全额现金、不借款，按金额扣双边佣金最低5元、过户费及卖方印花税。未计滑点的主结果和额外10bp情景分列。','',
        '|方案|交易笔数|净值收益%|结算最大回撤%|持仓分钟收盘回撤%|避开亏损|错过盈利|',
        '|---|---:|---:|---:|---:|---:|---:|']
    for r in main.itertuples():lines.append(f'|{NAMES[r.model]}|{r.trades}|{fmt(r.net_nav_pct)}|{fmt(r.settled_max_drawdown_pct)}|{fmt(r.minute_close_max_drawdown_pct)}|{r.losses_avoided}|{r.profits_missed}|')
    lines += ['', '## 每月主方案与原成交额规则','',
              '按信号日月份归属；10月仅截至13日。回撤为本月信号对应持仓路径内的回撤，包含下一交易日结算，不是单笔收益简单累加。', '',
              '|月份|原规则净收益%|主方案净收益%|原/新结算回撤%|原/新笔数|避亏/误拦盈利|',
              '|---|---:|---:|---:|---:|---:|']
    b=months[months.model.eq('amount_baseline')].set_index('month')
    for r in months[months.model.eq('combined_overlay')].itertuples():
        a=b.loc[r.month]
        lines.append(f'|{r.month}|{fmt(a.net_nav_pct)}|{fmt(r.net_nav_pct)}|{fmt(a.settled_max_drawdown_pct)} / {fmt(r.settled_max_drawdown_pct)}|{int(a.trades)} / {r.trades}|{r.losses_avoided} / {r.profits_missed}|')
    lines += ['', '## 训练、可见性和核验','',
        '- 每月此前100个市场日，前60日拟合、后40日只选门槛，模型和门槛固定预测下一月。验证后不重拟合。所有训练标签成熟于验证首日14:40前，验证标签成熟于测试首日14:40前。',
        '- 固定四项市场变量和五项个股变量；当日日线close/high/low等不进入特征，只使用滞后历史及T日已知preclose。市场快照只到14:40，个股路径严格截到14:40。',
        '- 过去60日只拟合B1真实历史折外Top1；使用固定alpha=100的岭回归。±10%截断的净收益目标是稳健评分，不能当作已校准的真实期望收益。',
        '- 预处理仅在训练内学习；不新增过滤、−0.5%/0%/0.5%评分门槛和现金均事先规定。验证以净值增长择门槛并限制回撤；交易样本不足15笔保持原规则。见[冻结设计](DESIGN.md)。',
        f'- 原CO7 {audit_result["fixed_B1_days_matched"]}天股票和原成交额门槛逐行一致。{audit_result["model_bundles"]}个模型重新加载，最大预测误差{audit_result["max_reloaded_prediction_error"]:.3g}。',
        f'- 与旧原始收益记录有{audit_result["source_return_differences_over_1bp"]}行相差超过1bp，全部见[input_reconciliation.csv](input_reconciliation.csv)。这类来源差异与门槛增益分开核查。',
        '- 最终态分钟档案不能证明历史实时送达；数据库fetch_time另存[source_availability.csv](source_availability.csv)。14:42价格提供延迟代理，仍未证明涨跌停排队、整手、盘口容量和真实成交。',
        '- 未来除权标志仅用于历史标签成熟后的训练审计，不作为当日特征和测试交易筛选。冻结B1完整原始范围与CO7扩展范围沿用原标注。',
        '- 结算回撤与持仓分钟close估值回撤分别列出；后者不包含分钟内高低及买卖盘口。少交易造成的机械降风险另与等平均仓位和逐月等笔数随机轮空对照。','',
        '## 配对不确定性与执行敏感性','',
        '|按交易日分块|主方案减基线的日均净收益差pp|95%区间pp|','|---|---:|---:|']
    for r in boot.itertuples():lines.append(f'|{r.block_days}|{r.paired_daily_delta_pp:.4f}|[{r.ci95_low_pp:.4f}, {r.ci95_high_pp:.4f}]|')
    lines += ['', '区间来自固定策略同日收益差的4,000次配对分块重抽样，保留短期时间相关性；没有校正此前全部历史模型搜索，不能当作独立盲测显著性。','',
              '|执行价格|额外总摩擦bp|原规则净值%|主方案净值%|','|---|---:|---:|---:|']
    for entry,cost in [('reference',0.),('1441',0.),('1442',0.),('1442',10.)]:
        s=sensitivity[sensitivity.entry.eq(entry)&sensitivity.extra_cost_bp.eq(cost)].set_index('model')
        lines.append(f'|{entry}|{cost:g}|{fmt(s.loc["amount_baseline","net_nav_pct"])}|{fmt(s.loc["combined_overlay","net_nav_pct"])}|')
    lines += ['', '完整记录：[逐月](monthly.csv)、[总体及分年](summary.csv)、[执行敏感性](execution_sensitivity.csv)、[逐月验证选阈值](threshold_validation.csv)、[同等仓位及随机轮空](exposure_and_random_controls.json)、[审计](audit.json)。',
              '', '本轮训练样本、每折划分、模型、全部验证与测试评分、原始输入哈希在 `outputs/stock_automl/b1_market_trade_gate_co8`。不因本轮历史结果自动修改线上策略。']
    (REPORT/'README.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':run()

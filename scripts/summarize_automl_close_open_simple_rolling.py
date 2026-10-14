"""Readable CO5 tables, monthly plot, and label-to-realized-return diagnostics."""
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scripts.experiment_automl_close_open_simple_rolling import OUT, REPORT, SOURCE

NAMES={
 'B1_frozen_original171':'原B1（冻结七因子）',
 'tree171_seven7_samepool':'同池七因子树',
 'logit_seven7':'逻辑回归7因子','logit_compact12':'逻辑回归滚动12因子','logit_broad76':'逻辑回归76因子',
 'ridge_seven7':'岭回归7因子','ridge_compact12':'岭回归滚动12因子','ridge_broad76':'岭回归76因子',
}

def table(d,columns):
    rows=[]
    for row in d[list(columns)].itertuples(index=False,name=None):
        cells=[]
        for x in row:
            if pd.isna(x):cells.append('—')
            elif isinstance(x,(float,np.floating)):cells.append(f'{x:.3f}')
            else:cells.append(str(x))
        rows.append('| '+' | '.join(cells)+' |')
    return '| '+' | '.join(columns.values())+' |\n| '+' | '.join(['---']*len(columns))+' |\n'+'\n'.join(rows)


def run():
    summary=pd.read_csv(REPORT/'summary.csv')
    monthly=pd.read_csv(REPORT/'monthly.csv')
    main=summary[summary.model.isin(NAMES)&summary.rule.eq('amount_gate')].copy()
    main['name']=main.model.map(NAMES)
    daily=pd.read_csv(OUT/'daily_execution.csv.gz',dtype={'symbol6':str},float_precision='round_trip')
    panel=pd.read_csv(SOURCE/'factor_panel.csv.gz',dtype={'symbol6':str},float_precision='round_trip')
    pool=panel.groupby('signal_date').agg(pool_strong=('strong','mean'),pool_weak=('weak','mean'),pool_gross=('close40_return','mean'))
    bridge=[];lift=[]
    for (model,rule),g in daily[daily.model.isin(NAMES)].groupby(['model','rule']):
        traded=g[g.trade & g.net_return.notna()].copy()
        traded['outcome']=np.where(traded.strong.eq(1),'strong',np.where(traded.weak.eq(1),'weak','middle'))
        for label,t in traded.groupby('outcome'):
            bridge.append({'model':model,'rule':rule,'outcome':label,'trades':len(t),
                           'net_mean_pct':t.net_return.mean()*100,'net_sum_pct':t.net_return.sum()*100,
                           'net_negative_pct':t.net_return.lt(0).mean()*100})
        merged=traded.merge(pool,left_on='signal_date',right_index=True)
        lift.append({'model':model,'rule':rule,'trades':len(merged),
                     'strong_lift_pp':(merged.strong-merged.pool_strong).mean()*100,
                     'weak_lift_pp':(merged.weak-merged.pool_weak).mean()*100,
                     'gross_lift_pp':(merged.close40_return-merged.pool_gross).mean()*100})
    pd.DataFrame(bridge).to_csv(REPORT/'label_return_bridge.csv',index=False)
    pd.DataFrame(lift).to_csv(REPORT/'selection_lift.csv',index=False)
    pool.assign(month=pool.index.str[:7]).groupby('month').mean().to_csv(REPORT/'pool_monthly.csv')
    monthly_main=monthly[monthly.model.isin(NAMES)&monthly.rule.eq('amount_gate')].copy()
    monthly_main['name']=monthly_main.model.map(NAMES)
    fields={'month':'月份','name':'模型','days':'信号日数','trades':'已知交易数',
            'unknown_gates':'成交额未知数','unknown_trade_outcomes':'交易结果未知数',
            'net_trade_mean_pct':'每笔净收益均值_%','net_sum_pct':'固定本金净收益合计_%',
            'strong_pct':'强势命中率_%','weak_pct':'弱势命中率_%','loss2_pct':'净亏损至少2%占比_%',
            'loss5_pct':'净亏损至少5%占比_%','worst_trade_pct':'最差单笔净收益_%',
            'ex_top_trade_mean_pct':'删除本月最好一笔后每笔均值_%',
            'ex_top_sum_pct':'删除本月最好一笔后收益合计_%'}
    monthly_main[list(fields)].rename(columns=fields).to_csv(REPORT/'月度对照.csv',index=False,encoding='utf-8-sig')
    plt.rcParams['font.family']='Arial Unicode MS'
    plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(2,1,figsize=(16,10),layout='constrained')
    cmap=plt.get_cmap('RdBu_r').copy();cmap.set_bad('#e7e9ed')
    for ax,metric,title in zip(axes,['net_trade_mean_pct','ex_top_trade_mean_pct'],
                              ['逐月每笔净收益（%）','各月删掉收益最高的一笔后，每笔净收益（%）']):
        pivot=monthly_main.pivot(index='model',columns='month',values=metric).reindex(NAMES)
        values=pivot.to_numpy(float)
        im=ax.imshow(values,cmap=cmap,vmin=-1.5,vmax=1.5,aspect='auto')
        ax.set_yticks(range(len(pivot)),[NAMES[n] for n in pivot.index],fontsize=11)
        ax.set_xticks(range(len(pivot.columns)),[v[2:] for v in pivot.columns],fontsize=10)
        for i in range(values.shape[0]):
            for j in range(values.shape[1]):
                v=values[i,j]
                ax.text(j,i,'—' if not np.isfinite(v) else f'{v:+.2f}',ha='center',va='center',fontsize=9,
                        color='white' if np.isfinite(v) and abs(v)>1 else '#182230')
        ax.set_title(title,loc='left',fontsize=15,pad=12)
    fig.suptitle('收盘开盘 · CO5 滚动对照\n成交额门槛通过后，已扣研究设定费用；2026-10 仅4个信号日',fontsize=17)
    fig.savefig(REPORT/'monthly_net_and_removed_top.png',dpi=160)
    plt.close(fig)
    common=main[main.scope.eq('B1_common_dates')].set_index('model').reindex(NAMES).reset_index()
    full=main[main.scope.eq('all')&main.model.ne('B1_frozen_original171')].copy()
    stress=main[main.scope.eq('all')].set_index('model').reindex(NAMES).reset_index()
    before=summary[summary.model.isin(NAMES)&summary.rule.eq('no_gate')&summary.scope.eq('all')].copy()
    before['name']=before.model.map(NAMES)
    monthly_wide=monthly_main.pivot(index='month',columns='name',values='net_trade_mean_pct').reset_index()
    perturb=pd.read_csv(REPORT/'perturbation.csv').query('scope == "all"').copy();perturb['name']=perturb.model.map(NAMES)
    selected=pd.read_csv(REPORT/'factor_selection_by_month.csv').query('selected')
    frequency=selected.groupby('factor').size().sort_values(ascending=False).reset_index(name='selected_months')
    frequency.to_csv(REPORT/'compact_factor_frequency.csv',index=False)
    text='''# CO5：简单模型滚动训练，与原 1.7.1 七因子对照

更新：2026-10-14。**本轮没有找到稳定盈利、可以替换 B1 的新方案。** 扩展因子确实降低了部分风险或改善部分命中，但这两者尚未同时转化为跨月净盈利。保留原 B1 资产，本轮结果作为后续研究证据。

## 先解释因子结论

CO4 的 16 个“通过因子”是严格双阶段验证后的强证据组，不能解释为其余 60 个无用。弱单变量关联、条件关联和业务合理性是不同问题。本轮做了：七因子控制、每月重选 12 因子、全部 76 因子。市值、绝对/相对换手、三个 0/1 因子、历史走势、全日和尾盘路径都在 76 因子模型中；未因单变量不显著删除。

CO4 最终名单利用了 2026 年表现，直接用它回测 2025–26 年会混入选择信息。因此本轮紧凑组只根据每轮训练段的日内强弱差重新选 12 个，接受正、负两个方向，也不设显著性硬门槛。共有多少月选中哪些变量见 [逐月因子](factor_selection_by_month.csv) 与 [选择频次](compact_factor_frequency.csv)。它不是同一个固定 12 因子组合。

## 实际执行

- 数据：CO4 冻结的 155,811 行、76 个因子；信号 14:40，收益为次晨 09:40 收盘价。测试 2025-07 至 2026-10-13，共 311 日、16 个月；10月仅4日，不与完整月份等量解释。
- 每个月：此前100个市场交易日，前80日训练与选因子、后20日选正则参数；再用这100日重拟合，整月固定。发现、验证和测试信号日互不重叠，边界前一日标签若已于当天09:40成熟可使用。缩放、截尾、填补均只在对应拟合段学习。
- 逻辑回归：弱/中/强三类，按 P(强)−P(弱) 排序。岭回归：预测封顶盈利、加重超过2%亏损惩罚后的净收益效用。6个方案均公布，不根据测试结果只保留赢家。
- 原B1保留真实冻结输出：2026-02-02至07-30的118日，115日有Top1、3日轮空。门槛通过91笔、未通过20笔，另4笔因历史成交额不足20日无法核定，保留未知。
- 同池七因子树采用原1.7.1参数、前20日每日滚动。CO4股本/均线构造与原输入不完全相同，**同配置控制不是B1复现**。简单模型的100日/月度更新与树的20日/每日更新也不同，不能把差异全部归因于模型类型。
- 每个模型先选第一名，再应用原个股成交额门槛，不递补。完整结果同时保留不使用门槛的版本。

## 共同日期对照：原B1可核实的2026年2–7月

以下强势命中率与亏损率均以门槛通过、结果已知的交易为分母。强势为次晨十分钟次高价 ≥ 买价×1.03；“净亏≥2%”为实际09:40净亏损，二者并非互补。

'''
    text+=table(common,{'name':'模型','trades':'笔数','net_trade_mean_pct':'每笔净收益%','strong_pct':'强势命中%','weak_pct':'弱势命中%','loss2_pct':'净亏≥2%占比','ex_month_top_trade_mean_pct':'每月删最好一笔后%','unknown_gates':'门槛未知数'})
    text+='''

原B1本段仍优于新模型。但每月删掉最好一笔后，B1由 +0.470%/笔变为 −0.120%/笔；再去掉最佳月份为 −0.359%/笔。仅去掉最佳月份（6月）还剩 +0.229%/笔。结论是**收益依赖月内少数大盈利单，不能只凭原总收益判断稳定性**。这项删单检验是刻意苛刻的集中度压力测试，不能单独证明策略来自运气。

[共同日期配对区间](paired_B1.csv)以同一天的净收益差、5交易日区块自助法计算；门槛或收益未知时两侧共同剔除。多数组合的95%区间跨零，不能声称新模型统计上稳定胜出；区间未对多次研究/多模型比较作校正。

## 较长区间：2025年7月—2026年10月13日

此表只含同一311日上的新样本方案。原B1缺这一完整区间的同口径原始冻结对照，不用新重训结果冒充。

'''
    full['positive_fraction']=full.positive_months.astype(str)+'/'+full.months.astype(str)
    text+=table(full,{'name':'模型','trades':'笔数','positive_fraction':'正收益月份','net_trade_mean_pct':'每笔净收益%','strong_pct':'强势命中%','loss2_pct':'净亏≥2%占比','loss5_pct':'净亏≥5%占比','worst_trade_pct':'最差一笔%','cvar5_pct':'最差5%均值%'})
    text+='''

76因子岭回归10/16个月为正，但总体 −0.073%/笔；只看15个完整月份仍为 −0.057%/笔（10/15个月为正），并非仅受10月影响。它的强势命中仅6.09%，大量盈利单太小，无法覆盖较少但较大的亏损。逻辑回归提高强势命中时，下跌尾部代价仍高。

## 逐月每笔净收益（%）

均应用成交额门槛、扣除费用，按信号日归属月份。—表示缺少该模型的原始对照，不是0。逐月笔数、收益合计、强弱命中、2%/5%亏损、最差单笔、删单后的数值见 [中文月度明细](月度对照.csv)。

'''
    text+=table(monthly_wide,{c:('月份' if c=='month' else c) for c in monthly_wide})
    text+='\n\n![逐月净收益及每月删去最好一笔](monthly_net_and_removed_top.png)\n\n## 极端盈利剔除\n\n每个模型独立删除净收益合计最高的月份；每月只删除收益最高的一笔，不删除所有并列最大值。下表都是剩余交易每笔净收益均值，单位%；原B1为2–7月，其余为完整311日。\n\n'
    text+=table(stress,{'name':'模型','best_month':'删除的最佳月','net_trade_mean_pct':'原始','ex_best_month_trade_mean_pct':'去最佳月','ex_month_top_trade_mean_pct':'每月去最好一笔','ex_both_trade_mean_pct':'同时去两者'})
    text+='''

所有新模型在上述三种压力条件下都未转正。`summary.csv` 同时给出按原日历将删单视作轮空的每日均值、月等权每日均值，避免只看删单后交易均值。未把多月净收益合计当作复利收益。

## 训练扰动

因子、超参固定，按日期+股票代码的固定哈希删除约2%拟合行；不从扰动模型中择优。这里是拟合敏感性，未包含重新选因子、调参、改版本或分钟噪声。下表为311日、成交额规则后每日均值，单位%；每日均值含明确轮空0、未知保留空值，两个版本未知日期有差异时另见配对差。

'''
    text+=table(perturb,{'name':'模型','changed_top1_days':'换股天数','top1_change_pct':'换股比例%','base_net_daily_pct':'原每日净均值%','perturbed_net_daily_pct':'扰动每日净均值%','paired_daily_delta_pp':'共同已知日差值pp'})
    text+='''

简单方案换股约1.9%–6.8%，树为39.2%。这支持继续研究“较长历史+正则化”的稳定拟合方向，但收益稳定性尚未过关；不能用更稳定的排名代替稳定盈利。

## 门槛、费用与业务目标

原成交额门槛对原B1有帮助；对本轮多数新模型并未改善净收益。[总表](summary.csv)中同时给出 `no_gate` 与 `amount_gate`，本轮没有看完测试结果后改门槛。未知门槛不假装通过或用0填结果；后续上线若遇到信息不足，可制定明确轮空政策，需单独记录为策略选择。

收益按每次买入5万元名义本金计算；佣金双边万2.5、每边最低5元；卖出印花税万5、双边过户万0.1。不额外叠加已包含于佣金的交易费。费率不变且价格不动时约0.102%往返成本。费用依据与账户佣金假设须区分：[财政部印花税减半公告](https://m.mof.gov.cn/czxw/202308/t20230827_3904226.htm)。另给双边合计10bp滑点情景；76因子岭回归由 −0.073%/笔降至约 −0.173%/笔。

76因子逻辑回归在实际交易日，比同日候选池平均强势率高11.57个百分点，但弱势率仍高0.46个百分点。71笔强势交易平均净赚4.64%，62笔弱势平均净亏4.06%，137笔中间类也平均净亏1.05%。因此问题包含“抓到上涨同时也抓到了更危险的股票”，而非完全没有识别出关联；只学强弱概率之差没有充分处理不同类别的损益幅度。

强势标签衡量早盘曾达到的次高价，收益衡量09:40真正留下的收益，两者也有目标偏差。[强/中/弱标签下的实际收益](label_return_bridge.csv)与[同日候选池比较](selection_lift.csv)可用于下一轮拆解。下一步优先把盈利幅度、到09:40的保留程度和亏损风险分开估计，并在历史验证段确定是否轮空；不因这轮亏损就否定业务相关因子，也不先升级更复杂网络。

## 数据与验证边界

- 814个模型文件与日期边界均核对，约159万行全候选分数留存。独立加载10月76因子逻辑模型、10/13同池树重新评分，最大差异0。
- 原B1冻结115个选择和收益保持一致；111个可知成交额门槛、4个原本未知门槛全部与旧账匹配，91笔通过。
- 2026-10-13数据源14:40仅4627只股票，低于常见约5225只。已观察候选路径完整不等于全市场覆盖完整；10月结论仅为小样本附录。
- 复用经哈希核验的CO4原始因子面板，新增训练、候选预测、350日成交额计算。按月完成文件支持断点续跑；重跑聚合会读已有本轮模型结果，不能称再训练一遍。
- 2025–26历史此前多次用于研究，因此虽严格前推，仍不是整个研究项目从未接触的独立盲测。未做超出本轮方案的测试后调参。
- 假定能在14:40和09:40分钟收盘价成交，未模拟涨跌停排队、盘口冲击或卖出无法成交；费用/滑点是情景假设，不是实盘收益。
- 14项定向测试通过。首次核对发现原冻结Top1文件缺3个轮空日，已从原118折清单补齐；成交额审计也保留原账4个未知状态，没有填成交易。未运行与本业务无关的全仓库测试。

## 复现和资产

运行方案：[DESIGN.md](DESIGN.md)；运行绑定：[run_manifest.json](run_manifest.json)；实际审计：[audit.json](audit.json)。源码提交与输入/源码/方案哈希见清单，模型训练行为使用1.7.1。冻结CO4包不改动。

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv-automl/bin/python -m scripts.build_automl_close_open_amounts
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv-automl/bin/python -m scripts.experiment_automl_close_open_simple_rolling
.venv-automl/bin/python -m scripts.report_automl_close_open_simple_rolling
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv-automl/bin/python -m scripts.audit_automl_close_open_simple_rolling
.venv-automl/bin/python -m scripts.summarize_automl_close_open_simple_rolling
.venv-automl/bin/python -m pytest -q tests/automl/test_close_open_simple_rolling.py tests/automl/test_close_open_factor_discovery.py
```

新包保存模型、训练样本、每折日期与因子选择、验证记录、全候选分数、日/月指标和哈希，保留本地并登记；用户已明确暂缓云盘上传。预处理与选因子的时间隔离参照 [scikit-learn 数据泄漏说明](https://scikit-learn.org/stable/common_pitfalls)。
'''
    (REPORT/'README.md').write_text(text)
    print('Wrote readable report and monthly plot')

if __name__=='__main__':run()

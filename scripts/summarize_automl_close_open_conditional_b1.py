"""Readable CO7 monthly decisions and settled equity report, without promotion."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.experiment_automl_close_open_conditional_b1 import OUT,REPORT,read
from scripts.report_automl_close_open_conditional_b1 import account,account_stats

NAMES={'B1_171':'1.7.1七因子基线'}
for prefix,title in [('top1_core','Top1基础信息'),('top5_core','Top5基础信息'),('top5_context','Top5＋8项走势'),('top5_all76','Top5＋全部76因子')]:
    for suffix,label in [('fixed35','固定35%'),('fixed50','固定50%'),('adaptive','验证选阈值')]:NAMES[prefix+'_'+suffix]=title+'／'+label
PRIMARY=['B1_171','top1_core_adaptive','top5_core_adaptive','top5_context_adaptive','top5_all76_adaptive']
DISPLAY=['B1_171','top1_core_fixed35','top5_all76_adaptive']


def table(frame,cols):
    out=['| '+' | '.join(cols.values())+' |','| '+' | '.join(['---']*len(cols))+' |']
    for _,row in frame.iterrows():
        cells=[]
        for c in cols:
            v=row[c]
            if pd.isna(v):s='—'
            elif c=='model':s=NAMES.get(v,v)
            elif isinstance(v,(float,np.floating)):s=f'{v:.3f}' if 'auc' in c else f'{v:.2f}'
            else:s=str(v)
            cells.append(s)
        out.append('| '+' | '.join(cells)+' |')
    return '\n'.join(out)


def charts(daily):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    fonts=[Path('/System/Library/Fonts/PingFang.ttc'),Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf')]
    for p in fonts:
        if p.exists():font_manager.fontManager.addfont(str(p));plt.rcParams['font.family']=font_manager.FontProperties(fname=str(p)).get_name();break
    plt.rcParams['axes.unicode_minus']=False
    fig,ax=plt.subplots(figsize=(11,5.2))
    for name,color in zip(DISPLAY,['#343e4a','#3c80bd','#e47b37']):
        g=daily[daily.model.eq(name)].sort_values('signal_date')
        dates=pd.to_datetime(g.signal_date)
        ax.plot([dates.iloc[0]-pd.Timedelta(days=1),*dates],[1,*list(g.nav_after/100000)],label=NAMES[name],color=color,lw=1.8)
    ax.axhline(1,color='#999999',lw=.8,ls='--');ax.grid(axis='y',alpha=.2)
    ax.set(title='第二阶段风险过滤：仍未扭转长区间亏损',ylabel='扣费净值（初始为1）',xlabel='信号日期；下一日09:40结算结果归属该信号')
    ax.legend(loc='upper right',frameon=False,fontsize=9)
    fig.text(.1,.01,'初始10万元，每次全部可用资金投入；未知成交额门槛按不下单；未计滑点。回撤只覆盖已结算时点。',fontsize=9,color='#555555')
    fig.tight_layout(rect=(0,.03,1,1));fig.savefig(REPORT/'net_value.png',dpi=170);plt.close(fig)


def run():
    summary=read(REPORT/'summary.csv');monthly=read(REPORT/'monthly.csv');stress=read(REPORT/'stress.csv');daily=read(OUT/'daily_execution.csv.gz')
    audit=json.loads((REPORT/'audit.json').read_text())
    summary['positive_calendar_months']=summary.positive_months.astype(str)+'/'+summary.months.astype(str)
    cols={'model':'方案','trades':'保留笔数','blocked_critical':'拦住大亏','blocked_profitable':'误拦盈利','nav_return_pct':'净值总收益%','max_drawdown_pct':'最大回撤%','positive_calendar_months':'正收益月/日历月'}
    primary=table(summary[summary.scope.eq('all')&summary.model.isin(PRIMARY)],cols)
    alltable=table(summary[summary.scope.eq('all')],cols)
    byyear=table(summary[summary.scope.isin(['2025','2026'])&summary.model.isin(DISPLAY)],{'model':'方案','scope':'年份','trades':'笔数','net_trade_mean_pct':'每笔净均值%','nav_return_pct':'净值收益%','max_drawdown_pct':'最大回撤%','positive_calendar_months':'正收益月份'})
    common=table(summary[summary.scope.eq('B1_frozen_dates')&summary.model.isin(DISPLAY)],{'model':'方案','trades':'笔数','critical_count':'大亏笔数','net_trade_mean_pct':'每笔净均值%','nav_return_pct':'净值收益%','max_drawdown_pct':'最大回撤%','positive_calendar_months':'正收益月份'})
    pivot=monthly[monthly.model.isin(DISPLAY)].pivot(index='month',columns='model',values='nav_return_pct').reset_index()
    mt=table(pivot,{'month':'月份','B1_171':'七因子基线%','top1_core_fixed35':'Top1固定35%','top5_all76_adaptive':'Top5全因子验证选阈值%'})
    ints=table(summary[summary.scope.eq('all')&summary.model.isin(DISPLAY)],{'model':'方案','blocked':'共拦截','blocked_critical':'拦住大亏','critical_recall_pct':'大亏召回%','blocked_profitable':'误拦盈利','profitable_rejected_pct':'盈利误拦%','critical_pct':'留下交易大亏%','selected_critical_auc':'大亏AUC'})
    riskdiag=summary[summary.scope.eq('all')&summary.model.isin(DISPLAY)].copy()
    # A pure attribution check: remove the same largest avoided loss from both
    # frozen paths; do not refit or change any decision.
    b=daily[daily.model.eq('B1_171')].copy();f=daily[daily.model.eq('top5_all76_adaptive')].copy()
    affected=f[f.baseline_trade&~f.trade].nsmallest(1,'net_return').signal_date.iloc[0]
    attribution=[]
    for model,g in [('B1_171',b),('top5_all76_adaptive',f)]:
        g.loc[g.signal_date.eq(affected),'trade']=False
        attribution.append({'model':model,'excluded_date':affected,**account_stats(account(g))})
    pd.DataFrame(attribution).to_csv(REPORT/'largest_avoided_loss_attribution.csv',index=False)
    stress_main=stress[stress.scope.eq('all')&stress.model.isin(DISPLAY)].copy()
    stress_main['stress']=stress_main.stress.map({'exclude_best_month':'删最好月','exclude_monthly_top':'每月删最好一笔','exclude_both':'两者都删'})
    st=table(stress_main,{'model':'方案','stress':'压力测试','remaining_trades':'剩余笔数','net_trade_mean_pct':'每笔净均值%','nav_return_pct':'净值收益%','max_drawdown_pct':'最大回撤%'})
    grids=read(REPORT/'validation_thresholds.csv');chosen=grids[grids.chosen]
    active=chosen.groupby('arm').threshold.apply(lambda x:int(x.le(1).sum())).to_dict()
    text=f'''# CO7：原B1折外Top1/Top5的第二阶段大亏风险识别

## 结论

**已按新要求完成：只用第一阶段历史折外选择训练第二阶段，温和惩罚、时间验证、逐月冻结，原1.7.1七因子保留对照。** 本轮没有达到“2025、2026可检验月份中绝大部分盈利”。风险评分在原模型选中子集的AUC约0.56–0.60，但过滤没有把它转化成跨年稳定正收益。与CO6测试日期范围不同，不直接比较两轮AUC来证明模型进步。

本轮主要判断指标是**月净收益、最大回撤、大亏拦截、盈利误拦和剩余交易数**。删最好月/每月最好一笔只作压力测试，不作为必须通过的门槛。按这个放宽后的标准，仍未出现足够好的方案：15个测试月份只有6个月盈利；去掉4天的2026年10月后为6/14，2025年8–12月五个月全部亏损。

### 预设的四条“验证选阈值”主线

2025-08-01至2026-10-13，288个信号日。下表净值按初始10万元、每笔全部可用资金投入、次晨退出再开下一笔计算；含佣金、印花税和过户费，不含滑点。**最大回撤是净值相对历史峰值的比例，非单笔收益累加回落；只覆盖已结算时点，未覆盖持仓中的收盘/盘中峰谷。**

{primary}

全部76因子自适应方案保留217/222笔，拦住2笔大亏、误拦2笔盈利和1笔其他交易，净值由−44.63%改善到−34.60%，最大回撤由57.16%降至49.40%。但改善主要来自避开2025-11-20一笔−16.35%的交易；两条冻结路径同时去掉该日后，基线净值收益为{attribution[0]['nav_return_pct']:.2f}%，过滤方案为{attribution[1]['nav_return_pct']:.2f}%，优势不再存在。这个归因检查不构成新的选股规则，也不是必须剔除单笔才能合格的门槛。

![净值对照](net_value.png)

## 1. 确保研究的是原B1的错误

- 原B1的七因子、四类、前20个市场交易日每日重训、类别优先顺延规则不变，锁定scikit-learn1.7.1。
- 2026年原118个预测日，224条Top2选择/类别全部一致，概率最大差小于1e−16；2025年3–8月232条、9–10月75条官方冻结Top2也全部一致，概率差为0。已核对官方最终连续母表结果，未把旧的中间回放文件当权威基准。
- 2025-02-05至11-03、2026-01-05至07-31保留原冻结候选和基础模型标签；其余日期按原B1的历史复权日线MA和股本代理定义扩展。扩展期采用CO4已核验的分钟完整性与候选池，**不称整段历史都是原来保存的B1预测**。原2–7月的同口径身份已实际复现。
- 第一阶段共408天、147,152条候选概率、1,868条最多Top5折外记录。每一条预测都来自当时仅看此前20日的模型，不使用该模型在自己拟合数据中的排名。
- 原输入快照两个终点日当时没有次晨文件，只补终点标签供之后的日期训练；原冻结区间所有已有预测均不受影响。

## 2. 第二阶段如何隔离训练与验证

每个测试月，取此前100个第一阶段折外市场日：前60日拟合风险模型，后40日独立验证阈值；冻结参数后用过去100日重新拟合，预测下一月。标签必须在下一阶段首日14:40前成熟。截尾、填充、标准化只在对应训练段估计。

比较四种输入：Top1原7因子＋基础预测信息14项；Top5同14项；Top5再加8项业务走势共22项；Top5全部76因子＋基础预测信息共83项。基础预测信息包括四类概率、概率领先幅度、当日合格候选数及排名。模型均为C=.03的强正则逻辑回归，按日等权，无严重类三倍训练权重。

标签为扣费后的09:40价格收益严格小于−2%。Top5仅扩大训练样本，**交易只过滤原Top1，不递补、不重新排序**。每条模型保留35%、50%固定阈值对照；自适应方案只在过去验证Top1上从35/40/45/50/60%和不拦截中选择，评分为“拦住大亏×1.5−误拦盈利−其他拦截×0.25”，验证至少15笔、保留≥70%、误拦盈利≤20%，并须优于不拦截。验证证据不足就关闭过滤。约束不保证下一月仍能满足。

15个月中，自适应规则启用月份分别为：Top1基础{active['top1_core']}，Top5基础{active['top5_core']}，Top5＋走势{active['top5_context']}，Top5全因子{active['top5_all76']}；其余月份保持原交易。Top1发现期通常只有约60条记录，识别能力有限，不能把关闭过滤说成风险模型成功。

固定方案见运行前提交 [DESIGN.md](DESIGN.md)，每月阈值候选/选择与验证计数见 [validation_thresholds.csv](validation_thresholds.csv)。阈值与拟合使用不同历史样本的方法依据见 [scikit-learn阈值文档](https://scikit-learn.org/stable/modules/classification_threshold.html)，本轮实际运行仍为1.7.1。

## 3. 拦截效果与固定阈值对照

基线222笔已完成交易，其中51笔净亏>2%，94笔盈利。下表展示基线、预先固定的Top1 35%门槛以及全因子自适应方案；其余结果全部列于下一张表，不择优隐藏。

{ints}

Top1固定35%拦6笔，3笔大亏、2笔盈利，保留216笔；它在2025年没有拦截任何交易，改善集中于2026年。Top5加更多因子并未稳定优于少因子。固定50%多数时候几乎不拦截；35%筛得更严时，误拦盈利会增加，未解决跨年收益问题。全因子自适应拦2笔大亏，同月随机拦同数量的期望约0.96笔；只有5笔被拦，不能据此证明可靠显著优势。

{alltable}

## 4. 分年与原B1共同区间

2025这里只含8–12月；2026含1–9月及10月4日，范围不同，不能将两年总收益直接当同长度年化结果。

{byyear}

原B1的2026年2–7月118日单独核对：

{common}

原B1该段仍有较好收益；新宽因子自适应门槛在这段没有拦截，不能把原收益当成二阶段贡献。Top1固定35%在同段有改善，但2025年没有相应泛化。原B1旧报告+57.61%未扣费；此处+43.63%按本轮全额现金投资、逐笔扣费口径算，不应混用。

## 5. 逐月净值收益

收益按信号月份归属，即该月信号对应的下一交易日09:40结算收益。10月只有4个信号日，单列为不完整月。每月笔数、月内回撤、大亏/误拦以及全部13条方案见 [逐月精简.csv](逐月精简.csv)；[monthly.csv](monthly.csv)为程序完整指标。

{mt}

“绝大多数月份盈利”可以作为候选策略的重要条件，并不要求每月删掉最大盈利后还赚钱。但还须同时看亏损月份的幅度、净值回撤、交易数量、费用和未来时间验证。本轮2025五个月连续亏损、全期净值为负，尚不需要依靠删最大盈利的严格压力测试才能判断不合格。

## 6. 集中度压力测试（辅助，不作硬门槛）

最好月按原每月净收益之和确定，三条展示方案均为2026年6月。删除指定交易后持有现金，其他选择/模型不变；不重选阈值。

{st}

所有方案的全期、分年、完整月、B1共同期压力结果见 [stress.csv](stress.csv)。最大避损的归因检查另存于 [largest_avoided_loss_attribution.csv](largest_avoided_loss_attribution.csv)。

## 7. 资金、未知数据与适用边界

- 单笔收益及大亏标签按5万元成交额计算；佣金万2.5双边且每侧最低5元，过户万0.1双边，印花税万5卖方。净值模拟每次先扣买入费用，剩余金额买入，不借款；退出再扣卖出费用。忽略整手、涨跌停真实成交约束、冲击和资金容量。费用为统一研究假设，不等于具体账户账单。
- 最大回撤是100%资金投入假设下的已结算交易净值回撤。低仓位会降低账户回撤，也会缩小收益，不能借仓位缩小把负期望变为正期望。完整指标另保留额外总10bp滑点情景。
- 资料不足的成交额门槛按不下单处理，原始未知仍保留字段，原B1的4个未知门槛没有补成已知通过/失败。实际入选且允许交易的未知卖价为0，因此本轮这些净值序列完整；若出现未知退出，代码会停止给出完整净值/回撤，不用零收益穿过去。
- 本轮基线实际222笔交易没有跨日除权标记。历史1,868条TopK训练候选中8条带除权标记，4条按价格口径落入大亏；当前标签仍为未复权价格扣费收益，未还原分红送转权益，个别训练标签可能与经济盈亏不同。该限制不能用于声称模型已经识别真实账户大亏。
- 2025早期用于第一阶段预热及二阶段训练/验证，不能伪造从1月起就有已训练的风险模型。2026-10-13行情覆盖弱于常规日期，延续CO4披露。整个历史区间已反复研究，本轮严格前推仍不是全项目全新盲测。

## 8. 判断与保留资产

本轮在B1折外选中子集中学风险，温和门槛保留了较多交易。但大亏召回仍低，扩大拦截又会损失盈利，2025持续亏损未被修复。**保留研究代码与原B1，不升级为交易策略。** 后续若继续，重点应转向“B1在什么历史可观察的环境下有交易优势”，并区分普通小亏累积与极端大亏；不能只调风险阈值期待消除所有负收益月份。

核验见 [audit.json](audit.json)：408个第一阶段模型、120个第二阶段模型包的日期和哈希检查通过；所有120个二阶段模型重开，验证及测试概率误差0；原2025/2026冻结预测逐项匹配。23项定向测试通过，未跑无关全仓库测试。第一阶段155,027行曾进入拟合，第二阶段1,752条独立折外候选曾进入拟合，逐折名单与权重全部保存。

训练入口：[experiment_automl_close_open_conditional_b1.py](../../../scripts/experiment_automl_close_open_conditional_b1.py)；源提交与哈希见 [run_manifest.json](run_manifest.json)。数据、模型、逐日及逐月证据保留在 `outputs/stock_automl/close_open_conditional_b1_r1`。独立研究包生成后登记本地镜像与数据库；以archive_receipt.json记录最终文件数、哈希和ZIP回读。用户要求暂缓云盘上传，旧CO4/CO5/CO6包保持原样。
'''
    (REPORT/'README.md').write_text(text)
    cols={'model':'方案','month':'月份','trades':'保留交易数','nav_return_pct':'月净值收益百分数','max_drawdown_pct':'月内最大回撤百分数','critical_count':'剩余大亏数','blocked_critical':'拦住大亏数','blocked_profitable':'误拦盈利数','strong_pct':'次高强势命中百分数'}
    human=monthly[list(cols)].copy();human['model']=human.model.map(NAMES);human.rename(columns=cols).to_csv(REPORT/'逐月精简.csv',index=False,encoding='utf-8-sig')
    h=summary[summary.scope.eq('all')][list({'model':0,'trades':0,'blocked_critical':0,'blocked_profitable':0,'nav_return_pct':0,'max_drawdown_pct':0,'positive_calendar_months':0})].copy()
    h['model']=h.model.map(NAMES)
    h.rename(columns={'model':'方案','trades':'保留交易数','blocked_critical':'拦住大亏数','blocked_profitable':'误拦盈利数','nav_return_pct':'净值收益百分数','max_drawdown_pct':'最大回撤百分数','positive_calendar_months':'盈利月份数'}).to_csv(REPORT/'summary_readable.csv',index=False,encoding='utf-8-sig')
    charts(daily)
    print('Wrote readable CO7 report and chart')


if __name__=='__main__':run()

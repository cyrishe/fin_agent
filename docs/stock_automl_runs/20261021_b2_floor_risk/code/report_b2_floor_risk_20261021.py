"""Readable report, diagnostic figure and per-trade review from frozen evidence."""
import json
import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from scripts import research_b2_floor_risk_20261021 as r


def main():
    result=json.loads((r.OUT/'test_results_exact.json').read_text())
    selection=json.loads((r.OUT/'selection_exact.json').read_text())
    f=r.load();te=f[f.split=='test'].copy();chosen=result['selected']
    rule=selection['selected']['rule'];model=joblib.load(r.OUT/rule['file'])
    te['风险分数']=model.predict_proba(te[r.CORE])[:,1]
    te['买前风险标记']=np.where(r.predict_rule(rule,te),'提示','不提示')
    cols={'sequence':'原表笔序','symbol':'股票代码','stock_name':'名称','买前风险标记':'买前风险标记','风险分数':'风险分数',
        'total_mv_yi':'D0总市值亿','float_mv_yi':'D0流通市值亿','d0_turnover_pct':'D0换手率',
        'board_streak_days':'连板数','opened_after_touch':'D0触板后打开','open_gap':'D1开盘涨幅','body5':'D1四K实体',
        'd0_normal_volume_ratio':'D0对板前量比','mv_source':'市值来源','share_age':'股本距D0日数',
        'open_floor':'事后D2开盘跌停','close_floor':'事后D2收盘跌停','all_locked':'事后全天封死',
        'original_net':'事后原卖价费后收益_非成交保证','d0_date':'D0日期_溯源','d1_date':'D1日期_溯源','卖出日':'D2日期_溯源'}
    review=te[list(cols)].rename(columns=cols)
    review.to_csv(r.OUT/'测试98笔风险与因子.csv',index=False,encoding='utf-8-sig')
    review[review['买前风险标记']=='提示'].to_csv(r.OUT/'测试15笔提示明细.csv',index=False,encoding='utf-8-sig')
    factors=pd.DataFrame(result['numeric']);cats=pd.DataFrame(result['categorical'])
    font=FontProperties(fname='/System/Library/Fonts/Hiragino Sans GB.ttc')
    plt.rcParams.update({'axes.unicode_minus':False,'figure.facecolor':'#f8fafc','axes.facecolor':'#f8fafc','font.size':11})
    fig,axes=plt.subplots(1,2,figsize=(13,5.2),layout='constrained')
    periods=['train','valid','test'];names=['训练 2022–24','验证 2025','测试 2026'];x=np.arange(3)
    for offset,label,color,group in [(-.17,'风险提示组','#dc6b4a','flagged'),(.17,'其余交易','#328a9d','retained')]:
        vals=[];errs=[];counts=[]
        for p in periods:
            m=chosen[p]
            k,n=(m['caught'],m['flagged']) if group=='flagged' else (m['missed'],m['n']-m['flagged'])
            value=k/n*100;ci=r.wilson(k,n);vals.append(value);errs.append([value-ci[0]*100,ci[1]*100-value]);counts.append((k,n))
        axes[0].bar(x+offset,vals,width=.3,color=color,label=label)
        axes[0].errorbar(x+offset,vals,yerr=np.array(errs).T,fmt='none',ecolor='#4b5563',capsize=3,lw=1)
        for xx,value,(k,n) in zip(x+offset,vals,counts):axes[0].text(xx,2,f'{k}/{n}',ha='center',va='bottom',color='white',fontsize=10)
    axes[0].set_title('固定组合模型：提示组有较高跌停比例',fontproperties=font,pad=16)
    axes[0].set_xticks(x,names,fontproperties=font);axes[0].set_ylabel('D2开盘或收盘跌停比例（%）',fontproperties=font)
    axes[0].legend(prop=font,frameon=False);axes[0].set_ylim(0,65)
    for state,label,color in [('否','D0未见开板','#dc6b4a'),('是','D0有开板','#328a9d'),('分钟内顺序不明','分钟内顺序不明','#8b90a0')]:
        rows=[cats[(cats.split==p)&(cats.state==state)].iloc[0] for p in periods]
        values=[a.rate*100 for a in rows]
        axes[1].plot(x,values,'o-',label=label,color=color,lw=2)
        for xx,value,a in zip(x,values,rows):axes[1].annotate(f'{int(a.risk)}/{int(a.n)}',(xx,value),xytext=(0,9),textcoords='offset points',ha='center',fontsize=9,color=color)
    axes[1].set_title('D0是否开板：早期倾向未延续到测试期',fontproperties=font,pad=16)
    axes[1].set_xticks(x,names,fontproperties=font);axes[1].set_ylim(0,48);axes[1].legend(prop=font,frameon=False,loc='upper right')
    for ax in axes:
        ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.18);ax.set_axisbelow(True)
    fig.suptitle('跌停风险因子研究｜标签仅用于事后评价，预测只用买前数据',fontproperties=font,fontsize=15)
    fig.savefig(r.OUT/'风险分组与时序变化.png',dpi=170);plt.close(fig)

    def pct(v):return f'{v*100:.2f}%'
    def median(key):
        a=factors[(factors.split=='all')&(factors.feature==key)].iloc[0]
        return (a.risk_median,a.control_median)
    rows=[]
    stability={'total_mv_yi':'两组几乎相同；各时期也没有明确区分。',
        'float_mv_yi':'两组接近，单独用大/小市值过滤没有支持。',
        'd0_turnover_pct':'全期跌停组较低，但2026年差异消失；训练低换手门槛在测试13笔中未捕获跌停。',
        'board_streak_days':'中位数相同，非单调；5板及以上可继续观察，尚未形成获选规则。',
        'open_gap':'全期几乎相同，时期之间方向变化，单独阈值不稳定。',
        'body5':'全期差异小，各时期方向不一致。',
        'd0_normal_volume_ratio':'全期跌停组缩量较明显；2026年反向，单阈值失败。'}
    for key in stability:
        a,b=median(key);fmt=pct if key in ['d0_turnover_pct','open_gap','body5'] else lambda v:f'{v:.2f}'
        rows.append(f'| {r.NUM[key]} | {fmt(a)} | {fmt(b)} | {stability[key]} |')
    model_rows=[]
    for p,title in zip(periods,['训练','验证','测试']):
        m=chosen[p]
        model_rows.append(f"| {title} | {m['n']} | {m['positives']} / {pct(m['base_rate'])} | {m['flagged']} | {m['caught']} | {m['false_flags']} | {pct(m['precision'])} | {pct(m['retained_risk'])} |")
    cand_rows=[]
    for c in result['candidates']:
        cand_rows.append(f"| {c['id']} | {c['description']} | {c['train']['caught']}/{c['train']['flagged']} | {c['valid']['caught']}/{c['valid']['flagged']} | {c['test']['caught']}/{c['test']['flagged']} | {'是' if c['selected'] else '否'} |")
    open_rows=[]
    for state in ['否','是','分钟内顺序不明']:
        values=[]
        for p in ['all',*periods]:
            a=cats[(cats.split==p)&(cats.state==state)].iloc[0];values.append(f'{int(a.risk)}/{int(a.n)}（{pct(a.rate)}）')
        open_rows.append('| '+state+' | '+' | '.join(values)+' |')
    runtime=json.loads((r.OUT/'runtime.json').read_text())
    report=f'''# B2/V2 跌停买前因子研究 · 2026-10-21

## 结论

**存在弱的组合倾向，尚不足以设为正式硬过滤。** 验证集选中的六因素逻辑回归，在2026年测试98笔中提示15笔，捕获4笔跌停，误报11笔非跌停。提示组跌停比例26.67%，其余83笔为12.05%；若跳过提示组，整体跌停比例从14.29%降至12.05%，减少2.24个百分点。同时会错过7笔按原卖价口径盈利的交易。

按测试月份聚类重采样，降低幅度95%区间为 **−1.78～+6.77个百分点**，包含0；AUC **0.548**。因此只能称有待继续检验的风险提示，不能宣称发现了稳定规律或提高了实际账户收益。

![风险分组和跨期变化](风险分组与时序变化.png)

## 1. 对象与信息边界

- 冻结V2原306笔交易：D2开盘跌停34、收盘跌停52、交集19、并集67；其他239笔仅表示D2开收盘均未跌停，可能盘中触板或出现亏损。全样本描述保留全部306笔。
- 本次风险模型属于已有B2/V2选中交易之后的额外研究。**没有重训B2选股模型，没有改变其六训两验滚动窗口、四K形态、月度停摆或D2卖出规则。** 本次训练/验证/测试切分是额外风险分类器的切分。
- D0为判断日，即买入前一个交易日。D0全部量价、连板、历史市值、分钟涨停路径在D0收盘已知。D1开盘涨幅和四K实体在09:34观察结束已知，实际买入均不早于09:35。`body5`为旧字段名，实际是**四K**，不是五分钟。
- 数值白名单14项、类别1项；模型只用总市值、D0换手、连板、D0触板后是否打开、D1开盘涨幅、D1四K实体六项。D1全日高低收、D2价格、涨跌停标签、实际买卖价格与收益均不进入预测。标签只参与训练和事后评分。
- 市值210笔为D0当日历史值；96笔为此前已知股本乘D0价，股本距D0为3～371日。日期无前视，但历史库是否修订及原披露时点尚未全部证明，不把它称为严格原时点数据库。估算来源在逐笔文件中保留。
- D0开板按分钟路径观察。63笔触板分钟内先后不明独立编码；“否”指未见打开证据，不保证逐笔成交层面从未开板。

## 2. 67笔与239笔有什么区别

数值为中位数；百分比已统一单位。全期差异仅用于描述，规则只由训练期提出。

| 因子 | 跌停67笔 | 其他239笔 | 跨期判断 |
| --- | ---: | ---: | --- |
{chr(10).join(rows)}

### D0开板状态

| D0触板后打开 | 全部306笔 | 训练 | 验证 | 测试 |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(open_rows)}

全期看“未见开板”似乎更危险，但2026年未见开板组14.29%、有开板组16.67%，早期差异未延续。不能把“D0不开板”直接设成禁买条件。

连板数按训练期固定分箱，≤2板的跌停率训练/验证/测试为22.22% / 12.50% / 11.11%，≥5板为27.03% / 30.00% / 27.27%；高板相对低板有观察价值。不过≥5板在训练期不高于训练整体27.63%，验证仅10笔、测试仅11笔，3板与4板也非单调。**这是固定分箱的描述，不是测试后追加的已验证规则。**

## 3. 如何训练、选择与测试

| 用途 | D0区间 | 笔数 | 跌停笔数 | 用途限制 |
| --- | --- | ---: | ---: | --- |
| 训练 | 2022-05～2024-12 | 152 | 42 | 分位阈值、填补/标准化、拟合模型、筛选候选 |
| 验证 | 2025年 | 54 | 10 | 选择唯一候选；不重新拟合 |
| 测试 | 2026-01～09 | 98 | 14 | 固定候选一次评估，标签结清至10-09 |
| 边界剔除 | 2024-12末 | 2 | 1 | D2进入2025年，不让训练标签跨进验证期 |

V2停摆导致部分月份无交易：测试98笔只分布在1、2、3、4、7、8、9月，共7个活跃月份。上述三组加剔除组为306笔，跌停42+10+14+1=67。没有随机打散；不把同一笔跨组复制。

预先写入`protocol.json`的探索流程：

1. 14个数值因子的训练Q25/Q50/Q75双向阈值，加3种开板状态，共85个单规则。按训练覆盖≥12笔且≤40%、风险高于基线，按超额捕获数量排序，取5个不同因子。
2. 5个单规则及其满足相同训练支持度的9个两两AND，再加固定参数逻辑回归、两层决策树，共16个验证候选。
3. 验证要求至少提示6笔、最多跳过35%、至少捕获3笔、提示组风险为基线至少1.25倍；合格者按剩余风险降低幅度选取。只有逻辑回归通过。
4. 两种模型均仅使用训练数据；逻辑回归L2、C=1，树最大深度2、最小叶子20。正值变量log1p、训练标准化；开板三种状态独立编码。
5. 风险阈值固定为训练分数Q75，严格大于；逻辑回归为 **{rule['threshold']!r}**。没有用测试集挑阈值、变量或“最好规则”，也没有用验证集重新训练。风险分数未校准，不宜当成实际跌停概率。

## 4. 最终候选的三个时期

| 区间 | 总笔数 | 原跌停数/比例 | 提示数 | 捕获跌停 | 非跌停误报 | 提示组跌停率 | 其余交易跌停率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(model_rows)}

测试捕获率4/14=28.57%；提示15/98=15.31%；遗漏10笔跌停。提示组风险为整体1.87倍，但只含4个正例。测试平均精确率AP=0.177，对照正例基线0.143；AUC0.548表明整体排序能力较弱。提示组风险Wilson95%区间10.90%～51.95%，其余组6.68%～20.78%。按7个测试月份聚类重采样3000次，风险降低区间跨0，不能据此拒绝“没有改善”。

测试上半年提示7笔捕获1笔，第三季度提示8笔捕获3笔；月度表现不一致。按市值来源分组：当日值54笔提示8笔捕获3笔，估算值44笔提示7笔捕获1笔。样本小且与时期混杂，不能解释为市值数据质量的因果差异。

同一固定模型，测试开盘跌停7笔捕获3笔、收盘跌停12笔捕获4笔、全天封死4笔捕获2笔。它们是同一预测的辅助统计，未分别训练或择优选目标。

## 5. 全部候选与失败记录

以下为“捕获跌停/提示总数”。测试列只作锁定后的结果记录，不用来改选冠军。

| ID | 规则 | 训练 | 验证 | 测试 | 验证选中 |
| --- | --- | ---: | ---: | ---: | --- |
{chr(10).join(cand_rows)}

低换手、相对缩量在早期样本上看起来较强，在测试期都未捕获跌停；决策树也没有延续训练表现。它们的失败必须与组合模型一并保留，避免只报道有效案例。

## 6. 能否实际采用

本轮支持保留这个固定逻辑回归作**观察性风险提示候选**，暂不作为正式禁买条件。它的7笔盈利误报只是按原卖价假设统计；由于D2跌停卖出可成交问题尚未修正，没有给出“过滤后账户收益提高”的结论。若以后验证硬过滤，还需重放现金、持仓、延期退出、成本和影子账户月度门槛，不能直接删掉坏交易再算收益。

这些历史V2交易及其策略曾被多次研究，2026年的测试是本次分类器未参与拟合的时间留出，**并非整个策略研发从未看过的盲测**。候选探索存在多重比较，且正例少、年度基线变化大。本次不再根据已见测试结果改阈值；后续应在真正新增时期持续记录固定分数及结果，再判断倾向是否稳定。

## 7. 可复现材料与修正记录

- `selection_exact.json`、`test_results_exact.json`、`test_predictions_exact.csv`是当前精确阈值的权威结果。
- 原`selection_frozen.json`和`test_results.json`保留审计：原JSON写入15位小数使树的1/3阈值变成略小于1/3，严格大于比较因此多提示边界样本。`precision_audit.json`记录更正：树测试提示25改为16，捕获3改为1；准确恢复原训练/验证行为。仅从训练X及已有模型重建原阈值，无重新训练、无追加搜索；**逻辑回归三个时期逐笔预测完全不变，验证选中者不变。**
- `verification.json`核验时间切分、边界剔除、训练专属标准化、16个模型/规则重载、逐笔混淆矩阵、精度修复；把未来字段与标签清空不改变预测。
- `train_samples.csv`、`valid_samples.csv`、`test_samples.csv`、`purged_samples.csv`，两份joblib、协议与运行环境均本地保存。因子原始来源为上一份306笔人工复核表，不从全数值列自动挑因子。
- [测试98笔风险与因子](测试98笔风险与因子.csv)、[测试15笔提示明细](测试15笔提示明细.csv)：原表笔序关联，日期放末尾溯源；市值/换手/连板/开板/四K与事后标签分列。CSV的涨跌幅与换手为小数比例，0.05表示5%。
- `factor_differences.csv`为全期与分期中位数、均值、Cliff差异；`factor_bins.csv`为训练固定分箱；`d0_open_categories.csv`为三种开板状态分期统计。
- 主实验脚本：`scripts/research_b2_floor_risk_20261021.py fit`后`test`；测试已打开时禁止重新fit。精度恢复、验证和展示脚本分别为同目录`audit_b2_floor_risk_precision_20261021.py`、`verify_b2_floor_risk_20261021.py`、`report_b2_floor_risk_20261021.py`。固定模型预测以exact阈值为准。
- 运行基线commit：`{runtime['git_base']}`，环境版本见`runtime.json`。原始研究脚本SHA：`{selection['code_sha256']}`；源数据SHA：`{selection['source_sha256']}`。
- 正式策略关联：KingdomAI `imac / B2_V2`，定义SHA `54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00`。本候选尚未取得稳定证据，**未写入生产库、未变更正式定义；本地归档，云盘待同步**。
'''
    (r.OUT/'README.md').write_text(report)
    print(json.dumps(dict(report=str(r.OUT/'README.md'),test_review=len(review),flagged=int(review['买前风险标记'].eq('提示').sum())),ensure_ascii=False))


if __name__=='__main__':main()

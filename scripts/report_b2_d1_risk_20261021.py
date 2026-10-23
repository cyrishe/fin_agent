"""Publish both decision times and both targets, including failed candidates."""
import json
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from scripts import research_b2_floor_risk_20261021 as old
from scripts import research_b2_d1_risk_20261021 as run
from scripts.build_b2_d1_risk_20261021 import OUT,ROOT,D0,EARLY,LATE

def main():
    r=json.loads((OUT/'test_results.json').read_text());s=json.loads((OUT/'selection_frozen.json').read_text())
    d=pd.read_csv(OUT/'factor_differences.csv');p=pd.read_csv(OUT/'test_predictions.csv',dtype={'symbol':str});f=run.load()
    names={**D0,**EARLY,**LATE};pct=lambda x:f'{x*100:.2f}%'
    primary=next(x for x in r['candidates'] if x['id']==s['choices']['close_early'])
    secondary=next(x for x in r['candidates'] if x['id']==s['choices']['union_early'])
    previous=json.loads((old.OUT/'test_results_exact.json').read_text())['selected']
    selected=[]
    for target in ['close','union']:
        name=s['choices'][target+'_early'];q=p[p.candidate==name]
        rows=f.merge(q[['sequence','flagged','score']].rename(columns={'score':'risk_score'}),on='sequence',validate='one_to_one')
        cols=['sequence','symbol','stock_name','flagged','risk_score',*run.GROUPS['close'],'open_floor','close_floor','original_net','d0_date','d1_date']
        rename={'sequence':'原表笔序','symbol':'股票代码','stock_name':'名称','flagged':'风险提示','risk_score':'未校准风险分数',**names,
                'opened_after_touch':'D0触板后打开','open_floor':'事后D2开盘跌停','close_floor':'事后D2收盘跌停',
                'original_net':'原卖价假设费后收益','d0_date':'D0日期_溯源','d1_date':'D1日期_溯源'}
        label='收盘跌停' if target=='close' else '开收盘跌停并集'
        rows[cols].rename(columns=rename).to_csv(OUT/f'测试98笔_{label}.csv',index=False,encoding='utf-8-sig')
        rows.loc[rows.flagged.eq(1),cols].rename(columns=rename).to_csv(OUT/f'提示明细_{label}.csv',index=False,encoding='utf-8-sig')
    oldp=pd.read_csv(old.OUT/'test_predictions_exact.csv',dtype={'股票':str})
    a=oldp[oldp.id=='logistic'].set_index(['D0','股票']);b=p[p.candidate==secondary['id']].set_index(['D0','symbol']).reindex(a.index)
    assert a.risk.eq(b.risk).all()
    paired=[]
    for label,mask in [('两版都提示',a.flagged.eq(1)&b.flagged.eq(1)),('仅新提示',a.flagged.eq(0)&b.flagged.eq(1)),('仅旧提示',a.flagged.eq(1)&b.flagged.eq(0)),('两版都不提示',a.flagged.eq(0)&b.flagged.eq(0))]:
        paired.append(dict(group=label,n=int(mask.sum()),risk=int(a.loc[mask,'risk'].sum())))
    run.save('previous_model_comparison.json',dict(previous_selection_sha256=old.sha(old.OUT/'selection_exact.json'),groups=paired,
        note='Previous frozen model unchanged; current baseline models use different winsorization and are not the previous model.'))
    font=FontProperties(fname='/System/Library/Fonts/Hiragino Sans GB.ttc')
    plt.rcParams.update({'axes.unicode_minus':False,'axes.facecolor':'#f8fafc','figure.facecolor':'#f8fafc'})
    fig,axes=plt.subplots(1,2,figsize=(12,4.8),layout='constrained')
    metrics=[previous['test'],secondary['test'],primary['test']];labels=['上一轮模型\n开/收盘并集','四K扩展模型\n开/收盘并集','四K扩展模型\n只看收盘跌停']
    xx=np.arange(3);tp=[x['caught'] for x in metrics];fp=[x['false_flags'] for x in metrics]
    axes[0].bar(xx,tp,color='#d86b4d',label='捕获目标跌停');axes[0].bar(xx,fp,bottom=tp,color='#9db9c5',label='非目标误报')
    for i,m in enumerate(metrics):
        axes[0].text(i,m['caught']/2,str(m['caught']),ha='center',color='white')
        axes[0].text(i,m['caught']+m['false_flags']/2,str(m['false_flags']),ha='center')
    axes[0].set_xticks(xx,labels,fontproperties=font);axes[0].set_title('测试98笔：捕获增加，误报也增加',fontproperties=font)
    axes[0].legend(prop=font,frameon=False);axes[0].set_ylim(0,55);axes[0].set_ylabel('提示笔数',fontproperties=font)
    for target,label,color in [('close','D2收盘跌停组','#d86b4d')]:
        vals=[];control=[]
        for part in ['train','valid','test']:
            z=d[(d.target==target)&(d.split==part)&(d.feature=='early_down_volume')].iloc[0]
            vals.append(z.risk_median*100);control.append(z.control_median*100)
        axes[1].plot(xx,vals,'o-',label=label,color=color,lw=2)
        axes[1].plot(xx,control,'o-',label='其余交易',color='#328a9d',lw=2)
        for line in [vals,control]:
            for x,y in zip(xx,line):axes[1].annotate(f'{y:.1f}%',(x,y),xytext=(0,9),textcoords='offset points',ha='center')
    axes[1].set_xticks(xx,['训练','验证','测试'],fontproperties=font);axes[1].set_ylim(10,65)
    axes[1].set_title('四K阴K量占比：描述性倾向仍在',fontproperties=font)
    axes[1].set_ylabel('阴K成交量占比中位数（%）',fontproperties=font);axes[1].legend(prop=font,frameon=False)
    for ax in axes:ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
    fig.savefig(OUT/'四K预警效果与因子.png',dpi=170);plt.close(fig)
    summaries=[]
    for label,m in [('D2收盘跌停',primary),('D2开盘或收盘跌停',secondary)]:
        for part,partlabel in [('train','训练'),('valid','验证'),('test','测试')]:
            x=m[part];summaries.append(f"| {label} | {partlabel} | {x['n']} | {x['positives']} | {x['flagged']} | {x['caught']} | {x['false_flags']} | {pct(x['retained_risk'])} |")
    candidates=[]
    for x in r['candidates']:
        rule=x['rule'];desc=rule['id'].split('_')[-1] if rule['kind']=='model' else names[rule['feature']]+rule['op']+f"{rule['value']:.6g}"
        candidates.append(f"| {x['target']} | {x['stage']} | {desc} | {x['train']['caught']}/{x['train']['flagged']} | {x['valid']['caught']}/{x['valid']['flagged']} | {x['test']['caught']}/{x['test']['flagged']} | {'入选' if x['selected'] else '未入选'} |")
    desc_rows=[]
    keys=['early_down_volume','position5','early_volume_ratio','early_volume_accel','early_path','day_return','day_volume_ratio','after4_return']
    for key in keys:
        vals=[]
        for part in ['train','valid','test']:
            z=d[(d.target=='close')&(d.split==part)&(d.feature==key)].iloc[0]
            fmt=(lambda x:f'{x:.3f}') if key in ['position5','early_volume_ratio','early_volume_accel','day_volume_ratio'] else pct
            vals.append(fmt(z.risk_median)+' / '+fmt(z.control_median))
        desc_rows.append('| '+names[key]+' | '+' | '.join(vals)+' |')
    report=f'''# D0＋D1四K / D1全天 → D2跌停研究

实验：B2_D1_FLOOR_RISK_20261021。延续原306笔V2交易；不改变原B2选股、滚动窗口、买卖或月度停摆。

## 结论

1. **前四K量价形态有一些额外信息，但尚不适合硬过滤。** 主目标D2最终收盘跌停：测试98笔中12笔目标，固定模型提示34笔、捕获5、误报29；剩余7/64=10.94%，原来12/98=12.24%，降低1.31个百分点，AUC0.636。提示过多且改善小。
2. 沿用此前67笔的“D2开盘或收盘跌停”目标：测试14笔目标中捕获9笔，提示41、误报32，剩余5/57=8.77%，原来14.29%，降低5.51个百分点，AUC0.679。但需要提示41.84%的交易，提示组精确率21.95%，低于上一轮26.67%。不能只看捕获数增加就称全面改善。
3. **D1全天模型没有一个通过预定验证门槛。** 并非全天信息必然无用，而是本轮样本和候选未证明它有稳定增量。测试里某个未入选模型的局部较好结果不能取代验证选择。
4. 两个入选模型的测试改善95%月份聚类区间分别为−5.03～+7.02、−0.17～+9.96个百分点，均包含0。仍属于历史探索候选，未修改正式策略。

![测试效果与因子倾向](四K预警效果与因子.png)

## 两个时点、两个标签

| 分析时点 | 可用信息 | 可解释为 |
| --- | --- | --- |
| D1 09:34 | D0数据＋D1前四根完整分钟K | 买前风险提示；原买入不早于09:35 |
| D1 15:00 | 上述信息＋D1全天量价路径 | 已买持仓的次日风险评估；不能倒用到D1买入决定 |

**主标签**为D2最终收盘跌停，共52笔，对照254笔；**辅助标签**为此前开盘/收盘并集67笔，对照239笔。两者分开训练与评价。开盘跌停后打开、收盘不跌停的15笔只属于辅助标签；“非目标”不等于盈利或盘中没触及跌停。

市值、换手、连板、D0缩量及封板/尾盘结构，加前四K量价，共23个买前变量；D1收盘组共47个变量。总市值等旧数据的96笔历史股本估算及原披露时间局限沿用前一研究。

## 新增量价与形态

- 四K：成交量/D0全天量，后两根/前两根量比，阴K量占比、阳线根数、收盘斜率、收盘路径长度、最大回撤、收盘区间位置、冲高回吐、上/下影。
- 全天：D1/D0成交量与换手比、日线涨幅/实体/上下影/收盘位置，四K之后至收盘变化，上午/午后/尾30分涨幅，下午/尾盘成交量占比、分钟回撤、收盘相对成交量加权典型价、涨停分钟比例等。
- 第一根09:31含原数据合并的09:30集合竞价；四K量因此可能超过D0全天量，尤其D0缩量封板时。比例没有用D1全天总量归一化；“四K占D1全天量”只在收盘组使用。
- 阴K量定义为收盘低于该根开盘的K线成交量占比，**不是卖单占比**；加权典型价是(H+L+C)/3按分钟量加权，不冒充真实逐笔VWAP。

下表是D2收盘跌停组 / 非收盘跌停组的中位数，用于观察；不是新增的测试后交易规则。

| 因子 | 训练 | 验证 | 测试 |
| --- | ---: | ---: | ---: |
{chr(10).join(desc_rows)}

较一致的描述是：收盘跌停组的四K阴K量占比、四K收盘区间位置在三个时期均较低；四K收盘路径长度也较小。说明仅看开盘涨幅和四K总实体可能遗漏内部量价分布。但单因子中位数差异不保证可用阈值，模型系数也受共线性影响，不能据此解释为资金出货。

相反，放量的方向会变：D1/D0量比在训练、验证的收盘跌停组较高，2026测试反而略低；四K后继续走弱的倾向也没有跨期保持。没有得到“D1放量/尾盘走弱就会D2跌停”的稳定结论。

## 样本与选择流程

- 训练152笔（D0 2022-05～2024-12），验证54笔（2025），测试98笔（2026-01～09），2笔跨年D2标签剔除。收盘标签依次30/9/12/1；并集标签42/10/14/1。测试实际有交易的月份为1、2、3、4、7、8、9月。
- 本次是既有V2交易之后的额外分类器，**不是改原B2六训两验窗口**。2026结果在前轮已被查看，故本轮是后续探索时间留出，不能称全新盲测。
- 每个目标：六变量基线逻辑回归；四K扩展与全天扩展各尝试C=0.1、C=1的L2逻辑回归和深度2、最小叶子20的树。无类别加权。全部仅用训练数据做2%/98%截尾、缺失填补和标准化。
- 另按训练Q25/Q50/Q75双向门槛探索，保留训练提示≥12且≤40%、风险高于基线的前三个不同因子。两目标/两组共788次单阈值评估，存在重叠；最终合计26个候选进入验证。全部成功与失败记录保留。
- 模型阈值固定为训练分数Q75，严格大于，采用无损浮点保存。验证要求提示≥6笔且≤35%、至少捕获3笔、提示组风险至少为整体1.25倍；合格者最大化剩余风险下降。不用验证重新拟合。
- 验证仅选择`{primary['id']}`与`{secondary['id']}`；两个全天组均无合格者。选择先冻结，随后才输出测试。阈值分别为{primary['rule']['threshold']!r}、{secondary['rule']['threshold']!r}，分数未校准，不是实际概率。

## 入选模型逐期结果

| 目标 | 区间 | 笔数 | 目标数 | 提示数 | 捕获 | 误报 | 剩余风险率 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(summaries)}

收盘目标测试召回41.67%，提示34.69%，漏7笔；提示组有13笔按原卖价口径盈利，其中12笔为非目标误报。并集目标召回64.29%，提示41.84%，漏5笔；提示组也有13笔原价盈利，其中12笔为非目标误报。**验证时≤35%的提示比例，在并集测试期升至41.84%，这是分布变化，不能事后提高阈值掩盖。**

按原价收益看，以上属于机会成本诊断，未证明实际成交。此次没有重放延期退出、资金占用、现金与影子账户反馈，不给出过滤后账户收益。

### 与上一轮固定模型比较

同一并集目标、同一98笔测试：上一轮提示15/捕获4/误报11，本轮提示41/捕获9/误报32；AUC从0.548至0.679，但提示范围扩大，精确率从26.67%降至21.95%。新旧逐笔交叉：{'; '.join(x['group']+' '+str(x['n'])+'笔，其中目标'+str(x['risk'])+'笔' for x in paired)}。这不是控制相同提示数量后的增量效果，不能归因于某一个新增因子。

本轮表中`baseline`是采用本轮截尾标准化重新拟合的六变量对照，**不是上一轮冻结模型**；上面的上一轮结果来自其原始模型和原始分数，单独保存关联哈希。

## 全部26个候选

下列数字为“捕获/提示”；close指收盘标签或收盘时点，union指并集标签，early指前四K时点。单规则数值保持小数原单位，0.05=5%。测试结果不改变入选状态。

| 目标 | 时点 | 模型/规则 | 训练 | 验证 | 测试 | 验证选择 |
| --- | --- | --- | ---: | ---: | ---: | --- |
{chr(10).join(candidates)}

## 数据、核验与接手

- 306笔D1各240根分钟齐全，OHLC与日线误差≤0.011，成交量相对差≤0.2%；原四K开盘、实体、最高、最低、回吐逐项复现。数据库缓存的手转股沿用原读取器×100，未用未来日总量修正早盘量。
- `verification.json`记录306笔未来分钟扰动测试：修改09:34后的价格、量和全天汇总，前四K特征不变；验证所有模型只在训练期拟合截尾、填补、标准化；全部26候选三个时期的78组逐笔标记和统计独立重算。
- `feature_dictionary.json`保存变量与可知时点；`研究因子与标签.csv`、`panel.pkl`、`minutes/`保留全部样本和分钟；`selection_frozen.json`、`test_results.json`、`test_predictions.csv`保留选择、结果与逐笔；14个joblib和代码均本地保存。
- [测试98笔：收盘跌停](测试98笔_收盘跌停.csv)、[收盘目标34笔提示明细](提示明细_收盘跌停.csv)、[测试98笔：开收盘并集](测试98笔_开收盘跌停并集.csv)、[并集目标41笔提示明细](提示明细_开收盘跌停并集.csv)。日期放末尾溯源，使用原表笔序。明细含收盘后观察列，但买前模型输入仅为协议内early白名单；事后标签和收益独立标明。
- 复现顺序：`build_b2_d1_risk_20261021`、`research_b2_d1_risk_20261021 protocol / fit / test`、`verify_b2_d1_risk_20261021`、`report_b2_d1_risk_20261021`，均在`scripts/`。当前测试已打开，fit拒绝覆盖实验。代码/模型/数据/环境哈希已留存。
- 正式策略关联KingdomAI `imac / B2_V2`，定义SHA `54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00`。本候选未取得充分稳定证据，未写生产库、未改变正式策略；本地归档，云盘待同步。后续新方案另登记，不在本次已见测试上调完阈值后称独立验证。
'''
    (OUT/'README.md').write_text(report)
    print(json.dumps(dict(report=str(OUT/'README.md'),paired=paired),ensure_ascii=False))

if __name__=='__main__':main()

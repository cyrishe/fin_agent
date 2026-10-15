"""Chinese comparison tables and exact rolling-rule provenance for R22."""
import json
from pathlib import Path
import pandas as pd
from scripts.experiment_limit_rebound_shape_windows_r22 import OUT,NAMES,SCOPES,EXITS
from scripts.experiment_limit_rebound_short_mixed_r12 import dump

DOC=Path('docs/stock_automl_runs/20261015_limit_rebound_shape_windows_r22')


def main():
    read=lambda name:json.loads((OUT/name).read_text())
    accounts=read('accounts.json');overall=read('overall.json');months=read('account_months.json')
    led=pd.read_pickle(OUT/'cash_ledger.pkl');rules=read('short_rules_frozen.json');old=read('original_rules_frozen.json')
    summary=[];monthly=[];rule_rows=[];differences=[]
    for a in accounts:
        if not a['body_gate'] and a['scope']!='后段9月起':continue
        ctx=(a['scope'],a['scheme'],a['body_gate'])
        z=next(x for x in overall if (x['scope'],x['scheme'],x['body_gate'])==ctx)
        l=led[(led.scope==ctx[0])&(led.scheme==ctx[1])&(led.body_gate==ctx[2])&(led.exit==a['exit'])]['return'].dropna()
        summary.append({'区间':a['scope'],'方案':NAMES[a['scheme']],'附加门槛':'实体>-8%' if a['body_gate'] else '无',
          '卖出':a['exit'],'入选笔':a['signals'],'有效笔':len(l),'再板率':z['hit'],'单笔均值':l.mean() if len(l) else None,
          '账户收益':a['total_return'],'最大回撤':a['max_drawdown'],'盈利月':a['positive_months'],'统计月':a['months']})
    grouped={}
    for m in months:
        if not m['body_gate'] or m['scope']=='扩展3月起':continue
        grouped.setdefault((m['scope'],m['月份'],m['scheme']),{})[m['exit']]=m
    for (scope,month,scheme),exits in sorted(grouped.items(),key=lambda x:(list(SCOPES).index(x[0][0]),x[0][1],list(NAMES).index(x[0][2]))):
        r={'区间':scope,'月份':month,'方案':NAMES[scheme],'入选笔':exits['D2']['信号笔数']}
        for ex in EXITS:r[f'{ex}均值']=exits[ex]['每笔均值']
        for ex in EXITS:r[f'{ex}月收益']=exits[ex]['账户月收益'];r[f'{ex}回撤']=exits[ex]['月内回撤']
        monthly.append(r)
    for s in sorted(rules+[dict(x,scheme='original') for x in old],key=lambda x:(x['month'],list(NAMES).index(x['scheme']))):
        tr=s['train_selected'];va=s['valid_selected'];tb=s['train_base'];vb=s['valid_base'];w=s['window']
        rule_rows.append({'月份':s['month'],'窗口':NAMES[s['scheme']],'训练起':w['train'][0],'训练止':w['train'][1],
          '验证月':'—'.join(w['valid']) if w['valid'][0]!=w['valid'][1] else w['valid'][0],
          '条件':s['selected']['text'],'训练原笔':tb['known'],'训练留笔':tr['known'],'训练原命中':tb['hit'],'训练命中':tr['hit'],
          '验证原笔':vb['known'],'验证留笔':va['known'],'验证原命中':vb['hit'],'验证命中':va['hit']})
    for x in read('paired_month_comparison.json'):
        differences.append({'滚动方案':NAMES[x['scheme']],'对照方案':NAMES[x['baseline']],'卖出':x['exit'],
          '胜月':x['win_months'],'平月':x['tie_months'],'负月':x['months']-x['win_months']-x['tie_months'],
          '月均差':x['mean_month_difference'],'差值下限':x['ci_low'],'差值上限':x['ci_high']})
    clean=lambda rows:json.loads(pd.DataFrame(rows).to_json(orient='records',force_ascii=False,double_precision=15))
    dump(OUT/'workbook_data.json',{k:clean(v) for k,v in [('总体对照',summary),('月度对照',monthly),('每月规则',rule_rows),('月度胜负',differences)]})
    notes=[
      '主对照：所有方案都在原每日选择之后加“实体>−8%”；被新增门槛过滤则当天轮空，不再替补。总体页另列后段“附加门槛=无”，隔离纯形态贡献。',
      'D0随机森林及其六个月训练/两个月验证、每月预测分数完全不变；本轮只缩短前五分钟形态规则的筛选窗口，没有重训日线模型。',
      '分钟档案从2025年1月开始。1训1验从3月可前推；2训1验从4月可前推。原6训2验从9月才可前推，因此早期并非不能筛形态。',
      '2月可行性检查：将1月按日历半月切开，再剔除跨边界的D4标签，用当时Top1预测仅剩4笔训练/2笔验证；未据此拟合或降低最低样本门槛。',
      '短窗训练使用历史各月自身模型的Top1前推分数；验证使用目标月模型已保存的验证分数、仅取最后1个月。训练D4必须早于验证月，验证D4必须早于预测月。',
      '形态仍是79项候选（47单条件、31双条件、1不筛选），不是新的复杂分类器。主目标三日再板，要求策略均值不下降，再经验证保留；目标月不参与选择。',
      '短窗最低样本事先固定：2训1验训练至少8笔、1训1验至少6笔，且保留≥40%；验证至少4笔且保留≥30%。训练再板至少提升3个百分点；不够则不筛形态。',
      '2训1验按两个月做稳定性检查；1训1验按训练日分两半。两者标准已在specification.json冻结，不能将窗口差异单独解释为长度的因果效果。',
      '原6训2验直接复用R19已保存规则；训练/验证筛选用Top1，评估迁移到Top3。每月规则页的训练/验证是Top1且未加−8%，与总体Top3交易数口径不同。',
      '固定最高6%=前五分钟最高价/前收盘−1≥6%；固定6%和负3%=前者且最低价/前收盘−1≥−3%。两个固定对照在本轮测试比较前确定，不从结果中挑阈值。',
      '分段扩展事先固定为：3月用1训1验，4—8月用2训1验，9月起用原6训2验；不按各月测试胜负选择窗口。对应20个月255笔，不包含1—2月回看。',
      'D1观察完整前五分钟后，在09:36—09:40标签分钟开价低于涨停、有成交的合格Top3中按时间先后及当时排名选1只。买价为分钟开价代理，非真实成交保证。',
      'D2/D3/D4为各自收盘退出；策略=D2封板即收盘卖，否则D3封板收盘卖，否则D4收盘卖。三日再板是D1—D3任一天收盘封板，包含买入当日。',
      '账户每次最多前收净值25%、受现金约束，最多4笔重叠持仓；收盘卖出款次晨可用。收盘净值计回撤，未计费用、滑点及盘中极值。',
      '每个比较区间独立从净值1开始；区间按D0选信号，保留到该区间末笔D4或最后已知日。因此早期区间会有次月清仓尾月，该尾月不是新增模型预测月。',
      '月度页按D1入场月统计入选笔/已平交易均值，账户收益按持仓日历月记账；跨月持仓照常计价。列“统计月”包含清仓尾月，最后2026年10月不完整。',
      '原测试段为2025-09至2026-10-13的D0信号，行情截至10-14。末端未平仓按已知收盘计价，缺失未来收益留空；价格参考不连续作事后数据清理，原样保存异常清单。',
      '月度胜负：后段14个月，同月账户收益相减。月均差及上下限均为收益率差（百分点）；95%范围由10000次成对月份重抽得到，包含负值表示优势仍不确定。',
      '该月未参与其规则选择，不等于全新盲测：历史范围、固定阈值与−8%条件此前已经研究。本轮没有把短窗测试赢家自动升级为未来规则。',
      '结论：短窗增加覆盖，早期2训1验比不筛形态少亏，但不如固定双条件；后段原6训2验的D2/D3收益较强，三日再板率未胜过固定规则，策略卖出优势较弱。',
      '来源：R18保存模型/预测、R19冻结规则、R20/R21已审计行情；另补13个分钟日。39条短窗未来扰动、326条验证分数、5196次选择及30560日资金净值核对通过。'
    ]
    dump(OUT/'workbook_notes.json',notes)
    def table(scope,schemes):
        rows=[]
        for scheme in schemes:
            o=next(x for x in overall if x['scope']==scope and x['scheme']==scheme and x['body_gate'])
            a={x['exit']:x for x in accounts if x['scope']==scope and x['scheme']==scheme and x['body_gate']}
            rows.append(f"|{NAMES[scheme]}|{o['n']}|{o['hit']:.2%}|{o['ret2']['mean']:+.2%}|{a['D2']['total_return']:+.2%}|{a['D2']['max_drawdown']:.2%}|{a['策略']['total_return']:+.2%}|{a['策略']['max_drawdown']:.2%}|")
        return '\n'.join(rows)
    later=table('后段9月起',SCOPES['后段9月起'][2]);early=table('早期4—8月',SCOPES['早期4—8月'][2]);march=table('新增3月',SCOPES['新增3月'][2])
    header='|方案|入选笔|三日再板|D2单笔均值|D2账户收益|D2回撤|策略账户收益|策略回撤|\n|---|---:|---:|---:|---:|---:|---:|---:|'
    selected='\n'.join(f"|{r['month']}|{NAMES[r['scheme']]}|{r['selected']['text']}|" for r in rules if r['month']<'2025-09')
    diff='\n'.join(f"|{x['对照方案']}|{x['卖出']}|{x['胜月']}/{x['平月']}/{x['负月']}|{x['月均差']*100:+.2f}|[{x['差值下限']*100:+.2f}, {x['差值上限']*100:+.2f}]|" for x in differences if x['滚动方案']==NAMES['original'])
    DOC.mkdir(exist_ok=True)
    (DOC/'README.md').write_text(f'''# R22：早期短窗前推与滚动/固定形态规则对照

## 回答用户的两个问题

1. **2025年1—8月不是没有分钟数据、也不是不能筛形态。** 原6训2验需要8个月历史，才从9月开始。本轮2训1验从4月开始、1训1验从3月开始；最多补出3—8月6个形态规则的按时序留出月份。日线随机森林仍保留原六个月训练和两个月验证。1月缺此前分钟；2月若把1月切半，在严格D4边界后只剩4/2笔Top1可用样本，未据此强行拟合。
2. **后段原6训2验对D2/D3收益有明显改善，但不能笼统说所有指标都优于固定规则。** 相对固定双条件，三日再板率同为66.22%；D2账户+82.29%对+20.11%、回撤11.48%对15.79%；策略账户+0.29%对−16.02%，回撤27.50%反而高于25.76%。与不筛形态的D2月差不确定范围仍含负值，不能把一次历史表现称为普遍稳定优势。

## 冻结设计

{chr(10).join('- '+n for n in notes[:11])}

短窗与原窗均不看目标月份选择规则；训练和验证之间按D4标签成熟日剔除跨界样本。最低样本、稳定性门槛随短窗预先调整，故比较的是整个短窗筛选方案，而不是单一长度因素。

## 早期留出：2025年4—8月

所有方案均叠加实体>−8%，新增门槛过滤后不替补。区间末笔继续计价至D4，所以账户包含9月清仓尾日。

{header}
{early}

2训1验相对不筛形态，D2账户−21.91%→−9.09%、策略−44.09%→−27.68%，属于少亏；相对固定最高6%，D2累计略差且回撤更大；固定双条件在这段更好。1训1验4—8月的实际选股与不筛形态完全相同，不能把“有规则可运行”当作增益。

## 最早补出的3月

1月训练、2月验证，实际选中最高涨幅≥0%，训练保留8笔、验证保留5笔；3月独立于该轮规则选择。

{header}
{march}

D2小幅转正，但只有17笔、1个目标月；策略仍亏11.38%，不据此称稳定。

## 后段共同区间：2025年9月—2026年10月现有数据

{header}
{later}

原6训2验仍是这组对照里D2/D3的较强结果；改成更短窗口并未进一步改善后段。固定最高6%的再板率67.50%还高于原6训2验66.22%，因此原滚动规则的主要优势体现为部分退出方式的收益，并非再板命中率全胜。

### 去掉−8%附加门槛的纯形态对照

后段D2单笔均值：不筛形态+0.17%、固定最高6%+0.12%、固定双条件+0.58%、原6训2验+1.51%。原滚动规则的D2改善在未叠加−8%时也存在，不是新增门槛单独造成；完整四种卖法及两个短窗列于Excel总体页“附加门槛=无”。

### 逐月胜负与改善的不确定范围

“胜/平/负”按同月账户收益比较；收益差和95%范围单位均为百分点。

|对照|退出|胜/平/负月|月均差|95%范围|
|---|---|---:|---:|---|
{diff}

D2相对固定双条件6胜7平1负，月均差+3.07个百分点，月份重抽范围约+1.20至+5.04个百分点；相对不筛形态9胜5负，范围−1.97至+5.62个百分点，仍包括变差的情况。D4与策略对固定双条件的范围也含负值。月数仅14且最后月不完整，这些范围是描述性稳健性检查，不是新盲测显著性证明。

## 早期实际选中规则（不举例）

|目标月|窗口|已冻结规则|
|---|---|---|
{selected}

1训1验20个月中5个月选到过滤规则；2训1验19个月中12个月选到过滤规则。训练可买Top1样本分别4—14笔和12—28笔，验证3—14笔；短窗弱势与可用样本少一致，但不能仅凭本实验归因。

## 预设的扩展拼接

3月用1训1验、4—8月用2训1验、9月后保持原6训2验，按历史可用长度预先规定，不从各月测试收益挑赢家。20个月255笔，D2账户+66.13%/回撤26.31%，策略−35.62%/40.17%。早期段仍拖累风险，不自动替换R21B，也不将固定双条件事后改选为早期正式模型。

## 资金与执行口径

{chr(10).join('- '+n for n in notes[11:20])}

## 保存与复现

- 脚本`experiment_limit_rebound_shape_windows_r22.py`按prepare、choose、evaluate执行；先保存全部39条短窗规则，再评估任何目标月。
- `specification.json`冻结窗口、候选规则、最少样本、执行、固定对照及统计方法；`selection_receipt.json`记录规则哈希。
- `forecast_top3.pkl`、`validation_top1.pkl`、`metadata.pkl`保存实际选择样本；新增13个分钟日及原始摘录位于`minute_days/`，其余已验证数据复用R18/R20/R21并记录哈希。
- `short_rules_frozen.json`保存39份训练/验证候选、关键指标及具体样本键；`original_rules_frozen.json`复制原14份规则；`policy_picks.pkl`保存全方案逐笔结果。
- `overall.json`、`accounts.json`、`account_months.json`、`paired_month_comparison.json`保存完整统计；`daily_nav.pkl`、`cash_ledger.pkl`及异常清单供复算。
- `verify_limit_rebound_shape_windows_r22.py`通过39次目标月及以后数据扰动、326条模型验证分数复推、13个原始分钟日、5196次逐时序选择、30560日现金净值检查；原R21B150笔精确复现。8项定向测试通过。
- Excel四页：总体对照、月度对照、每月规则、月度胜负；没有复制旧的股票明细大表。表数据和文档由`report_limit_rebound_shape_windows_r22.py`生成，工作簿由`build_limit_rebound_shape_windows_r22.mjs`生成。
- 输出目录：`{OUT}`。本轮本地完成，未注册新稳定模型、未声称云盘同步。原R21B文件保留，R22是用户要求的独立对照。
''')
    print(json.dumps({'summary':len(summary),'monthly':len(monthly),'rules':len(rule_rows),'comparisons':len(differences)},ensure_ascii=False))


if __name__=='__main__':main()

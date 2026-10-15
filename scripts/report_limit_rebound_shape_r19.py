"""Concise R19 workbook payload and durable research report."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from scripts.experiment_limit_rebound_shape_r19 import OUT,metrics,dump,sha

DOC=Path('docs/stock_automl_runs/20261015_limit_rebound_shape_r19')


def main():
    f=pd.read_pickle(OUT/'test_top1.pkl')
    frozen=json.loads((OUT/'all_rules_frozen.json').read_text())
    monthly=json.loads((OUT/'monthly_metrics.json').read_text())
    result=json.loads((OUT/'result.json').read_text())
    summary=[]
    for part,name in [('train','训练·14窗中位'),('valid','验证·14窗中位'),('test','测试·独立日期合计')]:
        for policy,key in [('全部Top1','test_base'),('五分规则','test_selected')]:
            rows=[r for r in monthly if r['集合']==part and r['方案']==policy]
            value=lambda k:result[key][k] if part=='test' else float(np.median([r[k] for r in rows]))
            metric=lambda k,v:result[key][k][v] if part=='test' else float(np.median([r[k][v] for r in rows]))
            summary.append({'集合':name,'方案':policy,'成交笔数':value('fills'),'三日再板率':value('hit'),
              'D2均值':metric('ret2','mean'),'D3均值':metric('ret3','mean'),'D4均值':metric('ret4','mean'),
              '策略均值':metric('strategy','mean'),'策略有效笔':metric('strategy','n'),'策略亏超5%':metric('strategy','bad5')})
    months=[]
    for s in frozen:
        r=s['month'];a,b=s['train_base'],s['train_selected'];c,d=s['valid_base'],s['valid_selected']
        x,y=[next(t for t in monthly if t['月份']==r and t['集合']=='test' and t['方案']==p) for p in ['全部Top1','五分规则']]
        months.append({'月份':r,'条件':s['selected']['text'],'训练区间':'～'.join(s['window']['train']),
          '验证区间':'～'.join(s['window']['valid']),'训原笔':a['known'],'训后笔':b['known'],
          '训原命中':a['hit'],'训后命中':b['hit'],'验原笔':c['known'],'验后笔':d['known'],
          '验原命中':c['hit'],'验后命中':d['hit'],'测原笔':x['fills'],'测后笔':y['fills'],
          '测原命中':x['hit'],'测后命中':y['hit'],'D2均值':y['ret2']['mean'],'D3均值':y['ret3']['mean'],
          'D4均值':y['ret4']['mean'],'策略均值':y['strategy']['mean'],'原策略均值':x['strategy']['mean']})
    details=[]
    for r in f.itertuples():
        why='普通' if r.ordinary else '特殊状态已排除'
        if r.audit_ok!=True:why='全天无成交·分钟缺失' if r.d1_volume==0 else r.reason
        elif r.trade and pd.isna(r.ret4):why='D4价格不连续·收益空'
        details.append({'日期':r.d0_date,'D1日期':r.d1_date,'评分':r.score,'形态':r.shape,
         '开盘涨幅':r.open_gap,'五分最高':r.high5,'五分最低':r.low5,'五分实体':r.body5,
         '高点回落':r.pullback5,'收价位置':r.position5,'触板分钟':r.touch5,
         '板下分钟':r.below5,'整分板下':r.whole5,'规则通过':int(r.rule_pass),
         '可买代理':int(r.trade),'执行入选':int(r.selected_trade),'买点分钟':r.entry_time if r.trade else None,
         '买入价':r.entry if r.trade else None,'买入分全板下':int(r.entry_whole_below) if r.trade else None,
         'D1封板':int(r.d1_up) if pd.notna(r.d1_up) else None,
         '三日再板':int(r.label_3) if r.observed_3 else None,
         'D2收益':r.ret2,'D3收益':r.ret3,'D4收益':r.ret4,'策略收益':r.strategy,'核对':why})
    def finite_records(rows):return json.loads(pd.DataFrame(rows).to_json(orient='records',force_ascii=False,double_precision=15))
    data={k:finite_records(v) for k,v in [('效果对照',summary),('月度规则',months),('测试Top1',details)]}
    dump(OUT/'workbook_data.json',data)
    chosen=f[f.selected_trade];base=f[f.trade]
    touchonly=metrics(f,{'atoms':[['touch5','>=',1]]})
    groups={name:metrics(g,{'atoms':[]}) for name,g in [('入选未触板',chosen[chosen.touch5.eq(0)]),('入选已触板',chosen[chosen.touch5.gt(0)])]}
    # Descriptive uncertainty of the frozen monthly policy; no further tuning.
    monthkeys=sorted(f.model_month.unique());rng=np.random.default_rng(20261015)
    sums={}
    for name,g in [('base',base),('selected',chosen)]:
        sums[name]={}
        for c in ['label_3','ret2','ret3','ret4','strategy']:
            a=g.groupby('model_month')[c].agg(['sum','count']).reindex(monthkeys,fill_value=0)
            sums[name][c]=a.to_numpy()
    draws=rng.integers(0,len(monthkeys),(10000,len(monthkeys)));ci={}
    for c in ['label_3','ret2','ret3','ret4','strategy']:
        means={}
        for name in sums:
            vals=sums[name][c][draws].sum(axis=1);means[name]=vals[:,0]/vals[:,1]
        ci[c]=np.quantile(means['selected']-means['base'],[.025,.975]).tolist()
    diagnostics={'touch_only_posthoc':touchonly,'selected_touch_split_posthoc':groups,
      'entry_minute_end_counts':chosen.entry_time.value_counts().sort_index().to_dict(),
      'selected_whole_entry':int(chosen.entry_whole_below.sum()),
      'paired_month_bootstrap_95_delta':ci,'bootstrap_note':'10000 paired month resamples of frozen policy; 14 months including incomplete October. Does not account for earlier historical research.'}
    dump(OUT/'diagnostics.json',diagnostics)
    DOC.mkdir(exist_ok=True)
    pct=lambda x:f'{x:+.2%}'
    rows='\n'.join(f"|{r['集合']}|{r['方案']}|{r['成交笔数']:g}|{r['三日再板率']:.2%}|{pct(r['D2均值'])}|{pct(r['D3均值'])}|{pct(r['D4均值'])}|{pct(r['策略均值'])}|" for r in summary)
    rule_table='\n'.join(f"|{r['月份']}|{r['条件']}|{r['测后笔']}|{r['测后命中']:.2%}|{pct(r['策略均值'])}|" for r in months)
    report=f'''# R19：D0 Top1 的前五分钟形态与简单规则

## 结论

前五分钟的信息对再板识别有增量。逐月冻结规则后，测试三日再板从70/140（50.00%）提高到45/67（67.16%）；成交代理笔数减半。D2、D3每笔毛收益改善，D4基本持平、策略卖出仍偏薄，不升级为稳定盈利交易模型。

最常选中：**D1 09:30—09:35最高涨幅≥6%，且最低涨幅≥−3%**，14个月用了8个月。这两个涨幅都相对D1前收盘参考价。其余月份用了最高涨幅、最低涨幅、实体涨幅或收价位置；没有月份单独选择粗粒度形态名称。整个测试成绩属于逐月选择的规则组合，不能冒称单条6%/−3%规则的成绩。

## 三集合效果

|集合|方案|成交笔数|D1—D3再板率|D2均值|D3均值|D4均值|策略均值|
|---|---|---:|---:|---:|---:|---:|---:|
{rows}

- 训练与验证展示14个滚动窗口各自结果的中位数；窗口相互重叠，不能相加当独立样本。训练是旧模型历史折外分数上的规则拟合，验证参与规则筛选，均不是最终留出成绩。
- 测试D0为2025-09-01至2026-10-13，共267个独立日期。五条D0/D1历史特殊状态L排除，两条全天无成交且分钟缺失；剩260条可核对候选。没有用低排名递补。
- 测试原140笔、过滤后67笔。D2/D3收益各140/67笔，D4/策略各139/66笔：一笔D4前收盘参考价调整2分钱，原始价格收益留空。
- “三日再板”是D1、D2、D3任一天收盘封板，包含买入当天D1；是历史命中比例，不保证未来涨停。
- D2/D3/D4收益=对应日收盘/模拟买价−1。策略=D2首封板收盘卖，否则D3首封板收盘卖，否则D4收盘卖，未再板也计入。全部未扣费，为每笔价格收益，未模拟资金占用和账户回撤。
- 2026-09仅留下3笔，策略均值−11.11%，说明识别再板仍可能伴随亏损；10月只有1笔已模拟成交，不能把该月当完整月份。

## 交易时点与形态

1. D0保留已保存R18完整分段随机森林的原Top1，不重新训练、不重新排分。
2. D1等09:35结束，读取结束标签09:31—09:35的5根分钟K。最高/最低涨幅、实体、高点回落、收价位置、板下/触板/整分板下根数，以及两段收价方向，共9类候选变量。
3. 形态用09:30开盘→09:32收价、09:32收价→09:35收价两段描述，分前弱后强、前强后弱、两段走强、两段走弱、平走。不能还原同一分钟最高最低出现顺序。
4. 条件通过后，只看结束标签09:36—09:40的分钟：第一根有成交且开盘低于涨停价的分钟，用其开盘作买价代理。若一直在板上或无合格分钟则不买，不把早先窗口最高价当可执行买价。
5. 09:36是09:35—09:36这一分钟的结束标记，其开盘是模拟入场点，并不代表09:36:00真实成交；没有加入处理延迟和逐笔委托队列。

67笔入选中58笔在第一根后续分钟出现买价代理，55笔（82.09%）该入场分钟的最高价也低于涨停价。后者比混合触板分钟提供更充分的交易窗口证据，但仍不能证明实际委托成交或可容纳的资金规模。它是事后执行诊断，没有拿来筛选09:35信号。

入选186条信号中119条在规定窗口没有买点。强势形态并不意味着来得及成交。入选成交67笔中，33笔前五分钟没有触板（19/33再板），34笔触板（26/34再板）。规则并不限于已触板股票。

仅要求前五分钟触板的事后对照：44笔，31/44再板（70.45%），策略均值−0.50%；触板命中高不等于收益高。此对照没用于改选规则。

## 训练、验证与冻结

- 沿用6个月训练＋2个月验证＋下1个月测试的滚动结构。首窗训练2025-01—06、验证07—08、测试09；末窗训练2026-02—07、验证08—09、测试10。
- 规则训练使用R18保存的历史逐月折外Top1预测，每条预测由更早时间训练的模型生成。R18预测和第一阶段模型文件均保留。本轮只训练简单条件，不重训森林。
- 预先保存79个候选（含无过滤），每个至多2个条件。训练至少15笔、保留40%，再板率至少提高3个百分点，策略均值不变差。6个月拆3个两月段，每段至少3笔，至少2段再板率不下降。
- 训练按命中率Wilson下界排序并惩罚第二个条件，每类最多1个，最多3个进入验证。验证至少6笔、保留30%，命中和策略均值均不低于原方案，再按训练/验证Wilson下界平均选优；无合格条件则保留全部Top1。
- 上述两月段是训练稳定性检查，并非本轮新增独立CV。第一阶段使用历史折外分数；阈值本身仍有训练选择偏差。测试没有参与挑选；14份规则全部保存后才汇总测试。
- 训练/验证每条D4结果都早于本块结束后的首日。训练事件边界剔除沿用R18；验证与测试可能包含同一长连板事件的不同日期。这个历史区间已反复研究，不属于从未看过的新外部测试。

## 逐月冻结规则

|测试月|条件|成交笔数|再板率|策略每笔均值|
|---|---|---:|---:|---:|
{rule_table}

按14个月成组、有放回重抽10000次，只用于描述固定策略的不确定性：再板率增量95%范围{pct(ci['label_3'][0])}～{pct(ci['label_3'][1])}；策略每笔均值增量范围{pct(ci['strategy'][0])}～{pct(ci['strategy'][1])}。该范围没有消除此前多轮历史研究的选择偏差。

## 数据与复核

- 603个唯一股票日，601个分钟日核对通过。全日240根分钟结束时间、OHLC内部关系、分钟汇总与日线价格及成交量、涨停上界均检查；2个分钟缺失样本日线成交量为0，均不计为交易或负例。
- 历史状态按区间连接，D0和D1均为N/R才纳入本轮普通股候选。L等特殊状态排除。原R18第一阶段曾仅排除S/Y；本轮没有重训修复第一阶段的旧训练池，不能称原森林严格只以普通股训练。
- 独立重算601个分钟日的前五分变量、入场顺序；2410条保存的Top1分数与R18原文件完全一致；7230项收益重算一致。将全部测试形态及结果改乱，14份选定规则仍逐字段一致。
- 5项定向测试通过：未来分钟不影响特征、未触板也可买、先封后开、无买点与缺数据区分、候选只用可见少量变量。
- 原始行情和历史状态是研究快照；全日核对用于发现数据冲突，不能证明当年实时09:35数据送达速度。

## 文件与复现

主目录：`{OUT}`。工作簿：`前五分钟形态_Top1简单规则_R19_20261015.xlsx`，只有效果对照、月度规则、测试Top1三页。逐笔日期列用D0评分日，第二列为D1分钟发生日；无代码名称。所有原Top1保留，筛选执行入选=1可得67笔；未入选行的收益是相同买价规则下的对照收益。

保存`specification.json`、逐月`rule_frozen.json`、`all_rules_frozen.json`、全部训练/验证/测试样本及分数、原始分钟摘录、`result.json`、`verification.json`和`diagnostics.json`。规则文件SHA256：`{sha(OUT/'all_rules_frozen.json')}`。

```sh
DYLD_LIBRARY_PATH=/tmp/fin_agent_sklearn171/sklearn/.dylibs PYTHONPATH=/tmp/fin_agent_sklearn171:. OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv-automl/bin/python -m scripts.experiment_limit_rebound_shape_r19 prepare
# 然后同环境依次运行 minutes、choose、report
# 独立复核：python -m scripts.verify_limit_rebound_shape_r19
# 报告输入：python -m scripts.report_limit_rebound_shape_r19
```

R18模型依赖在其原目录，不复制成新森林；本轮规则、样本及证据本地保存。尚未升级为交易模型，未声称已上传阿里云盘。
'''
    (DOC/'README.md').write_text(report)
    print(json.dumps({'workbook_rows':{k:len(v) for k,v in data.items()},'bootstrap95':ci},ensure_ascii=False,indent=2))


if __name__=='__main__':main()

"""Concise Chinese workbook inputs and research handoff for R21."""
import json
from pathlib import Path
import numpy as np,pandas as pd
from scripts.replay_limit_rebound_body8_r21 import OUT,POLICIES,EXITS
from scripts.experiment_limit_rebound_short_mixed_r12 import dump

DOC=Path('docs/stock_automl_runs/20261015_limit_rebound_body8_nofallback_r21b')


def main():
    f=pd.read_pickle(OUT/'all_candidates.pkl');ledger=pd.read_pickle(OUT/'cash_ledger.pkl')
    results=json.loads((OUT/'account_results.json').read_text());monthly=json.loads((OUT/'monthly_results.json').read_text())
    stats=json.loads((OUT/'trade_stats.json').read_text());summary=[];months=[];details=[]
    for scope in ['原测试段','全期含早期回看']:
        for exit_name in EXITS:
            for policy in ['old','body8']:
                r=next(x for x in results if (x['scope'],x['exit'],x['policy'])==(scope,exit_name,policy))
                l=ledger[ledger.policy.eq(policy)&ledger.scope.eq(scope)&ledger.exit.eq(exit_name)]['return'].dropna()
                summary.append({'区间':scope,'规则':'加实体·不顺延' if policy=='body8' else '原形态','卖出':exit_name,
                  '入选笔':r['signals'],'已平笔':r['closed'],'单笔均值':l.mean(),'胜率':l.gt(0).mean(),
                  '账户收益':r['total_return'],'最大回撤':r['max_drawdown'],'月均收益':r['monthly_mean_return'],
                  '未平笔':r['pending'],'异常剔除':r['excluded']})
    for r in sorted([x for x in monthly if x['方案']==POLICIES['body8']],key=lambda x:(x['月份'],list(EXITS).index(x['退出']))):
        old=next(x for x in monthly if x['方案']==POLICIES['old'] and (x['月份'],x['退出'])==(r['月份'],r['退出']))
        months.append({'月份':r['月份'],'区间':'早期回看' if r['月份']<'2025-09' else '原测试月','卖出':r['退出'],
          '入选笔':r['信号笔数'],'买入笔':r['账户买入'],'已平笔':r['收益有效笔'],'单笔均值':r['每笔均值'],'胜率':r['胜率'],
          '账户月收益':r['账户月收益'],'月内回撤':r['月内回撤'],'原月收益':old['账户月收益'],'原月回撤':old['月内回撤'],
          '月末净值':r['月末净值'],'未平笔':r['月末持仓'],'异常剔除':r['异常排除']})
    for r in f.sort_values(['d0_date','d0_rank']).itertuples():
        status='通过'
        if not r.ordinary:status='特殊状态·排除'
        elif r.audit_ok!=True:status='无量/分钟缺失'
        elif r.trade and pd.isna(r.ret4):status='末端未齐' if r.d0_date>='2026-10-12' else '价格不连续'
        details.append({'日期':r.d0_date,'D1日期':r.d1_date,'区间':r.phase,'D0排名':r.d0_rank,'评分':r.score,
          '五分最高':r.high5,'五分最低':r.low5,'五分实体':r.body5,'原形态':int(r.shape_pass),
          '实体合格':int(r.body_keep) if pd.notna(r.body5) else None,'可买代理':int(r.trade),
          '原入选':int(r.selected_old),'入选':int(r.selected_body8),
          '买点分钟':r.entry_time if r.trade else None,'买入价':r.entry if r.trade else None,'买入涨幅':r.entry_ret,
          '36最高价':r.m36_high,'36最低价':r.m36_low,'36最高涨幅':r.m36_high_ret,'36最低涨幅':r.m36_low_ret,
          '三日再板':int(r.label_3) if r.observed_3 else None,
          'D2收益':r.ret2,'D3收益':r.ret3,'D4收益':r.ret4,'策略收益':r.strategy,'核对':status})
    clean=lambda rows:json.loads(pd.DataFrame(rows).to_json(orient='records',force_ascii=False,double_precision=15))
    data={k:clean(v) for k,v in [('效果对照',summary),('月度收益',months),('每日前三',details)]}
    dump(OUT/'workbook_data.json',data)
    notes=[
      '规则：先按原形态和时序在D0前三确定当天选择；若五分实体≤−8%，当天轮空，不改选其他排名，也不等之后的其他股票。等于−8%剔除。',
      '先完整观察09:30—09:35。之后至09:40逐分钟取有成交、开价低于涨停价的候选，同一分钟优先D0排名；早已买入不因后来Top1开板而改选。',
      '买价为该分钟开价的回放代理，非真实成交证明；未模拟下单延迟、盘口或排队。09:36为09:35—09:36这根K的结束标签。',
      '36最高/最低价及对应涨幅仅作事后展示，不参与买点决定。若买点为09:37—09:40，买价可以不在09:36高低范围内。',
      '五分实体=09:35收价/09:30开价−1；五分最高/最低、买入涨幅、36高低涨幅均相对D1前收盘。两类涨幅的基准不同。',
      '明细日期为D0，D1日期为分钟发生日；筛“入选=1”得到245笔。未入选行的买价及收益为候选回放，不代表账户买入。原入选为未加−8%门槛时的选择。',
      '全期428个D0日、1284条前三候选：D0为2025-01-02至2026-10-13；D1数据截至2026-10-14，2026年10月不完整。',
      '2025年1—8月是“早期回看”：统一套用2025年9月的后定形态规则high5≥6%。该规则曾用1—6月训练、7—8月验证选出，早期不是独立测试。',
      '2025年9月后保持R19各月已冻结规则；−8%是看过历史结果后新增的研究条件，本轮不构成新盲测。D0保存模型及预测分数全部复用，未重新训练。',
      '日线模型为近6月训练＋2月验证＋下一月预测；D0模型只用日K。原形态规则的筛选曾使用历史分钟K，本轮执行也需要D1分钟K。',
      '账户：初始净值1；每次买入≤前一交易日收盘净值25%，现金不足缩减，最多4笔重叠持仓；收盘卖出资金次日上午可用。未扣费及滑点。',
      'D2、D3、D4和策略为四个独立账户。策略=D2封板则收盘卖，否则D3封板则收盘卖，否则D4收盘卖，包含一直不封板的样本。',
      '账户月收益=月末净值/上月末净值−1；月内回撤从上月末净值开始计算，反映逐日收盘回撤，不含盘中极值；整段最大回撤不按月重置。',
      '月度页从2025年1月连续记账，跨月持仓照常计价；汇总“原测试段”则从2025年9月独立初始化，不能把两种起点混为一谈。',
      '月度入选/买入/已平笔及单笔均值按D1买入月归属；已平笔含后来月份才卖出的交易。月末未平笔为当月末账户持仓数，口径不同。',
      '末端未卖出的仓位按已知收价计入净值，不填未来收益。已平笔及单笔均值仅用已知完整收益；空白不等于0。',
      '价格参考不连续时按退出期限排除该笔且不换股：全期D3排1笔、D4/策略排2笔；这是事后数据清理，不是假定买入时能预知的风控条件。',
      '历史特殊状态及缺失分钟候选沿用原排除口径。1276条有完整分钟证据，1270条普通候选可评估；前三排名不因排除而扩到第4名。',
      '原测试段156→150笔，6天因新增门槛轮空；全期263→245笔，18天轮空。新选择严格是原选择的子集，绝不因新增门槛另买其他股票。',
      '新旧方案按相同账户方法比较；详细收益与回撤见上表。早期明显较弱，新增条件不能据此认定全时期稳定。',
      '来源：outputs/stock_automl/limit_rebound_segments_r18_20261015（月度模型/日线）；limit_rebound_shape_top3_r20_20261015（冻结规则/已审计分钟）；本目录保存逐日净值及交易账。'
    ]
    dump(OUT/'workbook_notes.json',notes)
    def pct(x):return f'{x:+.2%}'
    table='\n'.join(f"|{r['区间']}|{r['规则']}|{r['卖出']}|{r['入选笔']}|{pct(r['单笔均值'])}|{pct(r['账户收益'])}|{r['最大回撤']:.2%}|" for r in summary)
    removed=f[f.selected_old&~f.selected_body8];added=f[~f.selected_old&f.selected_body8]
    rules=json.loads((OUT/'rules.json').read_text());rt='\n'.join(f"|{r['month']}|{r['phase']}|{r['rule']['text']}|" for r in rules)
    DOC.mkdir(exist_ok=True)
    (DOC/'README.md').write_text(f'''# R21B：原前三顺延，实体过滤后不再顺延

## 结果

保留R18日线模型、R19逐月形态、R20逐分钟交易顺延。本轮未重训，也未重新寻优阈值。用户最后明确：新增门槛过滤后不再顺延。因此先确定原每日选择，实体不合格则整天轮空，严格排除等于−8%。先前R21中间版允许替补，已作废并标SUPERSEDED，未交付。

原测试段（2025-09至2026-10现有数据）156→150笔：6笔原选择被过滤，6天全部轮空，零替补。各退出效果如下。再板率由100/154=64.94%变为{stats['body8_原测试段']['hits']}/{stats['body8_原测试段']['known']}={stats['body8_原测试段']['hit']:.2%}。

该条件由用户看过历史样本后提出，收益差异是历史描述；早期和原测试段应分别看待，不升级为已验证的稳定模型。

|区间|规则|卖出|信号数|每笔均值|账户收益|最大回撤|
|---|---|---|---:|---:|---:|---:|
{table}

## 覆盖与时间边界

- 22个现存D0月模型全部复用，1284条前三分数由保存模型重新推理、逐项完全一致。D0 2025-01-02至2026-10-13，D1 2025-01-03至2026-10-14，共428个D0日。
- 早期2025年1—8月用第一套已保存规则（9月版high5≥6%）补充回看。它曾使用1—6月训练、7—8月验证选定，因此不属于独立样本外结果；用户已选择“叠加原形态规则，早期单独标注”。
- 9月后继续各月原冻结规则。原历史测试区间此前已研究，−8%条件也由结果观察后提出，不是新盲测。
- 第一阶段日线RF用预测月前第8至第3月训练（6个月），前2个月验证；形态层原训练/验证需要历史分钟K，不能说整套策略训练都只需日K。
- 全期263→245笔，其中早期107→95笔、原测试段156→150笔。全期删原选择{len(removed)}笔、替补{len(added)}笔。排名计数{stats['body8_全期含早期回看']['rank_counts']}，逐笔时间和价格全部保留。

## 交易与表字段

{chr(10).join('- '+n for n in notes[:6])}

“每日前三”保存全部候选，可筛入选=1，不再另加重复的已选子表。股票代码和名称不导出；日期保持第一列。09:36完整高低价只用于回看，不提前用于买价选择。

## 账户口径与数据清理

{chr(10).join('- '+n for n in notes[10:18])}

两条参考价不连续记录为D0 2025-05-29（D3起）及2026-05-25（D4）；原、新政策均作同样期限排除。它们仍保留在候选明细并标异常；没有按未来损益判断是否入选，也没有用零收益填补。此类全路径排除是研究数据清理，真实交易须另处理公司行动和停牌。

## 各月形态

所有规则另加五分实体 > −8%，早期来源为2025-09。

|D0模型月|区间|原规则|
|---|---|---|
{rt}

## 复现及保存

- 处理：`scripts/replay_limit_rebound_body8_r21.py prepare`、`report`。
- 独立核对：`scripts/verify_limit_rebound_body8_r21.py`。
- 表输入/文档：`scripts/report_limit_rebound_body8_r21.py`；表生成：`scripts/build_limit_rebound_body8_r21.mjs`。
- 定向测试：`tests/automl/test_limit_rebound_body8_r21.py`（严格边界、顺延、跨月持仓、待卖仓位、现金约束、参考价异常）。
- 输出目录：`{OUT}`；`models.json`保存22个模型及分数路径和哈希，`rules.json`与`specification.json`保存规则及边界。
- `all_candidates.pkl`、`policy_picks.pkl`保留全体候选及两方案选择；`daily_nav.pkl`、`cash_ledger.pkl`、`account_exclusions.json`保存完整净值、账本及异常。
- `verification.json`记录原始分钟、时序选择、逐日现金账及Excel回读；本轮复用R18/R20已有数据快照，依赖路径及哈希已列出，未声称云盘同步或模型升级。

环境：`.venv-automl`，`PYTHONPATH=/tmp/fin_agent_sklearn171:.`，`DYLD_LIBRARY_PATH=/tmp/fin_agent_sklearn171/sklearn/.dylibs`；RF复推用sklearn1.7.1。
''')
    print(json.dumps({'sheets':{k:len(v) for k,v in data.items()},'notes':len(notes)},ensure_ascii=False))


if __name__=='__main__':main()

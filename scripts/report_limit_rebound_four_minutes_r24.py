"""Compact research record for R24; original workbooks are preserved."""
import json
import platform
from pathlib import Path
import pandas as pd
from scripts.replay_limit_rebound_cash_r23 import dump, sha

OUT=Path('outputs/stock_automl/limit_rebound_four_minutes_r24_20261015')
DOC=Path('docs/stock_automl_runs/20261015_limit_rebound_four_minutes_r24')
NAMES={'five':'五分原规则','four_old':'四分沿用门槛','four_fit':'四分重选规则'}
read=lambda name:json.loads((OUT/name).read_text())
pct=lambda x: '—' if x is None else f'{x:.2%}'


def main():
    accounts=read('accounts.json'); monthly=read('monthly.json'); rules=read('four_rules_frozen.json')
    discrimination=read('shape_discrimination.json'); monitor=pd.DataFrame(read('monitor_counts.json'))
    recovery=read('recovery.json'); verification=read('verification.json')
    lines=['# R24：前四分钟形态与前五分钟的对照','',
        '## 当前服务需要监控几只','',
        'D0收盘用当前月已保存的日线随机森林给涨停股票评分，交易端保留原始Top3。D1同时收集这最多3只的分钟数据。四根K在09:34结束，五根在09:35结束，随后还需等数据到达。形态筛选后可能剩0—3只；继续并行观察，首次出现板下买点时才选定，分钟回测同一根里按D0排名优先。最多买一只，新增实体>−8%条件否决后当天不替补。不能提前知道哪只将打开，也不能把形态结束时已固定唯一股票当成现有规则。',
        '', '回测以整分钟开价低于涨停、有成交量作为买点代理。分钟内不同股票谁先打开不可辨认；同分钟按D0排名是固定回测约定。服务若按真实逐笔先后选择，或形态结束即只锁一只，均需另作执行验证。',
        '', '### 2026年至10月14日：原五分规则剩余候选','', '|形态通过数|天数|','|---|---:|']
    counts=monitor[monitor.policy.eq('five')&monitor.date.ge('2026-01-01')].shape_pass.value_counts().sort_index()
    for n,v in counts.items():lines.append(f'|{n}|{v}|')
    lines += ['', '## 结论','',
        '四分钟能提前一分钟完成形态，但没有证据显示分类更强。共同买价/样本下，沿用门槛的通过组再板率由五分55.63%降至53.25%；四分重选仅43.69%，其D2大跌率31.98%还高于未通过组26.67%。四分重选在训练/验证可改善，而这些目标月份的区分度较弱，不能把总账户收益较高解释成形态模型更准确。',
        '', '四分重选且同股延后一分账户+116.59%、回撤20.79%、夏普2.10；与五分同样延后相比，10个月6胜4负，收益/夏普较高但回撤更大；与五分原买点相比5胜5负、回撤更大且夏普更低。四分沿用门槛且延后为+81.24%/25.49%/1.95，追回原漏8笔中的4笔（其中原盈利3笔），仍不构成全面改善。本轮保留完整对照，不替换原策略。',
        '', '## 账户对照','',
        '2026年1月至10月14日，100万元初始资金；空仓用现金一半，持仓时用剩余现金全部，D2（D1下一交易日）收盘卖；买卖各0.1%。10月未完整，末笔未平按已知收盘计价。日线模型、分数、Top3排序保持原样。',
        '', '|形态方案|执行|笔数|净收益|最大回撤|夏普|三日再板|','|---|---|---:|---:|---:|---:|---:|']
    for a in accounts:
        delayed=a['policy'].endswith('_delay'); key=a['policy'].removesuffix('_delay'); t=a['trade_stats']
        lines.append(f"|{NAMES[key]}|{'同股延后一分' if delayed else '首个合格分钟开价'}|{a['entries']}|{pct(a['total_return'])}|{pct(a['max_drawdown'])}|{a['sharpe']:.2f}|{t['hits']}/{t['known']}={pct(t['hit'])}|")
    lines += ['', '四分钟首个买点为结束标签09:35，即09:34—09:35分钟的首笔价；五分钟首个买点为标签09:36，即09:35—09:36分钟的首笔价。延后方案是在各自原选择已触发后，同一只至少等一根K，至09:40再次寻找板下开价，不替换其他股票。它不是把所有股票统一挪到09:35或09:36买。',
        '', '## 形态本身的区分度','',
        '为避免提前买价混入形态效果，以下对全部原Top3可买个体统一使用原五分钟后的买价和样本，比较形态通过/未通过。未叠加实体−8%门槛、未进行每日唯一选股。三日再板=D1—D3任一天收盘封板；大涨/大跌为D2收盘相对共同买价≥7%/<−5%，不是盘中极值。',
        '', '|形态方案|组别|个体数|三日再板|D2大涨率|D2大跌率|D2均值|','|---|---|---:|---:|---:|---:|---:|']
    for a in discrimination:
        lines.append(f"|{NAMES[a['policy']]}|{ {'all':'全部','pass':'通过','reject':'未通过'}[a['group']]}|{a['n']}|{pct(a['hit'])}|{pct(a['ret2']['up'])}|{pct(a['ret2']['down'])}|{pct(a['ret2']['mean'])}|")
    lines += ['', '## 每月账户表现','']
    for delayed in [False,True]:
        lines += [('### 同股延后一分' if delayed else '### 首个合格分钟开价'),'',
            '|月份|五分收益 / 回撤|四分沿用收益 / 回撤|四分重选收益 / 回撤|','|---|---:|---:|---:|']
        for month in sorted({a['month'] for a in monthly}):
            row=[month]
            for policy in NAMES:
                key=policy+('_delay' if delayed else '')
                a=next(x for x in monthly if x['month']==month and x['policy']==key)
                row.append(f"{pct(a['return_'])} / {pct(a['drawdown'])}")
            lines.append('|'+ '|'.join(row)+'|')
        lines.append('')
    lines += ['## 四分钟规则选择：训练与验证','',
        '沿用原79条候选规则，每条最多2条件；每月6个月训练、2个月验证，训练D0分数为因果折外预测；三日再板率为主，策略收益不下降与跨训练时间块稳定性为约束。只在Top1选择规则，再迁移至Top3。先冻结14个月全部规则，再评估对应测试月。训练和验证都要求D4在下一集合开始前已结束；未将测试月收益用于挑规则。下表为Top1形态选择的结果，不是每天Top3最终交易的命中率。',
        '', '|目标月|实际四分规则|训练不筛→筛后|验证不筛→筛后|','|---|---|---:|---:|']
    for r in rules:
        def item(part):
            a=r[part+'_base'];b=r[part+'_selected']
            return f"{pct(a['hit'])}({a['known']}) → {pct(b['hit'])}({b['known']})"
        lines.append(f"|{r['month']}|{r['selected']['text'].replace('五分','四分')}|{item('train')}|{item('valid')}|")
    lines += ['', '“沿用门槛”的四分方案直接借用原五分选出的每月阈值，是迁移对照；“重选规则”的四分方案才是用四分因子重新做训练/验证筛选。两者都完整报告，不按测试月结果事后拼接赢家。该历史区间已被多次研究，不是新的独立盲测。',
        '', '## 原先漏掉8笔的覆盖','', '|四分方案|追回同一股票|其中原盈利笔|','|---|---:|---:|']
    for r in recovery:
        name=NAMES[r['policy'].removesuffix('_delay')]+('，延后一分' if r['policy'].endswith('_delay') else '')
        lines.append(f"|{name}|{r['missed8_recovered_same_stock']}/8|{r['profitable_recovered']}|")
    lines += ['', '这里“追回”只表示同日同股票重新出现模拟买点，不保证实际成交，也不等于收益与原8笔相同；不同买价和现金路径会改变收益。',
        '', '## 核对与保存','',
        f"原14次五分规则选择从原R19精度数据完整复现；原106笔及同股延后98笔的股票、时间、买价、现金预算、股数和回款复现。四分特征对第5根及以后数据扰动保持不变，测试特征/标签扰动不改变规则选择。{verification['raw_unique_stock_days']}个原始股票日重算，{verification['independent_daily_cash_checks']}日账户现金独立重算通过；3个定向测试覆盖观察/买点边界、先触发排名优先和过滤不替补、同股延后。",
        '', '数值精度：R20传递表对特征保留15位小数，原R19部分记录保留原始浮点。发现1个唯一股票日在实体恰为−2%边界时存在浮点尾差；原冻结规则和历史五分结果保持不变。新四分特征统一15位小数，避免同一精确门槛受二进制尾差影响。',
        '', '执行限制：正整分钟成交量、分钟首笔价、全额成交均是回测代理，不含实时到达延迟、盘口深度、排队和冲击。已有原方案两笔买量超过整分钟历史成交量的容量问题没有因本次四分比较自动解决。',
        '', '源代码：scripts/experiment_limit_rebound_four_minutes_r24.py；报告：scripts/report_limit_rebound_four_minutes_r24.py；测试：tests/automl/test_limit_rebound_four_minutes_r24.py。完整样本、前10分钟摘录、冻结规则、分集合指标、各方案信号、账本、净值、异常及来源哈希位于outputs/stock_automl/limit_rebound_four_minutes_r24_20261015。原Excel未修改；本轮本地研究保存，云端待同步，未替换现有策略。','']
    (DOC/'README.md').write_text('\n'.join(lines))
    files=list(OUT.iterdir())+[DOC/'README.md',Path('scripts/experiment_limit_rebound_four_minutes_r24.py'),
        Path('scripts/report_limit_rebound_four_minutes_r24.py'),Path('tests/automl/test_limit_rebound_four_minutes_r24.py')]
    dump(OUT/'asset_manifest.json',{'python':platform.python_version(),'files':[{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)} for p in files if p.is_file() and p.name!='asset_manifest.json']})
    print(str(DOC/'README.md'))


if __name__=='__main__': main()

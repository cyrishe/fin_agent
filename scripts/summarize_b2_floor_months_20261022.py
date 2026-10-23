"""Calendar-month distribution of frozen V2 floor trades and two-day floors."""
import json
from decimal import Decimal, ROUND_HALF_UP

import numpy as np
import pandas as pd

from scripts import research_b2_floor_risk_20261021 as old

ROOT = old.ROOT
OUT = ROOT / 'docs/stock_automl_runs/20261022_b2_floor_months'
BASE = ROOT / 'docs/stock_automl_runs/20261022_b2_delayed_floor_exit'


def run():
    OUT.mkdir(exist_ok=True)
    f = old.load()
    calpath = ROOT / 'outputs/stock_automl/b2_2021_minute_r60_20261020/calendar.json'
    calendar = json.loads(calpath.read_text())
    index = {day: i for i, day in enumerate(calendar)}
    stpath = ROOT / 'docs/stock_automl_runs/20261021_b2_floor_factor_review/st.csv.gz'
    st = pd.read_csv(stpath).fillna({'end_date': '9999-12-31'})
    auditpath = BASE / 'minute_audit.json'
    audit = {x['sequence']: x for x in json.loads(auditpath.read_text())}
    r69path = ROOT / 'docs/stock_automl_runs/20261021_b2_d2_limit_follow_r69/results.json'
    r69 = {(x['D0'], x['股票']): x for x in json.loads(r69path.read_text())['rows']
           if x['V2放行'] == '是' and x['封板'] == '跌停'}
    assert len(r69) == 52
    details = []
    for row in f.to_dict('records'):
        d2 = row['卖出日']
        d3 = calendar[index[d2] + 1]
        status = st[st.stk_code.eq(row['stk_code']) & st.begin_date.le(d3) & st.end_date.gt(d3)]
        assert len(status) == 1
        rate = .2 if row['symbol'].startswith(('30', '68')) else (.05 if status.iloc[0].st_type in ('S', 'Y') else .1)
        floor = float((Decimal(str(row['d3_preclose'])) * (1 - Decimal(str(rate)))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
        op, cl = row['open_floor'] == '是', row['close_floor'] == '是'
        d3floor = abs(row['d3_close'] - floor) < .001
        d2locked = row['all_locked'] == '是'
        d3locked = bool(audit[row['sequence']]['d3_all_locked']) if op else None
        if op:
            evidence = audit[row['sequence']]
            assert evidence['d3_date'] == d3
            assert evidence['d3_close_floor'] == d3floor
            assert np.isclose(evidence['d3_floor'], floor)
            assert np.isclose(evidence['d3_close'], row['d3_close'])
            assert d2locked == (row['d2_high'] <= row['d2_floor'] + .001)
        if cl:
            previous = r69[(row['d0_date'], row['stk_code'])]
            assert previous['D2'] == d2 and previous['次日'] == d3
            assert np.allclose([previous['次收价'], previous['次日前收']], [row['d3_close'], row['d3_preclose']])
        details.append(dict(sequence=int(row['sequence']), symbol=row['symbol'], name=row['stock_name'],
            D0=row['d0_date'], D2=d2, D3=d3, month=d2[:7],
            open_floor=op, close_floor=cl, any_floor=op or cl,
            d2_all_locked=d2locked, d3_all_locked=d3locked, d3_close_floor=d3floor,
            two_close=cl and d3floor, open_two=op and cl and d3floor,
            two_locked=d2locked and bool(d3locked), d3_close=row['d3_close'], d3_floor=floor))
    d = pd.DataFrame(details)
    measures = ['any_floor', 'open_floor', 'close_floor', 'd2_all_locked', 'two_close', 'open_two', 'two_locked']
    months = pd.period_range(d.month.min(), d.month.max(), freq='M').astype(str)
    monthly = d.groupby('month')[measures].sum().reindex(months, fill_value=0).astype(int)
    monthly.insert(0, 'all_trades', d.groupby('month').size().reindex(months, fill_value=0))
    monthly['floor_fraction'] = monthly.any_floor / monthly.all_trades.replace(0, np.nan)
    totals = {k: int(d[k].sum()) for k in measures}
    assert totals == dict(any_floor=67, open_floor=34, close_floor=52, d2_all_locked=14, two_close=20, open_two=10, two_locked=5)
    assert monthly.all_trades.sum() == 306
    for k in measures: assert monthly[k].sum() == totals[k]
    # Merge overlapping observed D2-D3 windows only; do not infer unobserved streak lengths.
    intervals = []
    for symbol, group in d[d.two_close].groupby('symbol'):
        for x in group.sort_values('D2').to_dict('records'):
            if intervals and intervals[-1]['symbol'] == symbol and x['D2'] <= intervals[-1]['end']:
                intervals[-1]['end'] = max(intervals[-1]['end'], x['D3'])
                intervals[-1]['sequences'].append(x['sequence'])
            else:
                intervals.append(dict(symbol=symbol, start=x['D2'], end=x['D3'], sequences=[x['sequence']]))
    assert len(intervals) == 19
    sources = {str(p.relative_to(ROOT)): old.sha(p) for p in [old.SOURCE, calpath, stpath, auditpath, r69path]}
    result = dict(experiment='B2_FLOOR_MONTHS_20261022', month_basis='D2 actual market date, not D0 model month',
        consecutive_definition='D2 and next trading day D3 both close at historical price floor; at least two days, not complete streak length',
        totals=totals, all_trades=306, monthly=json.loads(monthly.reset_index(names='month').to_json(orient='records')),
        floor_trades=[x for x in details if x['any_floor']], consecutive_intervals=intervals,
        sources=sources, code_sha256=old.sha(__file__),
        verification='PASS: sums, all 34 prior minute audits, all 52 R69 next-day dates/prices, historical ST floor and overlapping windows')
    (OUT / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    rows = monthly[monthly.any_floor.gt(0)]
    table = '\n'.join(f"| {m} | {int(x.all_trades)} | {int(x.any_floor)} | {int(x.open_floor)} | {int(x.close_floor)} | {int(x.two_close)} | {int(x.open_two)} | {int(x.two_locked)} |" for m, x in rows.iterrows())
    detailtable = '\n'.join(f"| {x['sequence']} | {x['symbol']} {x['name']} | {x['D2']} | {x['D3']} | {'是' if x['open_floor'] else '否'} | {'是' if x['two_locked'] else '否'} |" for x in details if x['two_close'])
    report = f'''# 跌停交易的月份分布

冻结V2原306笔交易，按 **D2实际发生月份** 统计，不按D0模型月归属。全部跌停指此前定义的67笔“D2开盘或收盘跌停”的并集；不包含仅盘中触及跌停而开、收盘均离板的其他交易。笔数是策略交易次数，不是不同股票数量。

## 全部月份

| 月份 | 原交易数 | 开或收跌停 | 开盘跌停 | 收盘跌停 | 连续收盘跌停 | 其中开盘跌停组 | 连续全天封死 |
|---|---:|---:|---:|---:|---:|---:|---:|
{table}
| 所列月份小计 | {int(rows.all_trades.sum())} | 67 | 34 | 52 | 20 | 10 | 5 |

连续收盘跌停=同一股票D2和下一交易日D3均收于各自跌停价，即至少连续两天；连续全天封死=两天最高价都未高于各自跌停价。收盘跌停不代表整天没有卖出窗口。连续记录按起始D2月计一次，跨月不重复登记到D3月份。

表中只列有跌停交易的25个月，共280笔原交易；其他月份另有26笔交易，合为完整306笔。完整2022-05至2026-10的54个月（含无交易月和有交易但无上述跌停月）保存在[结果数据](results.json)，未列出的月份不代表市场没有跌停。2026-10只有本批交易截至10月9日的D2，不是完整月份。

## 连续跌停重点

34笔开盘跌停中，有10笔D2、D3连续收盘跌停，分布为2022-05两笔、2024-01两笔、2024-11一笔、2024-12一笔、2026-02一笔、2026-07两笔、2026-09一笔。

其中连续两天全天封死的5笔，分散在2024-01、2024-12、2026-02、2026-07、2026-09，各1笔。它们与上轮19笔延后持仓中D3全天封死的5笔逐项一致。本次补核实这5笔的D2也全天封死。

全部67笔中的连续收盘跌停为20笔；“10笔”只指之前34笔开盘跌停子集，并非更新或推翻原计数。

| 原序号 | 股票 | D2 | D3 | 属于34笔开盘组 | 两天全天封死 |
|---|---|---|---|---|---|
{detailtable}

恒尚节能603137的原序号267、268是两笔不同策略交易，D2分别为2026-07-16和07-17，对应D3为07-17和07-20，落在同一段至少三个交易日的连续收盘跌停中。因此20笔不能说成20个独立连跌事件；仅合并本次相互重叠的已核验D2—D3区间后为19段。没有追溯每只股票整个行情中首次跌停至最后跌停的完整长度。

名称沿用原表快照，历史是否ST和跌停幅度按当时记录计算，不由当前名称推断。

## 核验与保存

34笔D3标记与上轮完整分钟回执一致；52笔D2收盘跌停的D3日期、价格和前收与R69逐项一致；跌停价按官方前收、历史ST和板块规则四舍五入到分计算，已保留除息后的官方参考价。各月合计67／34／52／20／10／5与逐笔清单核对一致。

此次仅做已有固定样本的分布统计，未训练、修改策略或重算账户。数据和代码哈希、完整月份与67笔明细保存在结果数据中。脚本：`scripts/summarize_b2_floor_months_20261022.py`。
'''
    (OUT / 'README.md').write_text(report)
    print(json.dumps(totals, ensure_ascii=False))
    print(d[d.two_locked][['symbol', 'name', 'D2', 'D3']].to_string(index=False))


if __name__ == '__main__':
    run()

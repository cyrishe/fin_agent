"""Frozen V2 selections: D2-D3 upper-limit continuation and D3 return bands."""
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pandas as pd
from scripts import research_b2_floor_risk_20261021 as old

ROOT = old.ROOT
OUT = ROOT / 'docs/stock_automl_runs/20261022_b2_up_continuation'
BINS = [-float('inf'), -.10, -.05, 0, .05, .10, float('inf')]
LABELS = ['跌超10%', '跌5%至10%', '跌不足5%', '涨0%至5%', '涨5%至10%', '涨至少10%']


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    f = old.load()
    calpath = ROOT / 'outputs/stock_automl/b2_2021_minute_r60_20261020/calendar.json'
    calendar = json.loads(calpath.read_text())
    index = {day: i for i, day in enumerate(calendar)}
    stpath = ROOT / 'docs/stock_automl_runs/20261021_b2_floor_factor_review/st.csv.gz'
    st = pd.read_csv(stpath).fillna({'end_date': '9999-12-31'})
    r69path = ROOT / 'docs/stock_automl_runs/20261021_b2_d2_limit_follow_r69/results.json'
    r69 = json.loads(r69path.read_text())
    r69up = {(x['D0'], x['股票']): x for x in r69['rows'] if x['V2放行'] == '是' and x['封板'] == '涨停'}
    assert len(f) == 306 and len(r69up) == 91
    rows = []
    for x in f.to_dict('records'):
        d2 = x['卖出日']
        d3 = calendar[index[d2] + 1]
        caps = []
        for day, date in ((2, d2), (3, d3)):
            status = st[st.stk_code.eq(x['stk_code']) & st.begin_date.le(date) & st.end_date.gt(date)]
            assert len(status) == 1
            rate = .2 if x['symbol'].startswith(('30', '68')) else (.05 if status.iloc[0].st_type in ('S', 'Y') else .1)
            price = Decimal(str(x[f'd{day}_preclose'])) * (1 + Decimal(str(rate)))
            caps.append(float(price.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)))
        up2 = abs(x['d2_close'] - caps[0]) < .001
        up3 = abs(x['d3_close'] - caps[1]) < .001
        assert up2 == bool(x['d2_up']) and up3 == bool(x['d3_up'])
        morning = None
        if up2:
            source = r69up[(x['d0_date'], x['stk_code'])]
            assert source['D2'] == d2 and source['次日'] == d3
            assert abs(source['次收价'] - x['d3_close']) < .001
            assert abs(source['次日前收'] - x['d3_preclose']) < .001
            morning = source['上午7%'] == '是'
        rows.append(dict(sequence=int(x['sequence']), symbol=x['symbol'], name=x['stock_name'],
            D0=x['d0_date'], D1=x['d1_date'], D2=d2, D3=d3, model_month=x['model_month'],
            d2_limit_up=up2, d3_limit_up=up3, consecutive_up=up2 and up3,
            d2_morning_7pct=morning, d2_close=x['d2_close'], d3_reference=x['d3_preclose'],
            d3_close=x['d3_close'], d3_close_return=x['d3_close'] / x['d3_preclose'] - 1,
            d2_limit_price=caps[0], d3_limit_price=caps[1]))
    d = pd.DataFrame(rows).sort_values('sequence')
    assert d.d2_limit_up.sum() == 91 and d.d3_limit_up.sum() == 52 and d.consecutive_up.sum() == 29
    assert d.loc[d.consecutive_up, 'd2_morning_7pct'].eq(True).all()
    d['d3_band'] = pd.cut(d.d3_close_return, BINS, labels=LABELS, right=False)
    groups = {'全部选中306笔': d, 'D2涨停91笔': d[d.d2_limit_up], 'D2未涨停215笔': d[~d.d2_limit_up]}
    bands = [{**{'band': label}, **{name: int(g.d3_band.eq(label).sum()) for name, g in groups.items()}} for label in LABELS]
    for name, g in groups.items(): assert sum(x[name] for x in bands) == len(g)
    years = []
    for year, group in d.groupby(d.D2.str[:4]):
        years.append(dict(year=year, selected=len(group), d2_up=int(group.d2_limit_up.sum()),
            d3_up=int(group.d3_limit_up.sum()), consecutive=int(group.consecutive_up.sum())))
    monthly = []
    for month, group in d.groupby(d.D2.str[:7]):
        monthly.append(dict(month=month, selected=len(group), d2_up=int(group.d2_limit_up.sum()),
            d3_up=int(group.d3_limit_up.sum()), consecutive=int(group.consecutive_up.sum())))
    overlaps = []
    for symbol, group in d[d.consecutive_up].groupby('symbol'):
        last = None
        for x in group.sort_values('D2').to_dict('records'):
            if last is not None and x['D2'] <= last['end']:
                last['end'] = max(last['end'], x['D3'])
                last['sequences'].append(x['sequence'])
            else:
                last = dict(symbol=symbol, start=x['D2'], end=x['D3'], sequences=[x['sequence']])
                overlaps.append(last)
    sources = [old.SOURCE, calpath, stpath, r69path]
    result = dict(experiment='B2_UP_CONTINUATION_20261022', scope='Frozen V2 306 selected and purchased trades',
        period='D0 2022-05 through 2026-09; last D2 2026-10-09',
        definitions=dict(D2='Original planned sell day', D3='Next exchange trading day after D2',
            limit_up='Historical board/ST rounded upper price at daily close',
            d3_return='D3 close / D3 official reference preclose - 1, before fees; bins left-closed/right-open'),
        counts=dict(selected=306, d2_up=91, d3_up=52, consecutive_up=29,
            d3_up_after_d2_not_up=23, d2_up_without_d3_up=62,
            d2_morning_7pct_within_consecutive=29, overlapping_observed_intervals=len(overlaps)),
        bands=bands, years=years, months=monthly, overlap_windows=overlaps,
        sources={str(p.relative_to(ROOT)): old.sha(p) for p in sources}, code_sha256=old.sha(__file__),
        verification='All 306 D2/D3 upper limits independently reconstructed; all 91 D2 upper closes matched R69 dates and D3 prices; band totals reconcile.')
    (OUT / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    d.to_csv(OUT / '逐笔D3涨幅与连板.csv', index=False, encoding='utf-8-sig')
    bandtable = '\n'.join(f"| {r['band']} | {r['全部选中306笔']} | {r['D2涨停91笔']} | {r['D2未涨停215笔']} |" for r in bands)
    yeartable = '\n'.join(f"| {x['year']} | {x['selected']} | {x['d2_up']} | {x['consecutive']} |" for x in years)
    detail = '\n'.join(f"| {x['sequence']} | {x['symbol']} {x['name']} | {x['D2']} | {x['D3']} | {x['d3_close_return']:+.2%} |" for x in rows if x['consecutive_up'])
    report = f'''# V2选出股票：D2涨停后D3是否继续涨停

冻结的V2 **306笔实际选中并买入的交易**，D0范围2022-05—2026-09，最后一个D2为2026-10-09。D2是原定卖出日；D3是下一交易日。本报告是选出交易的后续行情观察，保留了原模型、买卖时点和月度停摆口径。

## 关键计数

- D2收盘涨停91／306笔；其中D3再次收盘涨停 **29／91笔=31.87%**。另外62笔D3没有再次收盘涨停。
- D2未收盘涨停215笔中，D3收盘涨停 **23／215笔=10.70%**。因此全306笔D3涨停共52笔。
- 29笔连续收盘涨停均属于R69记录的D2上午已超过7%组。原V2对该组计划D2盘中退出；D3行情是持股延续机会的事后观察，不是原交易规则已获得的第二板收益。若研究延长持有，须重新定义D2卖出并重放账户。
- 这里“连续”指D2、D3都收盘涨停，不等于连续两天一字封板；29是策略交易笔数，不是完全独立的市场连板事件。

## D3收盘涨跌幅分段

分母为D3官方前收价，尚未扣交易费。左端包含、右端不包含；“跌超10%”对应小于−10%，“涨至少10%”对应大于等于+10%。

| D3收盘涨跌幅 | 全部选中306笔 | D2涨停91笔 | D2未涨停215笔 |
|---|---:|---:|---:|
{bandtable}
| 合计 | 306 | 91 | 215 |

D2涨停组D3收盘平均+1.24%、中位+1.34%，但其中42／91笔D3收跌、26／91笔跌至少5%。29笔再次涨停中，有16笔落在“涨5%至10%”、13笔在“涨至少10%”；另有1笔涨至少10%却未到其涨停价。涨停价按前收与股价最小单位四舍五入后，实际百分比可以略低于10%；ST和板块幅度也不同。**判断涨停使用历史实际涨停价，不能把“≥10%”一格当成连板数。**

## 各年分布

| D2年份 | 选中交易 | D2涨停 | D2＋D3连续涨停 |
|---|---:|---:|---:|
{yeartable}
| 合计 | 306 | 91 | 29 |

29笔分布在18个D2月份。圣龙股份603178在2023-10-20与10-23的两笔选中交易对应重叠区间，证实10-20、10-23、10-24这段至少三个交易日收盘连板。因此29笔仅合并已观察到的相互重叠区间后为{len(overlaps)}段；没有追溯各股完整板次和更长连板长度。

## 29笔连续涨停交易

| 原序号 | 股票 | D2 | D3 | D3涨幅 |
|---|---|---|---|---:|
{detail}

个别股票名称沿用冻结原表快照，板块、历史ST与涨停价按当时信息计算。29笔D3涨幅均基于官方参考前收，与D2收盘一致；全部306笔日线涨停标记逐笔按历史限价复核，D2涨停91笔的D3日期、收盘价和参考前收与R69逐项一致。

来源与哈希、完整逐笔分段见[结果数据](results.json)和[逐笔明细](逐笔D3涨幅与连板.csv)。本轮未训练新的买前模型，也没有调整正式卖出规则或将事后D3走势计为原V2交易收益。
'''
    (OUT / 'README.md').write_text(report)
    print(json.dumps(result['counts'], ensure_ascii=False))
    print(json.dumps(bands, ensure_ascii=False))


if __name__ == '__main__':
    run()

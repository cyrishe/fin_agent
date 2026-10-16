"""KP1M: monthly breadth/return analysis of frozen KP1 hits; no scan or training."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'docs/stock_automl_runs/20261016_kline_survey_kp1'
CACHE = ROOT / 'outputs/stock_automl/kline_survey_kp1_20261016'
OUT = ROOT / 'docs/stock_automl_runs/20261016_kline_monthly_kp1m'
MONTHS = [f'2026-{m:02}' for m in range(4, 10)]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_report(r, s):
    ids = ['double_bottom', 'ma10_20_up', 'rsi_failure_bull', 'macd_cross_bull']
    labels = ['局部W突破', '10/20金叉', 'RSI失败摆动', 'MACD金叉']
    lines = ['# K线形态月度稳定性复核', '',
        '2026年4–9月、125个交易日，复用53份已保存命中分区，不重扫、不建模。统计全部64条规则，重点比较31条偏多候选。', '',
        '**修订结论：前次按平均收益差筛出的候选，不能视为上涨比例稳定提升的候选。22条频次适中的偏多规则，没有一条在全期后1、2或3日的上涨比例相对提升达到20%；也没有一条六个月每月三个期限上涨比例均优于市场。**', '',
        '先按[形态时间尺度复核](TIMEFRAME_REVIEW.md)区分未确认警报、当日突破、趋势指标与持续背景。不能把全部信号解释为出现即启动，也不能把短期表现弱自动解释成长期有效。', '',
        '## 统计口径', '',
        '上涨指从信号日收盘到第1、2、3个交易日收盘的累计涨跌为正，分别统计，不是各日单日涨幅，也不是期间曾经涨过。月份归属信号日，月末信号允许结局跨月。平盘不算上涨，缺少完整未来价格的样本不算下跌。', '',
        '每个信号先匹配同一日期、同一持有期限的全体合格沪深股票上涨比例，再按形态样本数加权汇总。各形态发生日期不同，匹配市场比例可以不同。上涨比例差=形态比例−匹配市场比例，单位百分点；相对提升=(形态比例÷匹配市场比例−1)×100%。50%到60%为+10个百分点、相对+20%。平均收益差另列，不能替代上涨比例。', '',
        '频次沿用此前固定筛选：半年500–25000次、至少60个信号日、每日市场占比90分位不超过10%。未根据本次月度表现修改规则。', '',
        '## 前四个候选的月度上涨比例差', '',
        '以下为后3日累计上涨比例差，单位百分点；不是相对提升百分比。', '',
        '| 信号月份 | 局部W突破 | 10/20金叉 | RSI失败摆动 | MACD金叉 |',
        '|---|---:|---:|---:|---:|']
    for month in MONTHS:
        vals = [r.loc[r.id.eq(pid)&r.scope.eq('all')&r.month.eq(month)&r.horizon.eq(3)].iloc[0] for pid in ids]
        lines.append('| '+month+' | '+' | '.join(f'{v.up_delta_pp:+.2f}' for v in vals)+' |')
    lines += ['', '| 形态 | 半年次数 | 日均 | 全期后3日上涨比例 | 匹配市场 | 相对提升 | 后3日比例领先月份 |', '|---|---:|---:|---:|---:|---:|---:|']
    for pid, label in zip(ids, labels):
        v = s.loc[s.id.eq(pid)].iloc[0]
        lines.append(f'| {label} | {v["count"]:,} | {v.daily_count:.1f} | {v.up_pct_3:.2f}% | {v.matched_market_up_pct_3:.2f}% | {v.up_relative_lift_pct_3:+.2f}% | {v.positive_breadth_months_3}/6 |')
    lines += ['', '## 月度平均收益差', '',
        '同样采用后3日、同日期匹配市场，单位百分点。收益差与上涨比例可能给出不同判断。', '',
        '| 信号月份 | 局部W突破 | 10/20金叉 | RSI失败摆动 | MACD金叉 |', '|---|---:|---:|---:|---:|']
    for month in MONTHS:
        vals = [r.loc[r.id.eq(pid)&r.scope.eq('all')&r.month.eq(month)&r.horizon.eq(3)].iloc[0] for pid in ids]
        lines.append('| '+month+' | '+' | '.join(f'{v.return_excess_pp:+.3f}' for v in vals)+' |')
    lines += ['', '## 候选优先级修订', '',
        '- **局部W突破保留为弱信号观察项。** 后3日上涨比例有5/6个月领先，但4月、9月差距很小，8月落后4.18个百分点。半年相对提升仅2.83%。排除触发日收盘涨停和一字K后，首日上涨比例差转为−0.59个百分点，第三日仅+0.97个百分点；不能称为稳健可交易优势。',
        '- **10/20金叉仅保留作行情分组对照。** 6–8月领先，4、5、9月落后；较充分的样本并未形成跨月稳定优势。',
        '- **RSI失败摆动从优先候选降为行情依赖研究项。** 7月强、4月弱，9月仅38次。去掉7月，其余五个月合计后3日上涨比例差为−5.40个百分点，平均收益差为−0.488个百分点。这是集中度诊断，不是另一次策略选择或独立验证。',
        '- **MACD金叉保留作宽候选池对照。** 后3日平均收益差5/6个月为正，但上涨比例仅3/6个月领先。它说明“少数较大涨幅抬高均值”与“更多股票上涨”不能混同，当前汇总尚不能确定具体分布成因。',
        '- **未找到可以据此直接进入建模的稳定强形态。** 不能为了凑候选，把单月优势或收益均值优势当作跨月上涨概率优势。', '',
        '## 全部偏多及背景规则', '',
        '以下各列“概率差”均为上涨比例差，单位百分点。月数表示同月后1、2、3日均领先；描述性月度符号并非显著性检验。', '',
        '| 形态 | 半年次数 | 日均 | 频次适中 | 后1日概率差 | 后2日概率差 | 后3日概率差 | 三期限均领先月份 |', '|---|---:|---:|---|---:|---:|---:|---:|']
    for v in s.loc[~s.role.eq('bear_control')].sort_values('count').itertuples(index=False):
        lines.append(f'| {v.name} | {v.count:,} | {v.daily_count:.1f} | {"是" if v.moderate_frequency else "否"} | {v.up_delta_pp_1:+.2f} | {v.up_delta_pp_2:+.2f} | {v.up_delta_pp_3:+.2f} | {v.months_all3_breadth_positive}/6 |')
    lines += ['', '## 明细与证据', '',
        '- [全部64条规则逐月及全期明细](monthly_metrics.csv)：包含后1/2/3日、样本量、日均、形态和匹配市场上涨比例、百分点差、相对提升、平均收益差；另有排除涨停一字及连续首次信号口径。',
        '- [按形态汇总与月度方向计数](pattern_summary.csv)。',
        '- [复算核验](verification.json)：53份缓存哈希一致，192组全期频次、上涨比例和收益差与KP1对齐，各月加权可还原全期；W底六个月匹配市场独立复核。', '',
        '六个月及重叠股票日不是大量独立行情样本；按月展示改善了可解释性，不自动建立统计显著性或样本外稳定性。未检验更长持有期、下午可见信号、费用和实际成交。底层行情口径和数据限制沿用KP1。材料保存为本地派生研究包，阿里云盘待同步，不改原KP1归档。']
    (OUT/'README.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def main():
    OUT.mkdir(exist_ok=True)
    manifest = json.loads((CACHE / 'cache_manifest.json').read_text())['file_sha256']
    paths = sorted((CACHE / 'parts').glob('*.pkl.gz'))
    for p in paths:
        assert digest(p) == manifest[str(p.relative_to(ROOT))], p
    defs = json.loads((BASE / 'definitions.json').read_text())
    freq = pd.read_csv(BASE / 'morphology_table.csv').set_index('id')
    b = pd.concat([pd.read_pickle(p) for p in paths], ignore_index=True)
    b['month'] = b.date.dt.strftime('%Y-%m')
    assert set(b.month.unique()) == set(MONTHS)
    days = b[['date', 'month']].drop_duplicates().groupby('month').size()
    for h in (1, 2, 3):
        # Flat is not up; unavailable forward prices remain missing.
        b[f'u{h}'] = np.where(b[f'r{h}'].notna(), (b[f'r{h}'] > 1e-9).astype(float), np.nan)
        b[f'mu{h}'] = b.groupby('date')[f'u{h}'].transform('mean')
        b[f'mr{h}'] = b.groupby('date')[f'r{h}'].transform('mean')
    rows = []
    for p in defs:
        pid = p['id']
        hits = b.loc[b['hit_' + pid]]
        for scope, e in [('all', hits), ('nonlimit', hits.loc[~hits.limit_up & ~hits.one_price]),
                         ('episode_start', hits.loc[hits['start_' + pid]])]:
            for month in ['all'] + MONTHS:
                g = e if month == 'all' else e.loc[e.month.eq(month)]
                for h in (1, 2, 3):
                    v = g.loc[g[f'r{h}'].notna()]
                    up = v[f'u{h}'].mean() * 100
                    market = v[f'mu{h}'].mean() * 100
                    rows.append(dict(id=pid, name=p['name'], role=freq.loc[pid, 'role'],
                        scope=scope, month=month, horizon=h, count=len(g), valid_count=len(v),
                        signal_days=g.date.nunique(), daily_count=len(g)/(days.sum() if month == 'all' else days[month]),
                        up_pct=up, matched_market_up_pct=market, up_delta_pp=up-market,
                        up_relative_lift_pct=100*(up/market-1) if market > 0 else np.nan,
                        mean_return_pct=v[f'r{h}'].mean(), market_return_pct=v[f'mr{h}'].mean(),
                        return_excess_pp=(v[f'r{h}']-v[f'mr{h}']).mean()))
    r = pd.DataFrame(rows)
    r.to_csv(OUT / 'monthly_metrics.csv', index=False)
    summaries = []
    for p in defs:
        pid = p['id']
        x = r.loc[r.id.eq(pid) & r.scope.eq('all')]
        m = x.loc[x.month.ne('all')]
        s = dict(id=pid, name=p['name'], role=freq.loc[pid, 'role'],
                 count=int(freq.loc[pid, 'count_6m']), daily_count=freq.loc[pid, 'avg_per_market_day'],
                 moderate_frequency=bool(freq.loc[pid, 'moderate_frequency']))
        for h in (1, 2, 3):
            mh = m.loc[m.horizon.eq(h)]
            total = x.loc[x.month.eq('all') & x.horizon.eq(h)].iloc[0]
            for key in ['up_pct','matched_market_up_pct','up_delta_pp','up_relative_lift_pct','return_excess_pp']:
                s[f'{key}_{h}'] = total[key]
            s[f'positive_breadth_months_{h}'] = int(mh.up_delta_pp.gt(0).sum())
            s[f'positive_return_months_{h}'] = int(mh.return_excess_pp.gt(0).sum())
            s[f'min_month_breadth_pp_{h}'] = mh.up_delta_pp.min()
            s[f'max_month_breadth_pp_{h}'] = mh.up_delta_pp.max()
        s['months_all3_breadth_positive'] = int(m.groupby('month').up_delta_pp.min().gt(0).sum())
        s['months_all3_return_positive'] = int(m.groupby('month').return_excess_pp.min().gt(0).sum())
        s['min_month_count'] = int(m.loc[m.horizon.eq(1), 'count'].min())
        summaries.append(s)
    s = pd.DataFrame(summaries)
    s.to_csv(OUT / 'pattern_summary.csv', index=False)
    write_report(r, s)
    # Reconcile every pattern and horizon with the earlier independently saved aggregates.
    checked = 0
    for row in r.loc[r.scope.eq('all') & r.month.eq('all')].itertuples():
        old = freq.loc[row.id]
        assert row.count == old.count_6m
        assert np.isclose(row.return_excess_pp, old[f'market_excess{row.horizon}_pp'], atol=1e-10)
        assert np.isclose(row.up_pct, old[f'win{row.horizon}_pct'], atol=1e-10)
        monthly = r.loc[r.id.eq(row.id) & r.scope.eq('all') & r.month.ne('all') & r.horizon.eq(row.horizon)]
        assert monthly.valid_count.sum() == row.valid_count
        for key in ['up_pct','matched_market_up_pct','up_delta_pp','return_excess_pp']:
            usable = monthly.loc[monthly.valid_count.gt(0)]
            assert np.isclose(np.average(usable[key], weights=usable.valid_count), getattr(row,key), atol=1e-10)
        checked += 1
    # Check market matching independently on the flagship W example.
    for month in MONTHS:
        v = b.loc[b.month.eq(month) & b.r3.notna()]
        e = v.loc[v.hit_double_bottom]
        market = v.groupby('date').u3.mean()
        independent = sum(market[d] for d in e.date) / len(e) * 100
        actual = r.loc[r.id.eq('double_bottom') & r.scope.eq('all') & r.month.eq(month) & r.horizon.eq(3)].iloc[0]
        assert np.isclose(independent, actual.matched_market_up_pct, atol=1e-10)
    receipt = dict(experiment='KP1M', source='KP1 saved stock-date hits and labels', pattern_rescan=False,
        model_training=False, verified_parts=len(paths), stock_days=len(b), trading_days=int(days.sum()),
        patterns=len(defs), aggregate_reconciliations=checked, independent_month_market_checks=6,
        monthly_rows=len(r), base_manifest_sha256=digest(CACHE/'cache_manifest.json'),
        definitions_sha256=digest(BASE/'definitions.json'), script_sha256=digest(Path(__file__)),
        month_basis='signal date', up_definition='t+h close/reference-price index > t close by >1e-9 return percentage points',
        baseline='same signal date, same horizon, all eligible market stocks with valid labels; weighted by pattern occurrence count',
        scopes='nonlimit excludes signal-day close-limit and one-price hits; episode_start uses cached starts; baseline remains full eligible market',
        note='Monthly signs are descriptive, not independent hypothesis tests. No 5–20 day test or afternoon execution test.')
    (OUT/'verification.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    print(s.loc[s.role.eq('bull') & s.moderate_frequency,
        ['id','name','count','months_all3_breadth_positive','months_all3_return_positive',
         'up_delta_pp_1','up_delta_pp_2','up_delta_pp_3','up_relative_lift_pct_3','positive_breadth_months_3']]
        .sort_values(['months_all3_breadth_positive','up_delta_pp_3'],ascending=False).round(3).to_string(index=False))
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()

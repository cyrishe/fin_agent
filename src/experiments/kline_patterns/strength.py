"""Conservative business triage, separate from mathematical pattern validity.

Thresholds are local exclusion conventions, not calibrated success probabilities.
All relevant dimensions must be small, and context must be uneventful, to skip
visual work. Missing evidence preserves eligibility. No weighted global score.
"""
import math

import pandas as pd

REVISION = 'conservative_strength_v1'


def number(value):
    try:
        return float(value) if math.isfinite(float(value)) else None
    except (ValueError, TypeError):
        return None


def ratio(a, b):
    a, b = number(a), number(b)
    return a / b if a is not None and b is not None and b > 0 else None


def default_priority(pattern):
    from .catalog import BASIC_PATTERN_IDS
    pid = pattern['id']
    if pid in BASIC_PATTERN_IDS:
        return '基础观察，通常并入普通走势；用户关注或出现明显矛盾时可优先。'
    if pid.startswith(('double_', 'inside_break_', 'three_methods_', 'ma20_60_', 'rsi_failure_')):
        return '结构信息较完整，优先观察其延续或失败；不是高胜率承诺。'
    return '局部变化或早期线索，结合位置、力度和用户问题选择。'


def price_boundaries(pattern, candidate, f):
    """Named structural references; MA boundaries move, candle/neck levels do not."""
    pid, at = pattern['id'], candidate['at']
    start, end = candidate['start'], candidate['end']
    r = f.iloc[at]
    def fixed(value):
        return pd.Series(value, index=f.index, dtype=float)
    if pid.startswith('double_'):
        key = 'lo_neck_inner' if pid.endswith('bottom') else 'hi_neck_inner'
        return [('颈线', fixed(r[key]))]
    if pid.startswith('inside_break_'):
        return [('母线高点', fixed(f.h.iloc[at-2])), ('母线低点', fixed(f.l.iloc[at-2]))]
    if pid.startswith('ma20_60_'):
        return [('当日MA20', f.ma20), ('当日MA60', f.ma60)]
    if pid in ('ma20_retest', 'ma20_breakdown'):
        return [('当日MA20', f.ma20)]
    if pid.startswith('boll_squeeze_'):
        return [('信号日前20日价格区间上沿', fixed(r.ph20)),
                ('信号日前20日价格区间下沿', fixed(r.pl20))]
    # This is the actual subject range, not just the final signal candle.
    subject = f.iloc[start:end+1]
    return [('主体高点', fixed(subject.h.max())), ('主体低点', fixed(subject.l.min()))]


def evolution(pattern, candidate, f):
    at = candidate['at']
    if at == len(f)-1:
        return '信号后暂无已完成K线。', []
    post = f.iloc[at+1:]
    move = ratio(f.c.iloc[-1]-f.c.iloc[at], f.c.iloc[at])
    notes = [f'截至{f.index[-1]:%Y-%m-%d}，后续{len(post)}根；较信号收盘变化{move:+.2%}。' if move is not None else '后续价格变化未知。']
    notable = []
    for label, line in price_boundaries(pattern, candidate, f):
        values = f.c.iloc[at:] - line.iloc[at:]
        known = values.notna()
        # Consecutive close crossings only. Missing bars cannot prove a crossing.
        up = known & known.shift(1, fill_value=False) & (values > 0) & (values.shift(1) <= 0)
        down = known & known.shift(1, fill_value=False) & (values < 0) & (values.shift(1) >= 0)
        last = number(values.iloc[-1])
        current = '未知' if last is None else '上方' if last > 0 else '下方' if last < 0 else '等于边界'
        initial = number(values.iloc[0])
        initial_side = '未知' if initial is None else '上方' if initial > 0 else '下方' if initial < 0 else '等于边界'
        notes.append(f'{f.index[at]:%Y-%m-%d}信号日收盘在{label}{initial_side}。')
        if up.any() or down.any():
            for date in values.index[up | down]:
                direction = '上穿' if up.loc[date] else '下穿'
                notes.append(f'{date:%Y-%m-%d}收盘{direction}{label}。')
            notes.append(f'最新收盘在{label}{current}。')
            notable.append(f'后续穿越{label}，需解释持续或失败')
        else:
            notes.append(f'后续未观察到收盘穿越{label}；最新在{current}。')
    return ''.join(notes), notable


def profile(pattern, candidate, f):
    """Return measured dimensions and a conservative skip decision for one hit."""
    pid, at = pattern['id'], candidate['at']
    start, end = candidate['start'], candidate['end']
    r = f.iloc[at]
    def value(key, offset=0):
        pos = at + offset
        return number(f.iloc[pos].get(key)) if 0 <= pos < len(f) else None
    def delta(key, a=0, b=-1):
        x, y = value(key, a), value(key, b)
        return abs(x-y) if x is not None and y is not None else None
    atr = value('atr', -1)
    body = ratio(value('body'), value('body_avg'))
    span = ratio(value('range'), atr)
    move5 = ratio(delta('c', 0, -5), atr)
    prior = None
    if start >= 6:
        prior = ratio(abs(f.c.iloc[start-1]-f.c.iloc[start-6]), f.atr.iloc[start-1])
    metrics = []
    def add(label, val, below, unit='倍'):
        metrics.append({'label': label, 'value': number(val), 'weak_below': below, 'unit': unit})

    if '_div_' in pid:
        side = 'lo' if pid.endswith('bull') else 'hi'
        # Values belong to the same pair of price pivots, never independent RSI pivots.
        add('两价格极值差/第二极值ATR', ratio(abs(r[side+'_p2']-r[side+'_p1']), r[side+'_atr2']), .1)
        if pid.startswith('rsi_'):
            add('同两极值RSI差', abs(r[side+'_rsi2']-r[side+'_rsi1']), 3, '点')
        else:
            add('同两极值DIF差/第二极值ATR', ratio(abs(r[side+'_dif2']-r[side+'_dif1']), r[side+'_atr2']), .03)
    elif pid.startswith('double_'):
        side = 'lo' if pid.endswith('bottom') else 'hi'
        neck = r[side+'_neck_inner']
        depth = neck-max(r.lo_p1, r.lo_p2) if side == 'lo' else min(r.hi_p1, r.hi_p2)-neck
        add('中段起伏/第二极值ATR', ratio(depth, r[side+'_atr2']), 1.25)
        add('收盘越颈线幅度/前日ATR', ratio(abs(r.c-neck), atr), .1)
    elif pid.startswith('inside_break_'):
        edge = f.h.iloc[at-2] if pid.endswith('bull') else f.l.iloc[at-2]
        add('越母线距离/前日ATR', ratio(abs(r.c-edge), atr), .1)
        add('末根实体/常态', body, 1.25)
        add('末根振幅/前日ATR', span, 1)
        add('末根/前两根平均实体', ratio(r.body, f.body.iloc[at-2:at].mean()), 2)
    elif pid.startswith('boll_squeeze_'):
        edge = r.bu if pid.endswith('bull') else r.bl
        add('越轨距离/前日ATR', ratio(abs(r.c-edge), atr), .1)
        add('带宽相对前日', ratio(value('bw'), value('bw', -1)), 1.1)
        add('末根振幅/前日ATR', span, .8)
    elif pid.startswith('rsi_failure_'):
        side = 'rlo' if pid.endswith('bull') else 'rhi'
        add('RSI越摆动边界幅度', abs(r.rsi-r[side+'_neck']), 2, '点')
        add('RSI内部摆动幅度', abs(r[side+'_neck']-r[side+'_p2']), 5, '点')
        add('五日收盘净变化/前日ATR', move5, .5)
    elif pid.startswith('rsi_'):
        edge = 50 if '_mid_' in pid else 30 if pid.endswith('bull') else 70
        add('RSI越阈值幅度', abs(r.rsi-edge), 2, '点')
        add('RSI当日变化', delta('rsi'), 3, '点')
        add('五日收盘净变化/前日ATR', move5, .5)
    elif pid.startswith('macd_'):
        if '_hist_' in pid:
            add('两日柱体收敛/前日ATR', ratio(delta('hist', 0, -2), atr), .03)
        else:
            add('越界幅度/前日ATR', ratio(abs(r.dif if '_zero_' in pid else r.dif-r.dea), atr), .03)
        add('DIF当日变化/前日ATR', ratio(delta('dif'), atr), .02)
        add('五日收盘净变化/前日ATR', move5, .5)
    elif pid.startswith(('ma5_10_', 'ma10_20_', 'ma20_60_')):
        short, long = ('ma5', 'ma10') if pid.startswith('ma5_') else ('ma10', 'ma20') if pid.startswith('ma10_') else ('ma20', 'ma60')
        add('均线间距/前日ATR', ratio(abs(r[short]-r[long]), atr), .05)
        add('短均线五日变化/前日ATR', ratio(delta(short, 0, -5), atr), .1)
        add('五日收盘净变化/前日ATR', move5, .5)
    elif pid == 'ma20_retest':
        add('此前离MA20距离/前日ATR', ratio(abs(f.c.iloc[at-1]-f.ma20.iloc[at-1]), atr), .5)
        add('日内回收/前日ATR', ratio(r.c-r.l, atr), .3)
        add('MA20五日变化/前日ATR', ratio(delta('ma20', 0, -5), atr), .2)
    elif pid == 'ma20_breakdown':
        add('跌破MA20幅度/前日ATR', ratio(abs(r.c-r.ma20), atr), .1)
        add('收盘变动/前日ATR', ratio(delta('c'), atr), .3)
        add('MA20五日变化/前日ATR', ratio(delta('ma20', 0, -5), atr), .1)
    elif pid == 'ma_bull_stack':
        add('MA5/30间距/前日ATR', ratio(abs(r.ma5-r.ma30), atr), .5)
        add('MA20五日变化/前日ATR', ratio(delta('ma20', 0, -5), atr), .2)
        add('五日收盘净变化/前日ATR', move5, .5)
    elif pid == 'ma_compression':
        old_spread = max(f.iloc[at-5][['ma5', 'ma10', 'ma20']])-min(f.iloc[at-5][['ma5', 'ma10', 'ma20']]) if at >= 5 else None
        add('五日前均线间距/前日ATR', ratio(old_spread, atr), .5)
        add('五日收盘净变化/前日ATR', move5, .5)
    elif pid == 'kdj_high_turn':
        add('K当日下降幅度', delta('k'), 3, '点')
        add('K/D间距', abs(r.k-r.d), 2, '点')
        add('五日收盘净变化/前日ATR', move5, .5)
    elif pid.startswith('volume_'):
        add('收盘变动/前日ATR', ratio(delta('c'), atr), .25)
        add('量能偏离常态', ratio(r.v, r.vbase) if pid.endswith('expand') else ratio(r.vbase, r.v), 1.7)
        add('五日收盘净变化/前日ATR', move5, .5)
    elif pattern['family'] == '单根影线' or pid.startswith('tweezer_'):
        add('末根振幅/前日ATR', span, .8)
        add('最长影线/前日ATR', ratio(max(r.upper, r.lower), atr), .5)
        add('主体前五日净移动/此前ATR', prior, .8)
    elif pid.startswith(('harami_', 'three_hold_')):
        lead = f.iloc[start]
        inner = f.iloc[start+1:end+1].body.mean()
        add('首根实体/常态', ratio(lead.body, lead.body_avg), 1.25)
        add('首根/后续平均实体', ratio(lead.body, inner), 2)
        add('主体前五日净移动/此前ATR', prior, 1)
    elif pattern['family'] == '蜡烛组合':
        add('末根实体/常态', body, 1.25)
        subject = f.iloc[start:end+1]
        add('主体全振幅/前日ATR', ratio(subject.h.max()-subject.l.min(), atr), 1)
        add('主体前五日净移动/此前ATR', prior, 1)
        if pid.startswith('engulf_'):
            add('末根/前根实体', ratio(r.body, f.body.iloc[at-1]), 1.5)
    else:
        raise ValueError(f'No strength convention for {pid}')

    after, crossings = evolution(pattern, candidate, f)
    protections = []
    # Crossing a tiny candle many days later is routine noise, not an automatic
    # reason to promote an originally weak signal. Larger explicit structures
    # retain meaningful failures; early substantial follow-through is separate.
    subject = f.iloc[start:end+1]
    structural_span = ratio(subject.h.max()-subject.l.min(), atr)
    if pid.startswith(('double_', 'inside_break_', 'three_methods_')) and structural_span is not None and structural_span >= 1:
        protections.extend(crossings)
    volume = ratio(value('v'), value('vbase'))
    if volume is None:
        protections.append('成交参与数据不足，保留判断空间')
    elif volume >= 1.5:
        protections.append('成交量至少为前20日均量1.5倍')
    if pid == 'ma20_retest' and (contraction := ratio(r.v, r.v5)) is not None and contraction <= .6:
        protections.append('回踩量能缩至此前五日均量六成以内')
    if pid.startswith(('harami_', 'three_hold_', 'three_methods_')):
        contraction = ratio(f.v.iloc[start+1:end+1].mean(), f.v.iloc[start])
        if contraction is not None and contraction <= .6:
            protections.append('整理段平均量缩至首根六成以内')
    history = f.iloc[max(0, start-20):start]
    if len(history) < 20 or not (history.valid > 0).all() or not (subject.valid > 0).all() or atr is None:
        protections.append('结构或前20日位置证据不完整，保留')
    elif subject.h.max() >= history.h.max()-.2*atr or subject.l.min() <= history.l.min()+.2*atr:
        protections.append('主体触及或越过此前20日价格区间边缘')
    if at < len(f)-1:
        excursion = ratio((f.c.iloc[at+1:at+4]-r.c).abs().max(), atr)
        if excursion is not None and excursion >= 1:
            protections.append('随后三根内收盘曾移动至少1个信号前日ATR')
    weak = bool(metrics) and all(m['value'] is not None and m['value'] < m['weak_below'] for m in metrics)
    # Unknown/strong dimensions or noteworthy context survive; not a claim of strength.
    skip = weak and not protections
    return {'revision': REVISION, 'measurements': metrics, 'skip_visual': skip,
            'reason': '各项对比都偏小，且未见区间边缘、显著量能或后续结构变化；保留数值事实，省略专门看图。' if skip else '未达到保守排除条件；不等于强信号，仍按问题选择。',
            'priority_hint': default_priority(pattern), 'context_reasons': protections,
            'evolution': after}


def brief(profile):
    dimensions = '；'.join(f"{m['label']}={m['value']:.2f}{m['unit']}" if m['value'] is not None else m['label']+'未知' for m in profile['measurements'])
    return dimensions+'。'+('；'.join(profile['context_reasons'])+'。' if profile['context_reasons'] else '')

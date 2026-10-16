"""Replayable K-line evidence workflow, shared by research and the chat adapter.

Deterministic facts -> independent basic reading -> candidate selection ->
isolated pattern reviews -> text-only main synthesis. The caller owns transport, persistence and usage records.
"""
from pathlib import Path
import time

import pandas as pd

from .catalog import CATALOG, DEFAULT_CATALOG
from .engine import detect, evaluate
from .run import clean, operands
from .visual_review import evidence_brief, interpret, target_brief
from .strength import profile as strength_profile, brief as strength_brief

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / 'src/skills/finance-business/skills/kline-analysis'
BASIC_SKILL = SKILL.parent / 'kline-basic-reading'
PATTERNS = {p['id']: p for p in CATALOG}


def subject_interval(pattern, f, at):
    """Actual event bars; indicator warmup/trend baselines remain background."""
    pid = pattern['id']
    if pid.startswith('rsi_failure_'):
        side = 'rlo' if pid.endswith('bull') else 'rhi'
        return int(at - f.iloc[at][side+'_age'] - f.iloc[at][side+'_sep']), at
    if '_div_' in pid or pid.startswith('double_'):
        side = 'lo' if pid.endswith(('bull', 'bottom')) else 'hi'
        row = f.iloc[at]
        return int(row[side+'_i1']), int(row[side+'_i2']) if '_div_' in pid else at
    if pattern['family'] == '单根影线':
        return at, at
    if pid.startswith('ma20_60_'):
        return at-3, at
    if pid.startswith('boll_squeeze_') and 'bw[-3]' in pattern['formula']:
        return at-3, at
    if pattern['family'] == '蜡烛组合':
        if pid.startswith(('engulf_', 'harami_', 'tweezer_')) or pid in ('dark_cloud', 'piercing'):
            count = 2
        elif pid.startswith('three_methods_'):
            count = 5
        elif pid.startswith('three_hold_') or pid == 'bull_swallow_three':
            count = 4
        else:
            count = 3
        return at-count+1, at
    lengths = {'ma20_retest': 2, 'ma20_breakdown': 2, 'ma_bull_stack': 6,
               'ma_compression': 1, 'kdj_high_turn': 2}
    if pid.startswith('macd_hist_'):
        return at-2, at
    if pid in lengths:
        return at-lengths[pid]+1, at
    # Remaining definitions are two-point crossing/return/volume events.
    if pid.startswith(('ma5_10_', 'ma10_20_', 'volume_', 'macd_cross_',
                       'macd_zero_', 'rsi_reentry_', 'rsi_mid_', 'boll_squeeze_')):
        return at-1, at
    raise ValueError(f'No event interval defined for {pid}')


def recent_facts(f, start, end):
    keys = ['o', 'h', 'l', 'c', 'v', 'vbase', 'ma5', 'ma20', 'ma60',
            'dif', 'dea', 'hist', 'rsi', 'body', 'lower', 'upper']
    rows = f.iloc[start:end+1][keys].copy()
    rows.insert(0, 'date', rows.index.strftime('%Y-%m-%d'))
    return clean(rows.to_dict('records'))


def candidate_facts(pattern, f, at, code, instance_id):
    start, end = subject_interval(pattern, f, at)
    evidence = []
    for condition in pattern['conditions']:
        value = evaluate(condition['expr'], f, at)
        evidence.append({'condition': condition['label'], 'expr': condition['expr'],
                         'result': None if pd.isna(value) else bool(value),
                         'values': operands(condition['expr'], f, at)})
    return clean({'stock_code': code, 'signal_date': str(f.index[at].date()),
                  'target': {'symbol': code, 'instance_id': instance_id,
                             'start_date': str(f.index[start].date()),
                             'end_date': str(f.index[end].date()),
                             'confirmation_date': str(f.index[at].date()),
                             'anchors': {label: {'date': str(f.index[pos].date())}
                                         for label, pos in [('A', start), ('B', end)]}},
                  'operands': evidence, 'recent_days': recent_facts(f, max(0, min(start, at-7)), at),
                  'volume_ratio_to_prior20': f.v.iloc[at]/f.vbase.iloc[at],
                  'context_notes': pattern_context(pattern, f, at, start)})


def pattern_context(pattern, f, at, start):
    """Small same-source degree measurements for interpretation, without a strength score."""
    import math
    r=f.iloc[at];notes=[]
    def ratio(a,b):
        return float(a/b) if pd.notna(a) and pd.notna(b) and b>0 else None
    def add(label,value,unit='倍'):
        if value is not None and math.isfinite(value):notes.append(f'{label}{value:+.2f}{unit}。')
    atr=f.atr.iloc[at-1] if at>0 else float('nan')
    if start>=6:
        add('主体开始前五个收盘间隔的净变化/前日ATR：',ratio(f.c.iloc[start-1]-f.c.iloc[start-6],f.atr.iloc[start-1]))
    if pattern['family'] in ('蜡烛组合','单根影线'):
        add('末根实体/此前20根平均实体：',ratio(r.body,r.body_avg))
        add('末根振幅/前日ATR：',ratio(r['range'],atr))
    add('信号收盘偏离MA20/前日ATR：',ratio(r.c-r.ma20,atr))
    pid=pattern['id']
    if '_div_' in pid:
        side='lo' if pid.endswith('bull') else 'hi'
        add('第二价格极值减第一极值/第二极值ATR：',ratio(r[side+'_p2']-r[side+'_p1'],r[side+'_atr2']))
        if pid.startswith('rsi_'):
            add('同两价格极值对应的RSI变化：',r[side+'_rsi2']-r[side+'_rsi1'],'点')
        else:
            add('同两价格极值对应的DIF变化/第二极值ATR：',ratio(r[side+'_dif2']-r[side+'_dif1'],r[side+'_atr2']))
    if pid.startswith('double_'):
        side='lo' if pid.endswith('bottom') else 'hi'
        key=side+('_neck_inner' if '_neck_inner[' in pattern['formula'] else '_neck')
        add('两个价格极值相隔：',r[side+'_sep'],'根K线')
        add('首极值前十个收盘间隔净变化/首极值前日ATR：',r[side+'_prior_move_atr'])
        add('收盘减颈线/前日ATR：',ratio(r.c-r[key],atr))
    elif pid.startswith('inside_break_'):
        boundary=f.h.iloc[at-2] if pid.endswith('bull') else f.l.iloc[at-2]
        add('收盘减母线突破边界/前日ATR：',ratio(r.c-boundary,atr))
    elif pid.startswith('boll_squeeze_'):
        add('收盘减此前20日区间边界/前日ATR：',ratio(r.c-(r.ph20 if pid.endswith('bull') else r.pl20),atr))
        boundary=r.ph20 if pid.endswith('bull') else r.pl20
        if pd.notna(boundary):
            crossed=r.c>boundary if pid.endswith('bull') else r.c<boundary
            notes.append('信号日收盘'+('已越过' if crossed else '尚未越过')+'此前20根价格区间边界；与布林越带分开解释。')
    return '\n'.join(notes)


def scan(f, code, window=10, catalog=DEFAULT_CATALOG):
    candidates = []
    unknown = 0
    for p in catalog:
        result = detect(p, f).iloc[-window:]
        unknown += int(result.isna().sum())
        for date in result.index[result.fillna(False).astype(bool)]:
            at = int(f.index.get_loc(date))
            cid = p['id']+'@'+str(date.date())
            start, end = subject_interval(p, f, at)
            candidates.append({'id': cid, 'pattern_id': p['id'], 'at': at,
                               'start': start, 'end': end,
                               'facts': candidate_facts(p, f, at, code, cid)})
    return candidates, unknown


def selected_candidates(response, candidates):
    """Enforce the user's actual call budget before any images are reviewed."""
    ids = (response.get('parsed') or {}).get('selected_ids')
    if not isinstance(ids, list) or any(not isinstance(cid, str) for cid in ids):
        raise ValueError('Selection must provide a list of candidate IDs')
    lookup = {c['id']: c for c in candidates}
    chosen = []
    for cid in dict.fromkeys(ids):
        if cid in lookup:
            chosen.append(lookup[cid])
        if len(chosen) == 3:
            break
    return chosen


def context_brief(f, window=10):
    """Computed facts for text selection/synthesis; not a second VLM input."""
    segment = f.iloc[-window:]
    valid = segment[segment.valid > 0]
    direction = lambda a, b: '高于' if a>b else '低于' if a<b else '等于'
    lines = [f"行情截至 {f.index[-1]:%Y-%m-%d}；分析窗口 {segment.index[0]:%Y-%m-%d} 至 {segment.index[-1]:%Y-%m-%d}，共 {len(segment)} 条日线。",
             '同源复权价缩放至截止日真实收盘价；成交量以股计算，比值无量纲；MACD柱=DIF−DEA。',
             f"程序统计有效K线：{int((valid.c>valid.o).sum())}阳、{int((valid.c<valid.o).sum())}阴、{int((valid.c==valid.o).sum())}根开收相同；缺失/无效 {len(segment)-len(valid)} 条。"]
    if len(valid):
        lines.append(f"窗口首尾收盘 {valid.c.iloc[0]:.2f} → {valid.c.iloc[-1]:.2f}，首尾变动 {100*(valid.c.iloc[-1]/valid.c.iloc[0]-1):+.2f}%；高低范围 {valid.l.min():.2f}—{valid.h.max():.2f}。")
        lines.append(f"窗口成交量最小日 {valid.v.idxmin():%Y-%m-%d}，最大日 {valid.v.idxmax():%Y-%m-%d}。")
    for key in ('ma5', 'ma20', 'ma60'):
        known = valid[valid[key].notna()]
        lines.append(f"窗口{key.upper()}关系：收盘高于 {int((known.c>known[key]).sum())} 日、低于 {int((known.c<known[key]).sum())} 日、相等 {int((known.c==known[key]).sum())} 日，未知 {len(segment)-len(known)} 日。")
    last = f.iloc[-1]
    facts = {'recent_days': recent_facts(f, max(0, len(f)-2), len(f)-1),
             'signal_date': str(f.index[-1].date()), 'volume_ratio_to_prior20': clean(last.v/last.vbase)}
    lines.append('最近两日及最新变化：\n'+evidence_brief(facts))
    if last.valid > 0:
        lines.append(f"最新开/收 {last.o:.2f}/{last.c:.2f}；下影{direction(last.lower,last.body)}实体。")
    averages = [f"{key.upper()}={last[key]:.3f}" for key in ('ma5', 'ma20', 'ma60') if pd.notna(last[key])]
    if averages:
        lines.append('最新同源均线：'+'；'.join(averages)+'。')
    if pd.notna(last.rsi):lines.append(f"最新RSI14={last.rsi:.2f}。")
    return '\n'.join(lines)


def subsequent_brief(candidate, f):
    """Describe observed evolution, without declaring a pattern invalid in code."""
    post = f.iloc[candidate['at']+1:]
    if not len(post): return '信号后暂无已完成K线。'
    text = (f"信号后至 {f.index[-1]:%Y-%m-%d} 共 {len(post)} 条日线；收盘较信号日 "
            f"{100*(f.c.iloc[-1]/f.c.iloc[candidate['at']]-1):+.2f}%；"
            f"随后最低 {post.l.min():.2f}（{post.l.idxmin():%Y-%m-%d}），最高 {post.h.max():.2f}（{post.h.idxmax():%Y-%m-%d}）。")
    signal = f.iloc[candidate['at']]
    for edge, mask in [('低于信号日最低价', post.c < signal.l),
                       ('高于信号日最高价', post.c > signal.h)]:
        dates = post.index[mask]
        text += (f"后续收盘已{edge}，首次 {dates[0]:%Y-%m-%d}。" if len(dates)
                 else f"后续收盘尚未{edge}。")
    return text


def candidate_brief(candidate, f):
    p = PATTERNS[candidate['pattern_id']]
    facts = candidate['facts']
    strength = candidate['strength']
    return '\n'.join([f"{candidate['id']} | {p['name']} | 主体{facts['target']['start_date']}至{facts['target']['end_date']}",
                      strength['priority_hint'], strength_brief(strength), strength['evolution']])


def compact_candidates(candidates):
    """Remove exact mother/subtype duplication; preserve distinct dates/processes."""
    by_id = {c['id']: c for c in candidates}
    kept = []
    for candidate in candidates:
        pid = candidate['pattern_id']
        subtype = pid.replace('_star', '_doji') if pid in ('morning_star', 'evening_star') else None
        other = by_id.get(subtype+'@'+candidate['facts']['signal_date']) if subtype else None
        if other and (other['start'], other['end']) == (candidate['start'], candidate['end']):
            continue
        kept.append(candidate)
    return kept


def analyze(f, code, question, folder, call, window=10, *, review_patterns=True, synthesize=True):
    """call(stage, prompt, images, structured) records every real LLM request."""
    from .render import render
    folder = Path(folder)
    started = time.perf_counter()
    # Full definitions remain available for semantically focused questions.
    # Ordinary observations get a low default priority rather than disappearing.
    candidates, unknown = scan(f, code, window, CATALOG) if review_patterns else ([], 0)
    for candidate in candidates:
        candidate['strength'] = strength_profile(PATTERNS[candidate['pattern_id']], candidate, f)
    compact = compact_candidates(candidates)
    eligible = [c for c in compact if not c['strength']['skip_visual']]
    skipped = [c for c in compact if c['strength']['skip_visual']]
    scan_seconds = time.perf_counter()-started
    context = context_brief(f, window)
    # Indicator warmup uses f in full; only the displayed candles are shortened.
    overview = folder/'current.png'
    render_start = time.perf_counter()
    metadata = render({'id':'basic','family':'基础','conditions':[],'focus_bars':window},
                      f, len(f)-1, code, overview, display_bars=window, compact=True)
    from .run import dump
    dump(folder/'current_metadata.json', metadata)
    render_seconds = time.perf_counter()-render_start
    basic_prompt = (BASIC_SKILL/'references/visual-prompt.md').read_text().strip()
    basic = call('vlm_basic', basic_prompt, [overview], False)
    basic_text = basic.get('response') if basic.get('finish_reason') == 'stop' else '基础视觉解读未完成，仅使用已提供的计算事实。'
    (folder/'basic_reading.md').write_text(basic_text or '基础解读为空。')
    selection = None
    chosen = []
    numeric_only = []
    if candidates:
        prompt = ((SKILL/'references/candidate-selection.md').read_text()+
                  '\n用户问题：'+question+'\n当前计算事实：\n'+context+'\n可选实例：\n'+
                  '\n\n'.join(candidate_brief(c, f) for c in eligible)+
                  '\n仅作数值答复、不单独看图的弱实例：\n'+
                  '\n'.join(candidate_brief(c, f) for c in skipped))
        selection = call('main_selection', prompt, [], True)
        # A malformed/failed selector does not cause arbitrary first-three picks.
        if selection.get('finish_reason') == 'stop' and isinstance((selection.get('parsed') or {}).get('selected_ids'), list):
            chosen = selected_candidates(selection, eligible)
            ids = (selection.get('parsed') or {}).get('numeric_only_ids', [])
            if isinstance(ids, list) and all(isinstance(cid, str) for cid in ids):
                numeric_only = selected_candidates({'parsed': {'selected_ids': ids}}, compact)
                numeric_only = [c for c in numeric_only if c['id'] not in {v['id'] for v in chosen}]
    reviews = []
    for i, candidate in enumerate(chosen, 1):
        p = PATTERNS[candidate['pattern_id']]
        sub = folder/f'candidate_{i}'
        sub.mkdir()
        images = []
        render_start = time.perf_counter()
        for name, width in [('detail', 10)]:
            path = sub/f'{name}.png'
            metadata = render(p, f, candidate['at'], code, path, display_bars=width,
                              target_start=candidate['start'], target_end=candidate['end'],
                              target_id=f'C{i}', compact=True,
                              display_until=len(f)-1)
            from .run import dump
            dump(sub/f'{name}_metadata.json', metadata)
            images.append(path)
        render_seconds += time.perf_counter()-render_start
        facts = {**candidate['facts'], 'target': {**candidate['facts']['target'], 'instance_id': f'C{i}'}}
        def visual_call(prompt, paths, structured):
            return call(f'vlm_{i}_interpretation', prompt, paths, structured)
        result = interpret(p, facts, images, visual_call, candidate['strength'])
        reviews.append({'candidate_id': candidate['id'], 'facts': facts, **result})
    handoff = []
    for candidate, item in zip(chosen, reviews):
        interpretation = item['interpretation']
        p = PATTERNS[candidate['pattern_id']]
        text = f"实例 {item['candidate_id']} | {p['name']}\n{target_brief(item['facts'])}"
        if interpretation and interpretation.get('finish_reason') == 'stop':
            text += '\n数值定义：成立；整体典型程度与作用见局部解读。\n该时点观测事实：\n'+evidence_brief(item['facts'], p, include_conditions=False)
            text += '\n单形态分析：\n'+(interpretation.get('response') or '')
            text += '\n后续行情（程序计算）：'+candidate['strength']['evolution']
        else:
            text += '\n数值定义成立，但本实例没有取得完整局部解读。'
        handoff.append(text)
    selection_complete = selection is None or (
        selection.get('finish_reason') == 'stop' and
        isinstance((selection.get('parsed') or {}).get('selected_ids'), list))
    handoff_text = '\n\n'.join(handoff) or (
        '本次没有深入解读的形态，使用基础走势和数值事实。' if selection_complete else
        '形态选择调用未完成，尚未进行局部视觉复核；不能据此判断没有值得解读的形态。')
    (folder/'synthesis_input.md').write_text(handoff_text)
    # Selector prose is a routing rationale, not a new source of market facts.
    # Fetch the referenced numeric records instead of forwarding its narrative.
    numeric_text = '\n\n'.join(candidate_brief(c, f)+'\n'+evidence_brief(c['facts'], PATTERNS[c['pattern_id']], include_conditions=False) for c in numeric_only)
    prompt = ((SKILL/'references/synthesis.md').read_text()+'\n用户问题：'+question+
              '\n当前窗口计算事实：\n'+context+'\n独立基础解读：\n'+(basic_text or '')+
              f'\n扫描 {len(CATALOG)} 项定义，数值命中 {len(candidates)} 个，弱实例 {len(skipped)} 个，选取 {len(chosen)} 个；'
              f'数据不足的形态日组合 {unknown} 个。未入选不等于不存在。'
              '\n与问题相关、仅作数值解释的实例（未作单独视觉解读）：\n'+(numeric_text or '无额外数值实例。')+
              '\n逐例返回：\n'+handoff_text)
    # Chat already owns synthesis and conversation context. Return evidence to
    # that agent rather than running a second, context-free final answer.
    final = call('main_synthesis', prompt, [], False) if synthesize else None
    if final:
        (folder/'answer.md').write_text(final.get('response') or '未取得完整回答')
    evidence_text = (context+'\n独立基础看图：\n'+(basic_text or '')+
        ('\n未运行形态扫描。' if not review_patterns else
         f'\n扫描{len(CATALOG)}项数值定义，命中{len(candidates)}个实例，数据不足{unknown}个形态日组合；未入选不等于不存在。\n'+
         numeric_text+'\n'+handoff_text))
    return {'question': question, 'code': code, 'asof': str(f.index[-1].date()),
            'candidates': candidates, 'unknown_pattern_days': unknown,
            'eligible_ids': [c['id'] for c in eligible], 'weak_ids': [c['id'] for c in skipped],
            'numeric_only_ids': [c['id'] for c in numeric_only],
            'selection': selection, 'selected_ids': [c['id'] for c in chosen],
            'basic': basic, 'reviews': reviews, 'synthesis': final, 'evidence_text': evidence_text, 'scan_seconds': scan_seconds,
            'render_seconds': render_seconds, 'seconds': time.perf_counter()-started}


def usage_totals(calls):
    """OpenAI-compatible prompt_tokens already includes image and cached input."""
    def group(items):
        usages = [c.get('usage') for c in items]
        known = [u for u in usages if u and all(isinstance(u.get(k), int)
                 for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))]
        complete = len(known) == len(items)
        totals = {k: sum(u[k] for u in known) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        return {'calls': len(items), 'usage_received': len(known), 'complete': complete,
                'reported_subtotal': totals,
                **{k: v if complete else None for k, v in totals.items()}}
    return {'main': group([c for c in calls if c['stage'].startswith('main_')]),
            'vlm': group([c for c in calls if c['stage'].startswith('vlm_')]),
            'basic': group([c for c in calls if c['stage']=='vlm_basic']),
            'vlm_patterns': group([c for c in calls if c['stage'].startswith('vlm_') and c['stage']!='vlm_basic']),
            'all': group(calls)}

import copy

import numpy as np
import pandas as pd
import pytest

from src.experiments.kline_patterns.analysis import PATTERNS, scan, compact_candidates
from src.experiments.kline_patterns.catalog import CATALOG
from src.experiments.kline_patterns.engine import prepare
from src.experiments.kline_patterns.strength import profile, evolution, price_boundaries
from src.experiments.kline_patterns.visual_review import interpret
from test_kline_pattern_lab import bars


def quiet_hammer():
    f = prepare(bars(160))
    f[['o', 'c']] = 100.;f['h'] = 103.;f['l'] = 97.
    f['v'] = f['vbase'] = 100.;f['atr'] = f['body_avg'] = 1.
    f.loc[f.index[-7], 'c'] = 100.3
    at = len(f)-1
    f.loc[f.index[at], ['o','c','h','l','body','range','lower','upper']] = [100.2,100.25,100.27,100.1,.05,.17,.1,.02]
    c = {'id': 'hammer', 'pattern_id': 'hammer_down', 'at': at, 'start': at, 'end': at}
    return f, c


def test_weak_requires_all_dimensions_and_no_context():
    f, c = quiet_hammer();p = PATTERNS[c['pattern_id']]
    assert profile(p, c, f)['skip_visual']
    # A single pronounced dimension is enough to prevent numeric rejection.
    for column, value in [('range', 1.1), ('lower', .6), ('v', 150.)]:
        changed = f.copy();changed.loc[changed.index[-1], column] = value
        assert not profile(p, c, changed)['skip_visual'], column
    f.loc[f.index[-1], 'h'] = 103.
    assert not profile(p, c, f)['skip_visual']  # price range edge, despite weak geometry


def test_unknown_or_equal_boundary_does_not_count_as_weak():
    f, c = quiet_hammer();p = PATTERNS[c['pattern_id']]
    for column in ['atr', 'vbase']:
        changed = f.copy();changed.loc[changed.index[-2 if column == 'atr' else -1], column] = np.nan
        assert not profile(p, c, changed)['skip_visual']
    f.loc[f.index[-1], 'range'] = .8
    assert not profile(p, c, f)['skip_visual']


def test_later_failure_preserved_without_rewriting_original_strength():
    f, c = quiet_hammer();p = PATTERNS[c['pattern_id']]
    original = profile(p, c, f)
    later = f.iloc[[-1]].copy();later.index = [f.index[-1]+pd.offsets.BDay()]
    later['c'] = 99.
    extended = pd.concat([f, later])
    now = profile(p, c, extended)
    assert now['measurements'] == original['measurements']
    assert original['skip_visual'] and not now['skip_visual']
    assert '主体低点' in now['evolution']
    assert profile(p, c, extended.iloc[:c['at']+1]) == original


def test_small_later_crossing_does_not_promote_a_tiny_candle():
    f, c = quiet_hammer();p = PATTERNS[c['pattern_id']]
    later = f.iloc[[-1]].copy();later.index = [f.index[-1]+pd.offsets.BDay()]
    later['c'] = 100.09  # just below its small 100.10 low
    result = profile(p, c, pd.concat([f, later]))
    assert result['skip_visual']
    assert '下穿主体低点' in result['evolution']  # facts kept; value is not exaggerated


def test_reclaim_then_break_again_have_explicit_directions():
    f, c = quiet_hammer();c['pattern_id'] = 'inside_break_bear';c['start'] -= 2
    at = c['at'];f.loc[f.index[at-2], 'l'] = 100.3
    later = pd.concat([f.iloc[[-1]], f.iloc[[-1]]])
    later.index = pd.bdate_range(f.index[-1]+pd.offsets.BDay(), periods=2)
    later['c'] = [100.4, 100.2]
    text, _ = evolution(PATTERNS['inside_break_bear'], c, pd.concat([f, later]))
    assert '收盘上穿母线低点' in text and '收盘下穿母线低点' in text
    assert text.index('收盘上穿母线低点') < text.index('收盘下穿母线低点')


def test_local_body_contrast_and_consolidation_volume_preserve_review():
    f, c = quiet_hammer();at = c['at'];c['start'] = at-1
    f.loc[f.index[-1], ['body','range']] = [1.1,.6]
    f.loc[f.index[-2], ['body','h','l']] = [.4,100.4,100.1]
    f.loc[f.index[-1], ['h','l']] = [100.5,100.0]
    out = profile(PATTERNS['engulf_bear'], c, f)
    assert out['measurements'][-1]['value'] == pytest.approx(2.75)
    assert not out['skip_visual']
    c['start'] = at-3
    f.loc[f.index[at-3], 'v'] = 300.
    assert any('整理段' in x for x in profile(PATTERNS['three_hold_bull'], c, f)['context_reasons'])


def test_structural_boundaries_not_always_last_candle():
    f = prepare(bars(180));at = len(f)-5
    c = {'at': at, 'start': at-2, 'end': at}
    edge = price_boundaries(PATTERNS['inside_break_bull'], c, f)
    assert edge[0][1].iloc[-1] == f.h.iloc[at-2]
    ma = price_boundaries(PATTERNS['ma20_retest'], c, f)
    pd.testing.assert_series_equal(ma[0][1], f.ma20)
    c = {'at': at, 'start': at-20, 'end': at}
    neck = price_boundaries(PATTERNS['double_bottom'], c, f)
    assert neck[0][1].iloc[-1] == f.lo_neck_inner.iloc[at]


def test_every_definition_has_finite_or_explicit_unknown_measurements():
    f = prepare(bars(400))
    for p in CATALOG:
        c = {'at': 399, 'start': 390, 'end': 399}
        out = profile(p, c, f)
        assert len(out['measurements']) >= 2, p['id']
        assert all(m['value'] is None or np.isfinite(m['value']) for m in out['measurements'])


def test_strength_is_price_scale_invariant_on_real_formula_hits():
    b = bars(260);g = b.copy();g[['open','high','low','close']] *= 7
    f, scaled = prepare(b), prepare(g)
    candidates, _ = scan(f, 'fixture', 12, CATALOG)
    assert candidates
    for c in candidates:
        p = PATTERNS[c['pattern_id']]
        a, z = profile(p, c, f), profile(p, c, scaled)
        assert a['skip_visual'] == z['skip_visual']
        for x, y in zip(a['measurements'], z['measurements']):
            assert x['value'] == pytest.approx(y['value']) if x['value'] is not None else y['value'] is None


def test_dedupe_only_exact_star_subtype_not_different_dates():
    def cand(pid, date, at):
        return {'id': pid+'@'+date, 'pattern_id': pid, 'start': at-2, 'end': at, 'facts': {'signal_date': date}}
    a = cand('morning_star', '2026-09-01', 10)
    b = cand('morning_doji', '2026-09-01', 10)
    c = cand('morning_star', '2026-09-02', 11)
    assert compact_candidates([a, b, c]) == [b, c]


def test_single_interpretation_uses_one_chart_without_duplicate_fact_context():
    calls = []
    strength = {'measurements': [], 'context_reasons': [], 'evolution': '后续日期2026-09-28'}
    def call(prompt, images, structured):
        calls.append((prompt, images, structured));return {'response': '局部分析', 'finish_reason': 'stop'}
    for symbol in ['FIRST', 'SECOND']:
        result = interpret(PATTERNS['engulf_bull'], {'target': {'symbol': symbol}}, [symbol+'.png'], call, strength)
        assert result['interpretation']['response'] == '局部分析'
    assert len(calls) == 2 and all(not c[2] for c in calls)
    assert calls[0][1] == ['FIRST.png'] and calls[1][1] == ['SECOND.png']
    assert 'FIRST' not in calls[1][0] and 'SECOND' not in calls[0][0]
    assert '后续日期2026-09-28' not in calls[0][0]
    assert PATTERNS['engulf_bull']['description'] in calls[0][0]

import pandas as pd
import pytest

from src.experiments.kline_patterns import analysis
from src.experiments.kline_patterns.engine import add_pivots, prepare
from test_kline_pattern_lab import bars


def test_selection_caps_instances_before_visual_and_deduplicates():
    candidates = [{'id': f'c{i}'} for i in range(5)]
    result = analysis.selected_candidates({'parsed': {'selected_ids': ['missing', 'c3', 'c3', 'c1', 'c4', 'c2']}}, candidates)
    assert [c['id'] for c in result] == ['c3', 'c1', 'c4']
    assert analysis.selected_candidates({'parsed': {'selected_ids': []}}, candidates) == []
    with pytest.raises(ValueError):
        analysis.selected_candidates({'parsed': {'selected_ids': 'c1'}}, candidates)


def test_usage_counts_cached_and_image_input_once_and_reports_missing():
    main = {'stage': 'main_selection', 'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120,
                                               'prompt_tokens_details': {'cached_tokens': 50}}}
    visual = {'stage': 'vlm_1_qualification', 'usage': {'prompt_tokens': 1000, 'completion_tokens': 30, 'total_tokens': 1030,
                                                     'prompt_tokens_details': {'image_tokens': 842}}}
    result = analysis.usage_totals([main, visual])
    assert result['main']['total_tokens'] == 120
    assert result['vlm']['total_tokens'] == 1030
    assert result['all']['total_tokens'] == 1150
    missing = analysis.usage_totals([main, visual, {'stage': 'vlm_1_interpretation'}])
    assert missing['all']['total_tokens'] is None
    assert missing['all']['reported_subtotal']['total_tokens'] == 1150
    assert missing['all']['usage_received'] == 2


def test_interval_excludes_warmup_and_distinguishes_pivot_confirmation():
    assert analysis.subject_interval(analysis.PATTERNS['macd_cross_bull'], pd.DataFrame(), 200) == (199, 200)
    f = pd.DataFrame({'l': [8,7,6,7,8,7,6,5,6,7], 'h': [20.]*10,
                      'dif': list(range(10)), 'rsi': [40.]*10, 'atr': [1.]*10})
    pivots = add_pivots(f)
    assert analysis.subject_interval(analysis.PATTERNS['macd_div_bull'], pivots, 9) == (2, 7)
    assert analysis.subject_interval(analysis.PATTERNS['double_bottom'], pivots, 9) == (2, 9)


def test_workflow_never_replenishes_rejected_selection_and_hands_off_all_results(tmp_path, monkeypatch):
    from src.experiments.kline_patterns import render
    f = prepare(bars())
    candidates = []
    for i in range(5):
        at = len(f)-1-i
        cid = f'instance{i}'
        candidates.append({'id': cid, 'pattern_id': 'doji', 'at': at, 'start': at, 'end': at,
                           'facts': analysis.candidate_facts(analysis.PATTERNS['doji'], f, at, 'test', cid)})
    monkeypatch.setattr(analysis, 'scan', lambda *args: (candidates, 0))
    monkeypatch.setattr(analysis, 'strength_profile', lambda *args: {
        'skip_visual': False, 'measurements': [], 'context_reasons': [], 'priority_hint': 'fixture', 'evolution': 'fixture'})
    monkeypatch.setattr(render, 'render', lambda *args, **kwargs: {})
    seen = []
    def call(stage, prompt, images, structured):
        seen.append((stage, prompt))
        assert len(images) == (0 if stage.startswith('main_') else 1)
        if stage == 'vlm_basic':
            assert 'instance0' not in prompt
            assert analysis.context_brief(f) not in prompt
        if stage == 'main_selection':
            assert '完整实例解读' not in prompt
            return {'parsed': {'selected_ids': [c['id'] for c in candidates]}, 'finish_reason': 'stop'}
        if stage == 'vlm_1_interpretation':
            return {'response': '未完成', 'finish_reason': 'length'}
        return {'response': '完整实例解读' if stage.endswith('interpretation') else '最终结果', 'finish_reason': 'stop'}
    result = analysis.analyze(f, 'test', '看K线', tmp_path, call)
    assert len(result['selected_ids']) == 3
    assert [stage for stage, _ in seen] == ['vlm_basic', 'main_selection', 'vlm_1_interpretation',
                                           'vlm_2_interpretation', 'vlm_3_interpretation', 'main_synthesis']
    handoff = (tmp_path/'synthesis_input.md').read_text()
    assert '完整实例解读' in handoff and 'instance0' in handoff and 'instance2' in handoff
    assert 'instance3' not in handoff and 'instance4' not in handoff


def test_empty_scan_skips_visual_and_continues_general_analysis(tmp_path, monkeypatch):
    from src.experiments.kline_patterns import render
    f = prepare(bars())
    monkeypatch.setattr(analysis, 'scan', lambda *args: ([], 0))
    monkeypatch.setattr(render, 'render', lambda *args, **kwargs: {})
    calls = []
    def call(stage, prompt, images, structured):
        calls.append(stage)
        if stage == 'main_synthesis':
            assert '本次没有深入解读的形态' in prompt
            assert not images
        else: assert stage == 'vlm_basic' and len(images) == 1
        return {'response': '普通走势分析', 'finish_reason': 'stop'}
    result = analysis.analyze(f, 'test', '看K线', tmp_path, call)
    assert calls == ['vlm_basic', 'main_synthesis'] and result['selected_ids'] == []


@pytest.mark.parametrize('weak,selected,finish', [(False, [], 'stop'), (True, ['one'], 'stop'), (False, ['one'], 'length')])
def test_one_candidate_is_checked_and_weak_or_failed_selection_never_spends_visual(tmp_path, monkeypatch, weak, selected, finish):
    from src.experiments.kline_patterns import render
    f = prepare(bars());at = len(f)-1
    candidate = {'id': 'one', 'pattern_id': 'doji', 'at': at, 'start': at, 'end': at,
                 'facts': analysis.candidate_facts(analysis.PATTERNS['doji'], f, at, 'test', 'one')}
    monkeypatch.setattr(analysis, 'scan', lambda *args: ([candidate], 0))
    monkeypatch.setattr(analysis, 'strength_profile', lambda *args: {
        'skip_visual': weak, 'measurements': [], 'context_reasons': [], 'priority_hint': 'fixture', 'evolution': 'fixture'})
    monkeypatch.setattr(render, 'render', lambda *args, **kwargs: {})
    calls = []
    def call(stage, prompt, images, structured):
        calls.append(stage)
        if stage == 'main_selection':
            assert 'basic_model_narrative' not in prompt
            return {'parsed': {'selected_ids': selected, 'rationale': '未选择'}, 'finish_reason': finish}
        return {'response': 'basic_model_narrative', 'finish_reason': 'stop'}
    result = analysis.analyze(f, 'test', '看K线', tmp_path, call)
    assert result['selected_ids'] == []
    assert calls == ['vlm_basic', 'main_selection', 'main_synthesis']


def test_numeric_route_uses_source_records_not_selection_narrative(tmp_path, monkeypatch):
    from src.experiments.kline_patterns import render
    f = prepare(bars());at = len(f)-1
    c = {'id': 'one', 'pattern_id': 'doji', 'at': at, 'start': at, 'end': at,
         'facts': analysis.candidate_facts(analysis.PATTERNS['doji'], f, at, 'test', 'one')}
    monkeypatch.setattr(analysis, 'scan', lambda *args: ([c], 0))
    monkeypatch.setattr(analysis, 'strength_profile', lambda *args: {
        'skip_visual': True, 'measurements': [], 'context_reasons': [], 'priority_hint': 'fixture', 'evolution': '原始演变事实'})
    monkeypatch.setattr(render, 'render', lambda *args, **kwargs: {})
    calls = []
    def call(stage, prompt, images, structured):
        calls.append(stage)
        if stage == 'main_selection':
            return {'finish_reason': 'stop', 'parsed': {'selected_ids': [], 'numeric_only_ids': ['one','missing'], 'rationale': 'unsupported_narrative'}}
        if stage == 'main_synthesis':
            assert '原始演变事实' in prompt and 'unsupported_narrative' not in prompt
            assert 'one' in prompt and not images
        return {'finish_reason': 'stop', 'response': '解释'}
    result = analysis.analyze(f, 'test', '看看十字星', tmp_path, call)
    assert result['numeric_only_ids'] == ['one'] and result['selected_ids'] == []
    assert calls == ['vlm_basic', 'main_selection', 'main_synthesis']

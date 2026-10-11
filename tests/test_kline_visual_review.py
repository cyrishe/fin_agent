import pytest
from src.experiments.kline_patterns.visual_review import review,evidence_brief
from src.experiments.kline_patterns.catalog import CATALOG

@pytest.mark.parametrize('decision,expected_calls', [(True,2),(False,1),(None,1),('true',1)])
def test_interpretation_requires_affirmative_visual_review(decision,expected_calls):
    calls=[]
    def call(prompt,images,structured):
        calls.append((prompt,structured))
        return {'parsed':{'match':decision,'assessment':'样例证据'}}
    result=review(CATALOG[0],{'operands':[]},[],call)
    assert len(calls)==expected_calls
    assert (result['interpretation'] is not None)==(expected_calls==2)
    assert calls[0][1] is True
    if expected_calls==2:assert calls[1][1] is False

def test_program_facts_preserve_direction_equality_and_unknown():
    facts={'operands':[{'condition':'前根颜色','result':False},{'condition':'背景','result':None}],
           'recent_days':[{'date':'2026-09-15','o':113.61,'c':114.86,'ma5':114.86,'dif':1.,'dea':2.,'hist':-1.}]}
    text=evidence_brief(facts)
    assert '前根颜色：不满足' in text and '背景：未提供确定结果' in text
    assert '2026-09-15：阳线' in text and '收盘等于MA5' in text
    assert 'DIF低于DEA' in text and 'MACD柱为负' in text
    assert 'MA20' not in text

def test_each_candidate_keeps_its_own_context():
    calls=[]
    def call(prompt,images,structured):
        calls.append(prompt)
        return {'parsed':{'match':False,'assessment':'不符合'}}
    for symbol in ['FIRST_SYMBOL','SECOND_SYMBOL']:
        review(CATALOG[0],{'target':{'symbol':symbol},'operands':[]},[],call)
    assert len(calls)==2
    assert 'FIRST_SYMBOL' in calls[0] and 'FIRST_SYMBOL' not in calls[1]
    assert 'SECOND_SYMBOL' in calls[1] and 'SECOND_SYMBOL' not in calls[0]


def test_shadow_lengths_are_supplied_as_computed_facts():
    p = next(p for p in CATALOG if p['id'] == 'star_down')
    text = evidence_brief({'recent_days': [{'date': '2026-09-24', 'o': 11.35, 'h': 11.47,
         'l': 11.29, 'c': 11.30, 'upper': .12, 'lower': .01, 'body': .05}]}, p)
    assert '上影0.1200、下影0.0100、实体0.0500' in text
    assert '上影高于下影' in text

def test_interpretation_uses_source_facts_not_qualification_numbers():
    prompts=[]
    def call(prompt,images,structured):
        prompts.append(prompt)
        return {'parsed':{'match':True,'assessment':'unsupported_model_number_123'}}
    facts={'recent_days':[{'v':80.,'hist':-3.,'c':11.34},{'v':100.,'hist':-2.,'o':11.29,'c':11.30}],
           'volume_ratio_to_prior20':.8}
    review(next(p for p in CATALOG if p['id']=='macd_hist_bull'),facts,[],call)
    assert 'unsupported_model_number_123' not in prompts[1]
    assert '0.800倍，低于此前20日均量' in prompts[1]
    assert '1.250倍，高于前一日成交量' in prompts[1]
    assert 'MACD柱绝对值低于前一交易日' in prompts[1]
    assert '信号日收盘低于前一交易日收盘' in prompts[1]
    assert '阳线' in prompts[1]

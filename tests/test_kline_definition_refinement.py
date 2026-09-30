"""Semantic positive/counterexamples for the complete-definition review."""
import numpy as np
import pandas as pd
import pytest
from src.experiments.kline_patterns.catalog import CATALOG
from src.experiments.kline_patterns.engine import detect, add_pivots, prepare
from src.experiments.kline_patterns.analysis import subject_interval, candidate_facts
from src.experiments.kline_patterns.visual_review import evidence_brief
from test_kline_pattern_lab import bars

PAT={p['id']:p for p in CATALOG}


@pytest.mark.parametrize('up',[True,False])
def test_ma20_60_confirmation_is_delayed_and_not_repeated(up):
    f=pd.DataFrame({'ma20':[9.]*6+[10.1,10.2,10.3,10.4],'ma60':10.,'c':11.,'valid':1.})
    if not up:
        for k in ('ma20','ma60','c'):f[k]=30-f[k]
    p=PAT['ma20_60_'+('up' if up else 'down')]
    assert list(detect(p,f).fillna(False))==[False]*8+[True,False]
    assert subject_interval(p,f,8)==(5,8)
    f.loc[7,'ma20']=9.8 if up else 20.2
    assert not detect(p,f,8)


@pytest.mark.parametrize('bull',[True,False])
def test_star_requires_a_real_final_body_even_after_a_large_opening_gap(bull):
    f=pd.DataFrame({'c':np.arange(200.,190.,-1),'o':np.arange(200.,190.,-1),'body_avg':2.,'range':5.,'valid':1.})
    f.loc[7,['o','c']]=[194,190];f.loc[8,['o','c']]=[188,188.2];f.loc[9,['o','c']]=[188,193]
    f['body']=(f.c-f.o).abs()
    p=PAT['morning_star' if bull else 'evening_star']
    def result(frame):
        x=frame.copy()
        if not bull:x['o']=400-frame.o;x['c']=400-frame.c
        return detect(p,x,9)
    assert result(f)
    f.loc[9,['o','body']]=[192.5,.5]  # midpoint recaptured, but tiny final real body
    assert not result(f)


def test_swallow_three_requires_falling_closes_not_just_red_green_colors():
    f=pd.DataFrame({'o':[11.,10.,9.,7.9],'c':[10.,9.,8.,11.1],'valid':1.,'body_avg':1.})
    f['body']=(f.c-f.o).abs()
    assert detect(PAT['bull_swallow_three'],f,3)
    f.loc[0,['o','c','body']]=[10.,9.,1.]
    f.loc[1,['o','c','body']]=[11.,10.,1.]
    assert not detect(PAT['bull_swallow_three'],f,3)


@pytest.mark.parametrize('bull',[True,False])
def test_three_methods_final_body_and_middle_contraction_have_meaning(bull):
    b=bars(40)
    for k in range(35):
        c=80+k*.5;b.iloc[k]=[c-.5,c+1,c-1,c,1000]
    b.iloc[-5:]=[[98,105,97,104,1000],[103,104,99,101,900],
                 [102,103,98,100,800],[101,102,98,99,700],[99,107,98,106,1200]]
    p=PAT['three_methods_'+('bull' if bull else 'bear')]
    def result(raw):
        x=raw.copy()
        if not bull:
            x['open']=200-raw.open;x['close']=200-raw.close
            x['high']=200-raw.low;x['low']=200-raw.high
        return detect(p,prepare(x),len(x)-1)
    assert result(b)
    b.iloc[-1,b.columns.get_loc('open')]=105.9
    assert not result(b)  # gap did the work; final candle itself has almost no body
    b.iloc[-1,b.columns.get_loc('open')]=99
    b.iloc[-4,b.columns.get_loc('close')]=99.1
    assert not result(b)  # the supposed contraction is still 65% of the first body


def test_retest_requires_a_genuine_approach_and_breakdown_a_price_decline():
    f=pd.DataFrame({'ma20':[9.,9.2,9.4,9.6,9.8,9.9,10.], 'c':[10.8]*6+[10.4],
                    'l':[10.6]*6+[10.1],'atr':1.,'v':500.,'v5':1000.,'valid':1.})
    assert detect(PAT['ma20_retest'],f,6)
    f.loc[5,'c']=10.1  # had already been hugging the MA
    assert not detect(PAT['ma20_retest'],f,6)
    g=pd.DataFrame({'c':[10.2,10.3],'ma20':[10.,10.4],'v':160.,'vbase':100.,'valid':1.})
    assert not detect(PAT['ma20_breakdown'],g,1)  # moving boundary, price actually rose
    g.loc[1,'c']=9.8
    assert detect(PAT['ma20_breakdown'],g,1)


def test_double_neckline_is_inside_the_two_pivots_and_v1_is_preserved():
    f=pd.DataFrame({'l':[8,7,6,7,8,7,6,5,6,7], 'h':[12.,12,50,12,12,12,12,50,12,12],
                    'dif':np.arange(10,dtype=float),'rsi':40.,'atr':1.})
    result=add_pivots(f)
    assert result.lo_neck.iloc[9]==50  # frozen v1 definition retains its old series
    assert result.lo_neck_inner.iloc[9]==12
    p=PAT['double_bottom']
    g=pd.DataFrame({'lo_p1':10.,'lo_p2':10.2,'lo_atr2':1.,'lo_sep':12.,'lo_prior_move_atr':-2.,
                    'lo_neck_inner':12.,'lo_event':0.,'c':[11.,12.3],'valid':1.})
    assert detect(p,g,1)
    g['lo_sep']=6;g['lo_prior_move_atr']=0
    assert detect(p,g,1)  # local W can be consolidation, not necessarily a major reversal


@pytest.mark.parametrize('bull',[True,False])
def test_bollinger_needs_persistent_compression_but_range_break_is_separate(bull):
    f=pd.DataFrame({'bw':[.1]*29+[.2],'bw_q20':.15,'c':[100.]*29+[103.],
                    'bu':[101.]*29+[102.],'bl':95.,'ph20':102.5,'pl20':94.,'valid':1.})
    p=PAT['boll_squeeze_'+('bull' if bull else 'bear')]
    def frame():
        x=f.copy()
        if not bull:
            x['c']=300-f.c;x['bu']=300-f.bl;x['bl']=300-f.bu
            x['ph20']=300-f.pl20;x['pl20']=300-f.ph20
        return x
    assert detect(p,frame(),29)
    assert subject_interval(p,frame(),29)==(26,29)
    f.loc[27,'bw']=.16
    assert not detect(p,frame(),29)
    f.loc[27,'bw']=.1;f['ph20']=104
    assert detect(p,frame(),29)  # valid band break; price-range confirmation remains separate


def test_degree_facts_are_computed_and_passed_as_short_prose():
    f=prepare(bars());p=PAT['engulf_bear'];at=len(f)-1
    facts=candidate_facts(p,f,at,'T','case')
    expected=f.iloc[at].body/f.iloc[at].body_avg
    assert f'{expected:+.2f}倍' in facts['context_notes']
    assert '末根实体/此前20根平均实体' in evidence_brief(facts,p)
    assert '胜率' not in facts['context_notes']


def test_degree_facts_never_read_after_the_signal_including_first_bar():
    b=bars();f=prepare(b)
    for at in (0,40,100):
        prefix=prepare(b.iloc[:at+1]);p=PAT['doji']
        full=candidate_facts(p,f,at,'T','case')['context_notes']
        truncated=candidate_facts(p,prefix,at,'T','case')['context_notes']
        assert full==truncated

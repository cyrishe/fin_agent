"""Semantic counterexamples and stage boundaries, independent of model wording."""
import numpy as np
import pandas as pd
import pytest
from src.experiments.kline_patterns.analysis import PATTERNS, scan, context_brief
from src.experiments.kline_patterns.engine import prepare, detect
from src.experiments.kline_patterns.render import render
from src.experiments.kline_patterns.visual_review import review
from test_kline_pattern_lab import bars


def test_two_bullish_candles_cannot_pass_engulfing_screen():
    b = bars(40)
    # A declining background, followed by the actual two-bullish counterexample.
    b.iloc[-8:-2, b.columns.get_loc('close')] = [121,120,119,118,117,116]
    b.iloc[-8:-2, b.columns.get_loc('open')] = [122,121,120,119,118,117]
    b.iloc[-8:-2, b.columns.get_loc('high')] = [123,122,121,120,119,118]
    b.iloc[-8:-2, b.columns.get_loc('low')] = [120,119,118,117,116,115]
    for pos, values in [(-2,[113.61,117.37,113.60,114.86,25520656]),
                        (-1,[114.86,120.99,114.60,120.90,39436472])]:
        b.iloc[pos] = values
    f = prepare(b)
    assert detect(PATTERNS['engulf_bull'],f,len(f)-1) == False
    candidates, _ = scan(f,'counterexample',1)
    assert not any(c['pattern_id']=='engulf_bull' for c in candidates)


@pytest.mark.parametrize('bull',[True,False])
def test_engulfing_requires_long_current_body_and_non_doji_previous(bull):
    f=pd.DataFrame({'c':[15,14,13,12,11,10,9,10,12.5],
                    'o':[15,14,13,12,11,10,9,12,9.5],
                    'body':[0,0,0,0,0,0,0,2,3],
                    'range':3.5,'body_avg':1.,'valid':1.},dtype=float)
    p=PATTERNS['engulf_bull' if bull else 'engulf_bear']
    def result(frame):
        candidate=frame.copy()
        if not bull:
            candidate['o']=200-frame.o;candidate['c']=200-frame.c
        return detect(p,candidate,8)
    assert result(f)
    f.loc[8,'body_avg']=4
    assert not result(f)  # A larger body than yesterday is not necessarily long.
    f.loc[7,['o','c','body','range']]=[10.02,10,.02,1]
    f.loc[8,['o','c','body','range','body_avg']]=[9.99,10.1,.11,.2,.08]
    assert not result(f)  # Engulfing a near-doji is excluded even with body expansion.


def test_default_scan_excludes_common_shapes_but_keeps_specific_candidates():
    from src.experiments.kline_patterns.catalog import BASIC_PATTERN_IDS
    b=bars(210)
    b.iloc[-1]=[100,102,98,100,1000]
    f=prepare(b)
    assert detect(PATTERNS['doji'],f,len(f)-1)  # Still available for explicit inspection.
    candidates,_=scan(f,'T',1)
    assert not (set(c['pattern_id'] for c in candidates)&BASIC_PATTERN_IDS)
    b.iloc[-3:]=[[100,105,95,102,1000],[101,103,97,100,900],[99,100,92,94,1100]]
    candidates,_=scan(prepare(b),'T',1)
    assert any(c['pattern_id']=='inside_break_bear' for c in candidates)


def test_inside_break_does_not_require_volume_expansion():
    f = pd.DataFrame({'o':[11.4782,11.5663,11.5467], 'h':[11.6446,11.6250,11.5858],
                      'l':[11.4684,11.5174,11.3216], 'c':[11.5956,11.5663,11.4489],
                      'v':[74124802.,75525146.,94962632.], 'vbase':97523451., 'valid':1.})
    assert detect(PATTERNS['inside_break_bear'],f,2)
    assert not detect(PATTERNS['volume_down_expand'],f,2)


@pytest.mark.parametrize('bull',[True,False])
def test_three_methods_needs_continuation_context_not_geometry_alone(bull):
    b=bars(40)
    for k in range(35):
        c=80+k*.5
        b.iloc[k]=[c-.5,c+1,c-1,c,1000]
    for pos,row in zip(range(35,40),[
            [98,105,97,104,1000],[103,104,99,101,900],
            [102,103,98,100,800],[101,102,98,99,700],[99,107,98,106,1200]]):b.iloc[pos]=row
    if not bull:
        original=b.copy()
        b['open']=200-original.open;b['close']=200-original.close
        b['high']=200-original.low;b['low']=200-original.high
    pattern=PATTERNS['three_methods_bull' if bull else 'three_methods_bear']
    assert detect(pattern,prepare(b),39)
    # Reverse the five-day context ending immediately before the pattern.
    b.iloc[29,b.columns.get_loc('close')]=110 if bull else 90
    b.iloc[29,b.columns.get_loc('high')]=max(b.iloc[29].open,b.iloc[29].close)+1
    b.iloc[29,b.columns.get_loc('low')]=min(b.iloc[29].open,b.iloc[29].close)-1
    assert not detect(pattern,prepare(b),39)


def test_macd_contraction_requires_three_negative_bars_and_missing_is_unknown():
    f=pd.DataFrame({'hist':[-2.7013,-2.4542,-2.1005], 'valid':1.})
    assert detect(PATTERNS['macd_hist_bull'],f,2)
    f.loc[2,'hist']=.1
    assert not detect(PATTERNS['macd_hist_bull'],f,2)  # crossing is a different event
    f.loc[2,'hist']=-2.1;f.loc[1,'hist']=np.nan
    assert pd.isna(detect(PATTERNS['macd_hist_bull'],f,2))


def test_render_crops_display_not_indicator_warmup(tmp_path):
    f=prepare(bars())
    expected=f.ma60.iloc[-1]
    before=f.copy(deep=True)
    meta=render({'id':'basic','family':'基础','conditions':[],'focus_bars':80},f,len(f)-1,'T',tmp_path/'basic.png',display_bars=20,compact=True)
    assert meta['display_bars']==20 and f.ma60.iloc[-1]==expected
    assert meta['panels']==['price','volume']
    pd.testing.assert_frame_equal(f,before)
    p=PATTERNS['inside_break_bear']
    meta=render(p,f,len(f)-1,'T',tmp_path/'pattern.png',display_bars=10,target_start=len(f)-3,target_end=len(f)-1,annotate_values=True,compact=True)
    assert meta['display_bars']==10 and meta['panels']==['price','volume']
    assert all('vbase' in row for row in meta['displayed_values'])
    assert len(meta['displayed_values'])==3


def test_chart_keeps_signal_box_and_includes_observed_follow_through(tmp_path):
    f=prepare(bars());at=len(f)-8
    meta=render(PATTERNS['inside_break_bear'],f,at,'T',tmp_path/'follow.png',
                display_bars=10,target_start=at-2,target_end=at,
                annotate_values=True,compact=True,display_until=len(f)-1)
    assert meta['display_end']==f.index[-1].strftime('%Y-%m-%d')
    assert meta['confirmation_date']==f.index[at].strftime('%Y-%m-%d')
    assert meta['end_date']==meta['confirmation_date']
    assert meta['display_bars']==17 and meta['panels']==['price','volume']
    assert meta['displayed_values'][-1]['date']==meta['confirmation_date']
    assert meta['annotations']==['母线低点']
    with pytest.raises(ValueError):
        render(PATTERNS['inside_break_bear'],f,at,'T',tmp_path/'invalid.png',display_until=at-1)


def test_context_count_is_from_open_close_not_change_from_previous_close():
    b=bars(40)
    b.iloc[-3:]=[[100,102,99,101,1000],[98,100,97,99,1100],[99,100,98,99,1200]]
    f=prepare(b)
    assert '2阳、0阴、1根开收相同' in context_brief(f,3)


def test_candle_review_keeps_necessary_relations_and_excludes_unrelated_indicators():
    seen=[]
    facts={'operands':[{'condition':'实体覆盖','result':True}],
           'recent_days':[{'o':10,'c':11,'dif':1,'dea':2,'hist':-1,'rsi':40}],
           'volume_ratio_to_prior20':.98}
    review(PATTERNS['engulf_bull'],facts,[],lambda prompt, images, structured: seen.append(prompt) or {'parsed':{'match':False}})
    assert 'MACD' not in seen[0] and 'RSI' not in seen[0]
    assert '实体覆盖：满足' in seen[0]


def test_subsequent_events_are_not_left_as_future_conditions():
    from src.experiments.kline_patterns.analysis import subsequent_brief
    f=pd.DataFrame({'c':[11,10,9], 'h':[12,11,10], 'l':[10.5,9.5,8.5]},index=pd.bdate_range('2026-09-21',periods=3))
    text=subsequent_brief({'at':0},f)
    assert '后续收盘已低于信号日最低价，首次 2026-09-22' in text
    assert '后续收盘尚未高于信号日最高价' in text


def test_latest_indicator_change_is_date_bound_and_uses_common_term():
    from src.experiments.kline_patterns.visual_review import evidence_brief
    facts={'recent_days':[{'date':'2026-09-24','hist':-2.45,'v':100,'c':293.5},
                         {'date':'2026-09-28','hist':-2.10,'v':121.1,'c':291.99}],
           'volume_ratio_to_prior20':.973}
    text=evidence_brief(facts)
    assert '2026-09-28成交量为此前20日均量的0.973倍' in text
    assert '2026-09-28相对前一交易日：MACD负柱缩短' in text

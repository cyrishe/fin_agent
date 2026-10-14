import numpy as np
import pandas as pd
from scripts.experiment_automl_close_open_simple_rolling import (
    FACTORS, mature, split_days, compact_factors, preprocess, transform, top1, retained_rows,
)
from scripts.report_automl_close_open_simple_rolling import attach_amounts, execution, robustness


def test_temporal_windows_and_label_maturity():
    days=pd.bdate_range('2025-01-01',periods=130).strftime('%Y-%m-%d').tolist()
    train,val=split_days(days,days[110])
    assert len(train)==80 and len(val)==20 and train[-1]<val[0]<val[-1]<days[110]
    d=pd.DataFrame({'signal_date':[days[89],days[89],days[90]],'next_date':[days[90],days[91],days[91]],
                    'strong':[1,0,np.nan],'weak':[0,1,np.nan],'close40_return':[.01,-.01,np.nan]})
    assert mature(d,train,val[0]).index.tolist()==[0]


def test_future_associations_cannot_change_selected_factors():
    rng=np.random.default_rng(17)
    days=pd.bdate_range('2025-01-01',periods=25).strftime('%Y-%m-%d')
    train=pd.DataFrame(rng.normal(size=(250,len(FACTORS))),columns=FACTORS)
    train['signal_date']=np.repeat(days,10)
    daily=pd.DataFrame([{'date':d,'factor':f,'delta':.01*(i+1)} for d in days for i,f in enumerate(FACTORS)])
    first,_=compact_factors(train,daily)
    future=daily.assign(date='2026-10-13',delta=-1e9)
    second,_=compact_factors(train,pd.concat([daily,future]))
    assert first==second and len(first)==12


def test_preprocessing_uses_training_only_and_keeps_binary():
    train=pd.DataFrame({'float_mv_100m_cny':[10.,20.,30.,np.nan],'ma_bull_5_10_20':[0.,1.,0.,1.]})
    fit=preprocess(train,list(train))
    before=transform(train,fit)
    extreme=pd.DataFrame({'float_mv_100m_cny':[1e20],'ma_bull_5_10_20':[1.]})
    transform(extreme,fit)
    assert np.array_equal(before,transform(train,fit))
    assert before[:,1].tolist()==[0,1,0,1] and np.isfinite(before).all()


def test_unknown_outcome_candidate_is_ranked_and_ties_use_code():
    frame=pd.DataFrame({'signal_date':['2025-01-01']*3,'symbol6':['000003','000002','000001'],
                        'close40_return':[.5,np.nan,-.1]})
    scored=pd.DataFrame({'score':[.1,.2,.2]})
    assert top1(frame,scored).tolist()==[2]
    scored.loc[1,'score']=.3
    assert top1(frame,scored).tolist()==[1]
    assert np.array_equal(retained_rows(frame),retained_rows(frame))


def test_amount_gate_uses_twenty_prior_market_slots_not_current_or_future():
    days=pd.bdate_range('2025-01-01',periods=23).strftime('%Y-%m-%d').tolist()
    amounts=pd.DataFrame({'date':days,'symbol6':'000001','amount':[10.]*20+[11.,1e9,1e9]})
    p=pd.DataFrame({'signal_date':[days[20]],'symbol6':['000001'],'close40_return':[.02],
                    'second_high':[103.],'signal_price':[100.]})
    a=attach_amounts(p,amounts,days)
    assert a.prior20_amount_median.iloc[0]==10 and a.amount_gate.iloc[0]==1
    b=attach_amounts(p,amounts.drop(index=0),days)
    assert np.isnan(b.amount_gate.iloc[0])
    assert np.isnan(execution(b,True).daily_net.iloc[0])


def test_outlier_removal_drops_one_per_month_and_preserves_cash_denominator():
    d=pd.DataFrame({'month':['2025-01','2025-01','2025-01','2025-02','2025-02'],
                    'trade':[True,True,False,True,True],
                    'net_return':[.10,-.02,np.nan,.03,-.01],
                    'daily_net':[.10,-.02,0.,.03,-.01]})
    r=robustness(d)
    assert r['best_month']=='2025-01'
    assert np.isclose(r['ex_month_top_trade_mean_pct'],-1.5)
    assert np.isclose(r['ex_month_top_daily_mean_pct'],-.6)
    assert np.isclose(r['ex_both_trade_mean_pct'],-1.)


def test_cash_and_unknown_trade_outcome_have_distinct_denominators():
    d=pd.DataFrame({'selected':[False,True,True],'amount_gate':[np.nan,1.,0.],
                    'net_return':[np.nan,np.nan,.5],'close40_return':[np.nan,np.nan,.5]})
    out=execution(d,True)
    assert out.daily_net.iloc[0]==0 and np.isnan(out.daily_net.iloc[1]) and out.daily_net.iloc[2]==0
    assert out.trade.tolist()==[False,True,False]
    assert not out.unknown_gate.any()

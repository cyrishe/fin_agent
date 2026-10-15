import numpy as np
import pandas as pd
from scripts.experiment_independent_open_drop_co10 import historical,choose,diagnostics,fit_model,score,FEATURES


def test_current_and_future_daily_prices_cannot_change_current_features():
    days=pd.bdate_range('2025-01-01',periods=15).strftime('%Y-%m-%d').tolist()
    d=pd.DataFrame([dict(symbol6=s,trade_date=day,preclose=100.,close=100+i,volume=100)
                    for s in ['000001','000002'] for i,day in enumerate(days)])
    cols=['lag_return','prior5_return','prior5_vol','prior10_vol','prior5_downside','prior5_worst','lag5_market_breadth']
    before=historical(d);before=before[before.trade_date.eq(days[11])][cols]
    d.loc[d.trade_date.ge(days[11]),['close','volume']]=999999
    after=historical(d);after=after[after.trade_date.eq(days[11])][cols]
    pd.testing.assert_frame_equal(before,after)


def test_missing_past_trading_day_is_not_silently_skipped():
    days=pd.bdate_range('2025-01-01',periods=15).strftime('%Y-%m-%d').tolist()
    d=pd.DataFrame([dict(symbol6=s,trade_date=day,preclose=100.,close=100+i,volume=100)
                    for s in ['000001','000002'] for i,day in enumerate(days)])
    d=d[~(d.symbol6.eq('000001')&d.trade_date.eq(days[8]))]
    z=historical(d);assert z[z.symbol6.eq('000001')&z.trade_date.eq(days[11])].prior5_vol.isna().all()


def test_threshold_uses_whole_population_without_B1_or_returns():
    v=pd.DataFrame(dict(label_known=True,big_drop=[True]*40+[False]*160,risk_score=[.8]*40+[.2]*160))
    t,g=choose(v,.3)
    assert (v.risk_score.ge(t)&v.big_drop).sum()==40
    assert v.risk_score.lt(t).sum()==160
    v['B1']=False;v['net_main']=999999
    assert choose(v,.3)[0]==t


def test_unknown_labels_are_not_negative_cases():
    z=pd.DataFrame(dict(label_known=[True,False],big_drop=[True,False],big_rise=[False,False],risk_score=[.8,.8]))
    r=diagnostics(z,.5)
    assert r['n']==2 and r['known']==1 and r['unknown']==1 and r['precision_pct']==100


def test_nonfall_includes_flat_but_extreme_does_not_and_preprocess_train_only():
    rng=np.random.default_rng(10);n=600
    d=pd.DataFrame(rng.normal(size=(n,len(FEATURES))),columns=FEATURES)
    d['sample_id']=range(n);d['signal_date']=np.repeat(['2025-01-01','2025-01-02'],n//2)
    d['label_known']=True;d['big_drop']=[True]*200+[False]*400
    d['nonfall']=[False]*200+[True]*400;d['big_rise']=[False]*400+[True]*200
    m,ids=fit_model(d,'nonfall_logistic');_,extreme=fit_model(d,'extreme_logistic')
    assert len(ids)==600 and len(extreme)==400
    a=score(m,d);d.loc[0,FEATURES]=1e9;b=score(m,d)
    np.testing.assert_array_equal(a[1:],b[1:])

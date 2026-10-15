import numpy as np
import pandas as pd
import pytest
from scripts.experiment_automl_close_open_conditional_b1 import rank_topk,choose_threshold,ARMS
from scripts.report_automl_close_open_conditional_b1 import settle_cash,account,account_stats


def validation(returns,risk):
    return pd.DataFrame({'selection_rank':1,'amount_gate':1.,'net_return':returns,'risk':risk})


def test_topk_preserves_original_class_priority_and_ignores_outcomes():
    d=pd.DataFrame({'signal_date':['2026-01-01']*4,'symbol6':['000003','000002','000001','000004'],
                    'predicted_class':['ge3','1to3','ge3','lt0'],
                    'p_ge3':[.4,.49,.4,.1],'p_1to3':[.2,.5,.1,.1],
                    'future_return':[-.1,.2,.3,.8]})
    a=rank_topk(d);d['future_return']=d.future_return*-100
    assert a.symbol6.tolist()==['000001','000003','000002']
    assert rank_topk(d).symbol6.tolist()==a.symbol6.tolist()


def test_risk_features_exclude_outcomes_and_preserve_declared_counts():
    assert [len(v[1]) for v in ARMS.values()]==[14,14,22,83]
    for _,factors in ARMS.values():
        assert not set(factors)&{'net_return','second_high','close40','next_corporate_action','strong','weak'}


def test_validation_threshold_requires_history_and_avoids_forcing_filter():
    threshold,_=choose_threshold(validation([-.04]*5+[.02]*5,[.6]*5+[.1]*5))
    assert threshold>1
    threshold,_=choose_threshold(validation([-.04]*10+[.02]*10,[.5]*20))
    assert threshold>1


def test_mild_rule_can_block_losses_without_blocking_winners():
    v=validation([-.04]*4+[.02]*16,[.55]*4+[.1]*16)
    threshold,rows=choose_threshold(v)
    assert threshold==.50
    chosen=next(r for r in rows if r['threshold']==threshold)
    assert chosen['critical_blocked']==4 and chosen['profitable_blocked']==0
    assert chosen['kept']==16 and chosen['score']==6


def test_profit_rejection_limit_is_checked_on_validation_only():
    v=validation([-.04]*4+[.02]*16,[.55]*4+[.55]*4+[.1]*12)
    threshold,_=choose_threshold(v)
    assert threshold>1  # 40% total rejection, 25% winner rejection.


def test_cash_account_pays_both_sides_and_never_borrows():
    end,buy=settle_cash(100_000.,0)
    assert buy+buy*.00025+buy*.00001==pytest.approx(100_000.)
    assert end==pytest.approx(buy*(1-.00025-.0005-.00001))
    small,small_buy=settle_cash(10_000.,0)
    assert small_buy+5+small_buy*.00001==pytest.approx(10_000.)
    assert small==pytest.approx(small_buy-5-small_buy*.00051)


def test_drawdown_is_relative_to_equity_peak_including_initial_capital():
    d=pd.DataFrame({'nav_before':[100,120,90],'nav_after':[120,90,108]})
    r=account_stats(d)
    assert r['max_drawdown_pct']==pytest.approx(25.)
    assert r['nav_return_pct']==pytest.approx(8.)


def test_missing_exit_blocks_full_account_claim_instead_of_filling_zero():
    d=pd.DataFrame({'signal_date':['2026-01-01','2026-01-02','2026-01-03'],
                    'trade':[True,True,False],'close40_return':[.01,np.nan,np.nan]})
    a=account(d)
    assert a.nav_after.iloc[0]>100_000 and a.nav_after.iloc[1:].isna().all()
    assert not account_stats(a)['nav_complete']
    assert np.isnan(account_stats(a)['max_drawdown_pct'])


def test_known_cash_days_have_no_fees_or_return_fabrication():
    d=pd.DataFrame({'signal_date':['2026-01-01'],'trade':[False],'close40_return':[np.nan]})
    a=account(d)
    assert a.nav_after.iloc[0]==100_000 and account_stats(a)['max_drawdown_pct']==0

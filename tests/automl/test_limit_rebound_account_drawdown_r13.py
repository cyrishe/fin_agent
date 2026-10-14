import numpy as np
import pandas as pd

from scripts.audit_limit_rebound_account_drawdown_r13 import replay


def test_drawdown_marks_unsold_position_and_includes_cash():
    dates=['2025-01-02','2025-01-03','2025-01-06']
    f=pd.DataFrame([{'d0_date':'2025-01-01','d1_date':dates[0],'symbol':'x',
                    'd1_open':100.,'d1_close':50.,'d2_close':100.,'d2_gross':0.}])
    m,monthly,daily,trades=replay(f,'d2_gross',dates,['2025-01'])
    assert np.isclose(m['账户最大回撤'],.125)
    assert np.isclose(m['账户总收益'],0.)
    assert np.isclose(daily[0]['cash'],.75)


def test_overlapping_trades_are_cash_funded_and_reconcile():
    dates=pd.bdate_range('2025-01-02',periods=5).strftime('%Y-%m-%d').tolist()
    rows=[]
    for i in range(2):
        rows.append({'d0_date':f'2025-01-0{i+1}','d1_date':dates[i],'symbol':str(i),
            'd1_open':100.,'d1_close':100.,'d2_close':90.,'d3_close':90.,'d4_close':110.,'d4_gross':.1})
    m,monthly,daily,trades=replay(pd.DataFrame(rows),'d4_gross',dates,['2025-01'])
    assert m['最高持仓数']==2
    assert np.isclose(m['账户总收益'],.05)
    assert np.isclose(m['账户最大回撤'],.05)
    assert np.isclose(sum(t['proceeds']-t['cost'] for t in trades),.05)

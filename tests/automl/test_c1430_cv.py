import numpy as np
import pandas as pd
from scripts.extract_automl_c1430_cv_paths import extract, EXPECTED
from scripts.experiment_automl_c1430_cv import folds
from scripts.c1430_cv_utils import QuantileClipper


def test_path_cutoff_and_concrete_window():
    times=EXPECTED+['14:31:00','14:40:00','15:00:00']
    bars=pd.DataFrame({'time':['2025-01-02 '+t for t in times],
                       'open':10.,'close':np.arange(len(times))/100+10,'turnover':1.})
    before=extract(bars)
    bars.loc[bars.time.str[-8:].gt('14:30:00'),['open','close','turnover']]=1e9
    assert extract(bars)==before
    assert np.isclose(before['path_last30_return'],12.09/11.79-1)
    assert before['path_late_amount_accel']==1.
    assert np.isclose(before['path_afternoon_amount_share'],90/210)


def test_folds_purge_labels_and_shared_events():
    dates=pd.bdate_range('2025-01-01',periods=158).strftime('%Y-%m-%d')
    p=pd.DataFrame({'d1_date':dates,'d1_i':np.arange(158),'symbol':'000001',
                    'first_limit_date':dates})
    # Same event on far earlier observation and first fold validation.
    p.loc[0,'first_limit_date']=p.loc[58,'first_limit_date']
    for n,t,v in folds(p):
        assert t.d1_i.max()+3<v.d1_i.min()
        assert not set(zip(t.symbol,t.first_limit_date))&set(zip(v.symbol,v.first_limit_date))
        assert v.d1_date.nunique()==25


def test_clipping_fits_training_only():
    c=QuantileClipper().fit(np.arange(100).reshape(-1,1))
    bounds=(c.low_.copy(),c.high_.copy())
    assert c.transform([[1e9]])[0,0]==bounds[1][0]
    assert np.array_equal(c.low_,bounds[0]) and np.array_equal(c.high_,bounds[1])

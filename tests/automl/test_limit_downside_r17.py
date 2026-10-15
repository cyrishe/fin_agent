import numpy as np
import pandas as pd
from scripts.experiment_limit_downside_r17 import labels, block, sample, X


def test_exact_label_boundaries_and_mid_bucket():
    f=pd.DataFrame({'close':[100.]*5,'d1_close':[100.]*5,'d2_close':[100.]*5,
                    'd3_close':[94.99,95.,97.99,98.,105.],
                    'd1_preclose':[100.]*5,'d2_preclose':[100.]*5,'d3_preclose':[100.]*5})
    known,gap,y=labels(f)
    assert known.all() and not gap.any()
    np.testing.assert_equal(y.to_numpy(),np.array([1.,np.nan,np.nan,0.,0.]))


def test_gap_and_incomplete_outcomes_do_not_become_negatives():
    f=pd.DataFrame({'close':[100.,100.],'d1_close':[100.,100.],'d2_close':[100.,100.],
                    'd3_close':[99.,np.nan],'d1_preclose':[90.,100.],
                    'd2_preclose':[100.,100.],'d3_preclose':[100.,100.]})
    known,gap,y=labels(f)
    assert not known.any() and y.isna().all() and gap.tolist()==[True,False]


def test_cutoff_removes_unobservable_labels_and_running_event():
    cal=pd.bdate_range('2025-01-01','2025-02-14').strftime('%Y-%m-%d').tolist()
    rows=[]
    for i,d in enumerate(cal):
        rows.append({'symbol':'A','first_limit_date':'2025-01-01','d0_date':d,'date_i':i})
        if i%2==0:rows.append({'symbol':'B','first_limit_date':d,'d0_date':d,'date_i':i})
    f=block(pd.DataFrame(rows),cal,'2025-01','2025-01')
    assert not f.symbol.eq('A').any()
    assert all(cal[i+3]<'2025-02-01' for i in f.date_i)


def test_sampling_uses_only_poles_and_is_reproducible():
    f=pd.DataFrame({'d0_date':['2025-01-02']*100,'symbol':[str(i) for i in range(100)],
                    'risk_y':[0.]*60+[1.]*20+[np.nan]*20})
    a,w=sample(f,'balanced_weight',171);b,v=sample(f,'balanced_weight',171)
    assert a.index.equals(b.index) and a.risk_y.notna().all() and len(a)==64
    assert np.isclose(w[a.risk_y.eq(0)].sum(),w[a.risk_y.eq(1)].sum())


def test_feature_allowlist_excludes_future_and_prior_model():
    assert not any(c.startswith(('d1_','d2_','d3_','d4_','risk_','label_','score')) for c in X)
    assert len(X)==35


def test_filter_evaluation_keeps_middle_and_excludes_missing_labels():
    from scripts.report_limit_downside_r17 import screening
    f=pd.DataFrame({'risk_known':[True,True,True,False],'risk_y':[1.,0.,np.nan,np.nan],
                    'risk_return':[-.06,.04,-.03,np.nan],'flag20':[True,True,False,True]})
    s=screening(f)
    assert s['known']==3 and s['flagged']==2 and s['kept']==1
    assert s['flag_bad_precision']==.5 and s['bad_recall']==1
    assert s['killed_safe']==1 and s['killed_up']==1
    assert s['kept_bad_rate']==0 and s['kept_mean_return']==-.03

import pandas as pd

from scripts.experiment_limit_rebound_monthly_r14 import block, window, selection_key


def test_ten_two_one_windows_are_past_only():
    assert window('2025-01')=={'train':['2024-01','2024-10'],'valid':['2024-11','2024-12'],'test':'2025-01'}
    assert window('2026-10')=={'train':['2025-10','2026-07'],'valid':['2026-08','2026-09'],'test':'2026-10'}


def test_cutoff_excludes_unknown_outcomes_and_ongoing_events_without_future_keys():
    cal=pd.bdate_range('2025-01-01','2025-03-31').strftime('%Y-%m-%d').tolist()
    f=pd.DataFrame({'symbol':['x']*len(cal),'d0_date':cal,'date_i':range(len(cal)),
                    'first_limit_date':cal})
    last=[i for i,d in enumerate(cal) if d.startswith('2025-01')][-1]
    f.loc[last,'first_limit_date']='2025-01-02'
    a=block(f,cal,'2025-01','2025-01')
    assert a.date_i.add(4).max()<=last
    assert '2025-01-02' not in a.first_limit_date.tolist()
    validation=block(f,cal,'2025-01','2025-01',purge_events=False)
    assert '2025-01-02' in validation.first_limit_date.tolist()
    f.loc[f.d0_date.ge('2025-02-01'),'first_limit_date']='2025-01-03'
    pd.testing.assert_frame_equal(a,block(f,cal,'2025-01','2025-01'))


def test_selection_uses_cv_and_validation_not_fit_or_test():
    a={'cv':{'hits_per_day':.5,'ap':.3},'valid':{'hits_per_day':.4,'ap':.4},'train':{'hits_per_day':1.}}
    b={'cv':{'hits_per_day':.6,'ap':.3},'valid':{'hits_per_day':.5,'ap':.4},'train':{'hits_per_day':.1}}
    assert selection_key(b)>selection_key(a)

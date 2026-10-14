import numpy as np
import pandas as pd

from scripts.experiment_limit_rebound_short_mixed_r12 import split_frames, top_five


def test_information_windows_are_disjoint_in_random_months():
    calendar = pd.bdate_range('2024-01-01','2026-10-13').strftime('%Y-%m-%d').tolist()
    panel = pd.DataFrame({'d0_date':calendar,'date_i':range(len(calendar)),
                          'first_i':range(len(calendar)),'first_limit_date':calendar,
                          'symbol':'000001','activated':True,'observed_3':True})
    spec = {'mixed':{'months':{
        '2024':{'train':['2024-03'],'valid':['2024-04'],'test':[]},
        '2025':{'train':['2025-01'],'valid':['2025-02'],'test':['2025-03']},
        '2026':{'train':['2026-01'],'valid':['2026-03'],'test':['2026-02']}}}}
    frames,_ = split_frames(panel,calendar,spec,'mixed',10)
    used = {part:set().union(*(set(range(int(r.date_i)-5,int(r.date_i)+5))
                              for r in f.itertuples())) for part,f in frames.items()}
    assert used['train'] and used['valid'] and used['test']
    assert not used['train'] & used['valid']
    assert not used['train'] & used['test']
    assert not used['valid'] & used['test']


def test_top5_is_fixed_before_activation_and_no_sixth_replacement():
    f=pd.DataFrame({'symbol':[f'{i:06}' for i in range(6)],
                    'd0_date':'2025-01-06','d1_date':'2025-01-07',
                    'activated':[False]*5+[True]})
    top=top_five(f,np.arange(6,0,-1))
    assert len(top)==5
    assert top.selected.sum()==0
    f.loc[2,'activated']=True
    top=top_five(f,np.arange(6,0,-1))
    assert top.loc[top.selected,'symbol'].tolist()==['000002']

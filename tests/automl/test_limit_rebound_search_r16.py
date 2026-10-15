import numpy as np
import pandas as pd
from scripts.experiment_limit_rebound_search_r16 import GROUPS,sample_train,window

def fixture():
 rows=[]
 for m in range(1,7):
  for y,n in [(0,100),(1,20)]:
   rows += [{'symbol':f'{m}{y}{i:04}','d0_date':f'2025-{m:02}-15','label_3':y} for i in range(n)]
 return pd.DataFrame(rows)

def test_all_algorithms_have_24_reproducible_variants():
 for a in ('rf','lgb','xgb'):
  groups=[g for g in GROUPS if g['algorithm']==a]
  assert len(groups)==12 and sum(len(g['seeds']) for g in groups)==24
 assert len({g['id'] for g in GROUPS})==36

def test_sampling_preserves_time_population_and_month_class_coverage():
 f=fixture();a,w=sample_train(f,'natural',171);b,_=sample_train(f,'natural',171);c,_=sample_train(f,'natural',191)
 assert a.equals(b) and not a.equals(c)
 assert len(a)==int(.8*len(f)) and not a.symbol.duplicated().any()
 assert set(a.symbol)<=set(f.symbol) and a.groupby([a.d0_date,a.label_3]).size().tolist()==[80,16]*6
 assert np.all(w==1)

def test_weights_equalize_class_mass_without_duplicating_rows():
 a,w=sample_train(fixture(),'balanced_weight',171)
 assert np.isclose(w[a.label_3.eq(0)].sum(),w[a.label_3.eq(1)].sum())
 b,v=sample_train(fixture(),'sqrt_weight',171)
 assert a.equals(b) and 1<v[b.label_3.eq(1)][0]/v[b.label_3.eq(0)][0]<5

def test_undersampling_is_balanced_and_only_removes_from_draw():
 f=fixture();a,_=sample_train(f,'natural',171);b,w=sample_train(f,'undersample',171)
 assert set(b.symbol)<=set(a.symbol) and b.label_3.value_counts().nunique()==1 and np.all(w==1)

def test_rolling_window_remains_six_two_one():
 assert window('2025-09')=={'train':['2025-01','2025-06'],'valid':['2025-07','2025-08'],'test':'2025-09'}

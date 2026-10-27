import numpy as np
import pandas as pd
from scripts.train_low_base_rf_co27 import FEATURES,fit
from scripts.train_low_base_co26 import replay

def test_random_forest_trains_all_three_classes_and_reloads_predictions():
 rng=np.random.default_rng(123);n=4500
 X=rng.normal(size=(n,len(FEATURES)));tr=pd.DataFrame(X,columns=FEATURES)
 tr['date']=np.repeat(pd.date_range('2024-01-01',periods=45).strftime('%Y-%m-%d'),100)
 tr['sample_id']=np.arange(n);tr['net2']=np.where(X[:,0]>0.5,.04,np.where(X[:,0]<-.5,-.04,0.0))
 te=tr.iloc[:30].copy();te.loc[te.index[0],FEATURES[0]]=np.nan
 bundle,p=fit(tr,te,2)
 assert bundle['model'].n_estimators==80 and bundle['model'].max_samples==.1
 assert set(bundle['model'].classes_)=={-1,0,1}
 np.testing.assert_allclose(p,replay(bundle,te),rtol=0,atol=1e-14)
 np.testing.assert_allclose(p.sum(axis=1),1,atol=1e-14)

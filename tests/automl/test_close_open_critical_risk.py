import numpy as np
import pandas as pd
import pytest
from scripts.experiment_automl_close_open_critical_risk import (
    net_classes,ordinal_targets,expanded_targets,project_cumulative,cost_matrix,decision_scores,
)


def test_actual_net_boundaries_and_unknown_not_fabricated():
    assert net_classes([-.020001,-.02,-.000001,0,.01,.03]).tolist()==[0,1,1,2,3,4]
    with pytest.raises(ValueError):net_classes([np.nan])


def test_fuzzy_targets_only_blur_noncritical_boundaries():
    r=np.array([-.02001,-.02,-.0001,.0001,.0099,.0101,.0299,.0301])
    hard=ordinal_targets(r);soft=ordinal_targets(r,True)
    assert np.array_equal(soft[:,0],hard[:,0])
    assert np.all(soft[0]==0) and soft[1,0]==1
    assert soft[2,1]<.5<soft[3,1] and soft[4,2]<.5<soft[5,2]
    assert np.all(np.diff(soft,axis=1)<=0)


def test_fractional_expansion_exactly_reproduces_soft_bce():
    x=np.array([[.3],[.8],[-.7]])
    q=np.array([0.,.4,1.]);w=np.array([3.,1.,2.])
    xx,yy,ww=expanded_targets(x,q,w)
    p=1/(1+np.exp(-x[:,0]));expanded_p=1/(1+np.exp(-xx[:,0]))
    expected=-np.sum(w*(q*np.log(p)+(1-q)*np.log1p(-p)))
    actual=-np.sum(ww*(yy*np.log(expanded_p)+(1-yy)*np.log1p(-expanded_p)))
    assert np.isclose(expected,actual) and np.isclose(ww.sum(),w.sum())


def test_cost_grows_with_optimistic_crossing_and_reaches_trade_rule():
    c=cost_matrix()
    assert c[0,4]>c[0,3]>c[0,2]>c[0,1]>c[0,0]
    assert c[0,4]>c[4,0]
    p=np.array([[.1,.1,.2,.2,.4],[0,0,0,0,1]])
    d=decision_scores(p,'cost_matrix',pd.RangeIndex(2))
    assert d.risk_decision_class.iloc[0]<3 and d.score.iloc[0]==-np.inf
    assert d.score.iloc[1]>0


def test_ordinal_projection_is_a_valid_five_class_distribution():
    p=project_cumulative(np.array([[.8,.9,.5,.7],[1,1,1,1],[0,0,0,0]]))
    assert np.all(p>=0) and np.allclose(p.sum(axis=1),1)
    assert p[0,0]==pytest.approx(.2)

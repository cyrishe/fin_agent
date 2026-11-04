import json
import pytest
from scripts.merge_close_open_versions import plan_merge


def row(version, day='2026-10-14', **kw):
    return dict(model_version=version, signal_date=day, version_desc='original', environment_json='{}', details_json='{}', selection_rank=1, stock_code='002812', actionable=1, signal_price=46.33, buy_at=day+' 14:40:00', buy_price=46.33, sell_at='2026-10-15 09:40:00', sell_price=47.22, gross_return_pct=1.921, net_return_pct=None, **kw)


def test_merge_same_trade_preserves_provenance_and_today():
    old, new, today = row('v1.0'), row('v1.1'), row('v1.0','2026-10-16')
    snapshot={'runs':[old,new,today], 'picks':[old,new,today]}
    merged,diffs,days=plan_merge(snapshot)
    assert not diffs and days == {'2026-10-14'}
    assert len(merged['picks']) == 2
    assert all(p['model_version']=='v1.0' for p in merged['picks'])
    assert merged['picks'][1] == today
    assert json.loads(merged['picks'][0]['details_json'])['version_merge']['previous_record']==old
    again,_,days=plan_merge(merged)
    assert again==merged and not days


def test_observation_difference_retained_in_audit():
    old,new=row('v1.0'),row('v1.1')
    for r in (old,new):
        r.update(actionable=0,buy_price=None,sell_price=None)
    new['stock_code']='603920'
    merged,diffs,_=plan_merge({'runs':[old,new],'picks':[old,new]})
    assert len(diffs)==1
    assert merged['picks'][0]['stock_code']=='603920'
    assert json.loads(merged['picks'][0]['details_json'])['version_merge']['previous_record']['stock_code']=='002812'


def test_changed_or_missing_trade_refuses_merge():
    old,new=row('v1.0'),row('v1.1')
    new['buy_price']=46.34
    with pytest.raises(ValueError,match='实际交易不一致'):
        plan_merge({'runs':[old,new],'picks':[old,new]})
    with pytest.raises(ValueError,match='实际交易不一致'):
        plan_merge({'runs':[old,new],'picks':[old]})

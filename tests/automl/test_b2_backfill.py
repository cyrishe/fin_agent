from types import SimpleNamespace
from decimal import Decimal
import pytest
from scripts.backfill_automl_b2 import settlement, assert_json_matches
from scripts.run_automl_b2 import concise


def test_d2_exit_uses_each_side_fee_and_continuity():
    p=settlement(10,SimpleNamespace(close=10.5),SimpleNamespace(preclose=10.5,close=11,volume=100))
    assert p['gross_return_pct']==10
    assert p['net_return_pct']==pytest.approx(float((Decimal('11')*Decimal('.999')/(Decimal('10')*Decimal('1.001'))-1)*100))
    assert settlement(10,None,None)=={}
    assert settlement(10,SimpleNamespace(close=10.5),SimpleNamespace(preclose=11,close=11,volume=100))=={}
    assert settlement(10,SimpleNamespace(close=10.5),SimpleNamespace(preclose=10.5,close=11,volume=0))=={}


def test_operational_output_distinguishes_replay_from_confirmation():
    p=dict(symbol6='002343',name='慈文传媒',selection_rank=1,score=.77,
           actionable=1,entry_price=9.3,entry_at='2026-10-16 09:35:00',historical_replay=True,
           shape_features=dict(high5=.10,low5=.001,body5=-.04))
    result=concise(dict(signal_date='2026-10-15',candidate_count=36,picks=[p]))
    z=result['前三'][0]
    assert z['四K最高%']==10 and z['实际确认'] is None and z['历史回放']
    assert z['模拟买价']==9.3 and 'features' not in z
    p.update(shape_ready=True,shape_pass=False,actionable=0)
    assert concise(dict(picks=[p]))['前三'][0]['说明']=='四K形态未通过'


def test_backfill_refuses_modified_payload_before_database(tmp_path):
    import json
    from scripts.backfill_automl_b2 import write
    (tmp_path/'prepared.json').write_text(json.dumps({'payload_sha256':'0'*64}))
    (tmp_path/'payload.json').write_text('[]')
    with pytest.raises(AssertionError):write(SimpleNamespace(output=tmp_path))


def test_mysql_json_roundtrip_accepts_float_roundoff_only():
    assert_json_matches({'score':.10091743119266061,'code':'600403'},
                        {'score':.1009174311926606,'code':'600403'})
    with pytest.raises(AssertionError):assert_json_matches({'score':.77},{'score':.77001})
    with pytest.raises(AssertionError):assert_json_matches({'code':'600403'},{'code':'600404'})

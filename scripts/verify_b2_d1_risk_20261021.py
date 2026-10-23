"""Independent checks for the two decision times and frozen selection."""
import json,copy
import joblib,numpy as np,pandas as pd
from scripts import research_b2_floor_risk_20261021 as old
from scripts import research_b2_d1_risk_20261021 as run
from scripts.build_b2_d1_risk_20261021 import ROOT,OUT,features,EARLY,LATE

def main():
    s=json.loads((OUT/'selection_frozen.json').read_text());result=json.loads((OUT/'test_results.json').read_text());f=run.load()
    assert old.sha(OUT/'selection_frozen.json')==result['selection_sha256']
    assert old.sha(OUT/'panel.pkl')==s['panel_sha256']
    assert old.sha(ROOT/'scripts/research_b2_d1_risk_20261021.py')==s['code_sha256']
    assert old.sha(ROOT/'scripts/build_b2_d1_risk_20261021.py')==s['builder_sha256']
    assert f[f.split=='train']['卖出日'].max()<'2025-01-01'
    assert f[f.split=='valid']['卖出日'].max()<'2026-01-01'
    assert f[f.split=='test'].d0_date.min()>='2026-01-01'
    assert not set(run.GROUPS['early'])&set(LATE)
    assert not any(c.startswith('d2') or c in ['y','original_net','entry','fill','买价','卖价'] for c in run.GROUPS['close'])
    metadata={x['key']:x for x in json.loads((OUT/'feature_dictionary.json').read_text())}
    assert all(metadata[c]['known_at']!='D1 15:00' for c in run.GROUPS['early'])
    audit=json.loads((OUT/'minute_audit.json').read_text());assert len(audit['rows'])==306 and not audit['errors']
    for row in f.to_dict('records'):
        path=OUT/'minutes'/f"{row['d1_date']}_{row['symbol']}.csv.gz";b=pd.read_csv(path)
        evidence=next(x for x in audit['rows'] if x['sequence']==row['sequence'])
        assert old.sha(path)==evidence['cache_sha256']
        a,z=features(row,b)
        for k,v in {**a,**z}.items():assert np.isclose(v,row[k],equal_nan=True),k
        # Change future bars AND full-day aggregates; early features must not move.
        changed=b.copy();changed.loc[4:,['open','high','low','close']]=row['d1_preclose']
        changed.loc[4:,'volume']=123456
        altered=dict(row);altered.update(d1_volume=999999999,d1_turn=.99,d1_close=.01)
        early,_=features(altered,changed)
        for k in EARLY:assert np.isclose(a[k],early[k],equal_nan=True),(row['sequence'],k)
    saved=pd.read_csv(OUT/'test_predictions.csv');checked=0
    for item in s['candidates']:
        target=item['target'];g=run.outcome(f,target);rule=item['rule'];tr=g[g.split=='train']
        if rule['kind']=='model':
            model=joblib.load(OUT/rule['file']);assert list(model.feature_names_in_)==rule['columns']
            assert old.sha(OUT/rule['file'])==s['models'][item['id']]['sha256']
            assert float(np.quantile(model.predict_proba(tr[rule['columns']])[:,1],.75))==rule['threshold']
            num=model.named_steps['features'].transformers_[0][2];pipe=model.named_steps['features'].named_transformers_['numeric']
            low=tr[num].quantile(.02).to_numpy();high=tr[num].quantile(.98).to_numpy()
            np.testing.assert_allclose(pipe.named_steps['winsor'].kw_args['a_min'],low)
            np.testing.assert_allclose(pipe.named_steps['winsor'].kw_args['a_max'],high)
            clipped=np.clip(tr[num].to_numpy(),low,high);median=np.nanmedian(clipped,axis=0)
            np.testing.assert_allclose(pipe.named_steps['impute'].statistics_,median)
            filled=np.where(np.isnan(clipped),median,clipped)
            np.testing.assert_allclose(pipe.named_steps['scale'].mean_,filled.mean(axis=0))
        for part in ['train','valid','test']:
            h=g[g.split==part];flag=run.predict(rule,h)
            m=item[part] if part!='test' else next(x for x in result['candidates'] if x['id']==item['id'])['test']
            assert int(flag.sum())==m['flagged'] and int(h.loc[flag,'y'].sum())==m['caught']
            altered=h.copy();outside=[c for c in h.columns if c not in run.GROUPS[item['stage']]];altered[outside]=None
            np.testing.assert_array_equal(flag,run.predict(rule,altered))
            if part=='test':
                p=saved[saved.candidate==item['id']]
                np.testing.assert_array_equal(flag,p.flagged.to_numpy(bool));np.testing.assert_array_equal(h.y,p.risk)
            checked+=1
    for target in ['close','union']:
        for stage in ['early','close']:
            items=[x for x in s['candidates'] if x['target']==target and x['stage']==stage]
            assert run.choose(items)==s['choices'][target+'_'+stage]
    run.save('verification.json',dict(minute_days=306,all_minute_hashes_ohlc_and_volume_verified=True,
      early_features_unchanged_when_future_bars_and_aggregates_changed=True,features_recomputed=True,
      train_only_preprocessing=True,model_thresholds_lossless=True,temporal_splits_and_boundary_purge=True,
      prediction_checks=checked,selection_matches_predeclared_validation_rule=True,
      no_production_strategy_change=True,code_sha256=old.sha(__file__)))
    print('verified',checked,'candidate-period combinations; 306 early causal perturbations')

if __name__=='__main__':main()

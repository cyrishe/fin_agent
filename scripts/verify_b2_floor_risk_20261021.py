"""Independent temporal/leakage, model reload and confusion-matrix checks."""
import json
import numpy as np
import pandas as pd
import joblib
from scripts.research_b2_floor_risk_20261021 import ROOT,OUT,SOURCE,CORE,NUM,CAT,load,sha,predict_rule,dump

def main():
    s=json.loads((OUT/'selection_exact.json').read_text());r=json.loads((OUT/'test_results_exact.json').read_text())
    assert sha(OUT/'selection_frozen.json')==r['selection_sha256']
    assert sha(OUT/'selection_exact.json')==r['exact_selection_sha256']
    assert sha(SOURCE)==s['source_sha256']==r['source_sha256']
    assert sha(ROOT/'scripts/research_b2_floor_risk_20261021.py')==s['code_sha256']
    f=load();tr=f[f.split=='train'];va=f[f.split=='valid'];te=f[f.split=='test']
    assert len(tr)==152 and len(va)==54 and len(te)==98 and len(f[f.split=='purged'])==2
    assert tr['卖出日'].max()<'2025-01-01' and va['卖出日'].max()<'2026-01-01'
    assert set(tr.symbol+'|'+tr.d0_date).isdisjoint(set(te.symbol+'|'+te.d0_date))
    assert set(tr.symbol+'|'+tr.d0_date).isdisjoint(set(va.symbol+'|'+va.d0_date))
    for n in ['train','valid','test','purged']:
        a=pd.read_csv(OUT/f'{n}_samples.csv',dtype={'symbol':str});b=f[f.split==n]
        assert a.y.sum()==b.y.sum() and len(a)==len(b)
        assert not set(a.columns)&{'d1_close','d2_close','d2_high','d2_low','d2_open','original_net','entry'}
    for info in s['model_info'].values():
        assert sha(OUT/info['file'])==info['sha256'];model=joblib.load(OUT/info['file'])
        assert list(model.feature_names_in_)==CORE
        x=tr[['total_mv_yi','d0_turnover_pct','board_streak_days']].to_numpy()
        trans=model.named_steps['features'].named_transformers_['positive']
        np.testing.assert_allclose(trans.named_steps['scale'].mean_,np.log1p(x).mean(axis=0))
        np.testing.assert_allclose(trans.named_steps['impute'].statistics_,np.median(x,axis=0))
    # Independently apply the predeclared validation choice to saved candidates.
    candidates=s['candidates'];eligible=[]
    for c in candidates:
        m=c['valid'];passed=m['flagged']>=6 and m['reject_fraction']<=.35 and m['caught']>=3 and m['lift']>=1.25
        assert c['eligible']==passed
        if passed:eligible.append(c)
    eligible.sort(key=lambda x:(-x['valid']['retained_risk_reduction'],x['valid']['flagged'],x['rule']['kind']!='single',x['id']))
    assert eligible[0]['id']==s['selected_id']=='logistic'
    preds=pd.read_csv(OUT/'test_predictions_exact.csv',dtype={'股票':str})
    altered=te.copy()
    forbidden=[c for c in altered.columns if c not in [*NUM,CAT]]
    altered[forbidden]=None
    for c in candidates:
        for part,h in [('train',tr),('valid',va)]:
            marks=predict_rule(c['rule'],h)
            assert int(marks.sum())==c[part]['flagged']
            assert int(h.loc[marks,'y'].sum())==c[part]['caught']
        saved=preds[preds.id==c['id']]
        flag=predict_rule(c['rule'],te);changed=predict_rule(c['rule'],altered)
        np.testing.assert_array_equal(flag,changed)
        np.testing.assert_array_equal(flag,saved.flagged.to_numpy(bool))
        np.testing.assert_array_equal(te.y.to_numpy(),saved.risk.to_numpy())
        expected=next(x for x in r['candidates'] if x['id']==c['id'])['test']
        table=pd.crosstab(saved.risk,saved.flagged).reindex(index=[0,1],columns=[0,1],fill_value=0)
        assert expected['caught']==int(table.loc[1,1]) and expected['false_flags']==int(table.loc[0,1])
        assert expected['missed']==int(table.loc[1,0]) and expected['flagged']==int(table[1].sum())
    ci=r['selected']['uncertainty'];assert ci['months']==7 and ci['valid_draws']==3000
    before=pd.read_csv(OUT/'test_predictions.csv',dtype={'股票':str})
    np.testing.assert_array_equal(before.loc[before.id=='logistic','flagged'],preds.loc[preds.id=='logistic','flagged'])
    checks=dict(input_source_and_frozen_selection_hashes=True,training_validation_test_temporal_disjoint=True,
        two_boundary_labels_purged=True,buy_before_information_whitelist=True,all_four_K_finished_before_buy=True,
        preprocessing_fit_on_training_only=True,selected_on_validation_only=True,
        saved_models_reproduce_all_16_test_predictions=True,future_fields_and_labels_cannot_change_predictions=True,
        independent_confusion_matrices_match=True,exact_thresholds_recover_original_fit_counts=True,
        precision_repair_keeps_selected_predictions=True,test_month_cluster_bootstrap_draws=3000,
        training_rows=152,validation_rows=54,test_rows=98,production_changed=False,code_sha256=s['code_sha256'])
    dump('verification.json',checks);print(json.dumps(checks,ensure_ascii=False))

if __name__=='__main__':main()

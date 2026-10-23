"""Restore exact training thresholds after JSON rounding; no fitting or selection changes.

Keep the original frozen files as evidence. Use *_exact.json/csv for comparison
reports. Quantile reconstruction consumes training X only, never future labels.
"""
import copy
import json
import joblib
import numpy as np
import pandas as pd
from scripts import research_b2_floor_risk_20261021 as r


def write(name, value):
    # Python JSON preserves a round-trip representation of every binary64 float.
    (r.OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def main():
    original=json.loads((r.OUT/'selection_frozen.json').read_text())
    selection=copy.deepcopy(original)
    result=json.loads((r.OUT/'test_results.json').read_text())
    f=r.load();tr=f[f.split=='train'];te=f[f.split=='test']
    quantiles={k:np.unique(tr[k].quantile([.25,.5,.75]).to_numpy()) for k in r.NUM}
    quantiles['board_streak_days']=np.unique(np.floor(quantiles['board_streak_days']))

    def restore(rule):
        if rule['kind']=='and':
            restore(rule['a']);restore(rule['b'])
        elif rule['kind']=='model':
            model=joblib.load(r.OUT/rule['file'])
            rule['threshold']=float(np.quantile(model.predict_proba(tr[r.CORE])[:,1],.75))
        elif rule['feature'] in r.NUM:
            q=quantiles[rule['feature']]
            v=float(q[np.argmin(np.abs(q-rule['value']))])
            assert np.isclose(v,rule['value'],rtol=1e-13,atol=1e-14)
            rule['value']=v

    differences=[];predictions=[];eligible=[]
    for item in selection['candidates']:
        old=copy.deepcopy(item['rule']);restore(item['rule'])
        for part in ('train','valid','test'):
            h=f[f.split==part];a=r.predict_rule(old,h);b=r.predict_rule(item['rule'],h)
            changed=int((a!=b).sum())
            if changed:differences.append(dict(id=item['id'],split=part,changed=changed,rounded_flags=int(a.sum()),exact_flags=int(b.sum())))
            if part!='test':
                m=r.metrics(h,b)
                # Recover the actual pre-serialization training/validation behavior.
                for key in ('flagged','caught','false_flags','missed'):
                    assert m[key]==item[part][key],(item['id'],part,key)
            if item['id']==original['selected_id']:assert changed==0
        m=item['valid'];ok=m['flagged']>=6 and m['reject_fraction']<=.35 and m['caught']>=3 and m['lift']>=1.25
        assert ok==item['eligible']
        if ok:eligible.append(item)
        flag=r.predict_rule(item['rule'],te)
        target=next(x for x in result['candidates'] if x['id']==item['id'])
        target['test']=r.metrics(te,flag);target['uncertainty']=r.cluster_ci(te,flag)
        for row,marked in zip(te.to_dict('records'),flag):
            predictions.append(dict(id=item['id'],D0=row['d0_date'],股票=row['symbol'],risk=int(row['y']),flagged=int(marked),open_floor=row['open_floor'],close_floor=row['close_floor'],original_net=row['original_net']))
    eligible.sort(key=lambda x:(-x['valid']['retained_risk_reduction'],x['valid']['flagged'],x['rule']['kind']!='single',x['id']))
    assert eligible[0]['id']==original['selected_id']=='logistic'
    selection['selected']=next(x for x in selection['candidates'] if x['id']==selection['selected_id'])
    for item in selection['train_single_rules']:restore(item['rule'])
    result['selected']=next(x for x in result['candidates'] if x['selected'])
    audit=dict(reason='Pandas JSON rounded binary64 values to 15 decimal places; strict > at tied tree scores changed behavior.',
        repair='Reconstruct original Q75 model thresholds and single-rule quantiles from training X and saved fitted models; lossless JSON serialization.',
        original_selection_sha256=r.sha(r.OUT/'selection_frozen.json'),original_test_sha256=r.sha(r.OUT/'test_results.json'),
        code_sha256=r.sha(__file__),differences=differences,models_refit=False,new_rule_search=False,
        original_train_validation_counts_recovered=True,validation_choice_unchanged=True,selected_predictions_all_splits_unchanged=True,
        selected_id=selection['selected_id'])
    selection['precision_audit']=audit;result['precision_audit']=audit
    write('selection_exact.json',selection)
    result['exact_selection_sha256']=r.sha(r.OUT/'selection_exact.json')
    write('test_results_exact.json',result)
    pd.DataFrame(predictions).to_csv(r.OUT/'test_predictions_exact.csv',index=False,encoding='utf-8-sig')
    write('precision_audit.json',audit)
    print(json.dumps(audit,ensure_ascii=False))


if __name__=='__main__':main()

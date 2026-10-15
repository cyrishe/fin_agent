"""Check completed CO5 folds, saved artifacts and representative score reproduction."""
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
from scripts.experiment_automl_close_open_simple_rolling import (
    OUT, REPORT, SOURCE, sha, mature, retained_rows, scores, tree_scores,
)

def run():
    panel=pd.read_csv(SOURCE/'factor_panel.csv.gz',dtype={'symbol6':str},float_precision='round_trip').sort_values(['signal_date','symbol6']).reset_index(drop=True)
    n_models=0;n_candidates=0;sample_errors=[]
    folds=sorted((OUT/'folds').glob('*/complete.json'))
    for f in folds:
        meta=json.loads(f.read_text())
        assert meta['discovery_dates'][-1]<meta['validation_dates'][0]
        assert meta['discovery_label_max']<=meta['validation_dates'][0]
        assert meta['validation_dates'][-1]<meta['first_test']
        assert meta['validation_label_max']<=meta['first_test']
        saved=pd.read_csv(f.parent/'predictions.csv.gz',dtype={'symbol6':str,'predicted_class':str},float_precision='round_trip')
        assert not saved.duplicated(['model','signal_date','symbol6']).any()
        assert saved.groupby('model').size().eq(meta['test_rows']).all()
        assert saved.model.nunique()==14
        assert len(pd.read_csv(f.parent/'factor_selection.csv').query('selected'))==12
        for entry in meta['models']:
            when=entry.get('decision_day',meta['first_test'])
            assert entry['fit_last']<when and entry['max_label_date']<=when
            assert sha(Path(entry['file']))==entry['sha256']
            n_models+=1
        n_candidates+=len(saved)
    # Reopen objects in another process and recompute every October score for one
    # broad model and every candidate on the last day for the daily tree control.
    folder=OUT/'folds'/'2026-10'
    saved=pd.read_csv(folder/'predictions.csv.gz',dtype={'symbol6':str,'predicted_class':str},float_precision='round_trip')
    for model_id,file,mask in [
        ('logit_broad76','2026-10_logit_broad76.joblib',panel.signal_date.str.startswith('2026-10')),
        ('tree171_seven7_samepool','2026-10-13_tree171_seven7_samepool.joblib',panel.signal_date.eq('2026-10-13'))]:
        fitted=joblib.load(OUT/'models'/file);te=panel[mask]
        actual=scores(fitted,te) if isinstance(fitted,dict) else tree_scores(fitted,te)
        a=te[['signal_date','symbol6']].join(actual).set_index(['signal_date','symbol6'])
        b=saved[saved.model.eq(model_id)].set_index(['signal_date','symbol6']).reindex(a.index)
        assert np.allclose(a.score,b.score,rtol=0,atol=1e-14,equal_nan=True)
        finite=np.isfinite(a.score)&np.isfinite(b.score)
        sample_errors.append({'model':model_id,'rows':len(a),'max_score_error':float((a.score[finite]-b.score[finite]).abs().max())})
    d=pd.read_csv(OUT/'daily_picks_with_gate.csv',dtype={'symbol6':str},float_precision='round_trip')
    b=d[d.model.eq('B1_frozen_original171')]
    original=pd.read_csv('docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top1_verified.csv',dtype={'symbol6':str},float_precision='round_trip')
    merged=b[b.selected].merge(original,on=['signal_date','symbol6'])
    assert len(b)==118 and b.selected.sum()==115 and len(merged)==115
    assert np.allclose(merged.close40_return,merged.actual_0940_return,rtol=0,atol=1e-14)
    # The frozen gate is compared to the separately archived source ledger.
    ledger=pd.read_csv('docs/stock_automl_runs/20261011_market_state_asof/daily_2026.csv',dtype={'Top1代码':str})
    ledger['signal_date']=pd.to_datetime(ledger['选股日']).dt.strftime('%Y-%m-%d')
    ledger['symbol6']=ledger['Top1代码'].str.zfill(6)
    compared=b[b.selected].merge(ledger,on=['signal_date','symbol6'])
    expected=compared['个股成交额低于此前20日中位数（标签）'].map({'否':1.,'是':0.})
    known=expected.notna()&compared.amount_gate.notna()
    assert len(compared)==115 and known.sum()==111
    assert compared.loc[known,'amount_gate'].eq(expected[known]).all()
    assert compared.amount_gate.isna().eq(expected.isna()).all()
    selected=d[d.selected]
    sample=selected.merge(panel[['signal_date','symbol6','day_vwap_distance','minute_volume_shares']],on=['signal_date','symbol6'])
    inferred=sample.signal_price/(1+sample.day_vwap_distance)*sample.minute_volume_shares
    amount_error=(sample.amount/inferred-1).abs()
    assert amount_error.max()<1e-12
    result={'completed_month_folds':len(folds),'model_hashes_and_chronology_checked':n_models,
            'candidate_score_rows':n_candidates,'representative_saved_score_reproduction':sample_errors,
            'original_B1_days':len(b),'original_B1_selected':int(b.selected.sum()),
            'original_B1_gate_passes':int(b.amount_gate.eq(1).sum()),'original_B1_known_gates_match':int(known.sum()),
            'original_B1_unknown_gates_match':int(expected.isna().sum()),
            'selected_rows_gate_unknown':int((d.selected&d.amount_gate.isna()).sum()),
            'amount_vwap_reconstruction_max_relative_error':float(amount_error.max())}
    (REPORT/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    with threadpool_limits(limits=1): run()

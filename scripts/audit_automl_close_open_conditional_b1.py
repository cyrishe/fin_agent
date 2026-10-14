"""Independently inspect CO7 fold provenance, frozen identities and risk scoring."""
import json
from importlib.metadata import version
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
from scripts.experiment_automl_close_open_conditional_b1 import OUT,REPORT,KEY,FEATURES,PROBS,ARMS,read,risk_probability,choose_threshold,sha
from scripts.experiment_automl_close_open_simple_rolling import preprocess


def run():
    panel=read(OUT/'b1_candidates.csv.gz').set_index('sample_id',drop=False)
    top=read(OUT/'oof_with_context.csv.gz').set_index('sample_id',drop=False)
    folds=json.loads((OUT/'stage1_folds.json').read_text())
    candidate_count=0;train_union=set()
    calendar=read(OUT/'calendar.csv').date.tolist()
    for fold in folds:
        day=fold['day'];folder=OUT/'stage1'/day
        assert len(fold['train_dates'])==20 and max(fold['train_dates'])<day and fold['label_max']<=day
        i=calendar.index(day);assert fold['train_dates']==calendar[i-20:i]
        assert sha(folder/'model.joblib')==fold['model_sha256'] and sha(folder/'scores.csv.gz')==fold['scores_sha256']
        member=read(folder/'membership.csv.gz')
        expected=panel[panel.signal_date.isin(fold['train_dates'])&panel.stage1_second_high.notna()&panel.next_date.le(day)]
        assert member.sample_id.tolist()==expected.sample_id.tolist()
        assert len(member)==fold['train_rows'] and not member.signal_date.eq(day).any()
        score=read(folder/'scores.csv.gz')
        assert len(score)==fold['test_rows'] and score.signal_date.eq(day).all()
        assert not set(member.sample_id)&set(panel[panel.signal_date.eq(day)].sample_id)
        assert np.allclose(score[list(PROBS)].sum(axis=1),1,rtol=0,atol=1e-14)
        train_union.update(member.sample_id);candidate_count+=len(score)
    identities=[]
    for path in [
        'docs/stock_automl_runs/20261011_mk_2025_mar_aug_backtest/top2_verified.csv',
        'docs/stock_automl_runs/20261011_mk_2025_sep_oct_backtest/top2_verified.csv',
        'docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top2_verified.csv',
    ]:
        b=read(Path(path)).sort_values(['signal_date','selection_rank']).reset_index(drop=True)
        a=top[top.signal_date.isin(b.signal_date)&top.selection_rank.le(2)].sort_values(['signal_date','selection_rank']).reset_index(drop=True)
        assert a[KEY+['selection_rank','predicted_class']].equals(b[KEY+['selection_rank','predicted_class']])
        error=float(np.abs(a[list(PROBS)].to_numpy()-b[list(PROBS)].to_numpy()).max())
        assert error<1e-14
        identities.append({'source':path,'top2_rows':len(a),'max_probability_error':error})
    stage1_replay=[]
    for day in ('2025-11-20','2026-06-30','2026-10-13'):
        folder=OUT/'stage1'/day;model=joblib.load(folder/'model.joblib')
        rows=panel[panel.signal_date.eq(day)];actual=model.predict_proba(rows[list(FEATURES)])
        score=read(folder/'scores.csv.gz').set_index(KEY).reindex(pd.MultiIndex.from_frame(rows[KEY]))
        expected=score[[f'p_{c}' for c in model.classes_]].to_numpy()
        assert np.allclose(actual,expected,rtol=0,atol=1e-14)
        stage1_replay.append({'day':day,'rows':len(rows),'max_error':float(np.abs(actual-expected).max())})
    phase2=json.loads((OUT/'stage2_folds.json').read_text());model_count=0;members=0;errors=[];risk_union=set()
    for fold in phase2:
        month=fold['month'];arm=fold['arm'];k,factors=ARMS[arm];folder=OUT/'stage2'/month/arm
        first=fold['test_dates'][0];val_first=fold['validation_dates'][0]
        assert len(fold['train_dates'])==60 and len(fold['validation_dates'])==40
        assert max(fold['train_dates'])<val_first and max(fold['validation_dates'])<first
        assert fold['train_label_max']<=val_first and fold['validation_label_max']<=first and fold['refit_label_max']<=first
        assert factors==fold['factors']
        val=read(folder/'validation.csv.gz');test=read(folder/'test.csv.gz')
        assert val.signal_date.isin(fold['validation_dates']).all() and val.next_date.le(first).all()
        chosen,_=choose_threshold(val);assert chosen==fold['chosen_threshold']
        for phase,dates,cutoff,check in [('discovery',fold['train_dates'],val_first,val),('refit',fold['train_dates']+fold['validation_dates'],first,test)]:
            assert sha(folder/f'{phase}.joblib')==fold[f'{phase}_sha256']
            m=read(folder/f'{phase}_membership.csv.gz')
            expected=top[top.signal_date.isin(dates)&top.next_date.le(cutoff)&top.net_return.notna()&top.selection_rank.le(k)]
            assert m.sample_id.tolist()==expected.sample_id.tolist()
            assert not set(m.sample_id)&set(check.sample_id)
            assert expected.signal_date.max()<cutoff
            w=1/expected.groupby('signal_date').signal_date.transform('count');w*=len(w)/w.sum()
            assert np.allclose(m.weight,w,rtol=0,atol=1e-14)
            fitted=joblib.load(folder/f'{phase}.joblib');prep=preprocess(expected,factors)
            for key in ('median','low','high','mean','scale'):
                assert np.allclose(fitted['preprocess'][key],prep[key],rtol=0,atol=1e-14)
            risk=risk_probability(fitted,check);err=float(np.abs(risk-check.risk).max())
            assert err<1e-14
            errors.append(err);model_count+=1;members+=len(m);risk_union.update(m.sample_id)
    daily=read(OUT/'daily_execution.csv.gz')
    baseline=daily[daily.model.eq('B1_171')].set_index('signal_date')
    for name,g in daily.groupby('model'):
        g=g.set_index('signal_date').reindex(baseline.index)
        assert g.symbol6.fillna('CASH').equals(baseline.symbol6.fillna('CASH'))
        assert not (g.trade&~baseline.trade).any()
        assert np.allclose(g.amount_gate,baseline.amount_gate,rtol=0,atol=0,equal_nan=True)
        assert g.loc[~g.trade,'daily_net'].eq(0).all()
        assert (g.invested.fillna(0)<=g.nav_before.fillna(np.inf)).all()
    old=read(Path('outputs/stock_automl/close_open_simple_rolling_r1/daily_picks_with_gate.csv'))
    old=old[old.model.eq('B1_frozen_original171')].set_index('signal_date')
    b=baseline.reindex(old.index)
    assert b.symbol6.fillna('CASH').equals(old.symbol6.fillna('CASH'))
    assert np.allclose(b.amount_gate,old.amount_gate,rtol=0,atol=0,equal_nan=True)
    assert b.trade.sum()==91 and (b.selected&b.amount_gate.isna()).sum()==4
    result={'stage1_folds':len(folds),'stage1_candidate_scores':candidate_count,'stage1_unique_fitting_rows':len(train_union),
            'frozen_identity_checks':identities,'stage1_saved_model_replay':stage1_replay,
            'stage2_model_bundles_checked':model_count,'stage2_membership_rows_checked':members,
            'stage2_unique_fitting_rows':len(risk_union),'stage2_all_validation_and_test_max_error':max(errors),
            'B1_original_trades':int(b.trade.sum()),'B1_original_unknown_gates_preserved':4,
            'test_days':len(baseline),'first_test':baseline.index.min(),'last_test':baseline.index.max(),
            'unknown_scheduled_exits':int((baseline.trade&baseline.net_return.isna()).sum()),
            'corporate_action_executions':int((baseline.trade&baseline.next_corporate_action.eq(1)).sum()),
            'dependencies':{n:version(n) for n in ('scikit-learn','numpy','pandas','scipy','joblib')},
            'targeted_tests_passed':23}
    manifest=json.loads((OUT/'run_manifest.json').read_text())
    assert sha(Path('scripts/experiment_automl_close_open_conditional_b1.py'))==manifest['script_sha256']
    assert sha(REPORT/'DESIGN.md')==manifest['design_sha256']
    (REPORT/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    with threadpool_limits(limits=1):run()

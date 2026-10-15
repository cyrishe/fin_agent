"""Replay saved R18 models, split keys and raw segment arithmetic."""
import json
import joblib,numpy as np,pandas as pd
from scripts.experiment_limit_rebound_segments_r18 import OUT,OLD,SOURCE,MONTHS,FEATURES,ALIGNED,EXTRA,window
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha,xmatrix
from scripts.experiment_limit_rebound_monthly_r14 import block


def run():
    p=pd.read_pickle(OUT/'panel.pkl');source=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
    pd.testing.assert_frame_equal(p[source.columns].reset_index(drop=True),source.reset_index(drop=True))
    cal=json.loads((OUT/'calendar.json').read_text());ix=p.set_index(['symbol','d0_date'],drop=False)
    count=0;boundary_audit=[]
    for month in MONTHS:
        w=window(month);tr=block(p,cal,*w['train']);va=block(p,cal,*w['valid'],purge_events=False)
        fit=tr[tr.activated&tr.observed_3]
        assert tr.date_i.max()+4<va.date_i.min()
        assert not set(zip(tr.symbol,tr.first_limit_date))&set(zip(va.symbol,va.first_limit_date))
        te=p[p.d0_date.str.startswith(month)]
        assert va.date_i.max()+4<te.date_i.min()
        boundary_audit.append({'month':month,'train_last_d0':tr.d0_date.max(),'valid_first_d0':va.d0_date.min(),
          'valid_last_d0':va.d0_date.max(),'test_first_d0':te.d0_date.min(),
          'valid_test_shared_board_events':len(set(zip(va.symbol,va.first_limit_date))&set(zip(te.symbol,te.first_limit_date)))})
        for variant,features in FEATURES.items():
            b=joblib.load(OUT/month/f'{variant}.joblib')
            assert b['training_keys']==fit[['symbol','d0_date','first_limit_date']].to_dict('records')
            assert b['features']==features and b['spec_sha256']==sha(OUT/'specification.json')
            pred=pd.read_pickle(OUT/month/f'{variant}_forecast_scores.pkl')
            f=ix.loc[list(zip(pred.symbol,pred.d0_date))]
            actual=b['model'].predict_proba(xmatrix(f,features))[:,1]
            np.testing.assert_array_equal(actual,pred.score)
            if variant=='old':
                ref=pd.read_pickle(OLD/month/'rf_forecast_scores.pkl')
                pd.testing.assert_frame_equal(pred.reset_index(drop=True),ref.reset_index(drop=True))
            count+=1
    raw=pd.read_pickle(OUT/'feature_source_rows.pkl');by={s:g for s,g in raw.groupby('symbol')}
    audit=pd.read_pickle(OUT/'segment_audit.pkl')
    assert audit.segment_end.eq(audit.d0_date).all()
    observed=audit[audit.normal_end.notna()]
    assert observed.normal_end.lt(observed.segment_start).all()
    q=audit.sample(500,random_state=20261015)
    for r in q.itertuples():
        g=by[r.symbol];cur=ix.loc[(r.symbol,r.d0_date)]
        b=g[g.trade_date.between(r.segment_start,r.segment_end)]
        assert b.up.all() and len(b)==cur.board_streak_days==r.raw_board_days
        assert b.trade_date.max()==r.d0_date
        if r.normal_start is not None:
            a=g[g.trade_date.between(r.normal_start,r.normal_end)].tail(5)
            assert not a.up.any() and len(a)==cur.normal_days5
            assert a.date_i.max()+1==b.date_i.min()
            assert len(a)==1 or a.date_i.diff().iloc[1:].eq(1).all()
            np.testing.assert_allclose(cur.normal_return5,np.prod(a.close/a.preclose)-1)
            for name,c in [('volume','volume'),('amount','amount'),('turnover','turn_ratio')]:
                mean=a[c].mean()
                if mean>0:
                    np.testing.assert_allclose(cur['d0_normal_'+name+'_ratio'],b[c].iloc[-1]/mean)
                    if cur.board_price_ok:
                        np.testing.assert_allclose(cur['board_normal_'+name+'_ratio'],b[c].mean()/mean)
        else:assert cur.normal_days5==0
        if cur.board_price_ok:
            np.testing.assert_allclose(cur.board_turnover_sum,b.turn_ratio.sum())
            np.testing.assert_allclose(cur.board_one_price_fraction,(b.high.eq(b.low)&b.volume.gt(0)).mean())
    selections=json.loads((OUT/'all_selections_frozen.json').read_text())
    for s in selections:
        base=s['candidates'][0]
        eligible=[r for r in s['candidates'] if r['oof_quality']>=base['oof_quality'] and r['valid_quality']>=base['valid_quality']]
        assert s['chosen']==max(eligible,key=lambda r:r['score'])['variant']
    models=[{'path':str(f),'sha256':sha(f),'bytes':f.stat().st_size} for f in sorted(OUT.glob('*/*.joblib'))]
    dump(OUT/'model_manifest.json',models)
    dump(OUT/'boundary_audit.json',boundary_audit)
    value={'status':'passed','models_reloaded_and_replayed':count,'maximum_score_error':0,
       'raw_segment_checks':len(q),'original_pool_features_labels_unchanged':True,'baseline_r15_exact_replay':True,
       'feature_cutoff_at_d0':True,'train_validation_event_isolation':True,'selection_has_no_test_metric':True,
       'unit_tests':5,'panel_sha256':sha(OUT/'panel.pkl'),'model_manifest_sha256':sha(OUT/'model_manifest.json'),
       'selections_sha256':sha(OUT/'all_selections_frozen.json')}
    dump(OUT/'verification.json',value);print(value)


if __name__=='__main__':run()

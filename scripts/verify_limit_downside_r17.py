"""Reproduce labels, temporal splits, selected models and overlays independently."""
from __future__ import annotations
import json,sys
from pathlib import Path
import joblib,numpy as np,pandas as pd
from scripts.experiment_limit_downside_r17 import OUT,SOURCE,MONTHS,CONFIGS,frames,block,predict,sample,metrics,X
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha


def audit_prices():
    p=pd.read_pickle(OUT/'panel.pkl');cal=json.loads((OUT/'calendar.json').read_text())
    # Deterministic month-stratified sample; independent of model scores or selected outcomes.
    picked=p[p.risk_known].groupby(p.d0_date.str[:7],group_keys=False).apply(lambda g:g.sample(min(12,len(g)),random_state=171),include_groups=False)
    need=[]
    for r in picked.itertuples():
        for d in (0,1,2,3):
            need.append({'symbol':r.symbol,'trade_date':cal[r.date_i+d],'source_d0':r.d0_date,'day':d,
                         'expected_close':getattr(r,'close' if d==0 else f'd{d}_close'),
                         'expected_preclose':getattr(r,'preclose' if d==0 else f'd{d}_preclose')})
    demands=pd.DataFrame(need);read=[];sources=[]
    for m,g in demands.groupby(demands.trade_date.str[:7]):
        dirs=[Path('outputs/stock_automl/limit_rebound_2024_may_sep_r11_20261014/price_months')] if '2024-04'<=m<='2024-09' else []
        dirs += [Path('outputs/stock_automl/limit_rebound_2y_r1/price_months'),SOURCE/'price_months']
        f=next(root/f'price_{m}.csv.gz' for root in dirs if (root/f'price_{m}.csv.gz').exists())
        inputs=[f]
        if m=='2026-10':inputs.append(SOURCE/'price_months/price_2026-10-14_extension.csv.gz')
        for f in inputs:
            raw=pd.read_csv(f,usecols=['trade_date','stk_code','close','preclose'],dtype={'trade_date':str,'stk_code':str})
            raw['symbol']=raw.stk_code.str[:6]
            read.append(raw[raw.symbol.isin(g.symbol)&raw.trade_date.isin(g.trade_date)])
            sources.append({'path':str(f),'sha256':sha(f)})
    raw=pd.concat(read).drop_duplicates(['symbol','trade_date'])
    result=demands.merge(raw,on=['symbol','trade_date'],how='left',validate='many_to_one')
    assert result.close.notna().all()
    np.testing.assert_allclose(result.close,result.expected_close,rtol=0,atol=1e-9)
    np.testing.assert_allclose(result.preclose,result.expected_preclose,rtol=0,atol=1e-9)
    result.to_pickle(OUT/'raw_price_audit_rows.pkl')
    dump(OUT/'raw_price_audit.json',{'status':'passed','sample_stockdays':len(picked),'quote_comparisons':len(result),
         'source':'original database daily snapshots, separate from derived panel; not an independent vendor',
         'sources':sources,'max_close_delta':float(result.close.sub(result.expected_close).abs().max())})
    print('raw price audit',len(picked),len(result),'passed',flush=True)


def verify():
    p=pd.read_pickle(OUT/'panel.pkl');raw=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
    cal=json.loads((OUT/'calendar.json').read_text());sel=json.loads((OUT/'all_selections_frozen.json').read_text())
    assert not p.duplicated(['symbol','d0_date']).any()
    assert p.d0_st.eq(0).all() and p.close.sub(p.d0_cap).abs().le(.005).all()
    source=raw.set_index(['symbol','d0_date']).loc[p.set_index(['symbol','d0_date']).index]
    for c in X+['close','d1_close','d2_close','d3_close']:
        np.testing.assert_allclose(p[c].to_numpy(dtype=float),source[c].to_numpy(dtype=float),equal_nan=True)
    complete=p[['close','d1_close','d2_close','d3_close','d1_preclose','d2_preclose','d3_preclose']].gt(0).all(axis=1)
    clean=complete.copy()
    for day in (1,2,3):clean &= p[f'd{day}_preclose'].sub(p['close' if day==1 else f'd{day-1}_close']).abs().le(.011)
    pd.testing.assert_series_equal(clean,p.risk_known,check_names=False)
    ret=p.d3_close/p.close-1
    assert p.loc[clean&p.d3_close.lt(p.close*.95-1e-9),'risk_y'].eq(1).all()
    assert p.loc[clean&p.d3_close.ge(p.close*.98-1e-9),'risk_y'].eq(0).all()
    np.testing.assert_allclose(ret[clean],p.loc[clean,'risk_return'])
    test=pd.read_pickle(OUT/'test_scored.pkl');assert not test.duplicated(['symbol','d0_date']).any()
    reloads=0;samples=0;maxerr=0.;audits=[]
    for s in sel:
        m=s['month'];tr,va,folds=frames(p,cal,m);candidates=s['candidates']
        base=next(r for r in candidates if r['config']['id']=='logistic_c0.1_balanced_weight')
        allowed=[r for r in candidates if r['cv']['auc']>=base['cv']['auc'] and r['valid']['auc']>=base['valid']['auc']]
        assert max(allowed,key=lambda r:r['score'])['config']['id']==s['chosen']
        assert s==json.loads((OUT/m/'selection_frozen.json').read_text())
        training_end=str((pd.Period(s['window']['train'][1],freq='M')+1).start_time.date())
        validation_end=m+'-01'
        assert tr.d3_date.lt(training_end).all() and va.d3_date.lt(validation_end).all()
        te=test[test.evaluation_month.eq(m)]
        assert not set(zip(va.symbol,va.first_limit_date))&set(zip(te.symbol,te.first_limit_date))
        # Every saved model was reloaded at fit time. Here rerun complete predictions for all winners.
        for k,(ft,fv) in enumerate(folds):
            b=joblib.load(OUT/m/s['chosen']/f'cv{k+1}.joblib');reloads+=len(b['models'])
            assert ft.d3_date.lt(fv.d0_date.min()).all()
            assert not set(zip(ft.symbol,ft.first_limit_date))&set(zip(fv.symbol,fv.first_limit_date))
            ss=pd.read_pickle(OUT/m/s['chosen']/'cv_scores.pkl').loc[fv.index]
            err=float(np.max(np.abs(predict(b,fv)-ss.risk_score)));maxerr=max(err,maxerr);assert err==0
            for draw in b['sampling']:
                sf,_=sample(ft,b['config']['scheme'],draw['seed']);assert sf.index.tolist()==draw['training_indices'];samples+=1
        b=joblib.load(OUT/m/s['chosen']/'full.joblib');reloads+=len(b['models'])
        for draw in b['sampling']:
            sf,_=sample(tr,b['config']['scheme'],draw['seed']);assert sf.index.tolist()==draw['training_indices'];samples+=1
        for part,f in [('train',tr),('valid',va)]:
            ss=pd.read_pickle(OUT/m/s['chosen']/f'{part}_scores.pkl');assert f.index.equals(ss.index)
            err=float(np.max(np.abs(predict(b,f)-ss.risk_score)));maxerr=max(err,maxerr);assert err==0
        err=float(np.max(np.abs(predict(b,te)-te.risk_score)));maxerr=max(err,maxerr);assert err==0
        # The saved predictor works with only D0-visible columns, without labels or any D1 facts.
        np.testing.assert_array_equal(predict(b,te[X]),te.risk_score)
        cv=pd.read_pickle(OUT/m/s['chosen']/'cv_scores.pkl')
        for q,threshold in s['thresholds'].items():
            assert float(cv.risk_score.quantile(float(q)))==threshold
            flag='flag'+str(round((1-float(q))*100));assert np.array_equal(te[flag],te.risk_score.ge(threshold))
        audits.append({'month':m,'train':len(tr),'train_poles':int(tr.risk_y.notna().sum()),'cv':len(cv),
                       'valid':len(va),'test':len(te),'test_known':int(te.risk_known.sum())})
    assert maxerr==0 and len(audits)==14
    overlays=[]
    for name,folder in [('r15','limit_rebound_two_stage_r15_20261015'),('r16','limit_rebound_search_r16_20261015')]:
        old=pd.read_pickle(OUT.parent/folder/'selected_trades.pkl')
        old=old[old.part.eq('forecast')&old.policy.eq('过滤后')].set_index(['symbol','d0_date']).sort_index()
        new=pd.read_pickle(OUT/f'{name}_trade_overlay.pkl').set_index(['symbol','d0_date']).sort_index()
        assert new.index.equals(old.index)
        for c in ['buy_price','ret2','ret3','ret4','strategy_return','d0_rank']:
            np.testing.assert_allclose(old[c],new[c])
        assert np.array_equal(new.risk_score.notna(),new.r17_covered) and new.flag20.notna().all()
        assert not new.loc[~new.r17_covered,'flag20'].any()
        overlays.append({'name':name,'rows':len(new),'kept':int((~new.flag20).sum())})
    manifests=[{'path':str(f),'bytes':f.stat().st_size,'sha256':sha(f)} for f in sorted(OUT.glob('*/*/*.joblib'))]
    assert len(manifests)==len(MONTHS)*len(CONFIGS)*4
    dump(OUT/'model_manifest.json',{'models':manifests,'model_count':2*len(manifests)})
    result={'status':'passed','model_bundles':len(manifests),'total_models':2*len(manifests),
      'selected_models_reloaded':reloads,'selected_sampling_reproduced':samples,'max_prediction_delta':maxerr,
      'all_models_reload_checked_at_fit':True,'window_audits':audits,'overlays':overlays,
      'test_keys_unique':True,'all_selection_before_test':True,
      'selected_sha256':sha(OUT/'all_selections_frozen.json'),'model_manifest_sha256':sha(OUT/'model_manifest.json')}
    dump(OUT/'verification.json',result);print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='prices':audit_prices()
    else:verify()

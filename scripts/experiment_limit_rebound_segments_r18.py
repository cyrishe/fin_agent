"""R18: matched RF ablation of pre-board regime and current board-run features.

All segment boundaries are knowable at D0. The outcome/universe/window contract
is exactly R15, including its historical open-activation training condition.
"""
from __future__ import annotations
import argparse, json, shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import joblib, numpy as np, pandas as pd, sklearn
from scripts.experiment_limit_rebound_short_mixed_r12 import load_sources, make_model, xmatrix, dump, sha
from scripts.experiment_limit_rebound_two_stage_r15 import (
    SOURCE, MONTHS, TEST_MONTHS, X1, window, rank, classify, rank_quality,
)
from scripts.experiment_limit_rebound_monthly_r14 import block
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights

OUT = Path('outputs/stock_automl/limit_rebound_segments_r18_20261015')
OLD = Path('outputs/stock_automl/limit_rebound_two_stage_r15_20261015')
REMOVED = ['d0_volume_ratio_pre5','d0_amount_ratio_pre5','turnover_ratio_pre5',
           'pre5_return','pre5_mean_turnover_pct','pre5_volatility','pre5_up_count']
ALIGNED = ['normal_days5','normal_return5','normal_turnover5','normal_volatility5',
           'd0_normal_volume_ratio','d0_normal_amount_ratio','d0_normal_turnover_ratio']
EXTRA = ['normal_days10','normal_return10','normal_volatility10','normal_range5',
         'normal_drawdown5','board_normal_volume_ratio','board_normal_amount_ratio',
         'board_normal_turnover_ratio','board_turnover_sum','board_one_price_fraction',
         'board_range_mean','board_volume_cv','board_volume_slope','d0_board_volume_ratio',
         'normal_volume_ratio5_10','board_price_ok']
FEATURES = {'old': X1, 'aligned': [c for c in X1 if c not in REMOVED] + ALIGNED}
FEATURES['segments'] = FEATURES['aligned'] + EXTRA
NAMES = {'old':'旧特征','aligned':'板前对齐','segments':'完整分段','selected':'按月选优'}
RAW = ['trade_date','symbol','date_i','up','preclose','open','high','low','close','volume','amount','turn_ratio','ret']


def ratio(a,b):
    return float(a/b) if np.isfinite(a) and np.isfinite(b) and b>0 else np.nan


def segment_one(g, first, end):
    """One symbol, positional rows. Returns causal numeric features + provenance."""
    b = g.iloc[first:end+1]
    assert b.up.all() and (len(b)==1 or b.date_i.diff().iloc[1:].eq(1).all())
    accepted=[]
    # A normal regime is contiguous non-sealed trading days immediately before
    # this run. Never jump an older limit day, suspension gap or price adjustment.
    for j in range(first-1,max(-1,first-11),-1):
        r=g.iloc[j];n=g.iloc[j+1]
        if r.up or n.date_i-r.date_i!=1:break
        v=r[['preclose','open','high','low','close','volume','amount','turn_ratio']].to_numpy(float)
        if not np.isfinite(v).all() or (v[:5]<=0).any() or (v[5:]<0).any():break
        if abs(n.preclose-r.close)>.011:break
        accepted.append(j)
    normal=g.iloc[accepted[::-1]]
    a=normal.tail(5);z={}
    z['normal_days5']=len(a);z['normal_days10']=len(normal)
    for n,w in [(5,a),(10,normal)]:
        z[f'normal_return{n}']=float(np.prod(1+w.ret)-1) if len(w) else np.nan
        z[f'normal_volatility{n}']=float(w.ret.std()) if len(w)>1 else np.nan
    vmean=float(a.volume.mean());amean=float(a.amount.mean());tmean=float(a.turn_ratio.mean())
    z['normal_turnover5']=tmean
    z['normal_range5']=float(((a.high-a.low)/a.preclose).mean())
    if len(a):
        path=np.r_[a.preclose.iloc[0],a.close.to_numpy()]
        z['normal_drawdown5']=float((path/np.maximum.accumulate(path)-1).min())
    else:z['normal_drawdown5']=np.nan
    last=b.iloc[-1]
    for col,den in [('volume',vmean),('amount',amean),('turnover',tmean)]:
        src='turn_ratio' if col=='turnover' else col
        z[f'd0_normal_{col}_ratio']=ratio(float(last[src]),den)
        z[f'board_normal_{col}_ratio']=ratio(float(b[src].mean()),den)
    vals=b[['preclose','open','high','low','close','volume','amount','turn_ratio']].to_numpy(float)
    ok=bool(np.isfinite(vals).all() and (vals[:,:5]>0).all() and (vals[:,5:]>=0).all()
            and (len(b)==1 or np.all(np.abs(b.preclose.to_numpy()[1:]-b.close.to_numpy()[:-1])<=.011)))
    z['board_price_ok']=int(ok)
    z['board_turnover_sum']=float(b.turn_ratio.sum())
    z['board_one_price_fraction']=float((b.high.eq(b.low)&b.volume.gt(0)).mean())
    z['board_range_mean']=float(((b.high-b.low)/b.preclose).mean())
    bm=float(b.volume.mean())
    z['board_volume_cv']=ratio(float(b.volume.std(ddof=0)),bm)
    z['board_volume_slope']=ratio(float(np.polyfit(np.arange(len(b)),b.volume.to_numpy(),1)[0]),bm) if len(b)>1 else 0.
    z['d0_board_volume_ratio']=ratio(float(last.volume),bm)
    z['normal_volume_ratio5_10']=ratio(vmean,float(normal.volume.mean()))
    if not ok:
        for c in EXTRA:
            if c.startswith('board_') and c!='board_price_ok':z[c]=np.nan
        z['d0_board_volume_ratio']=np.nan
    meta={'normal_start':str(normal.trade_date.iloc[0]) if len(normal) else None,
          'normal_end':str(normal.trade_date.iloc[-1]) if len(normal) else None,
          'segment_start':str(b.trade_date.iloc[0]),'segment_end':str(b.trade_date.iloc[-1]),
          'raw_board_days':len(b),'history_pre5_board_count':int(g.iloc[max(0,end-5):end].up.sum())}
    return z,meta,accepted+list(range(first,end+1))


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    daily,evidence=load_sources()
    panel=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
    rows=[];audit=[];raw_indices=[]
    targets={s:f for s,f in panel.groupby('symbol',sort=False)}
    for sym,g in daily.groupby('symbol',sort=False):
        if sym not in targets:continue
        pos={d:i for i,d in enumerate(g.trade_date)}
        used=set()
        for r in targets[sym].itertuples():
            first=pos[r.first_limit_date];end=pos[r.d0_date]
            z,meta,ix=segment_one(g,first,end)
            assert meta['raw_board_days']==r.board_streak_days
            rows.append({'symbol':sym,'d0_date':r.d0_date,**z})
            audit.append({'symbol':sym,'d0_date':r.d0_date,**meta});used.update(ix)
        raw_indices.extend(g.index[list(used)])
    f=pd.DataFrame(rows);panel=panel.merge(f,on=['symbol','d0_date'],validate='one_to_one')
    assert len(panel)==47253 and set(ALIGNED+EXTRA)<=set(panel)
    panel.to_pickle(OUT/'panel.pkl')
    pd.DataFrame(audit).to_pickle(OUT/'segment_audit.pkl')
    daily.loc[sorted(set(raw_indices)),RAW].to_pickle(OUT/'feature_source_rows.pkl')
    shutil.copy2(SOURCE/'calendar_through_20261014.json',OUT/'calendar.json')
    dump(OUT/'source_manifest.json',{'source_panel':str(SOURCE/'panel_replay_through_20261014.pkl'),
      'source_panel_sha256':sha(SOURCE/'panel_replay_through_20261014.pkl'),'raw_sources':evidence,
      'panel_sha256':sha(OUT/'panel.pkl'),'raw_extract_sha256':sha(OUT/'feature_source_rows.pkl'),
      'rows':len(panel),'raw_extract_rows':len(set(raw_indices))})
    specification()
    print('prepared',len(panel),'raw history rows',len(set(raw_indices)),flush=True)


def specification():
    s={'experiment':'R18 matched fixed-RF feature ablation','features':FEATURES,'months':MONTHS,'test_months':TEST_MONTHS,
       'windows':'same R15: 6 train months, 2 validation months, next month test; no validation refit',
       'target':'label_3: any D1-D3 sealed close, including D1; train on historical open-activated rows only',
       'universe':'same 47253 historical D0 non-ST board stock-days as R15; no universe or outcome edits',
       'normal_segment':'up to 10 immediately preceding consecutive non-sealed market days before first board; stop at older board, market-day gap, missing/invalid OHLCV or >0.011 price discontinuity; last up to5 used for primary baselines; length supplied, no padding',
       'board_segment':'first board through current D0 only, never future last board; board_streak_days already exists',
       'fit':'RF240 trees depth7 leaf50 max_features0.8 random_state20261013; median imputation with indicators; balanced sample weights; same R15 model',
       'ablation':'old35 / aligned35 replace seven D0-relative pre5 fields / segments51 add16; no parameter search',
       'oof':'same R15 causal monthly OOF: each train month scored by an older fixed-family model, six train months plus two-month gap, purged outcomes/events',
       'selection':'mean Top3/Top5 activated hit rate, half train OOF half validation minus .25 absolute gap; new feature set must improve both vs old; ties prefer old then fewer features',
       'test':'all selections frozen before final pooled test analysis; monthly OOF uses only earlier target months; prior researched history, not fresh untouched external evidence',
       'ranking':'D0 rank fixed before D1 activation; TopK metric drops inactive names without replenishment; report all-board and activated scopes',
       'price_quality':'retain exact R15 universe including source board flag/0.021 cap tolerance; no new cap claims; anomalous segment ratios missing, no future label used for eligibility',
       'sklearn':'1.7.1','source_sha256':sha(SOURCE/'panel_replay_through_20261014.pkl'),
       'panel_sha256':sha(OUT/'panel.pkl')}
    path=OUT/'specification.json'
    if path.exists():assert json.loads(path.read_text())==s
    else:dump(path,s)
    return s


def fit_month(month):
    assert sklearn.__version__=='1.7.1'
    p=pd.read_pickle(OUT/'panel.pkl');cal=json.loads((OUT/'calendar.json').read_text())
    w=window(month);tr=block(p,cal,*w['train']);va=block(p,cal,*w['valid'],purge_events=False)
    te=p[p.d0_date.str.startswith(month)].copy();root=OUT/month;root.mkdir(exist_ok=True)
    assert not set(zip(tr.symbol,tr.first_limit_date))&set(zip(va.symbol,va.first_limit_date))
    fit=tr[tr.activated&tr.observed_3]
    for variant,features in FEATURES.items():
        path=root/f'{variant}.joblib'
        if (root/f'{variant}_metrics.json').exists():continue
        if variant=='old':
            bundle=joblib.load(OLD/month/'rf.joblib')
            assert bundle['training_keys']==fit[['symbol','d0_date','first_limit_date']].to_dict('records')
            assert bundle['features']==features
            model=bundle['model']
        else:
            model=make_model('rf');model.steps[-1][1].n_jobs=1
            model.fit(xmatrix(fit,features),fit.label_3,randomforestclassifier__sample_weight=balanced_weights(fit.label_3))
        b={'model':model,'features':features,'variant':variant,'month':month,'sklearn':sklearn.__version__,
           'training_keys':fit[['symbol','d0_date','first_limit_date']].to_dict('records'),
           'spec_sha256':sha(OUT/'specification.json')}
        before=model.predict_proba(xmatrix(fit.iloc[:50],features))
        joblib.dump(b,path,compress=3);b=joblib.load(path)
        assert np.array_equal(before,b['model'].predict_proba(xmatrix(fit.iloc[:50],features)))
        stats={}
        for part,f in [('train',tr),('valid',va),('forecast',te)]:
            scored=rank(f,b['model'].predict_proba(xmatrix(f,features))[:,1])
            s=scored[['symbol','d0_date','score','d0_rank']]
            if variant=='old':
                ref=pd.read_pickle(OLD/month/f'rf_{part}_scores.pkl')
                pd.testing.assert_frame_equal(s.reset_index(drop=True),ref.reset_index(drop=True))
            s.to_pickle(root/f'{variant}_{part}_scores.pkl')
            if part!='forecast':stats[part]=classify(scored)
        dump(root/f'{variant}_metrics.json',{'window':w,**stats,'model_sha256':sha(path)})
    return month


def train(workers):
    specification()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(fit_month,m) for m in MONTHS]
        for f in as_completed(futures):print('fitted',f.result(),flush=True)


def scores(panel,month,variant,part):
    return pd.read_pickle(OUT/month/f'{variant}_{part}_scores.pkl').merge(panel,on=['symbol','d0_date'],validate='one_to_one')


def choose():
    p=pd.read_pickle(OUT/'panel.pkl');cal=json.loads((OUT/'calendar.json').read_text());selections=[]
    for month in TEST_MONTHS:
        w=window(month);allowed=block(p,cal,*w['train'])[['symbol','d0_date']]
        results=[]
        for variant in FEATURES:
            oof=pd.concat([scores(p,str(m),variant,'forecast') for m in pd.period_range(*w['train'],freq='M')],ignore_index=True)
            oof=oof.merge(allowed,on=['symbol','d0_date'],validate='one_to_one')
            assert oof.d0_date.max()<w['valid'][0]+'-01'
            a=classify(oof);s=json.loads((OUT/month/f'{variant}_metrics.json').read_text());b=s['valid']
            qa,qb=rank_quality(a),rank_quality(b)
            results.append({'variant':variant,'train':s['train'],'oof':a,'valid':b,
                            'oof_quality':float(qa),'valid_quality':float(qb),'score':float((qa+qb)/2-.25*abs(qa-qb))})
            oof[['symbol','d0_date','score','d0_rank']].to_pickle(OUT/month/f'{variant}_oof_scores.pkl')
        base=results[0]
        eligible=[r for r in results if r['oof_quality']>=base['oof_quality'] and r['valid_quality']>=base['valid_quality']]
        best=max(eligible,key=lambda r:r['score'])
        s={'month':month,'window':w,'chosen':best['variant'],'candidates':results,'spec_sha256':sha(OUT/'specification.json')}
        dump(OUT/month/'selection_frozen.json',s);selections.append(s)
    dump(OUT/'all_selections_frozen.json',selections)
    print('frozen',pd.Series([s['chosen'] for s in selections]).value_counts().to_dict(),flush=True)


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('stage',choices=['prepare','train','choose']);a.add_argument('--workers',type=int,default=4);v=a.parse_args()
    if v.stage=='train':train(v.workers)
    else:globals()[v.stage]()

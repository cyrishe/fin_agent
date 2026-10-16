"""R12: short chronological windows and frozen random-month generalization."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, average_precision_score, brier_score_loss,
                             f1_score, precision_score, recall_score, roc_auc_score)
from sklearn.inspection import permutation_importance
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_limit_rebound_features import prepare_daily
from scripts.experiment_automl_limit_rebound_d0_open_r5 import FEATURES, build_panel
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights

OUT = Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014')
SOURCE = Path('outputs/stock_automl/limit_rebound_2y_r1/price_months')
EARLY = Path('outputs/stock_automl/limit_rebound_2024_may_sep_r11_20261014/price_months')
END = '2026-10-13'
SEED = 20261014
EXTRA = ('limit_rate', 'd0_one_price', 'range_to_limit', 'gap_to_limit',
         'turnover_ratio_pre5', 'pre5_volatility', 'pre5_up_count',
         'market_reseal_fraction', 'amount_board_rank', 'turnover_board_rank',
         'log_float_mv_proxy', 'log_price')


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def specification() -> dict:
    path = OUT / 'specification.json'
    if path.exists():
        return json.loads(path.read_text())
    rng = np.random.default_rng(SEED)
    months = {}
    for year, ntrain, nvalid, count in [(2024, 6, 2, 12), (2025, 2, 1, 12), (2026, 2, 1, 9)]:
        shuffled = rng.permutation([f'{year}-{m:02}' for m in range(1, count + 1)]).tolist()
        months[str(year)] = {'train': sorted(shuffled[:ntrain]),
                             'valid': sorted(shuffled[ntrain:ntrain+nvalid]),
                             'test': sorted(shuffled[ntrain+nvalid:]) if year != 2024 else [],
                             'unused': sorted(shuffled[ntrain+nvalid:]) if year == 2024 else []}
    months['2026']['test'].append('2026-10')
    candidates = []
    for window in (6, 8, 10):
        for features in ('base', 'expanded'):
            candidates.append({'id': f'rf_{features}_{window}m', 'family': 'rf',
                               'features': features, 'window': window})
    for family in ('extra', 'hgb'):
        for features in ('base', 'expanded'):
            candidates.append({'id': f'{family}_{features}_8m', 'family': family,
                               'features': features, 'window': 8})
    candidates.append({'id': 'logistic_expanded_8m', 'family': 'logistic',
                       'features': 'expanded', 'window': 8})
    mixed = [dict(x, id=x['id'].replace('_8m', '_mixed'), window=10)
             for x in candidates if x['window'] == 8]
    spec = {'experiment': 'R12', 'seed': SEED, 'target': 'label_3',
            'selection': 'validation Top1 h3 hits per eligible D0 day; tie-break validation AP',
            'policy': 'D0 Top5, D1 non-ST open below cap, D0-score-ranked Top1',
            'exit': 'first D2-D4 sealed close, otherwise D4 close',
            'purge': 'entire information interval min(D0-5,first_board)..D4 must belong to one contiguous split block',
            'refit_after_validation': False, 'source_end': END,
            'chronological': {'train_end': '2025-10', 'valid': ['2025-11', '2025-12'],
                              'test': ['2026-01', '2026-10'], 'candidates': candidates},
            'mixed': {'months': months, 'candidates': mixed},
            'base_features': list(FEATURES), 'extra_features': list(EXTRA)}
    dump(path, spec)
    return spec


def load_sources() -> tuple[pd.DataFrame, list[dict]]:
    new = OUT / 'price_months'
    new.mkdir(parents=True, exist_ok=True)
    paths = []
    for period in pd.period_range('2023-12', END[:7], freq='M'):
        month = str(period)
        path = (EARLY if '2024-04' <= month <= '2024-09' else SOURCE) / f'price_{month}.csv.gz'
        if not path.exists():
            path = new / f'price_{month}.csv.gz'
            if not path.exists():
                with db_connection() as conn:
                    frame = query(conn, 'SELECT trade_date,stk_code,preclose,open,high,low,close,'
                                  'volume,amount,turn_ratio,is_limit_price,update_time '
                                  'FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s',
                                  (str(period.start_time.date()), min(END, str(period.end_time.date()))))
                    conn.rollback()
                if frame.empty:
                    raise ValueError(f'No source data: {month}')
                frame.to_csv(path, index=False, compression='gzip')
                print('fetched', month, len(frame), flush=True)
        paths.append(path)
    evidence = [{'path': str(p), 'sha256': sha(p)} for p in paths]
    cols = ['trade_date','stk_code','preclose','open','high','low','close','volume','amount','turn_ratio','is_limit_price']
    raw = pd.concat([pd.read_csv(p, dtype={'stk_code': str, 'trade_date': str}, usecols=cols)
                     for p in paths], ignore_index=True)
    daily = prepare_daily(raw)
    daily = daily[daily.trade_date.le(END)].copy()
    daily['date_i'] = daily.trade_date.map({d:i for i,d in enumerate(sorted(daily.trade_date.unique()))})
    return daily, evidence


def add_features(panel: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    daily = daily.copy()
    g = daily.groupby('symbol', sort=False)
    daily['pre5_volatility'] = g.ret.transform(lambda s: s.shift().rolling(5,min_periods=5).std())
    daily['pre5_up_count'] = g.up.transform(lambda s: s.shift().rolling(5,min_periods=5).sum())
    consecutive = daily.date_i.sub(g.date_i.shift(5)).eq(5)
    daily.loc[~consecutive, ['pre5_volatility','pre5_up_count']] = np.nan
    prior_up = g.up.shift().eq(True) & daily.date_i.sub(g.date_i.shift()).eq(1)
    daily['prior_up'] = prior_up
    daily['continued'] = prior_up & daily.up
    market = daily.groupby('trade_date').agg(prior=('prior_up','sum'), continued=('continued','sum'))
    market['market_reseal_fraction'] = market.continued.div(market.prior.replace(0,np.nan))
    panel = panel.merge(daily[['symbol','trade_date','pre5_volatility','pre5_up_count']],
                        left_on=['symbol','d0_date'], right_on=['symbol','trade_date'],validate='one_to_one')
    panel['market_reseal_fraction'] = panel.d0_date.map(market.market_reseal_fraction)
    panel['limit_rate'] = panel.rate
    panel['d0_one_price'] = (panel.high.eq(panel.low) & panel.volume.gt(0)).astype(int)
    panel['range_to_limit'] = panel.d0_range.div(panel.rate)
    panel['gap_to_limit'] = panel.d0_open_gap.div(panel.rate)
    panel['turnover_ratio_pre5'] = panel.d0_turnover_pct.div(panel.pre5_mean_turnover_pct.replace(0,np.nan))
    panel['amount_board_rank'] = panel.groupby('d0_date').d0_amount_100m.rank(pct=True)
    panel['turnover_board_rank'] = panel.groupby('d0_date').d0_turnover_pct.rank(pct=True)
    panel['log_float_mv_proxy'] = np.log(panel.amount.div(panel.turn_ratio.div(100).replace(0,np.nan)).where(lambda s:s.gt(0)))
    panel['log_price'] = np.log(panel.close.where(panel.close.gt(0)))
    return panel


def enrich_outcomes(panel: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    f = panel.copy()
    quotes = daily.set_index(['symbol','date_i'])[['close','preclose','up']]
    f['price_anomaly'] = False
    for gap in range(1,5):
        keys = pd.MultiIndex.from_arrays([f.symbol.to_numpy(), f.date_i.to_numpy()+gap])
        q = quotes.reindex(keys)
        for col in ('close','preclose','up'):
            f[f'd{gap}_{col}'] = q[col].to_numpy()
        f[f'd{gap}_gross'] = f[f'd{gap}_close'].div(f.d1_open).sub(1)
        if gap >= 2:
            f['price_anomaly'] |= f[f'd{gap}_preclose'].div(f[f'd{gap-1}_close']).sub(1).abs().gt(.02)
    f['execution_anomaly'] = f.d1_volume.le(0) | f.d1_open.le(0) | f.d4_close.isna()
    f['anomaly'] = f.price_anomaly | f.execution_anomaly
    f['exit_gap'] = 4
    for gap in (4,3,2):
        f.loc[f[f'd{gap}_up'].eq(True), 'exit_gap'] = gap
    f['exit_gross'] = np.nan
    for gap in (2,3,4):
        mask = f.exit_gap.eq(gap)
        f.loc[mask,'exit_gross'] = f.loc[mask,f'd{gap}_gross']
    return f


def prepare() -> None:
    spec = specification()
    daily, evidence = load_sources()
    print('daily',len(daily),flush=True)
    calendar = sorted(daily.trade_date.unique())
    dump(OUT/'calendar.json',calendar)
    panel,quality = build_panel(daily, OUT, score_all_d0=True, d0_start='2024-01-01')
    panel = add_features(panel,daily)
    panel = enrich_outcomes(panel,daily)
    panel['first_i'] = panel.first_limit_date.map({d:i for i,d in enumerate(calendar)})
    panel.to_pickle(OUT/'panel.pkl')
    dump(OUT/'source_manifest.json',{'sources':evidence,'quality':quality,'rows':len(panel),
                                    'panel_sha256':sha(OUT/'panel.pkl')})
    print('prepared',len(panel),flush=True)


def split_frames(panel: pd.DataFrame, calendar: list[str], spec: dict, group: str, window: int):
    month_map = {}
    if group == 'chronological':
        first = pd.Period('2025-10',freq='M') - window + 1
        month_map.update({str(x):'train' for x in pd.period_range(first,'2025-10',freq='M')})
        month_map.update({m:'valid' for m in ('2025-11','2025-12')})
        month_map.update({str(x):'test' for x in pd.period_range('2026-01','2026-10',freq='M')})
    else:
        for year in spec['mixed']['months'].values():
            for part in ('train','valid','test'):
                month_map.update({m:part for m in year[part]})
    assigned = np.array([month_map.get(d[:7], 'unused') for d in calendar])
    block = np.cumsum(np.r_[True,assigned[1:] != assigned[:-1]])
    start = np.minimum(panel.date_i.to_numpy()-5,panel.first_i.to_numpy()).astype(int)
    end = panel.date_i.to_numpy()+4
    valid = (start>=0)&(end<len(calendar))
    valid &= block[np.clip(start,0,len(block)-1)] == block[np.clip(end,0,len(block)-1)]
    split = panel.d0_date.str[:7].map(month_map)
    frames = {part: panel[valid & split.eq(part)].copy() for part in ('train','valid','test')}
    # No event or raw observation date may be shared across the three partitions.
    events = {p:set(zip(f.symbol,f.first_limit_date)) for p,f in frames.items()}
    for a,b in [('train','valid'),('train','test'),('valid','test')]:
        assert not events[a]&events[b], f'Shared board event: {a}/{b}'
    meta = {'month_assignment':month_map,'parts':{p:{'d0_rows':len(f),
          'fit_or_classification_rows':int((f.activated & f.observed_3).sum()),
          'days':int(f.d0_date.nunique()), 'd0_dates':[f.d0_date.min(),f.d0_date.max()],
          'boundary_removed':int(split.eq(p).sum()-len(f))} for p,f in frames.items()}}
    return frames, meta


def make_model(family: str):
    if family in ('rf','extra'):
        cls = RandomForestClassifier if family=='rf' else ExtraTreesClassifier
        tree = cls(n_estimators=240,max_depth=7,min_samples_leaf=50,max_features=.8,
                   n_jobs=2,random_state=20261013)
        return make_pipeline(SimpleImputer(strategy='median',add_indicator=True),tree)
    if family=='hgb':
        return HistGradientBoostingClassifier(max_iter=120,learning_rate=.05,max_leaf_nodes=15,
            max_depth=3,min_samples_leaf=100,l2_regularization=5,early_stopping=False,random_state=20261013)
    return make_pipeline(SimpleImputer(strategy='median',add_indicator=True),StandardScaler(),
                         LogisticRegression(C=.3,max_iter=1500,random_state=20261013))


def xmatrix(f: pd.DataFrame, features: list[str]):
    return f[features].replace([np.inf,-np.inf],np.nan)


def top_five(frame: pd.DataFrame, scores) -> pd.DataFrame:
    f = frame.copy()
    f['score'] = scores
    f = f.sort_values(['d0_date','score','symbol'],ascending=[True,False,True])
    f['d0_rank'] = f.groupby('d0_date').cumcount()+1
    f = f[f.d0_rank.le(5)].copy()
    f['active_rank'] = np.nan
    active = f[f.activated]
    f.loc[active.index,'active_rank'] = active.groupby('d1_date').cumcount()+1
    f['selected'] = f.active_rank.eq(1)
    return f


def evaluate(frame: pd.DataFrame, scores) -> tuple[dict,pd.DataFrame]:
    keep = frame.activated & frame.observed_3
    y = frame.loc[keep,'label_3'].to_numpy(int)
    p = np.asarray(scores)[keep.to_numpy()]
    pred = p>=.5
    top = top_five(frame,scores)
    metrics = {'d0_rows':len(frame),'rows':len(y),'positive':int(y.sum()),
               'base_rate':float(y.mean()),'ap':float(average_precision_score(y,p)),
               'auc':float(roc_auc_score(y,p)), 'accuracy':float(accuracy_score(y,pred)),
               'precision':float(precision_score(y,pred,zero_division=0)),
               'recall':float(recall_score(y,pred,zero_division=0)),
               'f1':float(f1_score(y,pred,zero_division=0)),
               'brier':float(brier_score_loss(y,p)), 'top':{}}
    total_days = int(frame.d0_date.nunique())
    for k in (1,2,3,5):
        chosen = top[top.active_rank.le(k)]
        observed = chosen[chosen.observed_3]
        clean = chosen[~chosen.anomaly]
        metrics['top'][str(k)] = {'days':int(chosen.d0_date.nunique()),'total_days':total_days,
            'picks':len(chosen),'observed_labels':len(observed), 'hits':int(observed.label_3.sum()),
            'hit_rate':float(observed.label_3.mean()) if len(observed) else 0.,
            'hits_per_day':float(observed.label_3.sum()/total_days),
            'd1_reboard':int(chosen.d1_up.eq(True).sum()),'clean_returns':len(clean),
            'excluded_returns':int(chosen.anomaly.sum()),
            'exit_mean':float(clean.exit_gross.mean()) if len(clean) else None,
            'exit_median':float(clean.exit_gross.median()) if len(clean) else None,
            'exit_win_rate':float(clean.exit_gross.gt(0).mean()) if len(clean) else None,
            'd3_mean':float(clean.d3_gross.mean()) if len(clean) else None}
    return metrics,top


def train(group: str) -> None:
    if sklearn.__version__!='1.7.1':
        raise RuntimeError('Use frozen sklearn 1.7.1 runtime')
    spec = specification()
    panel = pd.read_pickle(OUT/'panel.pkl')
    calendar = json.loads((OUT/'calendar.json').read_text())
    root = OUT/group
    root.mkdir(parents=True,exist_ok=True)
    results = []
    for candidate in spec[group]['candidates']:
        cid = candidate['id']
        meta_path = root/f'{cid}_metrics.json'
        if meta_path.exists():
            results.append(json.loads(meta_path.read_text()))
            continue
        frames,split_meta = split_frames(panel,calendar,spec,group,candidate['window'])
        fit = frames['train'][frames['train'].activated & frames['train'].observed_3]
        features = list(FEATURES) + (list(EXTRA) if candidate['features']=='expanded' else [])
        model = make_model(candidate['family'])
        weights = balanced_weights(fit.label_3)
        kwargs = {'sample_weight':weights} if candidate['family']=='hgb' else {
            list(model.named_steps)[-1]+'__sample_weight':weights}
        model.fit(xmatrix(fit,features),fit.label_3,**kwargs)
        # Fit trees in parallel, then fix prediction accumulation order for
        # byte-identical save/load checks and stable tie handling.
        if candidate['family'] in ('rf','extra'):
            model.steps[-1][1].n_jobs=1
        model_path = root/f'{cid}.joblib'
        joblib.dump({'model':model,'features':features,'candidate':candidate,'spec_sha256':sha(OUT/'specification.json'),
                     'sklearn':sklearn.__version__,'training_keys':fit[['symbol','d0_date']].to_dict('records')},
                     model_path,compress=3)
        result = {'candidate':candidate,'features':features,'split':split_meta,
                  'model_sha256':sha(model_path),'training_rows':len(fit),'parts':{}}
        restored = joblib.load(model_path)['model']
        for part in ('train','valid'):
            f=frames[part]
            scores=restored.predict_proba(xmatrix(f,features))[:,1]
            assert np.array_equal(scores,model.predict_proba(xmatrix(f,features))[:,1])
            metrics,top = evaluate(f,scores)
            result['parts'][part]=metrics
            top.to_pickle(root/f'{cid}_{part}_top5.pkl')
        dump(meta_path,result)
        results.append(result)
        m=result['parts']['valid']
        print(group,cid,'valid',m['top']['1'],'AP',m['ap'],flush=True)
    chosen=max(results,key=lambda r:(r['parts']['valid']['top']['1']['hits_per_day'],r['parts']['valid']['ap']))
    frozen={'selected':chosen['candidate'],'selection_rule':spec['selection'],
            'candidates':results,'spec_sha256':sha(OUT/'specification.json')}
    dump(root/'selection_frozen.json',frozen)
    print('FROZEN',group,chosen['candidate'],flush=True)


def test(group: str) -> None:
    spec=specification();root=OUT/group
    frozen=json.loads((root/'selection_frozen.json').read_text())
    assert frozen['spec_sha256']==sha(OUT/'specification.json')
    extension=OUT/'test_endpoint_extension.json'
    endpoint=json.loads(extension.read_text()) if extension.exists() else None
    if endpoint:
        assert endpoint['spec_sha256']==sha(OUT/'specification.json')
    panel=pd.read_pickle(OUT/(endpoint['panel_file'] if endpoint else 'panel.pkl'))
    calendar=json.loads((OUT/(endpoint['calendar_file'] if endpoint else 'calendar.json')).read_text())
    baseline='rf_base_8m' if group=='chronological' else 'rf_base_mixed'
    chosen_id=frozen['selected']['id']
    matched=chosen_id.replace('_expanded_','_base_')
    # Include an already-trained same-family, same-window control when it
    # exists, so feature changes are not confused with window changes.
    ids=list(dict.fromkeys([baseline,chosen_id]+([matched] if (root/f'{matched}.joblib').exists() else [])))
    results={}
    for cid in ids:
        bundle=joblib.load(root/f'{cid}.joblib');candidate=bundle['candidate']
        frames,_=split_frames(panel,calendar,spec,group,candidate['window'])
        if cid==frozen['selected']['id']:
            for part,frame in frames.items():
                frame.to_pickle(root/f'selected_{part}_all.pkl')
        model=bundle['model'];features=bundle['features'];f=frames['test']
        metrics,top=evaluate(f,model.predict_proba(xmatrix(f,features))[:,1])
        top.to_pickle(root/f'{cid}_test_top5.pkl')
        results[cid]=metrics
    chosen_id=frozen['selected']['id']
    bundle=joblib.load(root/f'{chosen_id}.joblib')
    frames,_=split_frames(panel,calendar,spec,group,bundle['candidate']['window'])
    valid=frames['valid'];valid=valid[valid.activated & valid.observed_3]
    importance=permutation_importance(bundle['model'],xmatrix(valid,bundle['features']),valid.label_3,
        scoring='average_precision',n_repeats=3,random_state=SEED,n_jobs=1)
    factor=pd.DataFrame({'feature':bundle['features'],'ap_loss':importance.importances_mean,
                          'std':importance.importances_std}).sort_values('ap_loss',ascending=False)
    factor.to_csv(root/'validation_factor_importance.csv',index=False)
    dump(root/'test_results.json',{'selection_frozen_sha256':sha(root/'selection_frozen.json'),
                                  'source_end':endpoint['source_end'] if endpoint else END,
                                  'selected':chosen_id,'results':results})
    print('TEST',group,results,flush=True)


def extend_test_endpoint() -> None:
    """Add newly available Oct 14 outcomes to frozen-model replay only."""
    source_day='2026-10-14'
    for group in ('chronological','mixed'):
        assert (OUT/group/'selection_frozen.json').exists()
    with db_connection() as conn:
        raw=query(conn,'SELECT trade_date,stk_code,preclose,open,high,low,close,volume,amount,'
                  'turn_ratio,is_limit_price,update_time FROM kcrp_stock_price WHERE trade_date=%s',
                  (source_day,))
        conn.rollback()
    assert len(raw)>5000
    source=OUT/'price_months'/f'price_{source_day}_extension.csv.gz'
    raw.to_csv(source,index=False,compression='gzip')
    daily=prepare_daily(raw).set_index('symbol')
    calendar=json.loads((OUT/'calendar.json').read_text())
    assert calendar[-1]=='2026-10-13'
    calendar.append(source_day);new_i=len(calendar)-1
    original=pd.read_pickle(OUT/'panel.pkl');f=original.copy()
    for gap in (2,3,4):
        mask=f.date_i.add(gap).eq(new_i)
        for col in ('close','preclose','up'):
            f.loc[mask,f'd{gap}_{col}']=f.loc[mask,'symbol'].map(daily[col])
        f.loc[mask,f'd{gap}_gross']=f.loc[mask,f'd{gap}_close'].div(f.loc[mask,'d1_open']).sub(1)
    for horizon in (3,4):
        f[f'observed_{horizon}']=np.logical_and.reduce([f[f'd{i}_up'].notna() for i in range(1,horizon+1)])
        f[f'label_{horizon}']=np.logical_or.reduce([f[f'd{i}_up'].eq(True) for i in range(1,horizon+1)]).astype(int)
    f['price_anomaly']=False
    for gap in (2,3,4):
        f['price_anomaly'] |= f[f'd{gap}_preclose'].div(f[f'd{gap-1}_close']).sub(1).abs().gt(.02)
    f['execution_anomaly']=f.d1_volume.le(0)|f.d1_open.le(0)|f.d4_close.isna()
    f['anomaly']=f.price_anomaly|f.execution_anomaly
    f['exit_gap']=4
    for gap in (4,3,2):
        f.loc[f[f'd{gap}_up'].eq(True),'exit_gap']=gap
    for gap in (2,3,4):
        mask=f.exit_gap.eq(gap)
        f.loc[mask,'exit_gross']=f.loc[mask,f'd{gap}_gross']
    assert f[list(FEATURES)+list(EXTRA)].equals(original[list(FEATURES)+list(EXTRA)])
    early=f.d0_date.lt('2026-10-01')
    assert f.loc[early].equals(original.loc[early]), 'Training/validation or earlier outcomes changed'
    panel_name='panel_replay_through_20261014.pkl';calendar_name='calendar_through_20261014.json'
    f.to_pickle(OUT/panel_name);dump(OUT/calendar_name,calendar)
    dump(OUT/'test_endpoint_extension.json',{'source_end':source_day,'source_sha256':sha(source),
         'panel_file':panel_name,'calendar_file':calendar_name,'panel_sha256':sha(OUT/panel_name),
         'spec_sha256':sha(OUT/'specification.json'),'features_unchanged':True,
         'training_validation_unchanged':True,'models_refitted':False})


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','train','test','spec','extend'])
    parser.add_argument('--group',choices=['chronological','mixed'],default='chronological')
    args=parser.parse_args()
    if args.action=='spec': print(json.dumps(specification(),ensure_ascii=False,indent=2))
    elif args.action=='prepare': prepare()
    elif args.action=='train': train(args.group)
    elif args.action=='extend': extend_test_endpoint()
    else: test(args.group)

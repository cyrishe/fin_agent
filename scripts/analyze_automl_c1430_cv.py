"""Frozen-model evaluation, paired day-block intervals and 14:40 Top3 review."""
import json
import hashlib
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from scripts.experiment_automl_c1430_cv import OUT, KEY, splits, score, dump, metrics
from scripts.experiment_automl_limit_rebound_d0_open_r5 import load_daily
import scripts.analyze_automl_limit_rebound_1430_long_trade as trade

BASE=Path('outputs/stock_automl/limit_rebound_1430_long_20261014')

def block_ci(delta):
    x=np.asarray(delta,float);blocks=[x[i:i+5] for i in range(0,len(x),5)]
    rng=np.random.default_rng(20261014)
    draws=[np.concatenate([blocks[i] for i in rng.integers(0,len(blocks),len(blocks))]).mean()
           for _ in range(3000)]
    return dict(mean=float(x.mean()),low=float(np.quantile(draws,.025)),high=float(np.quantile(draws,.975)))

def main():
    result=json.loads((OUT/'results.json').read_text());selection=json.loads((OUT/'selection_frozen.json').read_text())
    detail_model=json.loads((OUT/'top1_selection_addendum.json').read_text())['model']
    sets=splits(pd.read_pickle(OUT/'numeric_snapshot.pkl'))
    replay=[];comparison=[];monthly=[];same_days=[];ties=[]
    for name in result['selected']:
        same=pd.read_pickle(OUT/f'{name}_train_scores.pkl').merge(
            pd.read_pickle(OUT/f'{name}_oof.pkl')[KEY],on=KEY,validate='one_to_one')
        same_days.append(dict(model=name,**metrics(same,same.score.to_numpy())))
        for part,modelpart in [('train','train'),('valid','train'),('test','refit')]:
            asset=joblib.load(OUT/f'{name}_{modelpart}.joblib')
            saved=pd.read_pickle(OUT/f'{name}_{part}_scores.pkl')
            s=score(asset['model'],asset['config'],sets[part])
            error=float(np.max(np.abs(s-saved.score.to_numpy())))
            assert error==0
            replay.append(dict(model=name,split=part,max_error=error))
        for part in ['oof','valid','test']:
            suffix='oof.pkl' if part=='oof' else f'{part}_scores.pkl'
            z=pd.read_pickle(OUT/f'{name}_{suffix}')
            mx=z.groupby('d1_date').score.transform('max')
            tied=z[(z.score-mx).abs().le(1e-12)].groupby('d1_date')
            ties.append(dict(model=name,split=part,tied_days=int(tied.size().gt(1).sum()),
                             days=len(tied),tie_random_expected_hits=float(tied.label_next3.mean().sum())))
            z['month']=z.d1_date.str[:7]
            for month,x in z.groupby('month'):
                monthly.append(dict(name=name,split=part,month=month,**metrics(x,x.score.to_numpy())))
            top=z.sort_values(['d1_date','score','symbol'],ascending=[True,False,True]).groupby('d1_date').head(1)
            baseline=pd.read_pickle(OUT/f'baseline35_{suffix}')
            baseline=baseline.sort_values(['d1_date','score','symbol'],ascending=[True,False,True]).groupby('d1_date').head(1)
            paired=top.set_index('d1_date').label_next3-baseline.set_index('d1_date').label_next3
            comparison.append(dict(model=name,split=part,**block_ci(paired.sort_index().to_numpy())))
    dump('model_replay.json',replay);dump('paired_top1_block_ci.json',comparison)
    dump('same_oof_days_fit_diagnostic.json',same_days);dump('top1_ties.json',ties)
    pd.DataFrame(monthly).to_csv(OUT/'monthly_prediction.csv',index=False)
    # Reuse independently audited 220-bar paths; use fresh model scores for ranking.
    source=pd.read_csv(BASE/'review_scored_full_1440.csv.gz',dtype={'symbol':str,'stk_code':str},float_precision='round_trip')
    source=source.drop(columns=['score','split','original_rank','label_next3'])
    daily=load_daily();calendar=sorted(daily.trade_date.unique())
    trade.OUT=OUT  # New cap cache; do not overwrite the earlier experiment.
    trade_summary={};chosen_picks=[]
    for name in result['selected']:
        pieces=[]
        for part in ['oof','valid','test']:
            suffix='oof.pkl' if part=='oof' else f'{part}_scores.pkl'
            z=pd.read_pickle(OUT/f'{name}_{suffix}').assign(split=part)
            merged=z.merge(source,on=KEY,validate='one_to_one')
            assert len(merged)==len(z)
            pieces.append(merged)
        pool=pd.concat(pieces,ignore_index=True)
        eligible=pool[~pool.at_cap_1440].sort_values(['split','d1_date','score','symbol'],ascending=[True,True,False,True]).copy()
        eligible['day_rank']=eligible.groupby(['split','d1_date']).cumcount()+1
        close=daily.set_index(['symbol','date_i']).close
        for h in range(1,6):
            prices=close.reindex(pd.MultiIndex.from_arrays([eligible.symbol,eligible.date_i+1+h])).to_numpy(float)
            eligible[f'sell_d{h}_return_pct']=(prices/eligible.buy_1440-1)*100
        top=eligible[eligible.day_rank.le(3)].copy()
        assert not top.at_cap_1440.any()
        assert top[[f'sell_d{h}_return_pct' for h in range(1,6)]].notna().all().all()
        eligible.to_pickle(OUT/f'{name}_eligible_1440.pkl')
        stats={}
        for part,x in top.groupby('split'):
            top1=x[x.day_rank.eq(1)];whole=eligible[eligible.split.eq(part)]
            stats[part]=dict(rows=len(x),days=x.d1_date.nunique(),top3_hits=int(x.label_next3.sum()),
                top1_hits=int(top1.label_next3.sum()),blocked=int(pool[pool.split.eq(part)].at_cap_1440.sum()),
                top3_returns={f'D{h}':float(x[f'sell_d{h}_return_pct'].mean()) for h in range(1,6)},
                top1_returns={f'D{h}':float(top1[f'sell_d{h}_return_pct'].mean()) for h in range(1,6)},
                pool_d4=float(whole.groupby('d1_date').sell_d4_return_pct.mean().mean()))
        trade_summary[name]=stats
        if name==detail_model: chosen_picks=top
    details=trade.add_trade_fields(chosen_picks,daily,calendar)
    details.to_pickle(OUT/'selected_top3_details.pkl')
    dump('trade_summary.json',trade_summary)
    cap=dict(missing=int(details.d1_1440_market_cap_100m_est.isna().sum()),
             basis=details.market_cap_basis.value_counts().to_dict(),max_age=float(details.market_cap_age_days.max()))
    dump('trade_quality.json',dict(rows=len(details),at_cap=int(details.at_cap_1440.sum()),cap=cap))
    fields=['symbol','d0_date','d1_date','day_rank','score','d0_volume_10k','d0_turnover_pct',
            'd1_1440_volume_10k','d1_1440_amount_100m','d1_1440_turnover_pct_est',
            'd1_1440_market_cap_100m_est','market_cap_basis','market_cap_age_days','buy_1440',
            'label_next3',*[f'sell_d{h}_return_pct' for h in range(1,6)]]
    headers=['股票代码','封板日 D0','决策日','排名','模型分数','D0 成交量(万股)','D0 换手(%)',
             '14:40累计量(万股)','14:40累计额(亿元)','14:40换手估算(%)','14:40市值估算(亿元)',
             '市值口径','股本距D0(天)','14:40买入价','未来三日封板',*[f'买后D{h}收盘收益(%)' for h in range(1,6)]]
    sheet={k:json.loads(x[fields].to_json(orient='values',force_ascii=False)) for k,x in details.groupby('split')}
    data=dict(results=result,selection=selection,detail_model=detail_model,trade=trade_summary,headers=headers,sheets=sheet,
              leaderboard=pd.read_csv(OUT/'cv_leaderboard.csv').to_dict(orient='records'),
              folds=json.loads((OUT/'plan.json').read_text())['folds'],cap=cap)
    dump('workbook_data.json',data)
    print(json.dumps(dict(selection=selection,trade=trade_summary,quality=cap),ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__': main()

"""Compare a fresh B1 replay with frozen inputs and decisions, without writing DB."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.backfill_b1_history import FEATURES, PROBS, read, save_json, sha


def compare_picks(actual, reference, dates, output):
    keys=['signal_date','selection_rank']
    a=actual[actual.signal_date.isin(dates)]
    b=reference[reference.signal_date.isin(dates)]
    joined=a.merge(b,on=keys,how='outer',suffixes=('_new','_old'),indicator=True,validate='one_to_one')
    joined['same_stock']=joined.symbol6_new.eq(joined.symbol6_old)&joined._merge.eq('both')
    probability={}
    for col in PROBS:
        if col+'_old' not in joined:continue
        mask=joined.same_stock
        delta=(joined.loc[mask,col+'_new']-joined.loc[mask,col+'_old']).abs()
        probability[col]={'max_abs_delta':float(delta.max()) if len(delta) else None,'within_1e_12':int(delta.le(1e-12).sum())}
    joined.to_csv(output,index=False,float_format='%.17g')
    mismatch=set(joined.loc[~joined.same_stock,'signal_date'])
    return {'dates':len(dates),'matching_daily_lists':len(dates)-len(mismatch),
            'mismatching_dates':sorted(mismatch),'reference_picks':len(b),'new_picks':len(a),
            'matching_ranked_stocks':int(joined.same_stock.sum()),'probabilities_for_same_stock':probability,
            'reference_sha256':None}


def compare_inputs(actual, reference, output):
    reference=reference[reference.signal_date.between(actual.signal_date.min(),actual.signal_date.max())]
    dates=set(reference.signal_date);a=actual[actual.signal_date.isin(dates)]
    keys=['signal_date','symbol6'];cols=[*FEATURES,'signal_price','second_high']
    j=a[keys+cols].merge(reference[keys+cols],on=keys,how='outer',suffixes=('_new','_old'),indicator=True,validate='one_to_one')
    report={'new_rows':len(a),'reference_rows':len(reference),'common_rows':int(j._merge.eq('both').sum()),
            'new_only':int(j._merge.eq('left_only').sum()),'reference_only':int(j._merge.eq('right_only').sum()),'columns':{}}
    different=~j._merge.eq('both')
    for col in cols:
        x=pd.to_numeric(j[col+'_new']);y=pd.to_numeric(j[col+'_old'])
        eq=np.isclose(x,y,rtol=0,atol=1e-10,equal_nan=True)
        both=j._merge.eq('both');delta=(x[both]-y[both]).abs()
        report['columns'][col]={'different':int((both&~eq).sum()),'max_abs_delta':float(delta.max())}
        different|=~eq
    j[different].to_csv(output,index=False,float_format='%.17g')
    return report


def audit(run, repo):
    dest=run/'audit';dest.mkdir(exist_ok=True)
    p=read(run/'picks.csv');c=read(run/'candidates.csv.gz');folds=json.loads((run/'folds.json').read_text());report={'picks_sha256':sha(run/'picks.csv'),'candidates_sha256':sha(run/'candidates.csv.gz')}
    c=c[c.signal_date.le(folds[-1]['day'])]
    sources={
        'original_feb_jul':repo/'docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top2_verified.csv',
        'independent_aug_oct':repo/'docs/stock_automl_runs/20261011_aug_oct_independent20/top2_verified.csv'}
    for name,path in sources.items():
        old=read(path)
        date_file=path.parent/'folds.json'
        reference_dates={f['test_date'] for f in json.loads(date_file.read_text())} if date_file.exists() else set(old.signal_date)
        dates=sorted(reference_dates&{f['day'] for f in folds})
        report[name]=compare_picks(p,old,dates,dest/(name+'_picks.csv'))
        report[name]['reference_sha256']=sha(path)
        report[name]['scope']='Complete archived folds including no-pick days.' if date_file.exists() else 'Reference days with saved picks; complete daily/no-pick comparison is CO7 below.'
    path=repo/'docs/stock_automl_runs/20261015_close_open_conditional_b1/daily.csv'
    old=read(path);old=old[old.model.eq('B1_171')&old.signal_date.ge('2026-01-01')]
    dates=sorted(set(old.signal_date)&{f['day'] for f in folds})
    ref=old[old.selected].copy();ref['selection_rank']=1
    report['CO7_top1']=compare_picks(p[p.selection_rank.eq(1)],ref,dates,dest/'CO7_picks.csv')
    report['CO7_top1']['reference_sha256']=sha(path)
    reference_trades=old.set_index('signal_date').trade.reindex(dates).astype(bool)
    replay_trades=p.groupby('signal_date').actionable.any().reindex(dates,fill_value=False)
    report['CO7_top1']['matching_daily_trade_decisions']=int(reference_trades.eq(replay_trades).sum())
    old_trades=old[old.trade&old.signal_date.isin(dates)]
    new_trades=p[p.actionable&p.signal_date.isin(dates)]
    trade_keys=new_trades.merge(old_trades,on=['signal_date','symbol6'],how='outer',indicator=True)
    report['CO7_top1']['reference_trades']=len(old_trades)
    report['CO7_top1']['replayed_trades']=len(new_trades)
    report['CO7_top1']['identical_trade_keys']=int(trade_keys._merge.eq('both').sum())
    a=p[p.selection_rank.eq(1)].merge(old,on=['signal_date','symbol6'],suffixes=('_new','_old'),validate='one_to_one')
    gateeq=a.amount_gate_new.fillna(-1).eq(a.amount_gate_old.fillna(-1))
    report['CO7_top1']['same_stock_gate_comparable']=len(a)
    report['CO7_top1']['same_stock_gate_equal']=int(gateeq.sum())
    a['new_return']=a.close_40/a.signal_price-1
    report['CO7_top1']['same_stock_return_max_delta']=float((a.new_return-a.close40_return).abs().max())
    a.to_csv(dest/'CO7_same_stock_execution.csv',index=False,float_format='%.17g')
    execution_path=repo/'outputs/stock_automl/b1_market_trade_gate_co8/dataset.csv.gz'
    previous=read(execution_path)
    prices=p[p.selection_rank.eq(1)].merge(previous,on=['signal_date','symbol6'],suffixes=('_new','_old'),validate='one_to_one')
    report['CO7_top1']['execution_price_reference_sha256']=sha(execution_path)
    report['CO7_top1']['same_stock_price_rows']=len(prices)
    report['CO7_top1']['buy_price_max_delta']=float((prices.signal_price_new-prices.signal_price_old).abs().max())
    report['CO7_top1']['sell_price_max_delta']=float((prices.close_40-prices.exit_0940).abs().max())
    for name,path in {
        'jan_jul_inputs':repo/'outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz',
        'aug_oct_inputs':repo/'docs/stock_automl_runs/20261011_aug_oct_independent20/candidates.csv.gz'}.items():
        report[name]=compare_inputs(c,read(path),dest/(name+'_differences.csv.gz'))
        report[name]['reference_sha256']=sha(path)
    p['gross_return_pct']=np.where(p.actionable,p.close_40/p.signal_price*100-100,np.nan)
    p.to_csv(dest/'selections_with_returns.csv',index=False,float_format='%.17g')
    settled=p.actionable&p.close_40.notna()
    export=pd.DataFrame({'模型':'收盘开盘','版本':'v1.1','选股日期':p.signal_date,'排名':p.selection_rank,
                         '股票代码':p.symbol6,'股票名称':p.name,'可交易':p.actionable.astype(int),
                         '信号价格':p.signal_price,'买入时间':np.where(p.actionable,p.signal_date+' 14:40',''),
                         '买入价格':p.signal_price.where(p.actionable),
                         '卖出时间':np.where(settled,p.next_date+' 09:40',''),
                         '卖出价格':p.close_40.where(settled),'毛收益(%)':p.gross_return_pct})
    export.to_csv(dest/'选股与买卖明细.csv',index=False,encoding='utf-8-sig')
    monthly=[]
    for month in sorted({f['day'][:7] for f in folds}):
        rows=p[p.signal_date.str.startswith(month)];trade=rows[rows.actionable];settled=trade[trade.close_40.notna()]
        monthly.append({'month':month,'decision_days':sum(f['day'].startswith(month) for f in folds),
                        'selected_rows':len(rows),'actionable':len(trade),'settled':len(settled),
                        'mean_gross_return_pct':float(settled.gross_return_pct.mean()),
                        'win_rate_pct':float(settled.gross_return_pct.gt(0).mean()*100),
                        'gross_compound_pct':float(((1+settled.gross_return_pct/100).prod()-1)*100)})
    pd.DataFrame(monthly).to_csv(dest/'monthly.csv',index=False)
    report['run']={'first_day':folds[0]['day'],'last_day':folds[-1]['day'],'decision_days':len(folds),
                   'no_selection_days':sum(f['selection_count']==0 for f in folds),'picks':len(p),'actionable':int(p.actionable.sum()),
                   'settled':int((p.actionable&p.close_40.notna()).sum()),'monthly':monthly,
                   'prices':'D0 14:40 / D1 09:40 minute close proxies; no fees/slippage; not broker fills.'}
    save_json(dest/'report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',type=Path,required=True);parser.add_argument('--repo',type=Path,default=Path('.'))
    args=parser.parse_args();audit(args.run,args.repo)

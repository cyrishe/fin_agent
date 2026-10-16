"""Independent raw-price label reconciliation and real-data causal spot checks."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.research_kline_forward_kp1 import OUT, REPORT, board, chained_bars, dump
from src.experiments.kline_patterns.engine import prepare, detect
from src.experiments.kline_patterns.catalog import CATALOG


def main():
    raw = pd.concat([pd.read_pickle(p) for p in sorted((OUT/'source').glob('*.pkl.gz'))], ignore_index=True)
    raw = raw[raw.stk_code.map(board).notna()]
    cal = pd.DatetimeIndex(sorted(raw.loc[raw.volume.gt(0),'trade_date'].unique()))
    panel = pd.concat([pd.read_pickle(p) for p in sorted((OUT/'parts').glob('*.pkl.gz'))], ignore_index=True)
    samples = pd.concat([panel.sample(min(200,len(panel)),random_state=16),
                         panel[panel.r3.isna()].head(20)])
    groups = {k:g.set_index('trade_date').reindex(cal) for k,g in raw[raw.stk_code.isin(samples.code)].groupby('stk_code')}
    errors=[]; checked=0
    for row in samples.itertuples():
        b=groups[row.code]; t=cal.get_loc(row.date); price=1.; maxp=-np.inf; minp=np.inf; complete=True
        for h in (1,2,3):
            if t+h>=len(cal): complete=False
            else:
                r=b.iloc[t+h]
                ohlc=r[['open','high','low','close']].to_numpy(float)
                complete=complete and np.isfinite(ohlc).all() and min(ohlc)>0 and r.volume>0 and r.preclose>0
                complete=complete and r.high>=max(r.open,r.close,r.low) and r.low<=min(r.open,r.close,r.high)
                if complete:
                    price*=r.close/r.preclose
                    maxp=max(maxp,price*r.high/r.close)
                    minp=min(minp,price*r.low/r.close)
            for key,expect in [(f'r{h}',100*(price-1)),(f'mfe{h}',100*(maxp-1)),(f'mae{h}',100*(minp-1))]:
                actual=getattr(row,key)
                if complete:
                    assert np.isclose(actual,expect,atol=1e-9), (row.code,row.date,key,actual,expect)
                    errors.append(abs(actual-expect));checked+=1
                else: assert pd.isna(actual), (row.code,row.date,key,actual)
    causal=0
    for code in sorted(panel.code.unique())[::max(1,panel.code.nunique()//5)][:5]:
        b=raw[raw.stk_code.eq(code)].set_index('trade_date').reindex(cal)
        available=panel[panel.code.eq(code)]
        if available.empty:continue
        row=available.iloc[len(available)//2]
        prefix=prepare(chained_bars(b.loc[:row.date]))
        for p in CATALOG:
            d=detect(p,prefix).iloc[-1]
            assert bool(False if pd.isna(d) else d)==bool(row['hit_'+p['id']]),(code,row.date,p['id'])
            assert bool(pd.notna(d))==bool(row['known_'+p['id']])
            causal+=1
    summary=pd.read_csv(REPORT/'summary.csv')
    main_summary=summary[(summary.scope=='all')&(summary.window=='6m')&(summary['mode']=='all_hits')&summary.horizon.eq(3)].set_index('id')
    for p in CATALOG:
        e=panel[panel['hit_'+p['id']]]
        r=main_summary.loc[p['id']]
        assert len(e)==r.hits and e.code.nunique()==r.stocks and e.r3.notna().sum()==r.n
        if len(e.r3.dropna()): assert np.isclose(e.r3.mean(),r.mean_pct,atol=1e-12)
    result={'independent_raw_forward_values_checked':checked,'max_absolute_error_pp':max(errors,default=0),
            'sample_stock_days':len(samples),'real_data_prefix_pattern_checks':causal,
            'pattern_aggregates_reconciled':len(CATALOG),'duplicate_panel_keys':int(panel.duplicated(['code','date']).sum()),
            'model_training_calls':0,'status':'passed'}
    dump(REPORT/'verification.json',result);print(json.dumps(result))


if __name__=='__main__':main()

"""Extract six <=14:30 path descriptors; preserve a binary numeric snapshot."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scripts.mk_minute_archive import COMPACT_ROOT, COMPACT_ROOT_2025, day_file

OUT = Path('outputs/stock_automl/limit_rebound_1430_cv_20261014')
PATH_FEATURES = ['path_last30_return', 'path_rv5', 'path_max_drawdown',
                 'path_efficiency', 'path_late_amount_accel', 'path_afternoon_amount_share']
EXPECTED = [x.strftime('%H:%M:%S') for x in pd.date_range('09:31','11:30',freq='min')]
EXPECTED += [x.strftime('%H:%M:%S') for x in pd.date_range('13:01','14:30',freq='min')]

def extract(bars):
    bars = bars[bars.time.str[-8:].le('14:30:00')].sort_values('time')
    if bars.time.str[-8:].tolist() != EXPECTED:
        raise ValueError('Incomplete 14:30 prefix')
    c=bars.close.to_numpy(float); a=bars.turnover.to_numpy(float)
    r=np.diff(np.log(c[4::5]))
    path=np.r_[float(bars.open.iloc[0]),c]
    return dict(zip(PATH_FEATURES, [
        c[-1]/c[-31]-1, np.sqrt(np.sum(r*r)),
        np.min(path/np.maximum.accumulate(path)-1),
        (path[-1]-path[0])/np.abs(np.diff(path)).sum() if np.abs(np.diff(path)).sum()>0 else 0.,
        a[-30:].sum()/a[-60:-30].sum() if a[-60:-30].sum()>0 else np.nan,
        a[120:].sum()/a.sum()]))

def one_day(task):
    day,symbols=task
    cache=OUT/'path_days'/f'{day}.pkl'
    if cache.exists():
        return pd.read_pickle(cache)
    path=day_file(COMPACT_ROOT_2025 if day.startswith('2025') else COMPACT_ROOT,day)
    parts=[]
    for chunk in pd.read_csv(path,usecols=['time','symbol','open','close','turnover'],
                             dtype={'symbol':str},chunksize=250000):
        x=chunk[chunk.symbol.str[:6].isin(symbols)&chunk.time.str[-8:].le('14:30:00')]
        if len(x): parts.append(x)
    rows=[dict(symbol=s[:6],d1_date=day,**extract(b))
          for s,b in pd.concat(parts).groupby('symbol')]
    frame=pd.DataFrame(rows)
    if set(frame.symbol)!=symbols: raise ValueError(f'Missing symbols {day}')
    frame.to_pickle(cache)
    return frame

def main():
    OUT.mkdir(parents=True,exist_ok=True); (OUT/'path_days').mkdir(exist_ok=True)
    p=pd.read_csv('outputs/stock_automl/limit_rebound_1430_long_20261014/panel.csv.gz',
                  dtype={'symbol':str,'stk_code':str},float_precision='round_trip')
    tasks=list(p.groupby('d1_date').symbol.apply(set).items())
    parts=[]
    with ProcessPoolExecutor(max_workers=2) as ex:
        for n,f in enumerate(ex.map(one_day,tasks),1):
            parts.append(f)
            if n%20==0: print(f'path extraction {n}/{len(tasks)}',flush=True)
    paths=pd.concat(parts,ignore_index=True)
    merged=p.merge(paths,on=['symbol','d1_date'],validate='one_to_one')
    assert len(merged)==len(p)
    merged.to_pickle(OUT/'panel.pkl')
    print('saved',len(merged),flush=True)

if __name__=='__main__': main()

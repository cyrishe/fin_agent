"""CO5 exact 14:40 stock amounts, reused by every model's post-selection gate."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import pandas as pd
from scripts.build_automl_close_open_factor_panel import DATA as SOURCE, CLOCKS
from scripts.benchmark_automl_1440_inference import db_connection, query

OUT = Path('outputs/stock_automl/close_open_simple_rolling_r1/amounts')

def one(task):
    day, path = task
    dest = OUT / (day+'.csv.gz')
    if dest.exists():
        return day, len(pd.read_csv(dest)), 'cache'
    if path:
        b = pd.read_csv(path,usecols=['symbol','time','turnover'],dtype={'symbol':str,'time':str})
        b = b[b.time.str[-8:].isin(CLOCKS)].copy()
        b['symbol6'] = b.symbol.str[:6]
        b = b[b.symbol6.str.startswith(('0','3','6'))]
        b['valid'] = b.turnover.notna() & b.turnover.ge(0)
        g = b.groupby('symbol6').agg(amount=('turnover','sum'),n=('time','size'),unique=('time','nunique'),valid=('valid','all'))
        g = g[g.n.eq(220)&g.unique.eq(220)&g.valid].reset_index()[['symbol6','amount']]
    else:
        with db_connection() as db:
            g = query(db,"""SELECT LEFT(stk_code,6) symbol6, SUM(amount) amount,
                COUNT(*) n, COUNT(DISTINCT bar_end_time) n_unique,
                SUM(is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time
                    AND amount IS NOT NULL AND amount>=0) n_valid
                FROM aiia_stock_realtime_minute_snapshot_full
                WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                AND bar_end_time BETWEEN %s AND %s GROUP BY stk_code""",
                (day,day+' 09:31:00',day+' 14:40:00'))
        g = g[g.n.eq(220)&g.n_unique.eq(220)&g.n_valid.eq(220)].copy()
        g['symbol6'] = g.symbol6.astype(str).str.zfill(6)
        g = g[g.symbol6.str.startswith(('0','3','6'))][['symbol6','amount']]
    g['date'] = day
    g.to_csv(dest,index=False,float_format='%.17g')
    return day,len(g),'built'

if __name__ == '__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    avail = pd.read_csv(SOURCE/'availability.csv').fillna('')
    tasks = [(r.date,r.local_file) for r in avail.itertuples() if '2025-05-01'<=r.date<='2026-10-13']
    with ProcessPoolExecutor(max_workers=2) as pool:
        for i,res in enumerate(pool.map(one,tasks),1):
            if i%10==0 or i==len(tasks): print(i,len(tasks),res,flush=True)

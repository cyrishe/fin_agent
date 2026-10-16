"""R18A: append D1 pre-09:40 high-price entry and D2/D3/D4 close returns.

No model refit or reranking. Minute bars are used only for the requested replay.
"""
from __future__ import annotations
import json
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
import numpy as np,pandas as pd
from scripts.experiment_limit_rebound_segments_r18 import OUT as BASE,SOURCE
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha
from scripts.export_limit_rebound_short_mixed_r12 import filtered_local
from scripts.mk_minute_archive import COMPACT_ROOT,COMPACT_ROOT_2025,day_file
from scripts.benchmark_automl_1440_inference import db_connection,query
from scripts.experiment_automl_limit_rebound_d0_open_r5 import cap_price
from scripts.experiment_automl_limit_rebound_st_size_r4 import historical_status

OUT=BASE/'entry0940_r18a_20261015'
CLOCK=[f'09:{i:02}' for i in range(31,41)]
DAY_CLOCK=([f'{h:02}:{m:02}' for h,m in [(t//60,t%60) for t in range(571,691)]]+
           [f'{h:02}:{m:02}' for h,m in [(t//60,t%60) for t in range(781,901)]])
COLS=['open','high','low','close','volume']


def facts(bars,r):
    bad=lambda reason:{'minute_ok':False,'reason':reason,'can_buy0940':None,'buy0940':None,'buy_at_cap':None}
    if bars.empty:return bad('分钟缺失')
    b=bars.sort_values('time').copy();clock=pd.to_datetime(b.time).dt.strftime('%H:%M')
    if clock.tolist()!=DAY_CLOCK:return bad('全天分钟不齐')
    v=b[COLS].apply(pd.to_numeric,errors='coerce')
    if not np.isfinite(v.to_numpy()).all() or v[COLS[:4]].le(0).any().any() or v.volume.lt(0).any():return bad('非法量价')
    if (v.high.lt(v[['open','low','close']].max(axis=1)-.005).any()
        or v.low.gt(v[['open','high','close']].min(axis=1)+.005).any()):return bad('分钟OHLC冲突')
    if not np.isclose(v.open.iloc[0],r.d1_open,atol=.005,rtol=0):return bad('开盘价不符')
    if not np.isclose(v.close.iloc[-1],r.d1_close,atol=.005,rtol=0):return bad('收盘价不符')
    if not np.isclose(v.volume.sum(),r.d1_volume,atol=1,rtol=.001):return bad('全天量不符')
    early=v[clock.isin(CLOCK)]
    if len(early)!=10:return bad('早盘分钟不齐')
    if early.high.max()>r.d1_cap+.011:return bad('早盘超涨停价')
    traded=early[early.volume.gt(0)]
    opened=traded.low.lt(r.d1_cap-.005)
    can=bool(opened.any() and r.d1_st==0)
    high=float(traded.high.max()) if len(traded) else None
    return {'minute_ok':True,'reason':'通过','can_buy0940':int(can),
      'buy0940':high if can else None,'buy_at_cap':int(high>=r.d1_cap-.005) if can else None,
      'early_high':high,'early_low':float(traded.low.min()) if len(traded) else None,
      'early_volume':float(early.volume.sum()),'open_minutes':int(opened.sum())}


def one_day(day,demand):
    dest=OUT/'days';dest.mkdir(exist_ok=True)
    done=dest/f'{day}.json'
    if done.exists():return json.loads(done.read_text())
    frames=[];found={}
    try:
        path=day_file(COMPACT_ROOT_2025 if day.startswith('2025') else COMPACT_ROOT,day)
        local=filtered_local(path,set(demand.symbol));origin=str(path)
    except FileNotFoundError:local=pd.DataFrame();origin='local_missing'
    def collect(raw,source):
        if raw.empty:return
        raw=raw.copy();raw['symbol']=raw.symbol.astype(str).str[:6]
        raw['source']=source;frames.append(raw)
        for sym,g in raw.groupby('symbol'):
            match=demand[demand.symbol.eq(sym)]
            if match.empty:continue
            value=facts(g,match.iloc[0]);value.update({'symbol':sym,'d1_date':day,'source':source})
            if value['minute_ok'] or sym not in found:found[sym]=value
    collect(local,origin)
    missing=[s for s in demand.symbol if not found.get(s,{}).get('minute_ok',False)]
    if missing and day>='2026-01-01':
        try:
            with db_connection() as conn:
                marks=','.join(['%s']*len(missing))
                raw=query(conn,'SELECT stk_code AS symbol,bar_end_time AS time,open_price AS open,'
                  'high_price AS high,low_price AS low,latest_price AS close,volume FROM aiia_stock_realtime_minute_snapshot_full '
                  "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 AND is_finalized=1 AND is_fallback=0 "
                  'AND source_snapshot_time=bar_end_time AND stk_code IN ('+marks+')',(day,*missing))
                conn.rollback()
            if len(raw):
                for c in COLS:raw[c]=pd.to_numeric(raw[c],errors='coerce')
                raw['volume']*=100
            collect(raw,'database_finalized')
        except Exception as exc:
            # Never treat a failed read as a nontradable signal.
            for s in missing:
                if s not in found:found[s]={'symbol':s,'d1_date':day,'source':'database_error',
                    'minute_ok':False,'reason':type(exc).__name__,'can_buy0940':None,'buy0940':None,'buy_at_cap':None}
    rows=[found.get(s,{'symbol':s,'d1_date':day,'source':'missing','minute_ok':False,'reason':'分钟缺失',
                       'can_buy0940':None,'buy0940':None,'buy_at_cap':None}) for s in demand.symbol]
    if frames:pd.concat(frames,ignore_index=True).to_pickle(dest/f'{day}_raw.pkl')
    dump(done,rows);return rows


def return_columns(f):
    f=f.copy();continuous=f.d1_close.gt(0)
    for k in (2,3,4):
        continuous &= (f[f'd{k}_close'].gt(0)&f[f'd{k}_preclose'].gt(0)
          &f[f'd{k}_preclose'].sub(f[f'd{k-1}_close']).abs().le(.011))
        f[f'return0940_d{k}']=np.where(f.can_buy0940.eq(1)&continuous,f[f'd{k}_close']/f.buy0940-1,np.nan)
        f[f'return0940_d{k}_ok']=f.can_buy0940.eq(1)&continuous
    return f


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    original=pd.read_pickle(BASE/'segments_test_top5.pkl');f=original.copy()
    f['original_order']=range(len(f));assert len(f)==1335
    # The prior panel extended D2-D4 to Oct14, but left Oct13's D1 blank.
    # Complete only the replay's D1 metadata from the already saved Oct14 quotes.
    cal=json.loads((BASE/'calendar.json').read_text());idx={d:i for i,d in enumerate(cal)}
    mask=f.d1_date.isna();missing=f[mask]
    if len(missing):
        endpoint=SOURCE/'price_months/price_2026-10-14_extension.csv.gz'
        raw=pd.read_csv(endpoint,dtype={'stk_code':str,'trade_date':str});raw['symbol']=raw.stk_code.str[:6]
        raw=raw.set_index('symbol');assert not raw.index.duplicated().any()
        for ix,r in missing.iterrows():
            day=cal[idx[r.d0_date]+1]
            if r.symbol not in raw.index or raw.loc[r.symbol,'trade_date']!=day:continue
            q=raw.loc[r.symbol];f.loc[ix,'d1_date']=day
            for c in ['open','close','volume','preclose']:f.loc[ix,'d1_'+c]=float(q[c])
            f.loc[ix,'d1_up']=bool(q.is_limit_price==1 and q.close>q.preclose)
            f.loc[ix,'d1_cap']=cap_price(float(q.preclose),float(r.rate))
        keys=f.loc[mask&f.d1_date.notna(),['symbol','d1_date']].rename(columns={'d1_date':'asof_date'})
        status=historical_status(keys,OUT).set_index(['symbol','asof_date'])
        for ix,r in f[mask&f.d1_date.notna()].iterrows():f.loc[ix,'d1_st']=status.loc[(r.symbol,r.d1_date),'is_st']
        patches=[]
        for r in f[mask&f.d1_date.notna()].itertuples():
            patches.append({'excel_row':int(r.original_order)+2,
              'open_activated':int(r.d1_st==0 and r.d1_open>0 and r.d1_open<r.d1_cap-.005),
              'd1_sealed':int(r.d1_up)})
        dump(OUT/'endpoint_cell_patches.json',patches)
        dump(OUT/'endpoint_metadata.json',{'source':str(endpoint),'sha256':sha(endpoint),'filled_rows':int((mask&f.d1_date.notna()).sum()),
          'scope':'Complete final five D1 replay metadata and dependent open-activation/D1-sealed cells only; models, scores, ranks and three-day labels unchanged'})
    f.to_pickle(OUT/'demand.pkl')
    demand=f.dropna(subset=['d1_date'])
    jobs=list(demand.groupby('d1_date'));records=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(one_day,d,g) for d,g in jobs]
        for n,future in enumerate(as_completed(futures),1):
            records.extend(future.result())
            if n%20==0 or n==len(jobs):print('minute days',n,'/',len(jobs),flush=True)
    facts_frame=pd.DataFrame(records);facts_frame.to_pickle(OUT/'minute_facts.pkl')
    f=f.merge(facts_frame,on=['symbol','d1_date'],how='left',validate='one_to_one').sort_values('original_order')
    # No minute rows plus a valid daily quote with zero turnover establishes
    # that no trade occurred that day. Do not turn conflicting minute data into 0.
    no_trade=f.reason.eq('分钟缺失')&f.d1_volume.eq(0)&f.d1_open.gt(0)&f.d1_close.gt(0)
    f.loc[no_trade,'can_buy0940']=0;f.loc[no_trade,'reason']='全天无成交'
    f['reason']=f.reason.fillna('D1日线缺失')
    f=return_columns(f);f.to_pickle(OUT/'enriched_top5.pkl')
    rows=[]
    for r in f.itertuples():
        rows.append({'940前可买':int(r.can_buy0940) if pd.notna(r.can_buy0940) else None,
          '买入价':r.buy0940,'买价触板':int(r.buy_at_cap) if pd.notna(r.buy_at_cap) else None,
          'D2收益':r.return0940_d2,'D3收益':r.return0940_d3,'D4收益':r.return0940_d4})
    clean=json.loads(pd.DataFrame(rows).to_json(orient='records',force_ascii=False,double_precision=15));dump(OUT/'append_rows.json',clean)
    summary={'rows':len(f),'days':len(jobs),'verified_minutes':int(f.minute_ok.eq(True).sum()),
       'tradable':int(f.can_buy0940.eq(1).sum()),'not_tradable':int(f.can_buy0940.eq(0).sum()),
       'unknown':int(f.can_buy0940.isna().sum()),'entry_at_cap':int(f.buy_at_cap.eq(1).sum()),
       'zero_day_volume_not_tradable':int(no_trade.sum()),
       'missing_reasons':f.loc[~f.minute_ok.eq(True),'reason'].value_counts().to_dict(),
       'return_counts':{str(k):int(f[f'return0940_d{k}'].notna().sum()) for k in (2,3,4)},
       'source_top5_sha256':sha(BASE/'segments_test_top5.pkl'),'models_retrained':False,
       'notes':'Any positive-volume bar low below cap in 09:31..09:40 activates; buy at max high of positive-volume bars in entire window. Cap-priced hypothetical entries flagged. Each return needs continuity only through its horizon.'}
    dump(OUT/'coverage.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':run()

"""Build only the chosen models' daily Top5 detail, with audited minute timing."""
from __future__ import annotations

import io
import json
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import zstandard as zstd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_limit_rebound_short_mixed_r12 import OUT, dump
from scripts.export_limit_rebound_d0_top5_r9_detail import records
from scripts.mk_minute_archive import day_file, COMPACT_ROOT, COMPACT_ROOT_2025
from scripts.research_limit_rebound_d1_minute_r6 import minute_facts


def chosen_frames() -> pd.DataFrame:
    parts=[]
    for group in ('chronological','mixed'):
        frozen=json.loads((OUT/group/'selection_frozen.json').read_text())
        cid=frozen['selected']['id']
        for part in ('train','valid','test'):
            f=pd.read_pickle(OUT/group/f'{cid}_{part}_top5.pkl').copy()
            f['experiment_group']=group;f['part']=part;f['model_id']=cid
            f['day_seq']=pd.factorize(f.d0_date,sort=True)[0]+1
            parts.append(f)
    f=pd.concat(parts,ignore_index=True)
    assert f.groupby(['experiment_group','part','d0_date']).size().le(5).all()
    return f


def filtered_local(path: Path, wanted: set[str]) -> pd.DataFrame:
    """Filter fixed-format compact CSV bytes before parsing numeric columns."""
    selected=[];header=None;buffer=b'';codes={s.encode() for s in wanted}
    source=path.open('rb')
    stream=zstd.ZstdDecompressor().stream_reader(source) if path.suffix=='.zst' else source
    try:
        while chunk:=stream.read(4*1024*1024):
            lines=(buffer+chunk).split(b'\n');buffer=lines.pop()
            if header is None:
                header=lines.pop(0)
                if not header.startswith(b'time,symbol,'):
                    raise ValueError(f'Unexpected minute header: {path}')
            selected.extend(line for line in lines if len(line)>26 and line[20:26] in codes)
        if buffer and len(buffer)>26 and buffer[20:26] in codes:
            selected.append(buffer)
    finally:
        stream.close();source.close()
    if not selected:
        return pd.DataFrame()
    return pd.read_csv(io.BytesIO(b'\n'.join([header,*selected])),dtype={'symbol':str})


def one_day(day: str, demand: pd.DataFrame) -> list[dict]:
    cache=OUT/'minute_days'/f'{day}.json'
    if cache.exists():
        saved=json.loads(cache.read_text())
        if set(r['symbol'] for r in saved)==set(demand.symbol):
            return saved
    rows={}
    if day<'2025-01-01':
        return [{'d1_date':day,'symbol':s,'minute_complete':0,'minute_reason':'2024_no_archive'}
                for s in demand.symbol]
    try:
        path=day_file(COMPACT_ROOT_2025 if day.startswith('2025') else COMPACT_ROOT,day)
        bars=filtered_local(path,set(demand.symbol))
    except FileNotFoundError:
        bars=pd.DataFrame()

    def collect(bars: pd.DataFrame, source: str) -> None:
        if bars.empty:
            return
        bars['symbol']=bars.symbol.astype(str).str[:6]
        for symbol,g in bars.groupby('symbol'):
            match=demand[demand.symbol.eq(symbol)]
            if match.empty:
                continue
            r=match.iloc[0]
            facts=minute_facts(g,float(r.d1_cap),float(r.d1_close))
            if facts is not None and np.isclose(facts['minute_open'],r.d1_open,atol=.005,rtol=0):
                if np.isclose(facts['minute_total_volume'],r.d1_volume,rtol=.001,atol=1):
                    rows[symbol]={'d1_date':day,'symbol':symbol,'minute_complete':1,
                                  'minute_reason':source,**facts}
    collect(bars,'local_mk_none')
    missing=sorted(set(demand.symbol)-set(rows))
    if missing and day>='2026-01-01':
        try:
            with db_connection() as conn:
                marks=','.join(['%s']*len(missing))
                bars=query(conn,'SELECT stk_code AS symbol,bar_end_time AS time,open_price AS open,'
                    'high_price AS high,low_price AS low,latest_price AS close,volume '
                    'FROM aiia_stock_realtime_minute_snapshot_full '
                    "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                    'AND is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time '
                    f'AND stk_code IN ({marks})',(day,*missing))
                conn.rollback()
            if not bars.empty:
                for col in ('open','high','low','close','volume'):
                    bars[col]=pd.to_numeric(bars[col],errors='coerce')
                bars['volume']*=100
                collect(bars,'database_finalized')
        except Exception as exc:
            print('minute fallback failed',day,type(exc).__name__,flush=True)
    result=[rows.get(s,{'d1_date':day,'symbol':s,'minute_complete':0,
                       'minute_reason':'missing_or_price_volume_mismatch'}) for s in demand.symbol]
    dump(cache,result)
    return result


def minute_enriched(f: pd.DataFrame) -> tuple[pd.DataFrame,dict]:
    demand=f[['d1_date','symbol','d1_cap','d1_close','d1_open','d1_volume']].dropna(subset=['d1_date']).drop_duplicates(['d1_date','symbol'])
    found=[]
    groups=list(demand.groupby('d1_date'))
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(one_day,day,g):day for day,g in groups}
        for i,job in enumerate(as_completed(jobs),1):
            found.extend(job.result())
            if i%25==0:
                print('minute days',i,'/',len(groups),flush=True)
    facts=pd.DataFrame(found)
    for col in ('first_close_cap_min','final_close_cap_min','minute_open'):
        if col not in facts:
            facts[col]=np.nan
    facts.to_csv(OUT/'minute_facts.csv.gz',index=False,compression='gzip')
    joined=f.merge(facts[['d1_date','symbol','first_close_cap_min','final_close_cap_min','minute_open',
                          'minute_complete','minute_reason']],on=['d1_date','symbol'],how='left',validate='many_to_one')
    joined['minute_complete']=joined.minute_complete.fillna(0).astype(int)
    joined['minute_reason']=joined.minute_reason.fillna('d1_quote_missing')
    coverage={'unique_stock_days':len(demand),'complete':int(facts.minute_complete.sum()),
              'missing_by_reason':facts[facts.minute_complete.eq(0)].minute_reason.value_counts().to_dict()}
    return joined,coverage


def market_caps(f: pd.DataFrame) -> pd.DataFrame:
    cache=OUT/'market_cap_source.csv.gz'
    requested=OUT/'market_cap_requested_keys.csv.gz'
    keys=f[['symbol','d0_date']].drop_duplicates()
    if cache.exists():
        direct=pd.read_csv(cache,dtype={'symbol':str,'d0_date':str})
        if requested.exists():
            seen=pd.read_csv(requested,dtype={'symbol':str,'d0_date':str})
        elif (OUT/'top5_internal_audit.csv.gz').exists():
            seen=pd.read_csv(OUT/'top5_internal_audit.csv.gz',dtype={'symbol':str,'d0_date':str},
                             usecols=['symbol','d0_date']).drop_duplicates()
        else:
            seen=pd.DataFrame(columns=['symbol','d0_date'])
        needed=keys[~pd.MultiIndex.from_frame(keys).isin(pd.MultiIndex.from_frame(seen))]
    else:
        direct=pd.DataFrame(columns=['symbol','d0_date','total_mv'])
        needed=keys
    if len(needed):
        rows=[]
        with db_connection() as conn:
            for begin in range(0,len(needed),300):
                group=needed.iloc[begin:begin+300]
                args=[]
                for symbol,day in group.itertuples(index=False):
                    suffix='.SH' if symbol.startswith('6') else '.BJ' if symbol.startswith('9') else '.SZ'
                    args.extend([day,symbol+suffix])
                marks=','.join(['(%s,%s)']*len(group))
                raw=query(conn,'SELECT trade_date,stk_code,total_mv FROM kcrp_stock_pricevaluate '
                          f'WHERE (trade_date,stk_code) IN ({marks})',tuple(args))
                if not raw.empty: rows.append(raw)
            conn.rollback()
        if rows:
            raw=pd.concat(rows,ignore_index=True)
            raw['d0_date']=pd.to_datetime(raw.trade_date).dt.strftime('%Y-%m-%d')
            raw['symbol']=raw.stk_code.str[:6]
            direct=pd.concat([direct,raw[['symbol','d0_date','total_mv']]],ignore_index=True)
            direct=direct.drop_duplicates(['symbol','d0_date'],keep='last')
        assert not direct.duplicated(['symbol','d0_date']).any()
        direct.to_csv(cache,index=False,compression='gzip')
    keys.to_csv(requested,index=False,compression='gzip')
    f=f.merge(direct,on=['symbol','d0_date'],how='left',validate='many_to_one')
    history_root=Path('outputs/stock_automl/limit_rebound_st_size_r4_20261014/share_months')
    history=pd.concat([pd.read_csv(p,dtype={'symbol':str,'trade_date':str}) for p in sorted(history_root.glob('*.csv.gz'))],ignore_index=True)
    history=history.sort_values(['symbol','trade_date'])
    lookup={s:(g.trade_date.tolist(),g.total_share.to_numpy(float)) for s,g in history.groupby('symbol')}
    estimates=[]
    for symbol,day,close in zip(f.symbol,f.d0_date,f.close):
        days,shares=lookup.get(symbol,([],[]));pos=bisect_right(days,day)-1
        estimates.append(float(shares[pos]*close) if pos>=0 else np.nan)
    direct_mv=pd.to_numeric(f.total_mv,errors='coerce')
    f['mv_estimated']=~direct_mv.gt(0) & pd.Series(estimates,index=f.index).notna()
    f['market_cap_100m']=direct_mv.where(direct_mv.gt(0),estimates)/1e8
    return f


def run() -> None:
    f=chosen_frames()
    f,coverage=minute_enriched(f)
    f=market_caps(f)
    allrows=[]
    for group in ('chronological','mixed'):
        g=f[f.experiment_group.eq(group)].copy()
        rows=records(g)
        for row,r in zip(rows,g.itertuples(),strict=True):
            row['D0日期']=r.d0_date
            row['集合']={'train':'训练','valid':'验证','test':'测试'}[r.part]
            row['分钟齐全']=int(r.minute_complete)
            anomaly=bool(r.anomaly) or pd.isna(r.d1_open)
            row['异常']=int(anomaly)
            if pd.isna(r.d1_up): row['D1封板']=None
            if pd.isna(r.d1_st): row['当时ST']=None
            if pd.isna(r.d1_open) or pd.isna(r.d1_cap): row['D1开板']=None
            if anomaly:
                for col in ('冷静末涨','D2收涨','D3收涨','D4收涨','策略退出涨'):
                    row[col]=None
            for col,value in row.items():
                if isinstance(value,(float,np.floating)) and not np.isfinite(value): row[col]=None
        dump(OUT/group/'workbook_rows.json',rows)
        allrows.extend(rows)
    audit_cols=['experiment_group','part','model_id','d0_date','d1_date','symbol','d0_rank','score',
                'selected','activated','label_3','observed_3','anomaly','price_anomaly','execution_anomaly',
                'minute_complete','minute_reason','d1_open','d1_close','d2_close','d3_close','d4_close',
                'first_close_cap_min','final_close_cap_min','d1_cap','d1_volume','exit_gross']
    f[audit_cols].to_csv(OUT/'top5_internal_audit.csv.gz',index=False,compression='gzip')
    coverage['parts']={}
    for (group,part),g in f.groupby(['experiment_group','part']):
        chosen=g[g.selected]
        coverage['parts'][group+'_'+part]={'top5_rows':len(g),'selected':len(chosen),
            'minute_complete_top5':int(g.minute_complete.sum()),
            'minute_complete_selected':int(chosen.minute_complete.sum()),
            'market_cap_present':int(g.market_cap_100m.notna().sum()),
            'market_cap_estimated':int(g.mv_estimated.sum()),
            'selected_first_seal_minutes':chosen.loc[chosen.d1_up.eq(True)&chosen.minute_complete.eq(1),'first_close_cap_min'].dropna().describe().to_dict()}
    dump(OUT/'detail_coverage.json',coverage)
    print(json.dumps(coverage,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    run()

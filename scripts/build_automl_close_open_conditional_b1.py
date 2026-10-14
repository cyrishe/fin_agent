"""CO7: preserve frozen B1 inputs and extend the same seven-factor definitions."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.build_automl_tail_standard import prior_profile
from scripts.build_automl_close_open_factor_panel import DATA as SOURCE, FACTORS, CLOCKS, sha
from scripts.experiment_automl_mk_1m_st_policies import make_cohorts, POLICIES

OUT=Path('outputs/stock_automl/close_open_conditional_b1_r1')
REPORT=Path('docs/stock_automl_runs/20261015_close_open_conditional_b1')
LEGACY25=Path('outputs/stock_automl/mk_1m_2025_continuous/cohort/trainable_candidates.csv.gz')
LEGACY26=Path('outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz')
KEY=['signal_date','symbol6']


def adjusted_month(month):
    dest=OUT/'daily_adjusted'/f'{month}.csv.gz'
    if dest.exists():return month,'cached'
    first=pd.Timestamp(month+'-01');end=first+pd.offsets.MonthEnd(0)
    with db_connection() as db:
        d=query(db,'SELECT trade_date AS date,LEFT(stk_code,6) AS symbol6,preclose,close,adjpreclose,adjclose,volume,turn_ratio FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s',(first.strftime('%Y-%m-%d'),end.strftime('%Y-%m-%d')))
    d['date']=pd.to_datetime(d.date).dt.strftime('%Y-%m-%d')
    d['symbol6']=d.symbol6.astype(str).str.zfill(6)
    d.to_csv(dest,index=False,float_format='%.17g')
    return month,len(d)


def prepare():
    OUT.mkdir(parents=True,exist_ok=True);REPORT.mkdir(parents=True,exist_ok=True)
    (OUT/'daily_adjusted').mkdir(exist_ok=True)
    months=pd.period_range('2024-11','2026-10',freq='M').astype(str).tolist()
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(adjusted_month,months):print('daily',result,flush=True)
    raw=pd.concat([pd.read_csv(OUT/'daily_adjusted'/f'{m}.csv.gz',dtype={'symbol6':str},float_precision='round_trip') for m in months],ignore_index=True)
    raw=raw[raw.symbol6.str.startswith(('0','3','6')) & raw.date.le('2026-10-14')].copy()
    raw['date']=pd.to_datetime(raw.date)
    for c in ('preclose','close','adjpreclose','adjclose','volume','turn_ratio'):raw[c]=pd.to_numeric(raw[c],errors='coerce')
    raw['float_share']=raw.volume/(raw.turn_ratio/100);raw['float_mv']=raw.float_share*raw.close
    valid=raw.turn_ratio.gt(0)&raw.volume.gt(0)&raw.close.gt(0)&np.isfinite(raw.float_share)&np.isfinite(raw.float_mv)
    profile,calendar=prior_profile(raw,raw.loc[valid,['date','symbol6','float_share','float_mv']].copy())
    profile['signal_date']=pd.to_datetime(profile.signal_date).dt.strftime('%Y-%m-%d')
    availability=pd.read_csv(SOURCE/'availability.csv').fillna({'local_file':''})
    dates=availability.date.tolist();next_day=dict(zip(dates[:-1],dates[1:]))
    intervals=pd.read_csv(SOURCE/'historical_st.csv.gz',dtype={'symbol6':str})
    # Default CSV parsing reproduces the historical fitting process. The frozen
    # CO7 table below is then saved at 17 significant digits and read round-trip.
    b25=pd.read_csv(LEGACY25,dtype={'symbol6':str})
    b25=make_cohorts(b25,intervals)[0][POLICIES[1]]
    b26=pd.read_csv(LEGACY26,dtype={'symbol6':str})
    legacy=pd.concat([b25.assign(input_source='frozen_2025'),b26.assign(input_source='frozen_B1_2026')],ignore_index=True)
    legacy_days=set(legacy.signal_date)
    panel=pd.read_csv(SOURCE/'factor_panel.csv.gz',dtype={'symbol6':str},float_precision='round_trip')
    # Outcome-independent original candidate snapshots take priority. New dates
    # use the original adjusted-close MA calculation, with actual minute lows.
    new_days=[d for d in dates[:-1] if d not in legacy_days]
    wanted=panel[panel.signal_date.isin(new_days)].copy()
    minima=[]
    for day in new_days:
        dest=OUT/'minima'/f'{day}.csv.gz';dest.parent.mkdir(exist_ok=True)
        if dest.exists():m=pd.read_csv(dest,dtype={'symbol6':str},float_precision='round_trip')
        else:
            path=availability.set_index('date').loc[day,'local_file']
            if path:
                b=pd.read_csv(path,usecols=['symbol','time','low'],dtype={'symbol':str})
                b=b[b.time.str[-8:].isin(CLOCKS)];b['symbol6']=b.symbol.str[:6]
            else:
                b=pd.read_csv(SOURCE/'days'/f'{day}_db_bars.csv.gz',dtype={'symbol6':str},float_precision='round_trip')
                b=b[b.clock.isin(CLOCKS)]
            codes=set(wanted.loc[wanted.signal_date.eq(day),'symbol6'])
            m=b[b.symbol6.isin(codes)].groupby('symbol6').low.min().rename('min_low_so_far').reset_index()
            m.to_csv(dest,index=False,float_format='%.17g')
        minima.append(m.assign(signal_date=day));print('minute lows',day,len(m),flush=True)
    wanted=wanted.merge(pd.concat(minima),on=KEY,validate='one_to_one')
    orig_cols=['signal_date','symbol6','history_complete','adj_price_break_20d','value_complete','float_share','float_mv','prior_adjclose','avg_volume5_shares','volume_4of5_increasing','ma5_adj','ma10_adj','ma20_adj']
    wanted=wanted.drop(columns=['volume_4of5_increasing']).merge(profile[orig_cols],on=KEY,how='left',validate='one_to_one')
    keep=wanted.history_complete.eq(True)&wanted.adj_price_break_20d.eq(False)&wanted.value_complete.eq(True)
    wanted=wanted[keep].copy()
    wanted['volume_ratio']=wanted.minute_volume_shares*240/(220*wanted.avg_volume5_shares)
    wanted['turnover_so_far_pct']=wanted.minute_volume_shares/wanted.float_share*100
    wanted['float_mv_100m_cny']=wanted.float_mv/1e8
    for w in (5,10,20):wanted[f'ma{w}']=wanted[f'ma{w}_adj']*wanted.preclose/wanted.prior_adjclose
    wanted['ma_bull_5_10_20']=(wanted.ma5>wanted.ma10)&(wanted.ma10>wanted.ma20)
    wanted['all_intraday_lows_above_ma']=wanted.min_low_so_far>wanted[['ma5','ma10','ma20']].max(axis=1)
    wanted['input_source']='B1_definitions_extended'
    wanted['stage1_second_high']=wanted.second_high
    legacy['stage1_second_high']=legacy.second_high
    # Last archived day had no next-day file. Filling only that terminal label
    # cannot affect any prior frozen prediction, and is needed for later folds.
    for last in (b25.signal_date.max(),b26.signal_date.max()):
        keys=legacy.signal_date.eq(last)
        mapping=panel[panel.signal_date.eq(last)].set_index('symbol6').second_high
        legacy.loc[keys,'stage1_second_high']=legacy.loc[keys,'stage1_second_high'].fillna(legacy.loc[keys,'symbol6'].map(mapping))
    basecols=KEY+list(FEATURES)+['signal_price','stage1_second_high','input_source']
    inputs=pd.concat([legacy[basecols],wanted[basecols]],ignore_index=True).sort_values(KEY).reset_index(drop=True)
    inputs['next_date']=inputs.signal_date.map(next_day)
    for c in FEATURES:inputs[c]=inputs[c].astype(float)
    assert not inputs.duplicated(KEY).any() and inputs[list(FEATURES)].notna().all().all()
    # All extra features and execution labels join only after base pool selection.
    extra=[c for c in FACTORS if c not in FEATURES]
    inputs=inputs.merge(panel[KEY+extra+['second_high','max_high','close40','strong','weak','close40_return','next_corporate_action']],on=KEY,how='left',validate='one_to_one',indicator=True)
    missing=inputs[inputs._merge.ne('both')][KEY+['input_source']]
    missing.to_csv(REPORT/'missing_context.csv',index=False)
    inputs=inputs.drop(columns='_merge')
    # Preserve available original execution outcomes for keys missing CO4 context.
    oldout=legacy.set_index(KEY)
    ix=pd.MultiIndex.from_frame(inputs[KEY])
    oldclose=oldout.close_40.reindex(ix).to_numpy()
    oldsecond=oldout.second_high.reindex(ix).to_numpy()
    inputs['close40']=inputs.close40.fillna(pd.Series(oldclose,index=inputs.index))
    inputs['second_high']=inputs.second_high.fillna(pd.Series(oldsecond,index=inputs.index))
    inputs['close40_return']=inputs.close40/inputs.signal_price-1
    inputs['strong']=np.where(inputs.second_high.notna(),inputs.second_high>=inputs.signal_price*1.03-1e-10,np.nan)
    inputs['weak']=np.where(inputs.max_high.notna(),inputs.max_high<inputs.signal_price-1e-10,np.nan)
    inputs.insert(0,'sample_id',inputs.index)
    inputs.to_csv(OUT/'b1_candidates.csv.gz',index=False,float_format='%.17g')
    pd.DataFrame({'date':dates}).to_csv(OUT/'calendar.csv',index=False)
    manifest={'rows':len(inputs),'dates':inputs.signal_date.nunique(),'source_counts':inputs.groupby('input_source').size().to_dict(),
              'missing_co4_context':len(missing),'candidates_sha256':sha(OUT/'b1_candidates.csv.gz'),
              'original_B1_sha256':sha(LEGACY26),'legacy25_sha256':sha(LEGACY25),'co4_sha256':sha(SOURCE/'factor_panel.csv.gz'),
              'notes':'Frozen historical input parsing preserved. Extended dates reuse CO4 minute completeness/pool and original B1 adjusted-close MA/profile definition; not an archived original prediction.',
              'daily_snapshots':[{'path':str(p),'sha256':sha(p)} for p in sorted((OUT/'daily_adjusted').glob('*.gz'))]}
    (OUT/'input_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('COMPLETE', {k:v for k,v in manifest.items() if k!='daily_snapshots'},flush=True)


if __name__=='__main__':prepare()

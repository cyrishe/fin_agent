"""D2 opening-floor cohort: D2 close attempt, then D3 close/rise exits.

Unfilled stocks remain positions. No full strategy account or queue-fill claim.
"""
import argparse,gzip,io,json
from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np,pandas as pd,zstandard
from scripts import research_b2_floor_risk_20261021 as old

ROOT=old.ROOT;OUT=ROOT/'docs/stock_automl_runs/20261022_b2_delayed_floor_exit'
PRIOR=ROOT/'docs/stock_automl_runs/20261022_b2_risk_actions';FEE=.001;TICKET=10000.
POLICIES={
 'all_d3_close':'全部等到D3收盘',
 'd2_then_d3_close':'D2能收盘退出就退出，其余等D3收盘',
 'd2_then_above_previous':'D2能退先退，其余D3涨过昨收就退出',
 'd2_then_above_open':'D2能退先退，其余D3涨过开盘就退出',
 'd2_then_uptick':'D2能退先退，其余D3出现一分钟回升就退出',
 'all_above_previous':'全部等到D3，涨过昨收就退出'}

def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [clean(v) for v in x]
    if isinstance(x,(np.integer,np.bool_)):return x.item()
    if isinstance(x,(np.floating,float)):return float(x) if np.isfinite(x) else None
    return x
def write(name,x):(OUT/name).write_text(json.dumps(clean(x),ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def net(price,buy):return price/buy*(1-FEE)/(1+FEE)-1

def cohort():
    f=old.load();f=f[f.open_floor.eq('是')].copy()
    calendar=json.loads((ROOT/'outputs/stock_automl/b2_2021_minute_r60_20261020/calendar.json').read_text());index={d:i for i,d in enumerate(calendar)}
    f['d3_date']=[calendar[index[d]+1] for d in f['卖出日']]
    assert len(f)==34 and f.close_floor.eq('是').sum()==19
    return f

def get_local(row):
    day,sym=row['d3_date'],row['symbol'];dest=OUT/'minutes'/f'{day}_{sym}.csv.gz'
    if dest.exists():return dict(sequence=row['sequence'],path=str(dest),sha256=old.sha(dest),source='existing snapshot')
    sources=[(Path('/Volumes/ext/tushare_market_data')/day[:4]/f'{day.replace("-","")}.csv.gz','tushare'),
       (ROOT/'outputs/stock_automl'/('mk_1m_compact_2025' if day[:4]=='2025' else 'mk_1m_compact')/f'{day}.csv.zst','mk')]
    for source,kind in sources:
        if not source.exists():continue
        opener=gzip.open if kind=='tushare' else zstandard.open;lines=[]
        with io.BufferedReader(opener(source,'rb')) as stream:
            header=stream.readline()
            for line in stream:
                code=line[:6] if kind=='tushare' else line.split(b',',2)[1][:6]
                if code.decode()==sym:lines.append(line)
        if not lines:continue
        b=pd.read_csv(io.BytesIO(header+b''.join(lines)))
        if kind=='tushare':
            from scripts.import_tushare_minutes_2024 import convert
            b=convert(b,day)
        b.to_csv(dest,index=False,compression='gzip')
        return dict(sequence=row['sequence'],path=str(dest),sha256=old.sha(dest),source=str(source),source_sha256=old.sha(source))
    # Known prior caches already normalize database lots to shares.
    for directory in ['limit_rebound_shape_top3_r20_20261015','limit_rebound_shape_r19_20261015']:
        source=ROOT/'outputs/stock_automl'/directory/'minute_days'/f'{day}_raw.pkl'
        if not source.exists():continue
        b=pd.read_pickle(source);b=b[b.symbol.astype(str).str[:6].eq(sym)]
        for _,g in b.groupby('source'):
            if len(g)==240:
                g.to_csv(dest,index=False,compression='gzip')
                return dict(sequence=row['sequence'],path=str(dest),sha256=old.sha(dest),source=str(source),source_sha256=old.sha(source))
    return dict(sequence=row['sequence'],missing=True,day=day,symbol=sym)

def prepare():
    OUT.mkdir(exist_ok=True,parents=True);(OUT/'minutes').mkdir(exist_ok=True);f=cohort();audit=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(get_local,x) for x in f.to_dict('records')]
        for future in as_completed(futures):
            audit.append(future.result())
            if len(audit)%10==0:print('local',len(audit),flush=True)
    from scripts.benchmark_automl_1440_inference import db_connection,query
    with db_connection() as conn:
        path=OUT/'d3_daily.csv.gz'
        if not path.exists():
            keys=list(zip(f.stk_code,f.d3_date));marks=','.join(['(%s,%s)']*len(keys))
            data=query(conn,f'SELECT stk_code,trade_date,preclose,open,high,low,close,volume,amount,is_limit_price,update_time FROM kcrp_stock_price WHERE (stk_code,trade_date) IN ({marks})',tuple(v for pair in keys for v in pair))
            assert len(data)==34;data.to_csv(path,index=False,compression='gzip');print('daily',len(data),flush=True)
        corporate_path=OUT/'corporate_action.csv'
        if not corporate_path.exists():
            prices=pd.read_csv(path).set_index(['stk_code','trade_date'])
            mismatch=[(x.stk_code,x.d3_date) for x in f.itertuples() if not np.isclose(prices.loc[(x.stk_code,x.d3_date),'preclose'],x.d2_close)]
            assert mismatch,'No adjustment requires a corporate-action snapshot in this fixed cohort'
            marks=','.join(['(%s,%s)']*len(mismatch))
            actions=query(conn,f'SELECT stk_code,ann_date,record_date,ex_date,div_progress,per_shareqty,div_cash_pre_tax,div_cash_after_tax,div_payout_date,div_conversed_rate,div_bonus_rate FROM kcrp_stock_dividend WHERE (stk_code,ex_date) IN ({marks})',tuple(v for pair in mismatch for v in pair))
            actions.to_csv(corporate_path,index=False)
        for x in audit:
            if not x.get('missing'):continue
            b=query(conn,"SELECT stk_code AS symbol,bar_end_time AS time,open_price AS open,high_price AS high,low_price AS low,latest_price AS close,volume FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s AND stk_code=%s AND kline_type='1m' AND period_minutes=1 AND is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time ORDER BY bar_end_time",(x['day'],x['symbol']))
            assert len(b)==240,(x,len(b));b.volume=pd.to_numeric(b.volume)*100
            dest=OUT/'minutes'/f"{x['day']}_{x['symbol']}.csv.gz";b.to_csv(dest,index=False,compression='gzip')
            x.update(missing=False,path=str(dest),sha256=old.sha(dest),source='finalized database minutes; volume lots*100');print('DB',x['day'],x['symbol'],flush=True)
        conn.rollback()
    write('minute_sources.json',audit)

def exit_d3(b,floor,reference,kind):
    """Observe completed bar, then try future bar open; a signal stays active."""
    signal=None
    for i,q in b.iterrows():
        if signal is not None and i>signal and q.open>floor+.001 and q.volume>0:
            return dict(filled=True,day='D3',clock=str(q.time)[11:16],price=float(q.open),reason='rise_then_next_eligible_open',
                        trigger_clock=str(b.time.iloc[signal])[11:16],trigger_reference=float(reference_at_signal))
        if kind!='close' and signal is None:
            ref=(float(b.open.iloc[0]) if i==0 else float(b.close.iloc[i-1])) if kind=='uptick' else reference
            if q.close>ref+.001:signal=int(i);reference_at_signal=ref
    # A preplanned closing attempt; do not assume a fill while at the floor.
    last=b.iloc[-1]
    if last.close>floor+.001 and last.volume>0:
        return dict(filled=True,day='D3',clock='15:00',price=float(last.close),reason='closing_attempt',
                    trigger_clock=str(b.time.iloc[signal])[11:16] if signal is not None else None)
    return dict(filled=False,day=None,clock=None,price=None,reason='unfilled_at_D3_close',
                trigger_clock=str(b.time.iloc[signal])[11:16] if signal is not None else None)

def run():
    f=cohort();daily=pd.read_csv(OUT/'d3_daily.csv.gz').set_index(['stk_code','trade_date'])
    corporate=pd.read_csv(OUT/'corporate_action.csv')
    stpath=ROOT/'docs/stock_automl_runs/20261021_b2_floor_factor_review/st.csv.gz';st=pd.read_csv(stpath).fillna({'end_date':'9999-12-31'})
    audit=json.loads((OUT/'minute_sources.json').read_text());sources={x['sequence']:x for x in audit}
    rows=[];evidence=[]
    for r in f.to_dict('records'):
        a=sources[r['sequence']];assert old.sha(a['path'])==a['sha256']
        b=pd.read_csv(a['path']).sort_values('time').reset_index(drop=True);d=daily.loc[(r['stk_code'],r['d3_date'])]
        p2=PRIOR/'minutes'/f"{r['卖出日']}_{r['symbol']}.csv.gz";b2=pd.read_csv(p2).sort_values('time').reset_index(drop=True)
        assert len(b)==240 and b.time.is_unique and b.time.iloc[0][11:16]=='09:31' and b.time.iloc[-1][11:16]=='15:00'
        assert np.allclose([b.open.iloc[0],b.high.max(),b.low.min(),b.close.iloc[-1]],d[['open','high','low','close']].astype(float),atol=.011,rtol=0)
        assert np.isclose(b.volume.sum(),d.volume,rtol=.002)
        assert np.isclose(d.close,r['d3_close'])
        cash_per_share=0.
        if not np.isclose(d.preclose,r['d2_close']):
            action=corporate[corporate.stk_code.eq(r['stk_code'])&corporate.ex_date.eq(r['d3_date'])]
            assert len(action)==1 and action.iloc[0].record_date==r['卖出日']
            assert action[['div_bonus_rate','div_conversed_rate']].fillna(0).to_numpy().sum()==0
            cash_per_share=float(action.iloc[0].div_cash_pre_tax)
            assert np.isclose(r['d2_close']-d.preclose,cash_per_share,atol=.001)
        status=st[st.stk_code.eq(r['stk_code'])&st.begin_date.le(r['d3_date'])&st.end_date.gt(r['d3_date'])];assert len(status)==1
        rate=.2 if r['symbol'].startswith(('30','68')) else (.05 if status.iloc[0].st_type in ('S','Y') else .1)
        floor=float((Decimal(str(d.preclose))*(1-Decimal(str(rate)))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        assert (abs(d.close-floor)<.001)==(d.is_limit_price==2)
        d2_available=bool(b2.close.iloc[-1]>r['d2_floor']+.001 and b2.volume.iloc[-1]>0)
        assert d2_available==(r['close_floor']=='否')
        evidence.append(dict(sequence=r['sequence'],symbol=r['symbol'],d3_date=r['d3_date'],bars=240,OHLC_match=True,
            volume_ratio=b.volume.sum()/d.volume,d3_floor=floor,d3_open=d.open,d3_high=d.high,d3_low=d.low,d3_close=d.close,
            d3_return=d.close/d.preclose-1,d3_raw_return=d.close/r['d2_close']-1,
            d3_economic_return=(d.close+cash_per_share)/r['d2_close']-1,
            dividend_per_share=cash_per_share,d3_open_return=d.open/d.preclose-1,d2_available=d2_available,
            d3_close_floor=abs(d.close-floor)<.001,d3_all_locked=b.high.max()<=floor+.001,d2_minutes_sha256=old.sha(p2)))
        for policy in POLICIES:
            if policy.startswith('d2_') and d2_available:
                ex=dict(filled=True,day='D2',clock='15:00',price=r['d2_close'],reason='D2_closing_attempt',trigger_clock=None)
            else:
                kind='close' if policy.endswith('_close') else 'uptick' if policy.endswith('uptick') else 'rise'
                ref=float(b.open.iloc[0]) if policy.endswith('above_open') else float(d.preclose)
                ex=exit_d3(b,floor,ref,kind)
            shares=TICKET/(1+FEE)/r['entry']
            dividend_cash=shares*cash_per_share if ex['day']!='D2' else 0.
            realized=shares*ex['price']*(1-FEE) if ex['filled'] else 0.
            marked=0. if ex['filled'] else shares*d.close
            liquidation=realized if ex['filled'] else marked*(1-FEE)
            rows.append(dict(policy=policy,sequence=r['sequence'],symbol=r['symbol'],entry=r['entry'],D0=r['d0_date'],D2=r['卖出日'],D3=r['d3_date'],
                d2_available=d2_available,d2_open=r['d2_open'],d2_close=r['d2_close'],d3_open=float(d.open),d3_close=float(d.close),d3_floor=floor,
                d2_open_hyp_net=r['open_net'],d2_close_hyp_net=r['original_net'],d3_close_hyp_net=net(d.close,r['entry']),
                **ex,realized_proceeds=realized,dividend_cash=dividend_cash,open_market_value=marked,
                marked_return=(realized+dividend_cash+marked)/TICKET-1,
                liquidation_return=(liquidation+dividend_cash)/TICKET-1,
                realized_trade_return=(realized+dividend_cash)/TICKET-1 if ex['filled'] else None,
                delta_to_open_value=realized+dividend_cash+marked-TICKET*(1+r['open_net'])))
    detail=pd.DataFrame(rows);ev=pd.DataFrame(evidence);summaries=[]
    for group,ids in [('全部34笔',set(f.sequence)),('D2收盘未退出19笔',set(ev.loc[~ev.d2_available,'sequence']))]:
        for policy,g in detail[detail.sequence.isin(ids)].groupby('policy',sort=False):
            n=len(g);closed=g[g.filled];pending=g[~g.filled]
            summaries.append(dict(group=group,policy=policy,n=n,d2_exits=int(g.day.eq('D2').sum()),d3_exits=int(g.day.eq('D3').sum()),unfilled=len(pending),
                rise_exits=int(g.reason.eq('rise_then_next_eligible_open').sum()),mean_marked_return=g.marked_return.mean(),
                mean_liquidation_return=g.liquidation_return.mean(),mean_realized_on_closed=closed.realized_trade_return.mean(),
                mean_change_to_open=g.marked_return.mean()-g.d2_open_hyp_net.mean(),total_value=g.realized_proceeds.sum()+g.dividend_cash.sum()+g.open_market_value.sum(),
                realized_proceeds=g.realized_proceeds.sum(),dividend_cash=g.dividend_cash.sum(),open_market_value=g.open_market_value.sum(),
                delta_to_open_value=g.delta_to_open_value.sum(),worst_marked=g.marked_return.min(),
                mean_hyp_d2_open=g.d2_open_hyp_net.mean(),mean_hyp_d2_close=g.d2_close_hyp_net.mean(),mean_hyp_d3_close=g.d3_close_hyp_net.mean()))
    result=dict(experiment='B2_DELAYED_FLOOR_EXIT_20261022',cohort=34,d2_closing_available=int(ev.d2_available.sum()),d2_deferred=int((~ev.d2_available).sum()),
      delayed_d3_stats=dict(n=19,positive_close=int((ev.loc[~ev.d2_available,'d3_return']>0).sum()),mean_close_return=float(ev.loc[~ev.d2_available,'d3_return'].mean()),
        mean_raw_close_return=float(ev.loc[~ev.d2_available,'d3_raw_return'].mean()),mean_economic_return=float(ev.loc[~ev.d2_available,'d3_economic_return'].mean()),
        close_floor=int(ev.loc[~ev.d2_available,'d3_close_floor'].sum()),all_day_locked=int(ev.loc[~ev.d2_available,'d3_all_locked'].sum())),
      summaries=summaries,policies=POLICIES,
      accounting='Independent 10000 CNY tickets, buy/sell 0.1% each. Filled exit cash; unfilled marked at D3 close with no sale fee. One confirmed cash dividend 0.07/share is included before personal dividend tax; no reinvestment. Liquidation column deducts hypothetical future sale fee only for comparison. No account reinvestment.',
      execution='D2/D3 closing proxy requires close>floor and final bar volume>0. Rise means completed minute close>reference by .001, then next eligible bar open>floor with volume>0; persistent order. No floor queue fill assumed. Unfilled remains held.',
      boundaries=['Minute liquidity proxies cannot guarantee fill or capacity.','D3 opening gap above reference waits for first completed minute; no auction fill assumed.',
       'No horizon censoring: all 34 retained, unfilled explicitly marked. No realized performance claim for marked basket.',
       'Trade cohort and policy comparison already researched, no prospective validation or strategy changes.'],
      sources={str(old.SOURCE):old.sha(old.SOURCE),str(OUT/'d3_daily.csv.gz'):old.sha(OUT/'d3_daily.csv.gz'),str(OUT/'corporate_action.csv'):old.sha(OUT/'corporate_action.csv'),str(stpath):old.sha(stpath)},code_sha256=old.sha(__file__))
    write('results.json',result);write('minute_audit.json',evidence)
    detail.to_csv(OUT/'逐笔退出与未卖出.csv',index=False,encoding='utf-8-sig');ev.to_csv(OUT/'D3量价及封板.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(summaries).to_csv(OUT/'方案汇总.csv',index=False,encoding='utf-8-sig')
    print(json.dumps(clean(result),ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run']);a=p.parse_args();{'prepare':prepare,'run':run}[a.stage]()

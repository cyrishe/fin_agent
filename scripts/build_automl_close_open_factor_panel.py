"""CO4: as-of daily/minute factors and independently observed next-morning labels."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_st_status import load_st_intervals
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_tail_standard import four_of_five_volume_increasing
from scripts.experiment_automl_mk_1m_st_policies import make_cohorts, POLICIES
from scripts.mk_minute_archive import COMPACT_ROOT, COMPACT_ROOT_2025, day_files, trading_day

DATA = Path('outputs/stock_automl/close_open_factor_discovery_r1')
REPORT = Path('docs/stock_automl_runs/20261014_close_open_factor_discovery')
DAILY = Path('outputs/stock_automl/limit_rebound_2y_r1/price_months')
CLOCKS = tuple(pd.date_range('09:31', periods=120, freq='min').strftime('%H:%M:%S')) + tuple(
    pd.date_range('13:01', periods=100, freq='min').strftime('%H:%M:%S'))
WINDOWS = (5, 15, 30, 60)
BASE = {
    'signal_return': '14:40 close / 当日前收 - 1',
    'volume_ratio': '截至14:40股数 × 240 / (220 × 前五日日均股数)',
    'turnover_so_far_pct': '截至14:40股数 / 昨日流通股本代理 × 100',
    'float_mv_100m_cny': '昨日流通股本代理 × 昨收 / 1亿',
    'volume_4of5_increasing': '前五日成交量是否存在按时间递增的四项子序列',
    'ma_bull_5_10_20': '截至昨日的MA5 > MA10 > MA20',
    'all_intraday_lows_above_ma': '截至14:40最低价 > 三条历史均线最大值',
}
HISTORY = {
    'open_gap': '当天首根分钟open / 当日前收 - 1',
    'from_open': '14:40 close / 当天首根分钟open - 1',
    'lag_return': '昨日close / 昨日preclose - 1',
    'lag_close_position': '昨日(close-low)/(high-low)，平盘为0.5',
    'lag_upper_shadow': '昨日(high-max(open,close))/preclose',
    'prior5_return': '前5个交易日close/preclose的连乘 - 1',
    'prior10_return': '前10个交易日close/preclose的连乘 - 1',
    'prior20_return': '前20个交易日close/preclose的连乘 - 1',
    'prior5_volatility': '前5个交易日日收益标准差',
    'prior20_volatility': '前20个交易日日收益标准差',
    'prior5_range': '前5个交易日(high-low)/preclose的均值',
    'prior5_contract_fraction': '前5日成交量相对前一日缩量的比例',
    'relative_turnover5': '截至14:40换手率 / 前5日日均换手率',
    'relative_turnover20': '截至14:40换手率 / 前20日日均换手率',
    'distance_ma5': '14:40价 / 昨日MA5换算到当日未复权尺度 - 1',
    'distance_ma20': '14:40价 / 昨日MA20换算到当日未复权尺度 - 1',
    'ma5_over_ma10': '历史MA5/MA10 - 1',
    'ma10_over_ma20': '历史MA10/MA20 - 1',
}
PATH = {
    'return': '窗口末close/窗口首open - 1',
    'range': '(窗口最高high-最低low)/当日前收',
    'efficiency': '窗口净对数涨幅/逐分钟close路径绝对对数变化总和；首点为窗口open',
    'realized_vol': '逐分钟close路径对数收益平方和开方；首点为窗口open',
    'close_position': '(末close-窗口最低low)/(窗口最高high-最低low)，平盘0.5',
    'up_volume_fraction': 'close高于前一分钟close的分钟成交量占比；首点对窗口open',
    'volume_fraction': '窗口成交量/截至14:40总成交量',
    'vwap_distance': '窗口末close / (窗口成交额/成交股数) - 1',
    'volume_acceleration': '窗口后一半每分钟均量 / 前一半每分钟均量 - 1',
}
EXTRA = {
    'day_max_drawdown': '截至14:40 close相对先前close累计最高值的最小收益',
    'day_high_age': '截至14:40距最后一次最高high的交易分钟数 / 220',
    'day_low_age': '截至14:40距最后一次最低low的交易分钟数 / 220',
    'day_volume_hhi': '截至14:40各分钟成交量占比平方和',
    'day_up_fraction': '截至14:40上涨分钟数/220；首分钟对open',
    'morning_return': '11:30 close/09:31 open - 1',
    'afternoon_return': '14:40 close/13:01 open - 1',
    'afternoon_volume_fraction': '13:01至14:40量/截至14:40总量',
}
FACTORS = tuple(BASE) + tuple(HISTORY) + tuple(
    f'day_{name}' for name in PATH if name not in ('return', 'volume_fraction')) + tuple(EXTRA) + tuple(
    f'last{w}_{name}' for w in WINDOWS for name in PATH)
BINARY = set(tuple(BASE)[4:])


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def dictionary() -> pd.DataFrame:
    rows = [{'factor': k, 'group': group, 'formula': text, 'latest_input': 'T 14:40'}
            for group, values in [('original7', BASE), ('history', HISTORY), ('path', EXTRA)]
            for k, text in values.items()]
    rows += [{'factor': f'{prefix}_{k}', 'group': 'intraday' if prefix == 'day' else 'late',
              'formula': v, 'latest_input': 'T 14:40'}
             for prefix in ('day', *(f'last{w}' for w in WINDOWS)) for k, v in PATH.items()
             if prefix != 'day' or k not in ('return', 'volume_fraction')]
    return pd.DataFrame(rows).set_index('factor').loc[list(FACTORS)].reset_index()


def historical_features(raw: pd.DataFrame) -> pd.DataFrame:
    """Current/future daily OHLC never enter the current day's feature columns."""
    d = raw.copy()
    d['symbol6'] = d.stk_code.str[:6]
    d = d[d.symbol6.str.startswith(('0', '3', '6'))].sort_values(['symbol6', 'trade_date'])
    if d.duplicated(['symbol6', 'trade_date']).any():
        raise ValueError('Duplicate daily keys')
    for c in ('open', 'high', 'low', 'close', 'preclose', 'volume', 'turn_ratio'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    calendar = sorted(d.trade_date.unique())
    d['day_i'] = d.trade_date.map(dict(zip(calendar, range(len(calendar)))))
    d['ret'] = d.close/d.preclose - 1
    d['logret'] = np.log1p(d.ret)
    # Forward chaining uses only information already observed. Absolute adjusted
    # scales cancel when the historical MA is converted to today's preclose.
    d['chain_close'] = np.exp(d.groupby('symbol6').logret.cumsum())
    d['range_raw'] = (d.high-d.low)/d.preclose
    d['position_raw'] = ((d.close-d.low)/(d.high-d.low)).where(d.high.ne(d.low), .5)
    d['shadow_raw'] = (d.high-d[['open', 'close']].max(axis=1))/d.preclose
    d['share_raw'] = d.volume/(d.turn_ratio/100)
    d['mv_raw'] = d.share_raw*d.close
    g = d.groupby('symbol6', sort=False)
    d['contract_raw'] = d.volume.lt(g.volume.shift()).astype(float)
    for name, source in [('lag_return', 'ret'), ('lag_close_position', 'position_raw'),
                         ('lag_upper_shadow', 'shadow_raw'), ('prior_float_share', 'share_raw'),
                         ('prior_float_mv', 'mv_raw'), ('prior_chain_close', 'chain_close')]:
        d[name] = g[source].shift()
    for w in (5, 10, 20):
        d[f'prior{w}_return'] = np.expm1(g.logret.transform(lambda x: x.shift().rolling(w).sum()))
        d[f'ma{w}'] = g.chain_close.transform(lambda x: x.shift().rolling(w).mean()) * d.preclose/d.prior_chain_close
    for w in (5, 20):
        d[f'prior{w}_volatility'] = g.ret.transform(lambda x: x.shift().rolling(w).std(ddof=0))
        d[f'prior_turnover{w}'] = g.turn_ratio.transform(lambda x: x.shift().rolling(w).mean())
    d['avg_volume5_shares'] = g.volume.transform(lambda x: x.shift().rolling(5).mean())
    d['prior5_range'] = g.range_raw.transform(lambda x: x.shift().rolling(5).mean())
    d['prior5_contract_fraction'] = d.groupby('symbol6').contract_raw.transform(lambda x: x.shift().rolling(5).mean())
    for lag in range(1, 6):
        d[f'volume_tminus{lag}'] = g.volume.shift(lag)
    d['volume_4of5_increasing'] = four_of_five_volume_increasing(d).astype(float)
    d['history_complete'] = d.day_i.sub(g.day_i.shift(20)).eq(20)
    d['next_date'] = d.trade_date.map(dict(zip(calendar[:-1], calendar[1:])))
    d['next_preclose'] = g.preclose.shift(-1)
    d['next_corporate_action'] = np.where(d.next_preclose.notna(),
                                        d.next_preclose.div(d.close).sub(1).abs().gt(.001), np.nan)
    keep = ['trade_date', 'symbol6', 'preclose', 'next_date', 'history_complete', 'next_corporate_action',
            'prior_float_share', 'prior_float_mv', 'avg_volume5_shares', 'volume_4of5_increasing',
            'ma5', 'ma10', 'ma20', 'prior_turnover5', 'prior_turnover20']
    keep += [k for k in HISTORY if k.startswith('lag_') or k.startswith('prior')]
    return d[keep].rename(columns={'trade_date': 'signal_date'}).reset_index(drop=True)


def path_values(a: np.ndarray, preclose: float, total_volume: float) -> dict:
    """Columns are open, high, low, close, volume, turnover in raw currency/shares."""
    o, h, l, c, v, amount = a.T
    r = np.diff(np.log(np.r_[o[0], c]))
    span = h.max()-l.min()
    half = len(v)//2
    vwap = amount.sum()/v.sum() if v.sum() > 0 else np.nan
    return {'return': c[-1]/o[0]-1, 'range': span/preclose,
            'efficiency': r.sum()/np.abs(r).sum() if np.abs(r).sum() > 0 else 0.,
            'realized_vol': np.sqrt(np.square(r).sum()),
            'close_position': (c[-1]-l.min())/span if span > 0 else .5,
            'up_volume_fraction': v[r > 0].sum()/v.sum() if v.sum() > 0 else np.nan,
            'volume_fraction': v.sum()/total_volume if total_volume > 0 else np.nan,
            'vwap_distance': c[-1]/vwap-1 if vwap > 0 else np.nan,
            'volume_acceleration': v[half:].mean()/v[:half].mean()-1 if v[:half].mean() > 0 else np.nan}


def extract_features(stock: pd.DataFrame, item: pd.Series) -> dict | None:
    s = stock[stock.clock.isin(CLOCKS)].sort_values('clock')
    if tuple(s.clock) != CLOCKS:
        return None
    a = s[['open', 'high', 'low', 'close', 'volume', 'turnover']].to_numpy(float)
    o, h, l, c, v, amount = a.T
    if (not np.isfinite(a).all() or np.any(a[:, :4] <= 0) or np.any(v < 0) or
            np.any(amount < 0) or np.any(h < np.maximum(o, c)) or np.any(l > np.minimum(o, c)) or
            v.sum() <= 0 or not s.valid.all()):
        return None
    required = [item.prior_float_share,item.prior_float_mv,item.avg_volume5_shares,
                item.preclose,item.ma5,item.ma10,item.ma20]
    if not np.isfinite(required).all() or min(required) <= 0:
        return None
    turnover = v.sum()/item.prior_float_share*100
    out = {'signal_price': c[-1], 'signal_return': c[-1]/item.preclose-1,
           'minute_volume_shares': v.sum(), 'volume_ratio': v.sum()*240/(220*item.avg_volume5_shares),
           'turnover_so_far_pct': turnover, 'float_mv_100m_cny': item.prior_float_mv/1e8,
           'volume_4of5_increasing': item.volume_4of5_increasing,
           'ma_bull_5_10_20': float(item.ma5 > item.ma10 > item.ma20),
           'all_intraday_lows_above_ma': float(l.min() > max(item.ma5,item.ma10,item.ma20)),
           'open_gap': o[0]/item.preclose-1, 'from_open': c[-1]/o[0]-1,
           'relative_turnover5': turnover/item.prior_turnover5 if item.prior_turnover5 > 0 else np.nan,
           'relative_turnover20': turnover/item.prior_turnover20 if item.prior_turnover20 > 0 else np.nan,
           'distance_ma5': c[-1]/item.ma5-1, 'distance_ma20': c[-1]/item.ma20-1,
           'ma5_over_ma10': item.ma5/item.ma10-1, 'ma10_over_ma20': item.ma10/item.ma20-1}
    out.update({k: getattr(item,k) for k in HISTORY if k.startswith(('lag_', 'prior'))})
    out.update({f'day_{k}': value for k,value in path_values(a,item.preclose,v.sum()).items()
                if k not in ('return','volume_fraction')})
    out.update({'day_max_drawdown': np.min(c/np.maximum.accumulate(np.r_[o[0],c])[1:]-1),
                'day_high_age': (len(h)-1-np.flatnonzero(h == h.max())[-1])/220,
                'day_low_age': (len(l)-1-np.flatnonzero(l == l.min())[-1])/220,
                'day_volume_hhi': np.square(v/v.sum()).sum(),
                'day_up_fraction': np.mean(np.diff(np.r_[o[0],c]) > 0),
                'morning_return': c[119]/o[0]-1, 'afternoon_return': c[-1]/o[120]-1,
                'afternoon_volume_fraction': v[120:].sum()/v.sum()})
    for w in WINDOWS:
        out.update({f'last{w}_{k}': value for k,value in path_values(a[-w:],item.preclose,v.sum()).items()})
    return out


def morning_frame(bars: pd.DataFrame, day: str) -> pd.DataFrame:
    m = bars[bars.clock.isin(CLOCKS[:10])].copy()
    counts = m.groupby('symbol6').agg(n=('clock','size'), unique=('clock','nunique'),
                                      valid=('valid','all'), volume=('volume','sum'))
    bad = (~np.isfinite(m[['open','high','low','close']].to_numpy()).all(axis=1) |
           ~m[['open','high','low','close']].gt(0).all(axis=1) |
           m.high.lt(m[['open','close']].max(axis=1)) | m.low.gt(m[['open','close']].min(axis=1)))
    keys = counts.index[counts.n.eq(10) & counts.unique.eq(10) & counts.valid & counts.volume.gt(0)]
    keys = keys.difference(m.loc[bad,'symbol6'])
    m = m[m.symbol6.isin(keys)]
    highs = m.sort_values(['symbol6','high'],ascending=[True,False])
    second = highs.groupby('symbol6').nth(1).set_index('symbol6').high
    maximum = m.groupby('symbol6').high.max()
    close40 = m[m.clock.eq('09:40:00')].set_index('symbol6').close
    return pd.DataFrame({'second_high':second,'max_high':maximum,'close40':close40}).reset_index().assign(next_date=day)


def add_labels(frame: pd.DataFrame) -> pd.DataFrame:
    d = frame.copy()
    complete = d[['second_high','max_high','close40']].notna().all(axis=1)
    d['strong'] = np.where(complete, d.second_high.ge(d.signal_price*1.03 - 1e-10), np.nan)
    d['weak'] = np.where(complete, d.max_high.lt(d.signal_price - 1e-10), np.nan)
    d['close40_return'] = d.close40/d.signal_price-1
    return d


def normalize_bars(b: pd.DataFrame, db: bool = False) -> pd.DataFrame:
    if db:
        b = b.rename(columns={'bar_end_time':'time','stk_code':'symbol','open_price':'open',
                              'high_price':'high','low_price':'low','latest_price':'close','amount':'turnover'})
        if b.empty:
            return pd.DataFrame(columns=['symbol6','clock','open','high','low','close','volume','turnover','valid'])
        b['valid'] = (b.is_finalized.eq(1) & b.is_fallback.eq(0) &
                      pd.to_datetime(b.source_snapshot_time).eq(pd.to_datetime(b.time)))
        b['volume'] = pd.to_numeric(b.volume)*100
    else:
        b['valid'] = True
    b['symbol6'] = b.symbol.astype(str).str[:6]
    b['clock'] = b.time.astype(str).str[-8:]
    for c in ('open','high','low','close','volume','turnover'):
        b[c] = pd.to_numeric(b[c],errors='coerce')
    return b


def db_minutes(db, day: str, end: str, symbols: list[str] | None = None) -> pd.DataFrame:
    cols = ('stk_code,bar_end_time,open_price,high_price,low_price,latest_price,volume,amount,'
            'is_finalized,is_fallback,source_snapshot_time')
    sql = (f'SELECT {cols} FROM aiia_stock_realtime_minute_snapshot_full '
           'WHERE trade_date=%s AND kline_type=\'1m\' AND period_minutes=1 '
           'AND bar_end_time BETWEEN %s AND %s')
    params = [day,day+' 09:31:00',day+' '+end]
    if symbols is not None:
        if not symbols:
            return normalize_bars(pd.DataFrame(),True)
        sql += ' AND stk_code IN ('+','.join(['%s']*len(symbols))+')'
        params += symbols
    return normalize_bars(query(db,sql,tuple(params)),True)


def audit_availability() -> None:
    """Indexed point checks establish presence; build_day checks full paths."""
    DATA.mkdir(parents=True,exist_ok=True)
    local = {trading_day(p):str(p) for p in day_files([COMPACT_ROOT_2025,COMPACT_ROOT])}
    with db_connection() as db:
        calendar = query(db,"SELECT DISTINCT trade_date FROM kcrp_stock_price WHERE trade_date BETWEEN '2025-01-01' AND '2026-10-14' ORDER BY trade_date")
    days = pd.to_datetime(calendar.trade_date).dt.strftime('%Y-%m-%d').tolist()
    def check(day):
        with db_connection() as db:
            result = query(db,"SELECT bar_end_time,COUNT(*) n FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s AND kline_type='1m' AND bar_end_time IN (%s,%s) GROUP BY bar_end_time",(day,day+' 09:40:00',day+' 14:40:00'))
        return day,({str(t)[-8:]:int(n) for t,n in zip(result.bar_end_time,result.n)} if not result.empty else {})
    with ThreadPoolExecutor(max_workers=4) as pool:
        counts = dict(pool.map(check,[d for d in days if d not in local]))
    pd.DataFrame([{'date':d,'local_file':local.get(d,''),
                   'db_0940':counts.get(d,{}).get('09:40:00'),
                   'db_1440':counts.get(d,{}).get('14:40:00')} for d in days]).to_csv(DATA/'availability.csv',index=False)
    print(f'Availability: {len(days)} trading dates; {sum(d in local for d in days)} local files',flush=True)


def build_day(task) -> dict:
    day, path, meta = task
    folder = DATA/'days'; folder.mkdir(exist_ok=True)
    checkpoint = folder/(day+'.json')
    if checkpoint.exists():
        return json.loads(checkpoint.read_text())
    if path:
        bars = normalize_bars(pd.read_csv(path,dtype={'symbol':str}))
        morning = morning_frame(bars,day)
        snap = bars[bars.clock.eq('14:40:00')][['symbol6','close']]
        current = meta.merge(snap,on='symbol6',validate='one_to_one')
        current = current[current.history_complete & (current.close/current.preclose-1).between(.03,.06)]
        wanted = bars[bars.symbol6.isin(current.symbol6) & bars.clock.isin(CLOCKS)]
        source_hash = sha(Path(path))
    else:
        with db_connection() as db:
            morning = morning_frame(db_minutes(db,day,'09:40:00'),day)
            snap = query(db,"SELECT stk_code AS symbol6,latest_price AS close FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 AND bar_end_time=%s",(day,day+' 14:40:00'))
            if snap.empty:
                current = meta.iloc[:0]
            else:
                snap['close'] = pd.to_numeric(snap.close)
                current = meta.merge(snap,on='symbol6',validate='one_to_one')
                current = current[current.history_complete & (current.close/current.preclose-1).between(.03,.06)]
            wanted = db_minutes(db,day,'14:40:00',current.symbol6.tolist())
        frozen = folder/(day+'_db_bars.csv.gz')
        wanted.to_csv(frozen,index=False,float_format='%.17g')
        source_hash = sha(frozen)
    groups = {code:part for code,part in wanted.groupby('symbol6',sort=False)}
    rows = []
    for item in current.itertuples(index=False):
        stock = groups.get(item.symbol6)
        out = extract_features(stock,item) if stock is not None else None
        if out is not None:
            rows.append({'signal_date':day,'symbol6':item.symbol6,'next_date':item.next_date,
                         'preclose':item.preclose,'next_corporate_action':item.next_corporate_action,**out})
    pd.DataFrame(rows).to_csv(folder/(day+'_factors.csv.gz'),index=False,float_format='%.17g')
    morning.to_csv(folder/(day+'_morning.csv.gz'),index=False,float_format='%.17g')
    audit = {'date':day,'source':path or 'kingdomai finalized exact-timestamp minutes',
             'source_sha256':source_hash,'band_candidates':len(current),'complete_candidates':len(rows),
             'morning_stocks':len(morning)}
    checkpoint.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    print(f"{day}: {len(rows)}/{len(current)} complete; morning {len(morning)}",flush=True)
    return audit


def run(workers: int = 3, limit: int | None = None) -> None:
    DATA.mkdir(parents=True,exist_ok=True); REPORT.mkdir(parents=True,exist_ok=True)
    dictionary().to_csv(REPORT/'factor_dictionary.csv',index=False)
    paths = sorted(DAILY.glob('price_*.csv.gz'))
    raw = pd.concat([pd.read_csv(p,dtype={'stk_code':str,'trade_date':str}) for p in paths],ignore_index=True)
    profile = historical_features(raw)
    local = {trading_day(p):str(p) for p in day_files([COMPACT_ROOT_2025,COMPACT_ROOT])}
    availability = pd.read_csv(DATA/'availability.csv').fillna({'local_file':''})
    availability['local_file'] = availability.date.map(local).fillna('')
    availability.to_csv(DATA/'availability.csv',index=False)
    calendar = availability.date.tolist()
    profile['next_date'] = profile.signal_date.map(dict(zip(calendar[:-1],calendar[1:])))
    available = availability[availability.local_file.ne('') | availability.db_0940.fillna(0).gt(0)]
    by_day = {d:f for d,f in profile.groupby('signal_date',sort=False)}
    tasks = [(r.date,r.local_file,by_day.get(r.date,profile.iloc[:0])) for r in available.itertuples(index=False)]
    tasks = tasks[:limit] if limit else tasks
    with ProcessPoolExecutor(max_workers=workers) as pool:
        audits = list(pool.map(build_day,tasks))
    pd.DataFrame(audits).to_csv(REPORT/'minute_coverage.csv',index=False)
    factors = []
    mornings = []
    for day,_,_ in tasks:
        p = DATA/'days'/(day+'_factors.csv.gz')
        if next(a for a in audits if a['date'] == day)['complete_candidates'] > 0:
            factors.append(pd.read_csv(p,dtype={'symbol6':str}))
        mornings.append(pd.read_csv(DATA/'days'/(day+'_morning.csv.gz'),dtype={'symbol6':str}))
    factors = pd.concat(factors,ignore_index=True)
    morning = pd.concat(mornings,ignore_index=True)
    rows = factors.merge(morning,on=['symbol6','next_date'],how='left',validate='many_to_one')
    st_file = DATA/'historical_st.csv.gz'
    if not st_file.exists():
        with db_connection() as db:
            intervals = load_st_intervals(db)
        intervals.to_csv(st_file,index=False)
    intervals = pd.read_csv(st_file,dtype={'symbol6':str},parse_dates=['begin_date','end_date','ann_date'])
    cohorts,policy_counts = make_cohorts(rows,intervals)
    rows = add_labels(cohorts[POLICIES[1]])
    rows = rows[rows.signal_date.le('2026-10-13')].sort_values(['signal_date','symbol6'])
    rows.to_csv(DATA/'factor_panel.csv.gz',index=False,float_format='%.17g')
    manifest = {'experiment':'CO4','input_daily':[{'path':str(p),'sha256':sha(p)} for p in paths],
                'history_price_method':'causal chain of daily close/preclose; historical MA rescaled to current preclose',
                'minute_coverage':'minute_coverage.csv','factor_count':len(FACTORS),'policy_counts':policy_counts,
                'rows':len(rows),'labeled':int(rows.strong.notna().sum()),'days':int(rows.signal_date.nunique()),
                'panel_sha256':sha(DATA/'factor_panel.csv.gz'),'script_sha256':sha(Path(__file__)),
                'design_sha256':sha(REPORT/'DESIGN.md'),'st_sha256':sha(st_file)}
    (REPORT/'data_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in manifest.items() if k not in ('input_daily','policy_counts')},indent=2),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--audit-availability',action='store_true')
    args = parser.parse_args()
    if args.audit_availability:
        audit_availability()
    else:
        run(args.workers,args.limit)

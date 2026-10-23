"""D1 four-bar and closing-time features on the frozen V2 cohort."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import gzip,io,json
import numpy as np
import pandas as pd
import zstandard
from scripts import research_b2_floor_risk_20261021 as old

ROOT=old.ROOT
OUT=ROOT/'docs/stock_automl_runs/20261021_b2_d1_risk'
BASE=ROOT/'outputs/stock_automl'
EARLY={
 'open_gap':'D1开盘涨幅','body5':'四K实体','high5':'四K最高涨幅','low5':'四K最低涨幅',
 'pullback5':'四K冲高回吐','position5':'四K收盘位置','early_volume_ratio':'四K量/D0全天量',
 'early_volume_accel':'后两K量/前两K量','early_down_volume':'阴K成交量占比',
 'early_up_count':'四K阳线根数','early_slope':'四K收盘线性斜率',
 'early_path':'四K收盘路径长度','early_below_d0':'四K收低于D0比例',
 'early_drawdown':'四K收盘最大回撤','early_upper':'四K上影','early_lower':'四K下影'}
LATE={
 'day_return':'D1全天涨幅','day_body':'D1全天实体','day_range':'D1全天振幅',
 'day_upper':'D1上影','day_lower':'D1下影','day_position':'D1收盘位置',
 'day_volume_ratio':'D1量/D0量','day_turnover':'D1换手率','day_turnover_ratio':'D1换手/D0换手',
 'after4_return':'四K后至收盘涨幅','after4_low':'四K后最低相对四K收盘',
 'morning_return':'上午收盘相对开盘','afternoon_return':'午后相对上午收盘',
 'tail_return':'尾30分钟涨幅','day_drawdown':'D1分钟收盘最大回撤',
 'afternoon_volume':'下午量占比','tail_volume':'尾30分钟量占比',
 'early_volume_share':'前四K量占D1全天','day_down_volume':'D1阴K量占比',
 'close_vs_weighted':'D1收盘相对成交量加权典型价',
 'limit_up_fraction':'D1分钟收在涨停比例','day_close_limit':'D1收盘涨停',
 'return_change':'D1涨幅减D0涨幅','body_change':'D1实体减D0实体'}
D0={'total_mv_yi':'D0总市值亿','d0_turnover_pct':'D0换手率','board_streak_days':'连板数',
 'd0_normal_volume_ratio':'D0对板前量比','close_sealed_minutes':'D0收在板上分钟数',
 'last30_amount_fraction':'D0尾30分额占比'}

def write(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

def read_minutes(row):
    day=row['d1_date'];symbol=row['symbol'];dest=OUT/'minutes'/f'{day}_{symbol}.csv.gz'
    if dest.exists():return pd.read_csv(dest),{'cache':str(dest),'cache_sha256':old.sha(dest)}
    path=Path(row['minute_source']);path=path if path.is_absolute() else ROOT/path
    frame=None;meta={}
    if path.exists():
        tushare='tushare_market_data' in str(path)
        opener=gzip.open if path.suffix=='.gz' else zstandard.open
        lines=[]
        with io.BufferedReader(opener(path,'rb')) as stream:
            header=stream.readline()
            for line in stream:
                code=line[:6] if tushare else line.split(b',',2)[1][:6]
                if code.decode()==symbol:lines.append(line)
        frame=pd.read_csv(io.BytesIO(header+b''.join(lines)))
        if tushare:
            from scripts.import_tushare_minutes_2024 import convert
            frame=convert(frame,day)
        meta={'source':str(path),'source_sha256':old.sha(path),'volume_unit':'shares, as archived'}
    else:
        for name in ['limit_rebound_shape_top3_r20_20261015','limit_rebound_shape_r19_20261015']:
            p=BASE/name/'minute_days'/f'{day}_raw.pkl'
            if not p.exists():continue
            a=pd.read_pickle(p);a=a[a.symbol.astype(str).str[:6].eq(symbol)]
            if 'source' in a:
                for _,g in a.groupby('source'):
                    if len(g)==240:frame=g.copy();break
            elif len(a)==240:frame=a.copy()
            if frame is not None:
                meta={'source':str(p),'source_sha256':old.sha(p),'volume_unit':'shares; existing r19 loader multiplies DB lots by 100'};break
    if frame is None:raise FileNotFoundError(f'{day} {symbol}: no complete archived D1 minutes')
    frame=frame.sort_values('time').reset_index(drop=True)
    frame['symbol']=symbol;frame['time']=frame.time.astype(str)
    frame=frame[['time','symbol','open','high','low','close','volume']]
    frame.to_csv(dest,index=False,compression='gzip')
    meta.update(cache=str(dest),cache_sha256=old.sha(dest))
    return frame,meta

def features(row,b):
    q=b.iloc[:4];pre=row['d1_preclose'];o=float(q.open.iloc[0]);c=float(q.close.iloc[-1]);h=float(q.high.max());l=float(q.low.min())
    v=q.volume.to_numpy(float);cl=q.close.to_numpy(float)
    ratio=lambda a,b:float(a/b) if b else np.nan
    early={
      'open_gap':o/pre-1,'body5':c/o-1,'high5':h/pre-1,'low5':l/pre-1,'pullback5':(h-c)/pre,
      'position5':ratio(c-l,h-l),'early_volume_ratio':ratio(v.sum(),row['volume']),
      'early_volume_accel':ratio(v[2:].sum(),v[:2].sum()),'early_down_volume':ratio(v[q.close.lt(q.open)].sum(),v.sum()),
      'early_up_count':int(q.close.gt(q.open).sum()),'early_slope':float(np.polyfit(np.arange(4),cl/pre,1)[0]),
      'early_path':float(np.abs(np.diff(np.r_[o,cl])).sum()/pre),'early_below_d0':float((cl<pre).mean()),
      'early_drawdown':float((cl/np.maximum.accumulate(np.r_[o,cl])[1:]-1).min()),
      'early_upper':(h-max(o,c))/pre,'early_lower':(min(o,c)-l)/pre}
    # Everything below this line is available only at D1 close.
    dc=float(b.close.iloc[-1]);dh=float(b.high.max());dl=float(b.low.min());vol=float(b.volume.sum());noon=float(b.close.iloc[119])
    typical=(b.high+b.low+b.close)/3
    late={
      'day_return':dc/pre-1,'day_body':dc/o-1,'day_range':(dh-dl)/pre,'day_upper':(dh-max(o,dc))/pre,
      'day_lower':(min(o,dc)-dl)/pre,'day_position':ratio(dc-dl,dh-dl),
      'day_volume_ratio':ratio(vol,row['volume']),'day_turnover':row['d1_turn'],
      'day_turnover_ratio':ratio(row['d1_turn'],row['d0_turnover_pct']),
      'after4_return':dc/c-1,'after4_low':b.low.iloc[4:].min()/c-1,'morning_return':noon/o-1,
      'afternoon_return':dc/noon-1,'tail_return':dc/float(b.close.iloc[209])-1,
      'day_drawdown':float((b.close/np.maximum.accumulate(np.r_[o,b.close])[1:]-1).min()),
      'afternoon_volume':ratio(b.volume.iloc[120:].sum(),vol),'tail_volume':ratio(b.volume.iloc[210:].sum(),vol),
      'early_volume_share':ratio(v.sum(),vol),'day_down_volume':ratio(b.loc[b.close.lt(b.open),'volume'].sum(),vol),
      'close_vs_weighted':dc/float(np.average(typical,weights=b.volume))-1,
      'limit_up_fraction':float(b.close.ge(row['d1_cap']-.001).mean()),'day_close_limit':int(dc>=row['d1_cap']-.001),
      'return_change':dc/pre-1-row['d0_return'],'body_change':dc/o-1-row['d0_body']}
    return early,late

def task(row):
    b,meta=read_minutes(row)
    expected=[row['d1_open'],row['d1_high'],row['d1_low'],row['d1_close']]
    actual=[b.open.iloc[0],b.high.max(),b.low.min(),b.close.iloc[-1]]
    clocks=b.time.str[11:16].tolist()
    assert len(b)==240 and b.time.is_unique and clocks[:4]==['09:31','09:32','09:33','09:34'] and clocks[-1]=='15:00'
    assert np.allclose(expected,actual,rtol=0,atol=.011),(row['symbol'],row['d1_date'],expected,actual)
    volume_ratio=b.volume.sum()/row['d1_volume']
    # Validate only; never rescale early values with the future full-day volume.
    assert np.isclose(volume_ratio,1,rtol=.002),(row['d1_date'],row['symbol'],'volume',volume_ratio)
    early,late=features(row,b)
    for k in ['open_gap','body5','high5','low5','pullback5']:
        assert np.isclose(early[k],row[k],atol=1e-10),(k,early[k],row[k])
    return dict(sequence=row['sequence'],**early,**late),dict(sequence=row['sequence'],symbol=row['symbol'],D1=row['d1_date'],bars=240,OHLC_match=True,volume_ratio=float(volume_ratio),**meta)

def main():
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'minutes').mkdir(exist_ok=True)
    f=old.load();values=[];audit=[];errors=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(task,x):x for x in f.to_dict('records')}
        for future in as_completed(jobs):
            try:
                a,b=future.result();values.append(a);audit.append(b)
            except Exception as e:
                x=jobs[future];errors.append(dict(sequence=x['sequence'],symbol=x['symbol'],D1=x['d1_date'],error=str(e)))
            if (len(values)+len(errors))%30==0:print('minutes',len(values),'errors',len(errors),flush=True)
    write('minute_audit.json',dict(rows=audit,errors=errors))
    if errors:raise RuntimeError(f'{len(errors)} minute errors; see minute_audit.json')
    add=pd.DataFrame(values);panel=f.drop(columns=list(set(add.columns)&set(f.columns)-{'sequence'})).merge(add,on='sequence',validate='one_to_one')
    panel['y_close']=panel.close_floor.eq('是').astype(int);panel['y_union']=panel.y
    assert len(panel)==306
    panel.to_pickle(OUT/'panel.pkl')
    fields=[]
    for group,mapping,time in [('D0',D0,'D0收盘'),('early',EARLY,'D1 09:34'),('close',LATE,'D1 15:00')]:
        for key,label in mapping.items():fields.append(dict(key=key,label=label,group=group,known_at=time))
    fields.append(dict(key=old.CAT,label='D0触板后打开',group='D0',known_at='D0收盘'))
    write('feature_dictionary.json',fields)
    columns=['sequence','symbol','d0_date','d1_date','split',*dict.fromkeys([*D0,*EARLY,*LATE,old.CAT]),'y_close','y_union','original_net']
    panel[columns].to_csv(OUT/'研究因子与标签.csv',index=False,encoding='utf-8-sig')
    write('source.json',dict(source=str(old.SOURCE),source_sha256=old.sha(old.SOURCE),panel_sha256=old.sha(OUT/'panel.pkl'),code_sha256=old.sha(__file__),rows=306))
    print('ready',panel.shape,flush=True)


if __name__=='__main__':main()

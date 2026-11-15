from pathlib import Path
import json
import numpy as np
import pandas as pd
import duckdb

R=Path('/Volumes/ext/fin_agent'); P=Path(__file__).parent; P.mkdir(exist_ok=True)
B=R/'outputs/b2连续两日全量回放_20261113'; A=R/'outputs/b2_stage_review_r140_r142_20261026/archive_package/daily'
months=[p.name for p in sorted((B/'months').iterdir()) if (p/'manifest.json').exists() and '2018-01'<=p.name<='2022-04']
parts=[]
for month in months:
    src=(B/'近期输入'/month if (B/'近期输入'/month/'test_samples.pkl.gz').exists() else A/month)
    for f in [B/'months'/month/'train.pkl.gz',src/'validation_samples.pkl.gz',src/'test_samples.pkl.gz']:
        x=pd.read_pickle(f)[['stk_code','symbol','d0_date','close']]
        parts.append(x)
want=pd.concat(parts,ignore_index=True).drop_duplicates(['stk_code','d0_date'])
want=want[want.stk_code.str.endswith(('.SH','.SZ'))].copy()
want['qdc_symbol']=want.stk_code.str.replace('.SH','.XSHG',regex=False).str.replace('.SZ','.XSHE',regex=False)
want['day_key']=pd.to_datetime(want.d0_date).astype('int64')//86400000000000
want['year']=want.d0_date.str[:4];want=want.rename(columns={'close':'cap'})
assert not want.duplicated(['qdc_symbol','day_key']).any()
want[['stk_code','symbol','d0_date','qdc_symbol','cap']].to_pickle(P/'目标股票日.pkl.gz',compression='gzip')
bad=pd.read_csv(R/'outputs/b2_mainland_0d_20261028/hard_minute_stock_days.csv',dtype={'symbol':str,'day':str})
badkeys=set(zip(bad.symbol,bad.day));out=[]
con=duckdb.connect();con.execute("SET memory_limit='3GB'");con.execute('SET threads=3')
for year,w in want.groupby('year'):
    dest=P/f'原始分时聚合_{year}.pkl.gz'
    if dest.exists():
        p=pd.read_pickle(dest)
    else:
        files=sorted(Path('/Volumes/ext/minite_k_data/1分钟').glob(year+'*.parquet'))
        if not files:continue
        con.register('wanted',w[['qdc_symbol','day_key','cap']]);
        sql='''WITH b AS (SELECT m.symbol qdc_symbol,w.day_key,w.cap,ts,ts%86400000 clock,m.open,m.high,m.low,m.close,m.volume,m.amount FROM read_parquet(?) m JOIN wanted w ON m.symbol=w.qdc_symbol AND CAST(floor((ts+28800000)/86400000) AS BIGINT)=w.day_key WHERE ts%86400000 BETWEEN 5400000 AND 25200000), z AS (SELECT *,lag(close) OVER(PARTITION BY qdc_symbol,day_key ORDER BY ts) prev FROM b), a AS (SELECT qdc_symbol,day_key,count(*) bars,first(cap) cap, sum(CASE WHEN close<=0 OR open<=0 OR low<=0 OR high<greatest(open,close)-0.00001 OR low>least(open,close)+0.00001 OR volume<0 OR amount<0 THEN 1 ELSE 0 END) bad, sum(volume) v, sum(amount) amount, arg_min(open,ts) o, arg_max(close,ts) c, max(high) h, min(low) l, sum(CASE WHEN clock<=7200000 THEN volume ELSE 0 END) v_first30, sum(CASE WHEN clock>23400000 THEN volume ELSE 0 END) v_last30, sum(CASE WHEN clock<=12600000 THEN volume ELSE 0 END) v_morning, sum(CASE WHEN clock>=18000000 THEN volume ELSE 0 END) v_afternoon, arg_max(close,ts) FILTER(WHERE clock<=7200000) c_first30, arg_min(open,ts) FILTER(WHERE clock>23400000) o_last30, arg_max(close,ts) FILTER(WHERE clock<=12600000) c_morning, arg_min(open,ts) FILTER(WHERE clock>=18000000) o_afternoon, sum(CASE WHEN close>open THEN volume WHEN close<open THEN -volume ELSE 0 END) signed_v, avg(CASE WHEN close>open THEN 1.0 ELSE 0 END) up_fraction, sum(CASE WHEN close>0 AND prev>0 THEN pow(ln(close/prev),2) ELSE 0 END) rv2, sum(CASE WHEN close>0 AND prev>0 THEN abs(ln(close/prev)) ELSE 0 END) path, min(clock) FILTER(WHERE high>=cap-0.005) first_touch, sum(CASE WHEN close>=cap-0.005 THEN 1 ELSE 0 END) seal_bars, sum(CASE WHEN prev>=cap-0.005 AND close<cap-0.005 THEN 1 ELSE 0 END) seal_breaks, sum(CASE WHEN close>=cap-0.005 THEN volume ELSE 0 END) seal_v, sum(CASE WHEN clock>23400000 AND close<cap-0.005 THEN 1 ELSE 0 END) late_below FROM z GROUP BY 1,2) SELECT * FROM a'''
        p=con.execute(sql,[[str(f) for f in files]]).df();p.to_pickle(dest,compression='gzip')
    p['d0_date']=pd.to_datetime(p.day_key,unit='D').dt.strftime('%Y-%m-%d'); p['hardbad']=[(s,d) in badkeys for s,d in zip(p.qdc_symbol,p.d0_date)]
    valid=p.bars.between(240,242)&p.bad.eq(0)&~p.hardbad&p.v.gt(0)&p.o.gt(0)&p.l.gt(0)&p.cap.gt(0)&(p.c-p.cap).abs().le(.011)
    ratio=lambda a,b: a/b.where(b.gt(0))
    z=pd.DataFrame({'qdc_symbol':p.qdc_symbol,'d0_date':p.d0_date,'分钟记录数':p.bars,'硬异常':p.hardbad,'分时有效':valid,
      '首30分钟涨幅':ratio(p.c_first30,p.o)-1,'末30分钟涨幅':ratio(p.c,p.o_last30)-1,
      '上午涨幅':ratio(p.c_morning,p.o)-1,'下午涨幅':ratio(p.c,p.o_afternoon)-1,
      '首30分钟成交量占比':ratio(p.v_first30,p.v),'末30分钟成交量占比':ratio(p.v_last30,p.v),
      '下午成交量占比':ratio(p.v_afternoon,p.v),'下午相对上午成交量':ratio(p.v_afternoon,p.v_morning),
      '上涨K成交量方向比':ratio(p.signed_v,p.v),'上涨分钟比例':p.up_fraction,
      '全日分钟波动':np.sqrt(p.rv2),'走势效率':ratio(abs(np.log(ratio(p.c,p.o))),p.path),
      '触板时间':(p.first_touch-5400000)/60000,'收板分钟占比':p.seal_bars/p.bars,
      '分钟收盘开板次数':p.seal_breaks,'封板时成交量占比':ratio(p.seal_v,p.v),'尾盘板下分钟占比':p.late_below/30})
    features=[c for c in z if c not in ['qdc_symbol','d0_date','分钟记录数','硬异常','分时有效']];z.loc[~valid,features]=np.nan;out.append(z)
    print(year,'目标',len(w),'有分钟',len(z),'有效',int(valid.sum()),flush=True)
con.close();result=pd.concat(out,ignore_index=True);assert not result.duplicated(['qdc_symbol','d0_date']).any();result.to_pickle(P/'D0分时因子.pkl.gz',compression='gzip');(P/'字段.json').write_text(json.dumps(features,ensure_ascii=False,indent=2));print('因子完成',len(result),flush=True)

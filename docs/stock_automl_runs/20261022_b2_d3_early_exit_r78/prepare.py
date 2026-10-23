from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import sys,hashlib,json
import numpy as np,pandas as pd
sys.path.insert(0,'/data/cyris/work/b2_d2_intraday_r67_20261021')
import experiment as old
R=Path('/data/cyris/work/b2_d3_early_exit_r78_20261022')
S=pd.read_pickle('/data/cyris/work/b2_news_flow_years_r76_20261022/joined_306.pkl')
B=pd.read_csv('/data/cyris/work/b2_idle_extend_r77_20261022/inputs/逐笔D3涨幅与连板.csv',dtype={'symbol':str})
D=pd.read_pickle('/data/cyris/work/b2_d2_limit_follow_r69_20261021/daily.pkl')
S=S.assign(symbol6=S.stk_code.str[:6],idle=~S.d2_date.isin(set(S.d1_date)))
S=S.merge(B[['symbol','D0','d2_limit_up','d3_limit_up','consecutive_up']],left_on=['symbol6','d0_date'],right_on=['symbol','D0'],validate='one_to_one')
S=S[S.d2_limit_up].copy()
S=S.merge(D[['trade_date','stk_code','preclose','open','high','low','close','volume','is_limit_price']].rename(columns={'trade_date':'d3_date','open':'d3_open','high':'d3_high','low':'d3_low','close':'d3_close_daily','volume':'d3_volume','is_limit_price':'d3_limit_flag'}),on=['d3_date','stk_code'],validate='one_to_one')
assert len(S)==91 and np.allclose(S.d3_close,S.d3_close_daily,atol=.001)
st=pd.read_csv(R/'raw/st.csv.gz').fillna({'end_date':'9999-12-31'})
records=[];missing=[];barmap={}
for x in S.to_dict('records'):
    day=x['d3_date'];sym=x['symbol6']
    paths=[
        (Path('/data/cyris/tushare_market_data')/day[:4]/(day.replace('-','')+'.csv.gz'),'server_tushare'),
        (R/'raw/tushare_market_data'/day[:4]/(day.replace('-','')+'.csv.gz'),'transferred_tushare'),
        (R/'raw/limit_rebound_shape_top3_r20_20261015/minute_days'/f'{day}_raw.pkl','R20_cache'),
        (R/'raw/fin_agent/outputs/stock_automl/mk_1m_compact_2025'/f'{day}.csv.zst','transferred_compact'),
        (R/'raw/fin_agent/outputs/stock_automl/mk_1m_compact'/f'{day}.csv.zst','transferred_compact'),
    ]
    selected=None
    for p,kind in paths:
        if not p.exists():continue
        if kind=='R20_cache':
            z=pd.read_pickle(p)
            z=z[z.symbol.astype(str).str[:6].eq(sym)]
        else:
            z=old.extract_archive(p,day,sym)
        if len(z)==240:
            selected=(p,kind,z.copy());break
    if selected is None:
        missing.append({'id':x['id'],'D3':day,'symbol':sym,'reason':'no complete historical minute source'})
        continue
    p,kind,b=selected
    b['time']=pd.to_datetime(b.time)
    b=b.sort_values('time').reset_index(drop=True)
    assert b.time.dt.strftime('%H:%M').tolist()==old.CLOCK,(x['id'],'clock')
    for c in ['open','high','low','close','volume']:b[c]=pd.to_numeric(b[c])
    assert (b[['open','high','low','close']]>0).all().all() and b.volume.ge(0).all(),x['id']
    expected=[x['d3_open'],x['d3_high'],x['d3_low'],x['d3_close']]
    actual=[b.open.iloc[0],b.high.max(),b.low.min(),b.close.iloc[-1]]
    assert np.allclose(actual,expected,atol=.011,rtol=0),(x['id'],actual,expected)
    assert np.isclose(b.volume.sum(),x['d3_volume'],rtol=.003,atol=1),(x['id'],b.volume.sum(),x['d3_volume'])
    status=st[st.stk_code.eq(x['stk_code'])&st.begin_date.le(day)&st.end_date.gt(day)]
    assert len(status)==1,x['id']
    rate=.2 if sym.startswith(('30','68')) else (.05 if status.iloc[0].st_type in ('S','Y') else .1)
    floor=float((Decimal(str(x['preclose']))*(1-Decimal(str(rate)))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
    assert (abs(x['d3_close']-floor)<.001)==(x['d3_limit_flag']==2),(x['id'],floor,x['d3_limit_flag'])
    assert np.isclose(x['d2_close'],x['preclose'],atol=.001),x['id']
    barmap[x['id']]=(b,floor)
    records.append({'id':x['id'],'path':str(p),'source_type':kind,'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bars':len(b),'minute_daily_OHLC_match':True,'minute_daily_volume_ratio':float(b.volume.sum()/x['d3_volume'])})
pd.DataFrame(records).to_csv(R/'分钟来源及核验.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
pd.DataFrame(missing).to_csv(R/'缺失分钟7笔.csv',index=False,encoding='utf-8-sig')
assert len(records)==84 and len(missing)==7,(len(records),len(missing))
S['D3开盘涨幅']=S.d3_open/S.preclose-1
S['D3收盘涨幅']=S.d3_close/S.preclose-1
S['开盘卖相比收盘多拿百分点']=100*(S.d3_open-S.d3_close)/S.entry*(.999/1.001)
S['D3收盘跌停']=S.d3_limit_flag.eq(2)
S['D3收盘连板']=S.consecutive_up
S['D3收盘涨停']=S.d3_limit_up
S['分钟完整']=S.id.isin(barmap)
prices=['09:31','09:35','09:41','10:01','11:30','13:01','14:56','15:00']
rows=[]
for x in S.to_dict('records'):
    if x['id'] not in barmap:continue
    b,floor=barmap[x['id']]
    for clock in prices:
        if clock=='15:00':
            q=b.iloc[-1];i=len(b)-1;price=float(q.close)
            filled=bool(price>floor+.001 and q.volume>0)
            fillclock='15:00' if filled else ''
        else:
            i=b.index[b.time.dt.strftime('%H:%M').eq(clock)][0]
            q=b.iloc[i];price=float(q.open)
            eligible=b.iloc[i:][(b.open.iloc[i:]>floor+.001)&(b.volume.iloc[i:]>0)]
            if len(eligible):
                j=eligible.index[0]
                price=float(b.loc[j,'open']);filled=True
                fillclock=b.loc[j,'time'].strftime('%H:%M')
            else:
                price=float(b.close.iloc[-1]);filled=False;fillclock=''
        value=(price*(1-.001) if filled else price)/x['entry']/(1+.001)-1
        rows.append({'id':x['id'],'D0':x['d0_date'],'D2':x['d2_date'],'D3':x['d3_date'],'idle':x['idle'],'D2收盘涨停':True,'D3收盘涨停':x['d3_limit_up'],'D3收盘跌停':x['D3收盘跌停'],'预约卖出时点':clock,'实际代理成交时点':fillclock,'代理成交':filled,'卖出价或未售收盘估值':price,'每笔费后收益或未售估值收益':value,'相比D3收盘价格差_百分点':100*(price-x['d3_close'])/x['entry']*(.999/1.001)})
m=pd.DataFrame(rows)
m.to_csv(R/'84笔分钟预约卖出对照.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
S[['id','symbol6','d0_date','d1_date','d2_date','d3_date','idle','entry','d2_close','d3_open','d3_high','d3_low','d3_close','D3开盘涨幅','D3收盘涨幅','开盘卖相比收盘多拿百分点','D3收盘涨停','D3收盘跌停','分钟完整']].to_csv(R/'91笔D3开收对照.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print('91 open/close',S.D3开盘涨幅.mean(),S.D3收盘涨幅.mean(),'open better',(S['开盘卖相比收盘多拿百分点']>0).sum(),'same',(S['开盘卖相比收盘多拿百分点']==0).sum(),'minute',len(records),'missing',len(missing))
print(m.groupby('预约卖出时点').agg(n=('id','size'),avg_net=('每笔费后收益或未售估值收益','mean'),mean_delta_pp=('相比D3收盘价格差_百分点','mean'),proxy_filled=('代理成交','sum')).to_string())
S.to_pickle(R/'joined91.pkl')
(R/'verification.json').write_text(json.dumps({'cohort':91,'complete_d3_minutes':84,'missing_d3_minutes':7,'OHLC_and_volume_reconciled':True,'d3_official_limit_flags_reconciled':True,'cash_dividend_mismatches':0},ensure_ascii=False,indent=2)+'\n')

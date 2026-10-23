from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import json,hashlib,sys
import pandas as pd,numpy as np
sys.path.insert(0,'/data/cyris/work/b2_d2_intraday_r67_20261021')
import experiment as old
R=Path('/data/cyris/work/b2_d3_early_exit_r78_20261022')
P=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
M={x['id']:x for x in json.loads((P/'minute_source_manifest.json').read_text())}
s=pd.read_pickle(P/'joined_306.pkl')
d=pd.read_pickle('/data/cyris/work/b2_d2_limit_follow_r69_20261021/daily.pkl')
daily=d[['trade_date','stk_code','preclose','open','high','low','close','volume','is_limit_price']].rename(columns={'trade_date':'d2_date','preclose':'d2_preclose','close':'d2_close_daily','is_limit_price':'d2_limit_flag'})
s=s.merge(daily,on=['d2_date','stk_code'],validate='one_to_one')
st=pd.read_csv(R/'raw/st.csv.gz').fillna({'end_date':'9999-12-31'})
rows=[]
for x in s.to_dict('records'):
    src=M[x['id']]['source'];p=Path(src['path'])
    b=old.extract_archive(p,x['d2_date'],x['stk_code'][:6]) if src['type']=='archive' else pd.read_pickle(p) if p.suffix=='.pkl' else pd.read_csv(p)
    b['time']=pd.to_datetime(b.time);b=b.sort_values('time').reset_index(drop=True)
    assert len(b)==240 and b.time.dt.strftime('%H:%M').tolist()==old.CLOCK
    q=b[b.time.dt.strftime('%H:%M').eq('14:55')].iloc[0]
    status=st[st.stk_code.eq(x['stk_code'])&st.begin_date.le(x['d2_date'])&st.end_date.gt(x['d2_date'])]
    assert len(status)==1,x['id']
    rate=.2 if x['stk_code'].startswith(('30','68')) else .05 if status.iloc[0].st_type in ('S','Y') else .1
    if x['stk_code'].endswith('.SH') and x['d2_date']>='2026-04-27' and status.iloc[0].st_type in ('S','Y'):rate=.1
    ceiling=float((Decimal(str(x['d2_preclose']))*(1+Decimal(str(rate)))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
    actual=abs(x['d2_close']-ceiling)<.001
    assert actual==(x['d2_limit_flag']==1),(x['id'],ceiling,x['d2_close'],x['d2_limit_flag'])
    signal=abs(float(q.close)-ceiling)<.001
    rows.append(dict(id=x['id'],D0=x['d0_date'],D1=x['d1_date'],D2=x['d2_date'],D3=x['d3_date'],stk_code=x['stk_code'],idle=not x['d2_date'] in set(s.d1_date),D2涨停价=ceiling,D2_1455收盘价=float(q.close),D2_1455在涨停=signal,D2最终涨停=actual,D2_1455量=float(q.volume),D2收盘价=x['d2_close']))
z=pd.DataFrame(rows)
z.to_csv(R/'306笔D2_1455事前信号.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print('n',len(z),'signal',int(z.D2_1455在涨停.sum()),'finalup',int(z.D2最终涨停.sum()),'both',int((z.D2_1455在涨停&z.D2最终涨停).sum()),'false',int((z.D2_1455在涨停&~z.D2最终涨停).sum()),'miss',int((~z.D2_1455在涨停&z.D2最终涨停).sum()))
print(z.groupby(z.D0.str[:4])[['D2_1455在涨停','D2最终涨停']].sum().to_string())

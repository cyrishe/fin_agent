from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import hashlib,sys
import pandas as pd,numpy as np
sys.path.insert(0,'/data/cyris/work/b2_d2_intraday_r67_20261021')
import experiment as old
R=Path('/data/cyris/work/b2_d3_early_exit_r78_20261022')
D=pd.read_pickle('/data/cyris/work/b2_d2_limit_follow_r69_20261021/daily.pkl')
st=pd.read_csv(R/'raw/st.csv.gz').fillna({'end_date':'9999-12-31'})
cases=[('000421.SZ|2023-12-11','000421.SZ','2023-12-14',Path('/data/cyris/tushare_market_data/2023/20231214.csv.gz')),('600722.SH|2026-03-12','600722.SH','2026-03-17',R/'raw/fin_agent/outputs/stock_automl/mk_1m_compact/2026-03-17.csv.zst')]
out=[]
for id,stock,day,p in cases:
 assert p.exists(),p
 b=old.extract_archive(p,day,stock[:6]);b['time']=pd.to_datetime(b.time);b=b.sort_values('time').reset_index(drop=True)
 assert len(b)==240 and b.time.dt.strftime('%H:%M').tolist()==old.CLOCK
 d=D[(D.trade_date==day)&(D.stk_code==stock)].iloc[0]
 assert np.allclose([b.open.iloc[0],b.high.max(),b.low.min(),b.close.iloc[-1]],[d.open,d.high,d.low,d.close],atol=.011,rtol=0)
 assert np.isclose(b.volume.sum(),d.volume,rtol=.003,atol=1)
 status=st[st.stk_code.eq(stock)&st.begin_date.le(day)&st.end_date.gt(day)].iloc[0]
 rate=.2 if stock[:2] in ('30','68') else .05 if status.st_type in ('S','Y') else .1
 floor=float((Decimal(str(d.preclose))*(1-Decimal(str(rate)))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
 eligible=b[(b.open>floor+.001)&(b.volume>0)]
 if len(eligible):
  q=eligible.iloc[0];price=float(q.open);fill=True;clock=q.time.strftime('%H:%M')
 else:
  price=float(b.close.iloc[-1]);fill=False;clock=''
 out.append(dict(id=id,day=day,first_eligible_clock=clock,first_eligible_price=price,proxy_fill=fill,source=str(p),source_sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
pd.DataFrame(out).to_csv(R/'2笔1455误报D3分钟核验.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print(pd.DataFrame(out)[['id','day','first_eligible_clock','first_eligible_price','proxy_fill']].to_string(index=False))

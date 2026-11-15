from pathlib import Path
import sys,pandas as pd,numpy as np
q=Path(sys.argv[1]);year=sys.argv[2];summary=pd.read_csv(q/(year+'_原始10_逐批次.csv')); equity=pd.read_csv(q/(year+'_原始10_每日净值.csv'));out=[]
for r in summary.itertuples():
 g=equity[equity.month.eq(r.month)].sort_values('day');v=np.r_[100000.,g.nav.to_numpy(float)];d=100*np.max(1-v/np.maximum.accumulate(v));ret=(v[-1]/100000-1)*100;ok=np.isclose(d,r.drawdown_pct,atol=1e-8) and np.isclose(ret,r.return_pct,atol=1e-8);out.append({'month':r.month,'return_pct':ret,'drawdown_pct':d,'通过':ok});assert ok,(r.month,d,r.drawdown_pct,ret,r.return_pct)
pd.DataFrame(out).to_csv(q/'回撤独立核验.csv',index=False);print('独立核验账户数',len(out),'未通过',sum(not x['通过'] for x in out))

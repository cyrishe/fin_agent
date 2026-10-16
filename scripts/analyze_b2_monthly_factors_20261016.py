"""Monthly descriptive factors on frozen R26 trades, no optimization."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
P=Path('outputs/stock_automl/b2_monthly_factors_20261016')
f=pd.read_pickle(P/'trades.pkl');vals=json.load(open(P/'valuation.json'));vs={ (r['stk_code'],r['d0_date']):r for r in vals}
rows=[]
for r in f.to_dict('records'):
 v=vs[(r['stk_code'],r['d0_date'])];a=v['valuation'][0] if v['valuation'] else {};b=v['margin'][0] if v['margin'] else {}
 def num(v):return float(v) if v is not None else np.nan
 mv=num(a.get('total_mv'));src='估值表'
 if not np.isfinite(mv):mv=num(b.get('tot_mv'));src='担保券表' if np.isfinite(mv) else '缺失'
 rows.append(dict(date=r['entry_date'],month=r['entry_date'][:7],method='四K' if r['variant'].startswith('4') else '五K',symbol=r['symbol'],d0=r['d0_date'],mv=mv/1e8,mvsrc=src,amount=r['amount']/1e8,volume=r['volume']/1e8,turn=r['d0_turnover_pct']/100,pe=num(a.get('pe_ttm')),peupdate=a.get('update_time'),ret=r['return_'],label=r['label_3'] if r['observed_3'] else np.nan,buy=r['buy']))
a=pd.DataFrame(rows);a['pepos']=a.pe.where(a.pe.gt(0));a['pestate']=np.select([a.pe.isna(),a.pe.le(0)],['缺失','非正'],default='正值');a.to_pickle(P/'enriched.pkl')
summary=[]
for (month,method),g in a.groupby(['month','method'],sort=True):
 summary.append(dict(月份=month,方案=method,买入数=len(g),已卖数=g.ret.notna().sum(),笔均净收益=g.ret.mean(),市值中位亿=g.mv.median(),市值缺失=g.mv.isna().sum(),成交额中位亿=g.amount.median(),成交量中位亿股=g.volume.median(),换手中位=g.turn.median(),正PE中位=g.pepos.median(),PE有值=g.pe.notna().sum(),PE非正=g.pe.le(0).sum()))
# Fixed descriptive boundaries; not searched against observed returns.
features=[('mv','总市值','100亿',100.),('amount','成交额','10亿',10.),('volume','成交量','1亿股',1.),('turn','换手率','10%',.1),('pepos','正PE','50倍',50.)]
groups=[];corr=[]
for (month,method),g in a.groupby(['month','method'],sort=True):
 for col,name,label,cut in features:
  z=g[g[col].notna()&g.ret.notna()];lo=z[z[col].le(cut)];hi=z[z[col].gt(cut)]
  sufficient=len(lo)>=3 and len(hi)>=3
  groups.append(dict(月份=month,方案=method,变量=name,分界=label,低组数=len(lo),高组数=len(hi),低组笔均收益=lo.ret.mean(),高组笔均收益=hi.ret.mean(),高减低=hi.ret.mean()-lo.ret.mean(),低组亏超5率=lo.ret.lt(-.05).mean() if len(lo) else np.nan,高组亏超5率=hi.ret.lt(-.05).mean() if len(hi) else np.nan,样本提示='可观察' if sufficient else '至少一组不足3笔'))
  corr.append(dict(月份=month,方案=method,变量=name,有效数=len(z),秩相关=z[col].rank().corr(z.ret.rank()) if len(z)>=6 and z[col].nunique()>=3 else np.nan))
g=pd.DataFrame(groups);c=pd.DataFrame(corr);cons=[]
for (method,var),q in g[g.月份.ne('2026-10')].groupby(['方案','变量']):
 q=q[(q.低组数>=3)&(q.高组数>=3)]
 cs=c[(c.方案==method)&(c.变量==var)&c.月份.ne('2026-10')].dropna(subset=['秩相关'])
 cons.append(dict(方案=method,变量=var,可比月份=len(q),高组更好月数=int(q.高减低.gt(0).sum()),低组更好月数=int(q.高减低.lt(0).sum()),月差中位=q.高减低.median(),相关有效月=len(cs),正相关月数=int(cs.秩相关.gt(0).sum()),负相关月数=int(cs.秩相关.lt(0).sum())))
cons=pd.DataFrame(cons);print(pd.DataFrame(summary).to_string(index=False));print('CONSISTENCY');print(cons.to_string(index=False));print('coverage',a[['mv','pe']].notna().sum().to_dict(),'pe_nonpositive',int(a.pe.le(0).sum()))
for name,data in [('monthly',pd.DataFrame(summary)),('groups',g),('correlations',c),('consistency',cons)]:data.to_csv(P/(name+'.csv'),index=False)
detail=a[['date','method','ret','mv','amount','volume','turn','pe','pestate','mvsrc']].rename(columns=dict(date='买入日',method='方案',ret='净收益',mv='D0市值亿',amount='D0成交额亿',volume='D0成交量亿股',turn='D0换手',pe='D0市盈率TTM',pestate='PE情况',mvsrc='市值来源'))
# JSON nulls preserve unavailable values.
out=dict(monthly=pd.DataFrame(summary).to_dict('records'),groups=g.to_dict('records'),consistency=cons.to_dict('records'),detail=detail.to_dict('records'))
(P/'workbook_data.json').write_text(pd.Series(out).to_json(force_ascii=False))

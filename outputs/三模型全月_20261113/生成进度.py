from pathlib import Path
import pandas as pd,json
P=Path(__file__).parent;old=P.parent/'多年份训练与组合对照_20261113';F=P.parent/'D0分时形态研究_20261113';d=pd.read_csv(old/'逐月结果.csv');d=d[d['方法'].eq('各取前二去重')].set_index('月份');res=[]
for month in pd.period_range('2018-01','2026-11',freq='M').astype(str):
 r={'月份':month,'账户状态':'未完成','账户收益%':None,'最大收盘回撤%':None,'未结清估值收益%':None,'未结清期间最大回撤%':None,'假设买入笔数':None,'涨停价买入笔数':None,'入选股次':None,'D2收盘收益可计算股次':None,'D2收盘盈利股次':None,'D2收盘亏至少7%股次':None,'收益来源':'','备注':''}
 if month in d.index:
  a=d.loc[month];r.update({'账户状态':'已有完整回放' if a['完整'] else '路径未完整','账户收益%':a['账户收益%'],'最大收盘回撤%':a['最大收盘回撤%'],'假设买入笔数':a['买入笔数'],'涨停价买入笔数':a['涨停价买入笔数'],'入选股次':a['选股股次'],'D2收盘收益可计算股次':a['可计算股次'],'D2收盘盈利股次':a['上涨股次'],'D2收盘亏至少7%股次':a['大跌7%股次'],'收益来源':'既有研究','备注':'月初10万元独立账户；含成交假设'})
 if month=='2026-11':
  recent_dir=P.parent/'最近两周三模型_20261113/十一月独立';recent=json.loads((recent_dir/'模拟账户概要.json').read_text());transactions=pd.read_csv(recent_dir/'模拟买卖流水.csv');buys=transactions[transactions.action.eq('假设买入')];assert len(buys)==recent['买入笔数'];cap_buys=int((buys.price-buys['limit']).abs().le(.011).sum());r.update({'账户状态':'部分月未结清','未结清估值收益%':recent['截至当日收益%'],'未结清期间最大回撤%':recent['最大收盘回撤%'],'假设买入笔数':recent['买入笔数'],'涨停价买入笔数':cap_buys,'收益来源':'11月部分月独立账户','备注':'截至11月13日收盘，5笔仍持有，按当日收盘估值；非全月结果'})
 y=month[:4];path=P/'账户'/y/(y+'_原始10_逐批次.csv');trial=False
 if y=='2022' and not path.exists():path=P/'试跑_1.7.1/账户_2022'/(y+'_原始10_逐批次.csv');trial=True
 if path.exists():
  x=pd.read_csv(path);x=x[x.month.eq(month+'__各取前二去重')]
  if len(x)==1:
   a=x.iloc[0];events_path=path.parent/(y+'_原始10_逐笔.pkl.gz'); events=pd.read_pickle(events_path) if events_path.exists() else pd.DataFrame(); buys=events[events.month.eq(month+'__各取前二去重') & events.action.eq('假设买入')] if len(events) else pd.DataFrame();n_buys=len(buys) if len(events) else None;n_cap=int((buys.price-buys.order_limit).abs().le(.011).sum()) if len(buys) else (0 if len(events) else None);selection_path=path.parent/'每日选股.csv'; selection=pd.read_csv(selection_path) if selection_path.exists() else pd.DataFrame(); selection=selection[selection['月份'].astype(str).eq(month)] if len(selection) else selection; ret=pd.to_numeric(selection['价格收益%'],errors='coerce') if len(selection) else pd.Series(dtype=float);verified=(path.parent/'核验.json').exists() and (path.parent/'份额核验.json').exists();r.update({'账户状态':('试跑路径完整待同环境复现' if trial else '新回放完整' if verified else '回放完成待核验') if a['complete'] else ('持仓未结清' if a.positions>0 and a.unknown_orders==0 and a.unknown_sessions==0 else '新回放路径未完整'),'账户收益%':a.return_pct if a.complete else None,'最大收盘回撤%':a.drawdown_pct if a.complete else None,'未结清估值收益%':a.return_pct if not a.complete else None,'未结清期间最大回撤%':a.drawdown_pct if not a.complete else None,'假设买入笔数':n_buys,'涨停价买入笔数':n_cap,'入选股次':len(selection),'D2收盘收益可计算股次':int(ret.notna().sum()),'D2收盘盈利股次':int(ret.gt(0).sum()),'D2收盘亏至少7%股次':int(ret.le(-7).sum()),'收益来源':'1.7.1环境试跑' if trial else ('备用来源' if '2022-01'<=month<='2022-04' else '本次补跑'),'备注':f"未知买入 {a.unknown_orders}；未结持仓 {a.positions}；路径缺口 {a.unknown_sessions}"})
 res.append(r)
pd.DataFrame(res).to_csv(P/'月度进度.csv',index=False);print(pd.DataFrame(res).groupby('账户状态').size().to_string())

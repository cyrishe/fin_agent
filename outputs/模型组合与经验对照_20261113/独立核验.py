from pathlib import Path
import json,hashlib,pandas as pd,numpy as np
P=Path(__file__).parent;x=pd.read_pickle(P/'全部候选及结果.pkl.gz');q=pd.read_csv(P/'经验策略逐股.csv',dtype={'代码':str});d=pd.read_csv(P/'每日同池对照.csv');checks=0
for day,g in x.groupby('d0_date'):
 k=int(g.组合入选.sum());orders={'组合各取前二':g[g.组合入选].symbol.tolist(),'连板数优先':g.sort_values(['board_streak_days','symbol'],ascending=[False,True]).head(k).symbol.tolist(),'成交额优先':g.sort_values(['d0_amount_100m','symbol'],ascending=[False,True]).head(k).symbol.tolist()}
 for m,expected in orders.items():
  got=q[(q.D0.eq(day))&q.策略.eq(m)].代码.tolist();assert set(got)==set(expected) and len(got)==k;checks+=1
 for horizon,col in [('D2收盘','ret'),('D3收盘','d3_ret')]:
  z=g[g[col].notna()];v=d[(d.D0.eq(day))&d.比较终点.eq(horizon)&d.策略.eq('随机同池')].iloc[0];assert np.isclose(v['平均价格收益%'],z[col].mean(),atol=1e-10);assert v.池内可计算只数==len(z);checks+=1
v=x[x.ret.notna()];np.testing.assert_allclose(v.ret,100*(v.d2_close*.999/(v.d1_open*1.001)-1),atol=1e-10);v=x[x.d3_ret.notna()];assert v.corporate_events.eq(0).all() and v.d2_qdc_match.all();np.testing.assert_allclose(v.d3_ret,100*(v.d3_qdc_close*.999/(v.d1_open*1.001)-1),atol=1e-10)
z=pd.read_pickle(P/'带行业全部候选.pkl.gz');z=z[z.行业可用];diff=(z.d0_return-z.个股相对前收).abs();assert diff.le(.001).all();hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [P/'全部候选及结果.pkl.gz',P/'每日同池对照.csv',P/'逐年同日对照.csv',P/'差异月度重采样.csv',P/'行业强弱_逐年同日.csv']};out={'选股与随机基准核验数':checks,'测试日':x.d0_date.nunique(),'D2计算条数':int(x.ret.notna().sum()),'D3计算条数':int(x.d3_ret.notna().sum()),'行业可用股次':len(z),'行业个股涨幅最大差':float(diff.max()),'材料SHA256':hashes,'说明':'核验排名、均值、价格公式与源内行业前收一致；不证明数据源历史版本、实盘成交及策略盲测。'};(P/'核验.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))

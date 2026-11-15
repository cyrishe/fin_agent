from pathlib import Path
import sys,json,traceback
import pandas as pd
P=Path(__file__).parent;methods=['原连板模型','收益兼顾下跌','分时模型','分时加远期模型']
months=sys.argv[1:] or [p.name for p in sorted((P/'远期逐月模型').iterdir()) if (p/'预测.pkl.gz').exists()]
for month in months:
 try:
  a=pd.read_pickle(P/'逐月模型'/month/'预测.pkl.gz');b=pd.read_pickle(P/'远期逐月模型'/month/'预测.pkl.gz');b=b[['d0_date','symbol','stage','分时加远期模型','分时加远期模型_rank']];z=a.merge(b,on=['d0_date','symbol','stage'],validate='one_to_one');v=z[z.stage.eq('validation')];per={}
  for method in methods:
   g=v[v[method+'_rank'].le(5)].groupby('d0_date').agg(n=('ret','size'),known=('ret','count'),utility=('utility','mean'));per[method]=g[g.n.eq(5)&g.known.eq(5)].utility
  common=set.intersection(*(set(q.index) for q in per.values()));scores={m:float(q.loc[sorted(common)].mean()) for m,q in per.items()} if len(common)>=10 else {};choice=max(scores,key=lambda m:(scores[m],m=='原连板模型')) if scores else '原连板模型';t=z[z.stage.eq('test')].copy();t['四方案验证选定']=t[choice];o=t.sort_values(['d0_date','四方案验证选定','symbol'],ascending=[True,False,True]);t.loc[o.index,'四方案验证选定_rank']=o.groupby('d0_date').cumcount().to_numpy()+1
  out=P/'组合逐月模型'/month;out.mkdir(parents=True,exist_ok=True);t.to_pickle(out/'预测.pkl.gz',compression='gzip');(out/'概要.json').write_text(json.dumps({'月份':month,'验证共同日期':len(common),'四方案验证分数':scores,'选择':choice,'说明':'验证以D1开盘到D2收盘收益惩罚大跌，不能代表最终账户收益'},ensure_ascii=False,indent=2));print(month,choice,len(common),flush=True)
 except Exception as e:print('失败',month,repr(e),flush=True)

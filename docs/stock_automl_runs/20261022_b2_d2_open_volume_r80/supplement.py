from pathlib import Path
import numpy as np
import pandas as pd

root=Path('/data/cyris/work/b2_d2_open_volume_r80_20261022')
x=pd.read_csv(root/'100笔D2开盘同刻量比.csv',dtype={'股票':str})
opening=x[x['D2开盘跌停'].eq(1)].copy()
opening['年份']=opening['D2跌停日'].str[:4]

def auc(scores,y):
    a=np.asarray(scores[y==1],float)
    b=np.asarray(scores[y==0],float)
    if not len(a) or not len(b):
        return np.nan
    return float(((a[:,None]>b[None,:]).sum()+.5*(a[:,None]==b[None,:]).sum())/(len(a)*len(b)))

rows=[]
for cond,sub in [('开盘跌停',opening),('09:31仍封死',opening[opening['首分钟整根封死'].eq(1)])]:
    for period,g in [('全部',sub)]+list(sub.groupby('年份')):
        for n in (5,10,20):
            col=f'D2开盘首分钟量_股除板前{n}日同刻均值'
            z=g[g[f'板前{n}日同刻完整'].eq(1)&g[col].notna()]
            y=z['D2全天一价封死'].eq(0).astype(int).to_numpy()
            rows.append({'已知条件':cond,'年份':period,'板前同刻均值日数':n,
                         '可比笔数':len(z),'全天封死':int((y==0).sum()),'后来打开':int((y==1).sum()),
                         '封死量比中位':z.loc[z['D2全天一价封死'].eq(1),col].median(),
                         '打开量比中位':z.loc[z['D2全天一价封死'].eq(0),col].median(),
                         '量比AUC':auc(z[col].to_numpy(),y),
                         '绝对首分钟量AUC':auc(z['开盘首分钟量_股'].to_numpy(),y)})
pd.DataFrame(rows).to_csv(root/'09_31_跨年与基线稳健性.csv',index=False,encoding='utf-8-sig',float_format='%.6f')

z=opening[opening['首分钟整根封死'].eq(1)&opening['板前5日同刻完整'].eq(1)]
v='D2开盘首分钟量_股除板前5日同刻均值'
bucket=[]
for t in (0.5,1,2,5,10):
    high=z[v].ge(t)
    bucket.append({'开盘量比阈值':t,'可比笔数':len(z),'高量笔数':int(high.sum()),
                   '高量后打开':int((high&z['D2全天一价封死'].eq(0)).sum()),
                   '高量仍全天封死':int((high&z['D2全天一价封死'].eq(1)).sum()),
                   '低量笔数':int((~high).sum()),
                   '低量后打开':int((~high&z['D2全天一价封死'].eq(0)).sum()),
                   '低量全天封死':int((~high&z['D2全天一价封死'].eq(1)).sum())})
pd.DataFrame(bucket).to_csv(root/'09_31仍封死_固定量比阈值.csv',index=False,encoding='utf-8-sig')

z=x[x['板前5日同刻完整'].eq(1)&x[v].notna()].copy()
rows=[]
for label,g in [('100笔触板',z),('34笔开盘触板',z[z['D2开盘跌停'].eq(1)]),
                ('66笔盘中触板',z[z['D2开盘跌停'].eq(0)])]:
    for outcome in ['首触后曾有后续完整分钟离板','D2全天一价封死']:
        y=g[outcome].astype(int).to_numpy()
        pos=g.loc[y==1,v]
        neg=g.loc[y==0,v]
        rows.append({'样本':label,'结果':outcome,'可比':len(g),'结果是':len(pos),'结果否':len(neg),
                     '是组开盘量比中位':pos.median(),'否组开盘量比中位':neg.median(),
                     '量比AUC':auc(g[v].to_numpy(),y)})
pd.DataFrame(rows).to_csv(root/'100笔开盘量比与触板后离板.csv',index=False,encoding='utf-8-sig',float_format='%.6f')

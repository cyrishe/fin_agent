from pathlib import Path
import json, numpy as np, pandas as pd
ROOT=Path('/data/cyris/work/b2_d2_open_volume_r80_20261022')
x=pd.read_csv(ROOT/'100笔D2开盘同刻量比.csv',dtype={'股票':str})
x['D2年']=x['D2跌停日'].str[:4]
assert len(x)==100 and int(x['D2开盘跌停'].sum())==34 and int(x['D2全天一价封死'].sum())==14
one='D2开盘首分钟量_股除板前5日同刻均值'
five='D2开盘前5分钟量_股除板前5日同刻均值'
ten='D2开盘前10分钟量_股除板前5日同刻均值'
def auc(scores,positive):
 a=np.array(scores[positive==1],float)
 b=np.array(scores[positive==0],float)
 if not len(a) or not len(b):return np.nan
 return float(((a[:,None]>b[None,:]).sum()+.5*(a[:,None]==b[None,:]).sum())/(len(a)*len(b)))
def summary(group,label):
 z=group[group['板前5日同刻完整'].eq(1)]
 out={'组别':label,'总笔数':len(group),'有完整板前5日同刻笔数':len(z),'一价封死':int(group['D2全天一价封死'].sum()),
      '首分钟整根仍封死':int(group['首分钟整根封死'].sum()),
      '后续整分钟离板':int(group['首触后曾有后续完整分钟离板'].sum())}
 for col,abbr in [(one,'09:31'),(five,'09:35'),(ten,'09:40')]:
  vals=z[col].dropna()
  out[abbr+'量比中位']=vals.median() if len(vals) else np.nan
  out[abbr+'量比P25']=vals.quantile(.25) if len(vals) else np.nan
  out[abbr+'量比P75']=vals.quantile(.75) if len(vals) else np.nan
 return out
all_groups=[]
for label,g in x.groupby('跌停分类'):
 all_groups.append(summary(g,label))
all_groups.append(summary(x,'全部100笔'))
pd.DataFrame(all_groups).to_csv(ROOT/'100笔_开盘量比分组.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
opening=x[x['D2开盘跌停'].eq(1)].copy()
assert len(opening)==34
early=opening[opening['首分钟整根封死'].eq(1)]
later=opening[opening['首分钟整根封死'].eq(0)]
print('opening floor',len(opening),'first minute sealed',len(early),'opened first minute',len(later))
print('full5 coverage',int(opening['板前5日同刻完整'].sum()),'first minute still sealed full5',int(early['板前5日同刻完整'].sum()))
rows=[]
for feature,asof,group in [(one,'09:31完成','开盘跌停34'),(five,'09:35完成','开盘跌停34'),(ten,'09:40完成','开盘跌停34'),
                           (one,'09:31完成','首分钟仍封死'),(five,'09:35完成','前5分钟仍封死'),(ten,'09:40完成','前10分钟仍封死')]:
 sub={'开盘跌停34':opening,'首分钟仍封死':early,'前5分钟仍封死':opening[opening['前5分钟均未高于跌停'].eq(1)],'前10分钟仍封死':opening[opening['前10分钟均未高于跌停'].eq(1)]}[group]
 sub=sub[sub['板前5日同刻完整'].eq(1)&sub[feature].notna()].copy()
 pos=(sub['D2全天一价封死'].eq(0)).astype(int)
 sealed=sub.loc[pos.eq(0),feature]
 reopened=sub.loc[pos.eq(1),feature]
 out={'时间':asof,'已知开盘条件':group,'可比笔数':len(sub),'全天一价封死':len(sealed),'当日后来打开':len(reopened),
      '封死量比中位':sealed.median(),'后来打开量比中位':reopened.median(),
      '放量预测打开_AUC':auc(sub[feature].to_numpy(),pos.to_numpy()),
      '封死量比最小':sealed.min(),'封死量比最大':sealed.max(),'打开量比最小':reopened.min(),'打开量比最大':reopened.max()}
 rows.append(out)
stats=pd.DataFrame(rows)
stats.to_csv(ROOT/'开盘跌停34笔_同刻量比分辨力.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
print(stats.to_string(index=False))

bucket=[]
for n in (5,10,20):
 col=f'D2开盘首分钟量_股除板前{n}日同刻均值'
 z=opening[opening[f'板前{n}日同刻完整'].eq(1)&opening[col].notna()].copy()
 for period,part in [('全部',z),('2022-24',z[z.D2年.le('2024')]),('2025-26',z[z.D2年.ge('2025')])]:
  for threshold in (.5,1,2):
   hi=part[col]>=threshold
   bucket.append({'板前日数':n,'时期':period,'量比阈值':threshold,'可比笔数':len(part),
                  '高量笔数':int(hi.sum()),'高量中打开':int((hi&part['D2全天一价封死'].eq(0)).sum()),
                  '低量笔数':int((~hi).sum()),'低量中封死':int((~hi&part['D2全天一价封死'].eq(1)).sum())})
pd.DataFrame(bucket).to_csv(ROOT/'开盘跌停34笔_固定量比阈值.csv',index=False,encoding='utf-8-sig')

coverage=x.groupby(['D2年','D2开盘跌停','D2全天一价封死']).agg(笔数=('股票','size'),
   完整5日=('板前5日同刻完整','sum'),完整10日=('板前10日同刻完整','sum'),完整20日=('板前20日同刻完整','sum')).reset_index()
coverage.to_csv(ROOT/'逐年同刻覆盖.csv',index=False,encoding='utf-8-sig')
cols=['股票','D0选中日','D2跌停日','D0已连板天数','D2开盘价','D2跌停价','D2全天一价封死','首分钟整根封死','前5分钟均未高于跌停','前10分钟均未高于跌停',
      '首分钟后曾高于跌停','首触后曾有后续完整分钟离板','D3收盘跌停','板前5日同刻完整',
      '开盘首分钟量_股','板前5日开盘首分钟量_股均值',one,
      '开盘前5分钟量_股','板前5日开盘前5分钟量_股均值',five,
      '开盘前10分钟量_股','板前5日开盘前10分钟量_股均值',ten,
      '板前10日同刻完整','D2开盘首分钟量_股除板前10日同刻均值',
      '板前20日同刻完整','D2开盘首分钟量_股除板前20日同刻均值',
      '首板前20日均量_万股','D2成交量_万股','D2量除首板前20日均量']
opening[cols].sort_values(['D2跌停日','股票']).to_csv(ROOT/'开盘跌停34笔_逐笔同刻成交量.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
print('BUCKET5')
print(pd.DataFrame(bucket).query('板前日数==5').to_string(index=False))
print('COVERAGE')
print(coverage.to_string(index=False))
analysis={'n_all_touch':len(x),'n_open_floor':len(opening),'n_all_day_locked':int(opening['D2全天一价封死'].sum()),
          'n_first_minute_still_locked':len(early),'n_opened_first_minute':len(later),
          'n_complete5_all':int(x['板前5日同刻完整'].sum()),
          'n_complete10_all':int(x['板前10日同刻完整'].sum()),
          'n_complete20_all':int(x['板前20日同刻完整'].sum()),
          'n_complete5_opening':int(opening['板前5日同刻完整'].sum()),
          'feature_clock_starts_at_0931_including_auction':True}
(ROOT/'analysis.json').write_text(json.dumps(analysis,ensure_ascii=False,indent=2)+chr(10))

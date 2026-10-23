from pathlib import Path
import pandas as pd
root=Path('/data/cyris/work/b2_d2_open_volume_ranks_r81_20261022')
x=pd.read_csv(root/'100笔_开盘量能候选变量.csv',dtype={'股票':str})
a=x[x['D2开盘跌停'].eq(1)&x['首分钟整根封死'].eq(1)&x['板前20日同刻完整'].eq(1)].copy()
a=a.sort_values(['首分钟量除板前20日同刻最大','D2跌停日','股票']).reset_index(drop=True)
a['板前20日同刻最大量比_低到高名次']=a.index+1
a['板前20日同刻最大量比分段']='中段9'
a.loc[a.index[:5],'板前20日同刻最大量比分段']='低端5'
a.loc[a.index[-5:],'板前20日同刻最大量比分段']='高端5'
cols=['股票','D0选中日','D2跌停日','板前20日同刻最大量比_低到高名次','板前20日同刻最大量比分段',
      '开盘首分钟量_股','D2开盘首分钟量_股除板前5日同刻均值',
      'D2开盘首分钟量_股除板前10日同刻均值',
      'D2开盘首分钟量_股除板前20日同刻均值',
      '首分钟量除板前20日同刻最大','首分钟量占板前20日日均量',
      '首分钟量占D1全天量','D1全天量除板前20日日均量',
      'D2全天一价封死','首触后曾有后续完整分钟离板','首触后完整离板分钟数','D3收盘跌停']
a[cols].to_csv(root/'09_31仍封死19笔_核心变量与结局.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
c=x[x['D2开盘跌停'].eq(0)&(x['D2首触跌停分钟']>'09:31')&x['板前20日同刻完整'].eq(1)].copy()
c=c.sort_values(['首分钟量除板前20日同刻最大','D2跌停日','股票']).reset_index(drop=True)
c['板前20日同刻最大量比_低到高名次']=c.index+1
c[['股票','D0选中日','D2跌停日','D2首触跌停分钟','板前20日同刻最大量比_低到高名次',
   '开盘首分钟量_股','首分钟量除板前20日同刻最大',
   'D2开盘首分钟量_股除板前10日同刻均值','首触后曾有后续完整分钟离板',
   '首触后完整离板分钟数','D3收盘跌停']].to_csv(root/'开盘未跌停51笔_09_31变量与触板后结局.csv',
   index=False,encoding='utf-8-sig',float_format='%.8f')
r=pd.read_csv(root/'逐变量逐笔高低排序.csv',dtype={'股票':str})
out=[]
for cohort in ['A_开盘跌停且09_31仍封死','C_开盘未跌停且09_31前未触板']:
    for feature in ['F03_首分钟对板前10日同刻均量','F05_首分钟对板前20日同刻最大',
                    'F08_首分钟占D1全天量','F09a_D1全天量对板前20日日均']:
        z=r[(r['时点与样本']==cohort)&(r['变量']==feature)].sort_values('低到高名次')
        for k in (3,5,7):
            lo=z.head(k);hi=z.tail(k)
            out.append({'样本':cohort,'变量':feature,'两端各取笔数':k,
              '低端good':int(lo['good后来打开或离板'].sum()),
              '低端bad':int(lo['bad全天封死或不再离板'].sum()),
              '高端good':int(hi['good后来打开或离板'].sum()),
              '高端bad':int(hi['bad全天封死或不再离板'].sum()),
              '低端D3收盘跌停':int(lo['D3收盘跌停'].sum()),
              '高端D3收盘跌停':int(hi['D3收盘跌停'].sum())})
pd.DataFrame(out).to_csv(root/'极值取3_5_7笔敏感性.csv',index=False,encoding='utf-8-sig')
assert len(a)==19 and int(a['D2全天一价封死'].sum())==13
assert len(c)==51 and int(c['首触后曾有后续完整分钟离板'].sum())==47
print('a',len(a),'c',len(c),'rank cases',len(r))

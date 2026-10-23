from pathlib import Path
import json, numpy as np, pandas as pd
ROOT=Path('/data/cyris/work/b2_d2_open_volume_ranks_r81_20261022')
R80=Path('/data/cyris/work/b2_d2_open_volume_r80_20261022')
x=pd.read_csv(R80/'100笔D2开盘同刻量比.csv',dtype={'股票':str})
p=pd.read_csv(R80/'preboard_daily_selected.csv.gz',dtype={'stk_code':str})
pre=pd.read_csv(R80/'首板前同刻分钟源_逐股日.csv.gz',dtype={'stk_code':str})
valid=pre[pre['有效'].eq(1)].set_index(['trade_date','stk_code'])
assert len(x)==100 and len(p)==1880 and int(pre['有效'].sum())==1806
rows=[]
for _,r in x.iterrows():
    row=r.to_dict(); code=r['股票']; vol=float(r['开盘首分钟量_股'])
    hist=p[(p.stk_code==code)&(p['对应首板日']==r['首板日'])].sort_values('trade_date',ascending=False)
    if int(r['板前20日同刻完整']):
        dates=hist.head(20).trade_date.tolist()
        hv=valid.loc[[(d,code) for d in dates],'开盘首分钟量_股'].to_numpy(float)
        assert len(hv)==20 and np.isfinite(hv).all()
        row['首分钟量除板前20日同刻最大']=vol/hv.max() if hv.max()>0 else np.nan
        row['首分钟量在板前20日同刻百分位']=((hv<vol).sum()+.5*(hv==vol).sum())/20
        row['首分钟量占板前20日日均量']=vol/float(hist.head(20).volume.mean())
    row['首分钟量占D1全天量']=vol/(float(r['D1成交量_万股'])*10000)
    row['首分钟量占D0全天量']=vol/(float(r['D0成交量_万股'])*10000)
    row['D1全天量除板前20日日均量']=float(r['D1成交量_万股'])/float(r['首板前20日均量_万股']) if float(r['首板前20日均量_万股'])>0 else np.nan
    if int(r['板前5日同刻完整']):
        base=float(r['板前5日开盘前5分钟量_股均值'])-float(r['板前5日开盘首分钟量_股均值'])
        sub=float(r['开盘前5分钟量_股'])-vol
        row['09_32至09_35量除板前5日同刻均量']=sub/base if base>0 else np.nan
    rows.append(row)
feat=pd.DataFrame(rows)
feat.to_csv(ROOT/'100笔_开盘量能候选变量.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
base=feat[feat['板前20日同刻完整'].eq(1)].copy()
cohorts={
 'A_开盘跌停且09_31仍封死':(base[(base['D2开盘跌停'].eq(1))&(base['首分钟整根封死'].eq(1))],'D2全天一价封死'),
 'B_开盘跌停全体':(base[base['D2开盘跌停'].eq(1)],'D2全天一价封死'),
 'C_开盘未跌停且09_31前未触板':(base[(base['D2开盘跌停'].eq(0))&(base['D2首触跌停分钟']>'09:31')],'首触后曾有后续完整分钟离板'),
 'D_开盘跌停且09_35仍封死':(base[(base['D2开盘跌停'].eq(1))&(base['前5分钟均未高于跌停'].eq(1))],'D2全天一价封死'),
 'E_开盘跌停且09_40仍封死':(base[(base['D2开盘跌停'].eq(1))&(base['前10分钟均未高于跌停'].eq(1))],'D2全天一价封死')}
f0931={
 'F01_首分钟绝对成交股数':'开盘首分钟量_股',
 'F02_首分钟对板前5日同刻均量':'D2开盘首分钟量_股除板前5日同刻均值',
 'F03_首分钟对板前10日同刻均量':'D2开盘首分钟量_股除板前10日同刻均值',
 'F04_首分钟对板前20日同刻均量':'D2开盘首分钟量_股除板前20日同刻均值',
 'F05_首分钟对板前20日同刻最大':'首分钟量除板前20日同刻最大',
 'F07_首分钟占板前20日日均量':'首分钟量占板前20日日均量',
 'F08_首分钟占D1全天量':'首分钟量占D1全天量',
 'F09_首分钟占D0全天量':'首分钟量占D0全天量',
 'F09a_D1全天量对板前20日日均':'D1全天量除板前20日日均量'}
f0935={
 'F10_前5分钟对板前5日同刻均量':'D2开盘前5分钟量_股除板前5日同刻均值',
 'F11_前5分钟对板前20日同刻均量':'D2开盘前5分钟量_股除板前20日同刻均值',
 'F12_09_32至09_35对板前5日同刻均量':'09_32至09_35量除板前5日同刻均量'}
f0940={
 'F13_前10分钟对板前5日同刻均量':'D2开盘前10分钟量_股除板前5日同刻均值',
 'F14_前10分钟对板前20日同刻均量':'D2开盘前10分钟量_股除板前20日同刻均值'}
def auc(score,y):
    a=np.asarray(score[y==1],float);b=np.asarray(score[y==0],float)
    if not len(a) or not len(b):return np.nan
    return float(((a[:,None]>b[None,:]).sum()+.5*(a[:,None]==b[None,:]).sum())/(len(a)*len(b)))
rank_rows=[];segments=[];thirds=[]
for cohort,(g,outcome) in cohorts.items():
    features=f0931 if cohort[0] in 'ABC' else (f0935 if cohort[0]=='D' else f0940)
    for name,col in features.items():
        sub=g[g[col].notna()].copy()
        sub['good']=sub[outcome].eq(0).astype(int) if cohort[0] in 'ABDE' else sub[outcome].astype(int)
        sub=sub.sort_values([col,'D2跌停日','股票','D0选中日']).reset_index(drop=True)
        n=len(sub);k=min(5,n//3)
        if n<3*k or k<1:continue
        sub['低到高名次']=np.arange(1,n+1)
        sub['分段']=np.where(sub.index<k,'低端'+str(k),np.where(sub.index>=n-k,'高端'+str(k),'中段'))
        for _,r in sub.iterrows():
            rank_rows.append({'时点与样本':cohort,'变量':name,'低到高名次':int(r['低到高名次']),
                              '变量值':float(r[col]),'分段':r['分段'],'股票':r['股票'],
                              'D0选中日':r['D0选中日'],'D2日':r['D2跌停日'],
                              '首触跌停分钟':r['D2首触跌停分钟'],
                              'good后来打开或离板':int(r['good']),
                              'bad全天封死或不再离板':int(1-r['good']),
                              'D3收盘跌停':int(r['D3收盘跌停']),
                              'D2全天一价封死':int(r['D2全天一价封死']),
                              '首触后完整离板分钟数':int(r['首触后完整离板分钟数'])})
        lo=sub.iloc[:k];mid=sub.iloc[k:n-k];hi=sub.iloc[n-k:]
        segments.append({'时点与样本':cohort,'变量':name,'可比笔数':n,'总体good':int(sub.good.sum()),
          '总体bad':int(n-sub.good.sum()),'低端笔数':len(lo),'低端good':int(lo.good.sum()),
          '低端bad':int(len(lo)-lo.good.sum()),'低端最大变量值':float(lo[col].max()),
          '中段笔数':len(mid),'中段good':int(mid.good.sum()),
          '中段bad':int(len(mid)-mid.good.sum()),'高端笔数':len(hi),
          '高端good':int(hi.good.sum()),'高端bad':int(len(hi)-hi.good.sum()),
          '高端最小变量值':float(hi[col].min()),
          '高减低good百分点':100*(hi.good.mean()-lo.good.mean()),
          '高量预示good的历史AUC':auc(sub[col].to_numpy(),sub.good.to_numpy()),
          '低端D3再跌停':int(lo['D3收盘跌停'].sum()),
          '高端D3再跌停':int(hi['D3收盘跌停'].sum())})
        for i,idx in enumerate(np.array_split(np.arange(len(sub)),3)):
            part=sub.iloc[idx]
            thirds.append({'时点与样本':cohort,'变量':name,'低到高三等分':i+1,'笔数':len(part),
              'good':int(part.good.sum()),'bad':int(len(part)-part.good.sum()),
              'good比例':part.good.mean(),'变量最小':float(part[col].min()),
              '变量最大':float(part[col].max()),'D3再跌停':int(part['D3收盘跌停'].sum())})
pd.DataFrame(rank_rows).to_csv(ROOT/'逐变量逐笔高低排序.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
pd.DataFrame(segments).to_csv(ROOT/'变量高低极值_good_bad.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
pd.DataFrame(thirds).to_csv(ROOT/'变量三等分_good_bad.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
checks={'cohort_counts':{key:len(g) for key,(g,_) in cohorts.items()},
 'good_counts':{key:int((g[outcome].eq(0) if key[0] in 'ABDE' else g[outcome].eq(1)).sum()) for key,(g,outcome) in cohorts.items()},
 'features':{**f0931,**f0935,**f0940},'rows_extreme':len(segments),'rows_ranked':len(rank_rows)}
(ROOT/'analysis.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(checks,ensure_ascii=False))
s=pd.DataFrame(segments)
print(s[s['时点与样本'].str.startswith('A_')][['变量','可比笔数','低端good','低端bad','中段good','中段bad','高端good','高端bad','高减低good百分点','高量预示good的历史AUC']].to_string(index=False))
print(s[s['时点与样本'].str.startswith('C_')][['变量','可比笔数','低端good','低端bad','高端good','高端bad','高减低good百分点']].to_string(index=False))

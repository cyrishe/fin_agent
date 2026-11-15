from pathlib import Path
import numpy as np
import pandas as pd

R=Path('/Volumes/ext/fin_agent/outputs'); P=Path(__file__).parent; H=R/'三模型全月_20261113'; N=R/'最近两周三模型_20261113'
sources={k:pd.read_pickle(p) for k,p in {'early':H/'早期/全部输入.pkl.gz','late':H/'全部输入.pkl.gz','near':H/'近月/全部输入.pkl.gz','alt':H/'2022备用来源/全部输入.pkl.gz'}.items()}
selections={str(y):pd.read_csv(H/f'账户/{y}/每日选股.csv',dtype={'symbol':str}) for y in range(2018,2027)}
frames=[]
for month in pd.period_range('2018-01','2026-10',freq='M').astype(str):
    source='alt' if '2022-01'<=month<='2022-04' else 'early' if month<'2022-01' else 'near' if month=='2026-10' else 'late'
    g=sources[source]; g=g[g.d0_date.str.startswith(month)].copy(); s=selections[month[:4]]; s=s[s['月份'].eq(month)]
    assert not s.duplicated(['d0_date','symbol']).any()
    g=g.merge(s[['d0_date','symbol']].assign(selected=True),on=['d0_date','symbol'],how='left',validate='one_to_one');g['selected']=g.selected.fillna(False)
    assert g.selected.sum()==len(s),(month,g.selected.sum(),len(s))
    g['来源']=source;frames.append(g)
x=pd.read_pickle(N/'十一月全候选与排名.pkl.gz'); q=pd.read_csv(N/'十一月十三日双源校验.csv',dtype={'symbol':str});q['usable']=q.公开源最新分钟日期.fillna('').str.startswith('2026-11-13')&q.最新价差.le(.011);q=q[['symbol','前收','开盘','最新价','原始涨停标记','usable']];x=x.merge(q,on='symbol',how='left',validate='many_to_one')
a=x.d0_date.eq('2026-11-12')&x.usable.fillna(False)&(x.前收-x.close).abs().le(.011);x.loc[a,'d1_up']=x.loc[a,'原始涨停标记'].astype(bool);x.loc[a,'d1_open']=x.loc[a,'开盘'];x.loc[a,'d1_close']=x.loc[a,'最新价']
b=x.d0_date.eq('2026-11-11')&x.usable.fillna(False)&(x.前收-x.d1_close).abs().le(.011);x.loc[b,'d2_up']=x.loc[b,'原始涨停标记'].astype(bool);x.loc[b,'d2_close']=x.loc[b,'最新价'];x.loc[b,'d2_preclose']=x.loc[b,'前收']
x['ret']=(100*(x.d2_close*.999/(x.d1_open*1.001)-1)).where(x.d1_open.gt(0)&x.d2_close.gt(0)&x.d1_volume.gt(0)&(x.d1_preclose-x.close).abs().le(.011)&(x.d2_preclose-x.d1_close).abs().le(.011));x['selected']=x.票数.gt(0);x['来源']='11月研究行情';frames.append(x)
cols=['d0_date','symbol','selected','来源','board_streak_days','d0_amount_100m','d1_up','d2_up','ret','market_limit_count','market_up_fraction','d0_one_price','d1_open','d1_cap'];x=pd.concat([f.reindex(columns=cols) for f in frames],ignore_index=True)
rows=[];details=[]
for day,g in x.groupby('d0_date',sort=True):
    chosen=g[g.selected];k=len(chosen)
    if not k:continue
    groups={'组合':chosen,'连板优先':g.sort_values(['board_streak_days','symbol'],ascending=[False,True]).head(k),'成交额优先':g.sort_values(['d0_amount_100m','symbol'],ascending=[False,True]).head(k)}
    assert all(len(v)==k for v in groups.values())
    for metric in ['D1收板','D1D2连续收板','D2价格收益']:
        g=g.copy();
        if metric=='D1收板': g['v']=pd.to_numeric(g.d1_up,errors='coerce')*100
        elif metric=='D1D2连续收板':g['v']=(pd.to_numeric(g.d1_up,errors='coerce')*pd.to_numeric(g.d2_up,errors='coerce'))*100
        else:g['v']=pd.to_numeric(g.ret,errors='coerce')
        known=g[g.v.notna()]; values={}; complete={}
        for name,z in groups.items():
            v=g.loc[z.index,'v'];values[name]=v.mean();complete[name]=v.notna().all()
        row={'D0':day,'指标':metric,'来源':g.来源.iloc[0],'选股数':k,'候选总数':len(g),'候选结果已知':len(known),'池结果覆盖率%':100*len(known)/len(g),'组合完整':complete['组合'],'连板完整':complete['连板优先'],'成交额完整':complete['成交额优先'],'组合':values['组合'],'随机期望':known.v.mean(),'连板优先':values['连板优先'],'成交额优先':values['成交额优先'],'市场涨停数':g.market_limit_count.iloc[0],'市场上涨占比':g.market_up_fraction.iloc[0]}
        rows.append(row)
    for name,z in groups.items():
        for _,r in z.iterrows():details.append({'D0':day,'策略':name,'代码':r.symbol,'D0连板数':r.board_streak_days,'D1封板':r.d1_up,'D2封板':r.d2_up,'D2收益%':r.ret})
daily=pd.DataFrame(rows);daily.to_csv(P/'逐日对照.csv',index=False,encoding='utf-8-sig');pd.DataFrame(details).to_csv(P/'逐股对照.csv',index=False,encoding='utf-8-sig')
summary=[]; runs=[];windows=[]
for metric in ['D1收板','D1D2连续收板','D2价格收益']:
 for baseline in ['随机期望','连板优先']:
    z=daily[daily.指标.eq(metric)].copy().sort_values('D0').reset_index(drop=True);valid=z.组合完整 & (z[baseline].notna()) & (z.连板完整 if baseline=='连板优先' else True);z['差']=z['组合']-z[baseline];z['valid']=valid;z['落后']=valid & z['差'].lt(-1e-9);z['run']=(~z['落后']).cumsum()
    for _,g in z[z.落后].groupby('run'):
        runs.append({'指标':metric,'对照':baseline,'起始D0':g.D0.iloc[0],'结束D0':g.D0.iloc[-1],'连续天数':len(g),'结束是否样本尾部':g.index[-1]==z.index[-1]})
    hist=z[z.D0.lt('2026-11-01')];recent=z[z.D0.ge('2026-11-01')]; summary.append({'指标':metric,'对照':baseline,'历史共同日':int(hist.valid.sum()),'历史落后日':int(hist.落后.sum()),'历史落后最长连续天数':max([r['连续天数'] for r in runs if r['指标']==metric and r['对照']==baseline and r['结束D0']<'2026-11-01'] or [0]),'近期共同日':int(recent.valid.sum()),'近期落后日':int(recent.落后.sum()),'近期均差':recent.loc[recent.valid,'差'].mean()})
    n=int(recent.valid.sum()); rv=recent[recent.valid]; rmean=rv['差'].mean() if len(rv)==n and n else np.nan; rcount=int(rv.落后.sum())
    if n:
     for i in range(len(hist)-n+1):
      w=hist.iloc[i:i+n]
      if w.valid.all():windows.append({'指标':metric,'对照':baseline,'窗口天数':n,'起始D0':w.D0.iloc[0],'结束D0':w.D0.iloc[-1],'平均差':w['差'].mean(),'落后天数':int(w.落后.sum()),'近期平均差':rmean,'近期落后天数':rcount})
pd.DataFrame(summary).to_csv(P/'汇总.csv',index=False,encoding='utf-8-sig');pd.DataFrame(runs).to_csv(P/'连续落后区间.csv',index=False,encoding='utf-8-sig');pd.DataFrame(windows).to_csv(P/'历史等长窗口.csv',index=False,encoding='utf-8-sig')
print(pd.DataFrame(summary).to_string(index=False))

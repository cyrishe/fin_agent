from pathlib import Path
import pandas as pd,numpy as np,json
R=Path('/data/cyris/work/b2_news_flow_combo_r75_20261022');f=pd.read_pickle(R/'panel306.pkl')
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
out=[]
for r in f.to_dict('records'):
 d=pd.read_pickle(R/'flow_raw'/(r['stk_code']+'.pkl'));d['trade_date']=pd.to_datetime(d.trade_date).dt.strftime('%Y-%m-%d');d=d.set_index('trade_date');i=cal.index(r['d0_date'])
 for offset in range(-10,4):
  date=cal[i+offset];x=d.loc[date] if date in d.index else None
  total=sum(float(x[f'{k}_buy_value']) for k in ['huge','large','medium','small']) if x is not None else np.nan
  row={'id':r['id'],'split':r['split'],'D0':r['d0_date'],'相对交易日':offset,'日期':date,'D2开收跌停':r['y_union'],'连续跌停':r['连续跌停'],'事后数据':int(offset>0)}
  for k in ['main','huge','large','small']:
   row[k]=float(x[f'{k}_buy_value']-x[f'{k}_sell_value'])/total if total>0 else np.nan
  out.append(row)
p=pd.DataFrame(out);p.to_csv(R/'逐笔D0前后资金路径_事后列隔离.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
stat=[]
for split in ['all','train','valid','test']:
 g=p if split=='all' else p[p.split.eq(split)]
 for label in ['D2开收跌停','连续跌停']:
  for flag in [0,1]:
   for day,h in g[g[label].eq(flag)].groupby('相对交易日'):
    for k in ['main','huge','large','small']:
     stat.append({'split':split,'标签':label,'标签值':flag,'相对交易日':day,'资金类':k,'有效笔数':int(h[k].notna().sum()),'净额占成交额中位数':h[k].median(),'净额占成交额均值':h[k].mean()})
pd.DataFrame(stat).to_csv(R/'分组资金路径.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
coverage=f.groupby(f.d0_date.str[:4]).agg(笔数=('id','size'),D0资金存在=('flow_D0_row','sum'),D0收盘前已更新=('flow_D0_timestamp_by_close','sum'),D1买前已更新=('flow_D0_timestamp_by_buy','sum'))
coverage.to_csv(R/'资金可用时点_分年.csv',encoding='utf-8-sig')
bins=[]
for feature in ['flow_main_frac_D0','flow_main_change','flow_huge_frac_D0','flow_main_pre5','news_days_r5','news_day_change','risk_days_r5']:
 edges=np.unique(f.loc[f.split.eq('train'),feature].dropna().quantile([.25,.5,.75]).to_numpy());edges=np.r_[-np.inf,edges,np.inf]
 for split in ['train','valid','test']:
  g=f[f.split.eq(split)]
  for j in range(len(edges)-1):
   h=g[g[feature].gt(edges[j])&g[feature].le(edges[j+1])]
   bins.append({'因子':feature,'split':split,'档':j+1,'训练固定下界':edges[j],'训练固定上界':edges[j+1],'笔数':len(h),'D2开收跌停':int(h.y_union.sum()),'连续跌停':int(h['连续跌停'].sum()),'D2开收跌停率':h.y_union.mean()})
pd.DataFrame(bins).to_csv(R/'训练分位分箱_跨年风险.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
print(coverage.to_string())
for split in ['train','valid','test']:
 for val in [0,1]:
  h=f[f.split.eq(split)&f.r_outflow.eq(val)]
  print(split,'outflow',val,'n',len(h),'floor',h.y_union.sum(),'consecutive',h['连续跌停'].sum())
print(pd.DataFrame(stat).query("split == 'test' and 标签 == '连续跌停' and 资金类 == 'main' and 相对交易日 in [-2,-1,0,1,2,3]").to_string(index=False))
try:
 import matplotlib;matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 fig,axes=plt.subplots(1,2,figsize=(11,4),sharey=True)
 for ax,split,title in zip(axes,['all','test'],['All 306 trades (20 consecutive-floor)','2026: 98 trades (5 consecutive-floor)']):
  for flag,color,label in [(1,'#b43939','Consecutive D2/D3 floor'),(0,'#237796','Other trades')]:
   z=pd.DataFrame(stat).query("split == @split and 标签 == '连续跌停' and 资金类 == 'main' and 标签值 == @flag")
   ax.plot(z['相对交易日'],z['净额占成交额中位数']*100,marker='o',label=label,color=color)
  ax.axvspan(.1,3.2,color='#eaeaea');ax.axhline(0,color='#777',lw=.6);ax.axvline(0,color='#777',ls='--',lw=.6);ax.set_title(title);ax.set_xlabel('Trading days relative to D0');ax.set_xticks(range(-10,4,2));ax.grid(alpha=.2)
 axes[0].set_ylabel('Median main net / turnover (%)');axes[1].legend(fontsize=8);fig.suptitle('D1+ shaded: post-purchase explanatory data, excluded from buy model');fig.tight_layout();fig.savefig(R/'flow_event_study.png',dpi=170);plt.close(fig)
except ImportError:print('matplotlib unavailable; CSV preserved')


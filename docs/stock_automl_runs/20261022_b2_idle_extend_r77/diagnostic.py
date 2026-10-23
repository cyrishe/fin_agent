import pandas as pd, numpy as np
from scipy.stats import fisher_exact
R='/data/cyris/work/b2_idle_extend_r77_20261022/'
s=pd.read_csv(R+'306笔逐笔对照.csv',dtype={'股票':str})
print('baseline floor idle',s.loc[s.轮空,'D2卖出未核实'].sum(),'to d3',s.loc[s.轮空,'D3卖出未核实_跌停或零日量'].sum())
print(pd.crosstab(s.loc[s.轮空,'D2卖出未核实'],s.loc[s.轮空,'D3卖出未核实_跌停或零日量']))
print('morning by year')
print(s[s.轮空].groupby([s.d0_date.str[:4],'上午7%触发']).agg(n=('id','size'),delta=('多持一日差_百分点','mean'),sum_1w=('多持一日差_百分点',lambda x:x.sum()*100),d2_up=('d2_limit_up','sum'),consecutive=('consecutive_up','sum'),d3floor=('D3卖出未核实_跌停或零日量','sum')).to_string())
a=int(s[s.轮空 & s.d2_limit_up].consecutive_up.sum());b=int((s.轮空 & s.d2_limit_up).sum())-a
c=int(s[~s.轮空 & s.d2_limit_up].consecutive_up.sum());d=int((~s.轮空 & s.d2_limit_up).sum())-c
print('Fisher D2-up continue idle vs nonidle',[[a,b],[c,d]],fisher_exact([[a,b],[c,d]]))
base=pd.read_csv(R+'全期_close_原D2卖出_账本.csv')
ext=pd.read_csv(R+'全期_close_D3轮空延期_账本.csv')
m=base.merge(ext,on='id',suffixes=('_base','_ext'),validate='one_to_one')
orig=s[['id','d2_close','d3_close','entry']].copy()
m=m.merge(orig,on='id',validate='one_to_one')
m['direct_delta_if_base_shares']=np.where(m.轮空_base,m.买入投入元_base/(m.entry*1.001)*(m.d3_close-m.d2_close)*.999,0)
print('account delta',ext['损益元'].sum()-base['损益元'].sum(),'direct',m.direct_delta_if_base_shares.sum(),'allocation-residual',ext['损益元'].sum()-base['损益元'].sum()-m.direct_delta_if_base_shares.sum())
print('buy cost difference',m.loc[m.轮空_base,['买入投入元_base','买入投入元_ext']].sum())
print('risk tail all idle')
print(s[s.轮空].sort_values('多持一日差_百分点').head(8)[['id','d2_date','d3_date','多持一日差_百分点','D3卖出未核实_跌停或零日量']].to_string(index=False))

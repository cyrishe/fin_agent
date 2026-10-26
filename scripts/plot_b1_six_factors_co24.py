"""Render the complete CO24 monthly comparison without hiding losing arms."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import json
import numpy as np
import pandas as pd
from scripts.research_b1_six_factors_co24 import REPORT,read

def main():
 font=FontProperties(fname='/System/Library/Fonts/STHeiti Light.ttc')
 names={'A7':'原七因子','B_breakout':'+突破守位','C_pullback':'+回踩量与恢复','D_persistence':'+急拉与均价线','E13':'+全部六因子'}
 d=read(REPORT/'daily_picks.csv.gz');m=read(REPORT/'common_known_monthly.csv')
 bad=json.loads((REPORT/'common_known_scope.json').read_text())['excluded_dates'];d=d[~d.signal_date.isin(bad)]
 fig,axes=plt.subplots(2,1,figsize=(15,9),gridspec_kw={'height_ratios':[1.4,1]})
 for arm,label in names.items():
  g=d[d.arm.eq(arm)].sort_values('signal_date');eq=(1+g.daily_net.fillna(0)).cumprod()
  axes[0].plot(pd.to_datetime(g.signal_date),eq,label=label,lw=1.6 if arm!='A7' else 2.5)
 axes[0].set_title('CO24：共同可核定日的固定滚动对照（事后评价子集）',fontproperties=font,fontsize=16)
 axes[0].set_ylabel('已知成交净值（初始=1）',fontproperties=font);axes[0].legend(prop=font,ncol=3);axes[0].grid(alpha=.2)
 table=m.pivot(index='arm',columns='month',values='net_compound_pct').reindex(names)
 lim=max(10,np.nanpercentile(abs(table.to_numpy()),95))
 axes[1].imshow(table.to_numpy(),aspect='auto',cmap='RdYlGn',vmin=-lim,vmax=lim)
 axes[1].set_yticks(range(len(names)),list(names.values()),fontproperties=font)
 axes[1].set_xticks(range(len(table.columns)),table.columns,rotation=45,ha='right')
 for i,row in enumerate(table.to_numpy()):
  for j,x in enumerate(row):axes[1].text(j,i,f'{x:+.1f}',ha='center',va='center',fontsize=8)
 axes[1].set_title('逐月净复利（%）；五组使用相同338个可核定日期',fontproperties=font)
 fig.text(.02,.01,'2025-02—2026-07；剔除未知日仅供可比评价，不能事前规避停牌；每边0.1%费用，无额外滑点。',fontproperties=font,fontsize=10)
 fig.tight_layout(rect=[0,.035,1,1]);fig.savefig(REPORT/'monthly_comparison.png',dpi=170);plt.close(fig)
if __name__=='__main__':main()

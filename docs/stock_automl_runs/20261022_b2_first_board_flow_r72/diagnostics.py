from pathlib import Path
import json,hashlib
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score
R=Path('/data/cyris/work/b2_first_board_flow_r72_20261022')
t=pd.read_csv(R/'首板前量价资金趋势_709候选.csv',dtype={'股票':str})
p=pd.read_csv(R/'逐笔冻结预测_709候选.csv',dtype={'股票':str})
assert len(t)==len(p)==709
t['期间']=np.where(t.D0选中日.str[:4].le('2024'),'2022-24',t.D0选中日.str[:4])
medfields=['pre20_price_return','pre20_volume_trend_log','pre20_signed_amount_share','pre20_up_volume_share','pre20_cmf','pre20_price_volume_corr','pre20_first_volume_logratio','pre20_first_turnover_logratio','first_range','first_signed_flow_proxy']
out=[]
for target in ['三日急跌标签','三日持续大涨标签']:
 for period in ['2022-24','2025','2026']:
  a=t[(t['期间']==period)&t.prehistory_days.eq(20)]
  for value in [0,1]:
   b=a[a[target]==value]
   for col in medfields:
    out.append({'标签':target,'期间':period,'标签值':value,'样本数':len(b),'特征':col,'中位数':float(b[col].median())})
pd.DataFrame(out).to_csv(R/'资金趋势代理_按结果中位数.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
test=p[p.D0选中日.str[:4].eq('2026')].copy()
test['month']=test.D0选中日.str[:7]
months=np.array(sorted(test.month.unique()))
rng=np.random.default_rng(20261022)
ci={}
for name,label,score in [('crash','三日急跌标签','crash_score'),('strong','三日持续大涨标签','strong_score')]:
 vals_auc=[];vals_ap=[];vals_lift=[]
 for _ in range(3000):
  chosen=rng.choice(months,len(months),replace=True)
  a=pd.concat([test[test.month.eq(m)] for m in chosen],ignore_index=True)
  y=a[label].to_numpy(int);pred=a[score].to_numpy(float)
  if y.min()==y.max():continue
  vals_auc.append(roc_auc_score(y,pred))
  ap=average_precision_score(y,pred)
  vals_ap.append(ap);vals_lift.append(ap-y.mean())
 ci[name]={'month_bootstrap_valid':len(vals_auc),'auc_95pct':[float(v) for v in np.quantile(vals_auc,[.025,.975])],
           'ap_95pct':[float(v) for v in np.quantile(vals_ap,[.025,.975])],
           'ap_minus_base_95pct':[float(v) for v in np.quantile(vals_lift,[.025,.975])]}
x=p[(p.D0选中日.str[:4].eq('2026'))&(p.V2放行.eq(1))]
groups=[]
for group,g in x.groupby('实验高分组'):
 groups.append({'group':group,'n':len(g),'crash':int(g['三日急跌标签'].sum()),'strong':int(g['三日持续大涨标签'].sum()),
                'D2_floor':int(g['D2开收跌停'].sum()),'D2D3_consecutive_floor':int(g['D2D3连续收盘跌停'].sum())})
obj={'test_2026_months':months.tolist(),'bootstrap':ci,'v2_test_groups':groups}
(R/'diagnostics.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
m=json.loads((R/'manifest.json').read_text())
for fn in ['diagnostics.py','diagnostics.json','资金趋势代理_按结果中位数.csv']:
 m['files'][fn]=hashlib.sha256((R/fn).read_bytes()).hexdigest()
(R/'manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(obj,ensure_ascii=False,indent=2))

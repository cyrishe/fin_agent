from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd

R=Path('/data/cyris/work/b2_prebuy_volume_warning_r83_20261022')
p=pd.read_pickle('/data/cyris/work/b2_news_flow_combo_r75_20261022/panel306.pkl')
rows=[]
sha={}
for _,r in p.iterrows():
 code=str(r.symbol).zfill(6)
 day=str(r.d1_date)[:10]
 path=R/'d1_minutes'/f'{day}_{code}.csv.gz'
 assert path.exists(),path
 bars=pd.read_csv(path,compression='gzip')
 assert len(bars)==240,(path,len(bars))
 assert bars.time.astype(str).str[11:16].iloc[:4].tolist()==['09:31','09:32','09:33','09:34'],path
 for col in ['open','high','low','close','volume']:assert np.isfinite(bars[col].to_numpy(dtype=float)).all()
 v=bars.volume.to_numpy(dtype=float)
 first2=v[:2].sum();last2=v[2:4].sum()
 raw_acc=last2/first2 if first2>0 else np.nan
 raw_early=v[:4].sum()/(float(r.d0_volume_wan)*1e4)
 assert abs(raw_acc-float(r.early_volume_accel))<=.0001,(path,raw_acc,r.early_volume_accel)
 assert abs(raw_early-float(r.early_volume_ratio))<=.0001,(path,raw_early,r.early_volume_ratio)
 raw_gap=float(bars.open.iloc[0])/float(r.d1_preclose)-1
 assert abs(raw_gap-float(r.open_gap))<=.0005,(path,raw_gap,r.open_gap)
 sha[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
 rows.append({'股票':r.stock,'D0选中日':str(r.d0_date),'D1买入日':day,'D1首分钟量_股':v[0],
              'D1前四分钟量_股':v[:4].sum(),'D1前两分钟量_股':first2,
              'D1后两分钟量_股':last2,'后两根量除前两根量_原始重算':raw_acc,
              'D1首分钟开盘涨幅':raw_gap,'D1前四K最高涨幅':float(bars.high.iloc[:4].max())/float(r.d1_preclose)-1,
              'D1前四K最低涨幅':float(bars.low.iloc[:4].min())/float(r.d1_preclose)-1,
              'D1前四K价格一字':int(float(bars.high.iloc[:4].max()-bars.low.iloc[:4].min())<.0001),
              'D1全天分钟量_股':v.sum(),'D1原始分钟SHA256':sha[str(path)]})
d=pd.DataFrame(rows)
assert len(d)==306
d.to_csv(R/'D1前四分钟原档复核_306笔.csv',index=False,encoding='utf-8-sig')
(R/'D1分钟源SHA.json').write_text(json.dumps(sha,ensure_ascii=False,indent=2))
print('audited',len(d),'first4 floor-ish',len(d[(d['D1前四K最高涨幅']<=-.095)&(d['D1前四K价格一字'].eq(1))]))

from pathlib import Path
import json,hashlib,pandas as pd
P=Path(__file__).parent
records=[]
for folder in [P,P/'早期',P/'近月',P/'2022备用来源']:
 path=folder/'全部输入.pkl.gz'
 if not path.exists() or path.stat().st_size==0:continue
 data=pd.read_pickle(path)
 for f in sorted((folder/'模型').glob('*/*.json')):
  if f.name.startswith('.') : continue
  j=json.loads(f.read_text());kind=j['方法'];day=j['决策日'];cut=day[:7]+'-01' if kind=='月训六个月' else day;end=data.d2_date.lt(cut) if kind=='月训六个月' else data.d2_date.le(day);x=data[data.d0_date.ge(j['训练起始D0'])&data.d0_date.lt(cut)&end.fillna(False)&data.utility.notna()];model=f.with_suffix('.joblib');pred=f.with_suffix('.pkl.gz')
  valid=len(x)==j['训练股次'] and x.d2_date.max()==j['最晚标签日'] and x.d0_date.max()==j['训练末D0'] and model.exists() and pred.exists() and hashlib.sha256(model.read_bytes()).hexdigest()==j['模型SHA256']
  records.append({'方法':kind,'日期':day,'训练股次':len(x),'最晚标签':x.d2_date.max(),'通过':valid,'模型SHA256':j['模型SHA256']})
pd.DataFrame(records).to_csv(P/'训练窗口核验.csv',index=False);print('已核验',len(records),'未通过',sum(not r['通过'] for r in records))

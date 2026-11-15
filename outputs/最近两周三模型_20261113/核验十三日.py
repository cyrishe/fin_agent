from pathlib import Path
import hashlib,json,joblib,pandas as pd,numpy as np
P=Path(__file__).parent;x=pd.read_pickle(P/'十三日全部候选与排名.pkl.gz');assert len(x)==37 and x.分时有效.eq(True).all();src=json.loads((P/'十三日候选来源审计.json').read_text());assert src['十二日复现'].get('both')==37 and src['十二日复现'].get('left_only',0)==0 and src['十二日复现'].get('right_only',0)==0 and not src['十二日特征差'];models={'每日滚动':P/'十三日每日模型.joblib','每月训练':P/'十一月模型/每月训练/2026-11-01.joblib'}
for name,path in models.items():
 b=joblib.load(path);pred=b['model'].predict(x[b['features']].replace([np.inf,-np.inf],np.nan));np.testing.assert_allclose(pred,x[name],atol=1e-12,rtol=0)
for name in ['每日滚动','每月训练','六训二验']:
 ordered=x.sort_values([name,'symbol'],ascending=[False,True]);expected=np.arange(1,len(x)+1);assert np.array_equal(ordered[name+'排名'].to_numpy(),expected)
selected=x[x[['每日滚动排名','每月训练排名','六训二验排名']].le(2).any(axis=1)];t=pd.read_csv(P/'十三日三模型候选.csv',dtype={'symbol':str});assert set(selected.symbol)==set(t.symbol) and len(t)==4;tr=pd.read_pickle(P/'十三日训练样本键.pkl.gz');assert tr.d0_date.lt('2026-11-13').all() and tr.实际D2日期.le('2026-11-13').all();assert tr[tr.d0_date.eq('2026-11-11')].实际D2日期.eq('2026-11-13').all();o={'候选数':37,'分钟有效':37,'组合股票':len(t),'训练股次':len(tr),'训练最晚D0':tr.d0_date.max(),'训练最晚标签':tr.实际D2日期.max(),'12日51个日线因子':'37只全部一致','逐候选重载评分':'两个新模型37只全部一致','六训二验':'原1.7.1环境评分，10月模型原环境逐个复现','今日模型SHA256':hashlib.sha256(models['每日滚动'].read_bytes()).hexdigest(),'说明':'跨源事后收盘重建；盘前历史版本、明日实际成交未核验'};(P/'十三日核验.json').write_text(json.dumps(o,ensure_ascii=False,indent=2));print(json.dumps(o,ensure_ascii=False))

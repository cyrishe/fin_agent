from pathlib import Path
import hashlib,json,joblib
import numpy as np,pandas as pd
R=Path('/data/cyris/work/b2_first_board_flow_r72_20261022')
R71=Path('/data/cyris/work/b2_board_volume_r71_20261022')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
t=pd.read_csv(R/'首板前量价资金趋势_709候选.csv',dtype={'股票':str,'首板日':str,'D0选中日':str})
p=pd.read_csv(R/'逐笔冻结预测_709候选.csv',dtype={'股票':str,'首板日':str,'D0选中日':str})
f=json.loads((R/'feature_columns.json').read_text())
sel=json.loads((R/'selection.json').read_text())
old=pd.read_csv(R71/'preboard_daily_source.csv.gz',dtype={'stk_code':str,'trade_date':str,'对应首板日':str})
board=pd.read_csv(R71/'board_daily_source.csv.gz',dtype={'stk_code':str,'trade_date':str})
assert len(t)==len(p)==709 and len(f)==51
assert t[['股票','D0选中日']].equals(p[['股票','D0选中日']])
assert (t['首板日']<=t['D0选中日']).all()
assert (old.trade_date<old['对应首板日']).all()
assert not any(x in f for x in ['D0选中日','本轮D0已连板天数_仅审计','V2放行','D2开收跌停','三日急跌标签','三日持续大涨标签'])
assert t[['股票','D0选中日']].duplicated().sum()==0
assert t['D2开收跌停'].sum()==161 and t.loc[t.V2放行.eq(1),'D2开收跌停'].sum()==67
assert t['三日急跌标签'].sum()==265 and t['三日持续大涨标签'].sum()==106
split=np.select([(t.D0选中日<'2025-01-01')&(t.D3日<'2025-01-01'),
 (t.D0选中日>='2025-01-01')&(t.D0选中日<'2026-01-01')&(t.D3日<'2026-01-01'),
 t.D0选中日>='2026-01-01'],['train','validation','test'],default='purged')
assert pd.Series(split).value_counts().to_dict()=={'train':419,'validation':165,'test':120,'purged':5}
eps=t['股票']+'|'+t['首板日']
for a,b in [('train','validation'),('train','test'),('validation','test')]:
 assert not set(eps[split==a]).intersection(eps[split==b])
for target in ['crash','strong']:
 name=sel['targets'][target]['selected'];cols=sel['features']['full'] if name.startswith('full_') else sel['features']['volume']
 model=joblib.load(R/f'{target}_{name}_train2022_24.joblib')
 assert np.allclose(model.predict_proba(t[cols])[:,1],p[f'{target}_score'],atol=5e-9,rtol=0)
 assert sha(R/f'{target}_{name}_train2022_24.joblib')==sel['targets'][target]['model_sha256']
for prefix,meta_name,output_name in [
 ('unified_','unified_selection.json','统一三分类逐笔预测.csv'),
 ('unified_balanced_','unified_balanced_selection.json','统一三分类平衡逐笔预测.csv')]:
 info=json.loads((R/meta_name).read_text())
 model_path=R/f"{prefix}{info['selected']}_train2022_24.joblib"
 mdl=joblib.load(model_path)
 output=pd.read_csv(R/output_name,dtype={'股票':str})
 probs=mdl.predict_proba(t[f])
 assert len(output)==len(t)==709
 for k,c in enumerate(mdl.classes_):
  assert np.allclose(probs[:,k],output[f'{c}评分'],atol=5e-9,rtol=0)
 assert sha(model_path)==info['model_sha256']
for filename in ['首板前量价资金趋势_709候选.csv','逐笔冻结预测_709候选.csv','模型候选与分期指标.csv']:
 with (R/filename).open(encoding='utf-8-sig') as stream:
  assert not any(line.startswith("'") for line in stream)
pd.DataFrame({'股票':t['股票'],'D0选中日':t['D0选中日'],'首板日':t['首板日'],'D3标签截止日':t['D3日'],'分组':split}).to_csv(R/'训练验证测试分组.csv',index=False,encoding='utf-8-sig')
obj={'source_read_only':True,'rows':len(t),'features':len(f),'split_counts':pd.Series(split).value_counts().to_dict(),
 'd2_floor':int(t['D2开收跌停'].sum()),'v2_d2_floor':int(t.loc[t.V2放行.eq(1),'D2开收跌停'].sum()),
 'crash':int(t['三日急跌标签'].sum()),'strong':int(t['三日持续大涨标签'].sum()),
 'temporal_order_pass':True,'no_episode_cross_split':True,'saved_model_scores_match':True}
(R/'verification.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
m=json.loads((R/'manifest.json').read_text())
for fn in ['verify.py','verification.json','训练验证测试分组.csv']:
 m['files'][fn]=sha(R/fn)
(R/'manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(obj,ensure_ascii=False,indent=2))

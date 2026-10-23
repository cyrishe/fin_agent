from pathlib import Path
import pandas as pd,numpy as np,json,joblib,hashlib,bisect,platform,subprocess,csv,sklearn
R=Path('/data/cyris/work/b2_news_flow_combo_r75_20261022');A=Path('/data/cyris/work/b2_news_factor_r74_20261022')
f=pd.read_pickle(R/'panel306.pkl');p=pd.read_pickle(R/'predictions306.pkl').set_index('id')
s=pd.read_pickle('/data/cyris/work/b2_model_economics_r73_20261022/joined_98.pkl')
g=s.merge(f[['id','stock_name']],on='id',validate='one_to_one')
g['net7']=g.sell7/g.entry*.999/1.001-1
base=p.loc[g.id,'union_early_lr01'].to_numpy(bool);rows=[];checks={}
names=['fit_D1_news','fit_D1_flow','fit_D1_news_flow','fit_sens_D1_q80','fit_sens_D1_flow_q80','fit_sens_D1_news_flow_q80']
rng=np.random.default_rng(20261022);summary=[];monthly=[]
for name in names:
 a=p.loc[g.id,name].to_numpy(bool)
 for i,r in enumerate(g.to_dict('records')):
  if a[i]==base[i]:continue
  rows.append({'方案':name,'股票':r['stk_code'],'名称':r['stock_name'],'D0':r['d0_date'],'变化':'新增过滤' if a[i] else '恢复买入','费后收益率':r['net7'],'对原D1净贡献元_每笔1万':r['net7']*1e4*(int(base[i])-int(a[i])),'D2开收跌停':r['D2开收跌停'],'连续跌停':r['D2D3连续收盘跌停'],'全天封死':r['all_day_locked']})
 values=g.net7.to_numpy()*1e4*(base.astype(int)-a.astype(int))
 mon=pd.Series(values).groupby(g.d0_date.str[:7]).sum()
 vals=mon.to_numpy()[rng.integers(0,len(mon),(2000,len(mon)))].sum(axis=1)
 summary.append({'方案':name,'新增过滤':int((a&~base).sum()),'恢复买入':int((~a&base).sum()),'全部变化净贡献':float(values.sum()),'月份聚类95下界':float(np.quantile(vals,.025)),'月份聚类95上界':float(np.quantile(vals,.975))})
 for month,x in mon.items():monthly.append({'方案':name,'D0月':month,'对原D1净贡献元_每笔1万':x})
pd.DataFrame(rows).to_csv(R/'模型换单_新增过滤与恢复买入.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
pd.DataFrame(summary).to_csv(R/'替换D1的完整增量与不确定性.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
pd.DataFrame(monthly).to_csv(R/'相对D1_逐月净贡献.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
rename={'stk_code':'股票','stock_name':'名称','d0_date':'D0','d1_date':'D1','split':'分期','board_streak_days':'连板数','d0_normal_volume_ratio':'D0量比_首板前常态','d0_turnover_pct':'D0换手率百分点','total_mv_yi':'总市值亿元','news_docs_r5':'近5日公告数','news_docs_p5':'前5日公告数','news_days_r5':'近5日公告日数','news_day_change':'公告日数增减','risk_days_r5':'近5日风险提示日数','abnormal_days_r5':'近5日异动公告日数','specific_denial':'具体题材否认','generic_denial':'泛化否认措辞','flow_main_net_D0':'D0主力净额元','flow_main_frac_D0':'D0主力净额占成交额','flow_huge_frac_D0':'D0超大单净额占成交额','flow_large_frac_D0':'D0大单净额占成交额','flow_small_frac_D0':'D0小单净额占成交额','flow_main_pre5':'前5日主力占比均值','flow_main_change':'D0主力占比减前5均值','flow_main_trend5':'近5比前5主力占比变化','flow_main_negative5':'近5日主力净流出天数','flow_D0_timestamp_by_buy':'资金当前版本时间不晚于买前','flow_create_time':'资金建档时间','flow_update_time':'资金更新时间','y_union':'D2开收跌停','连续跌停':'连续跌停','original_net':'原收盘费后收益率'}
out=f[list(rename)].rename(columns=rename)
out['当前7卖法费后收益率_仅2026']=f.id.map(g.set_index('id').net7)
for name,label in [('union_early_lr01','D1过滤'),('fit_D1_flow','D1资金过滤Q75'),('fit_D1_news_flow','全组合过滤Q75'),('fit_sens_D1_flow_q80','D1资金过滤Q80'),('fit_sens_D1_news_flow_q80','全组合过滤Q80')]:
 out[label]=p.loc[f.id,name].to_numpy()
out.to_csv(R/'306笔_量能消息资金与模型.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
out[out['连续跌停'].eq(1)].to_csv(R/'20笔连跌_事前线索.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
# Minimal reproducibility audit.
assert len(f)==306 and len(pd.read_pickle(R/'features709.pkl'))==709
assert f.id.is_unique and len(p)==306 and p.index.is_unique
assert f.split.value_counts().to_dict()=={'train':152,'test':98,'valid':54,'purged':2}
checks['rows_and_splits']='PASS'
mi=json.loads((R/'model_info.json').read_text());es=json.loads((R/'economic_selection.json').read_text());maxerr=0
for name,z in mi.items():
 assert not any(c.startswith('POST_') or c.startswith('day_') or c in ['d1_volume','d1_close','d2_close','original_net','y_union'] for c in z['features'])
 m=joblib.load(R/('model_'+name+'.joblib'));q=m.predict_proba(f[z['features']])[:,1]
 maxerr=max(maxerr,float(np.max(abs(q-p.loc[f.id,'fit_'+name+'_score'].to_numpy()))))
 assert np.array_equal(q>z['threshold'],p.loc[f.id,'fit_'+name].to_numpy(bool))
for name,z in es['models'].items():
 m=joblib.load(R/('economic_'+name+'.joblib'));q=m.predict_proba(f[z['features']])[:,1]
 for th,v in z['thresholds'].items():
  assert np.array_equal(q>v,p.loc[f.id,'fit_econ_'+name+'_'+th].to_numpy(bool))
checks['ten_models_reload_max_probability_error']=maxerr;assert maxerr<1e-12
assert np.max(abs(p.fit_D1_score-p.union_early_lr01_score))<1e-10
checks['frozen_D1_reproduced']='PASS'
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
docs={x['document_id'].split(':')[1]:x for x in json.loads((A/'classified_official_documents.json').read_text())}
ft=pd.read_pickle(R/'features709.pkl')
traces=[]
for r in ft.to_dict('records'):
 i=cal.index(r['D0']);raw=json.loads((A/'cninfo_windows'/(r['id'].replace('|','_')+'.json')).read_text())
 dd={x['announcementId']:docs[x['announcementId']] for x in raw['items']}
 eligible=[d for d in dd.values() if d['available_at']<=r['D0']+' 15:00:00' and i-4<=bisect.bisect_left(cal,d['available_at'][:10])<=i]
 assert len(eligible)==r['news_docs_r5']
 for d in eligible:traces.append({'id':r['id'],'D0截止':r['D0']+' 15:00:00','文档':d['document_id'],'可用时间':d['available_at'],'链接':d['url']})
pd.DataFrame(traces).to_csv(R/'公告近5日_逐条可用时点.csv',index=False,encoding='utf-8-sig')
checks['709_news_windows_independent_count_and_cutoff']='PASS'
fa=pd.read_csv(R/'资金定义核验.csv')
assert fa.main_minus_huge_large.abs().max()<1e-5
assert fa.fraction_diff_provider.abs().max()<1e-6
checks['flow_main_equals_huge_plus_large']='PASS'
checks['flow_vintage']={'train152_timestamp_supported':int(f.loc[f.split.eq('train'),'flow_D0_timestamp_by_buy'].sum()),'valid54':int(f.loc[f.split.eq('valid'),'flow_D0_timestamp_by_buy'].sum()),'test98':int(f.loc[f.split.eq('test'),'flow_D0_timestamp_by_buy'].sum()),'D0_close_available':int(f.flow_D0_timestamp_by_close.sum()),'fully_point_in_time_verified':False}
ac=pd.read_csv(R/'2026当前卖法_组合得失.csv')
for r in ac.to_dict('records'):
 l=pd.read_csv(R/(r['方案']+'_ledger.csv'));d=pd.read_csv(R/(r['方案']+'_daily.csv'))
 assert len(l)==98-r['取消整笔']
 assert np.isclose(l.pnl.sum()/1e6,r['账户收益率'],atol=1e-8)
 assert np.isclose(d.nav.iloc[-1],1e6+l.pnl.sum(),atol=1e-6)
 assert np.isclose(d.drawdown.max(),r['最大回撤'],atol=1e-8)
 assert np.isclose(r['净贡献元_每笔1万'],r['避亏元_每笔1万']-r['少赚元_每笔1万'],atol=3e-8)
checks['accounts_cash_reconciliation']=len(ac)
b=pd.read_csv(R/'baseline_daily.csv');old=pd.read_csv('/data/cyris/work/b2_model_economics_r73_20261022/morning7_baseline_daily.csv')
assert np.allclose(b.nav,old.nav,rtol=0,atol=1e-6)
checks['R73_daily_baseline_exact']='PASS'
# CSV serialization: never prefix numeric strings with an apostrophe.
for path in R.glob('*.csv'):
 with path.open(encoding='utf-8-sig',newline='') as stream:
  for line in csv.reader(stream):
   assert not any(v.startswith("'") for v in line),(path.name,line)
checks['csv_no_leading_apostrophes']='PASS'
checks['runtime']={'python':platform.python_version(),'pandas':pd.__version__,'numpy':np.__version__,'sklearn':sklearn.__version__,'host':platform.node(),'server_workdir':str(R)}
(R/'verification.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
print(json.dumps(checks,ensure_ascii=False,indent=2))
print(pd.DataFrame(summary).to_string(index=False))


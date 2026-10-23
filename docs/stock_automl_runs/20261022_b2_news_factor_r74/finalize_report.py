from pathlib import Path
import pandas as pd,numpy as np,json,hashlib,subprocess,platform
R=Path('/data/cyris/work/b2_news_factor_r74_20261022')
f=pd.read_pickle(R/'features709.pkl');c=pd.read_csv(R/'连续跌停20笔_公司与事前新闻.csv');cov=pd.read_csv(R/'306公开搜索_覆盖.csv')
f=f.merge(cov[['id','searched']],on='id',how='left');f['searched']=f.searched.fillna(0).astype(int)
cols={'stk_code':'股票','historical_name':'当时简称','d0_date':'选中日','d1_date':'买入日','board_streak_days':'连板天数','d0_5_risk_warning_days':'D0前5日风险提示天数','d1_5_risk_warning_days':'D1前5日风险提示天数','d1_10_risk_warning_days':'D1前10日风险提示天数','d1_5_abnormal':'前5日异动公告数','d1_5_reduction_plan':'前5日减持计划数','d1_5_enforcement':'前5日监管处罚提及数','d1_5_earnings_negative_title':'前5日业绩负面标题数','d1_5_valuation_warning':'前5日高估值提示数','d1_5_theme_denial':'前5日否认热点业务措辞数','d1_5_deal_uncertainty':'前5日重组不确定措辞数','d1_5_announcement_count':'前5日公告数','d1_10_announcement_count':'前10日公告数','media_complete':'新浪窗口检索完成','d1_5_news_count':'前5日新浪相关新闻数','d1_5_media_adverse':'前5日新浪负面标题数','searched':'公开搜索已执行','D2D3连续收盘跌停':'事后D2D3连续收盘跌停','close_net':'原收盘假设费后收益率'}
f[f.v2_allowed][list(cols)].rename(columns=cols).to_csv(R/'306入选_新闻因子简表.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
assert len(f)==709 and f.id.nunique()==709 and f.v2_allowed.sum()==306
assert len(c)==20 and c['股票'].nunique()==19
assert int(c.drop_duplicates('股票')['归母净利润_万元'].lt(0).sum())==7
assert f.official_complete.eq(1).all()
for timing in ['d0','d1']:
 for suffix in ['announcement_count','risk_warning_days','abnormal','reduction_plan','valuation_warning','theme_denial']:
  assert f[f'{timing}_10_{suffix}'].ge(f[f'{timing}_5_{suffix}']).all()
for w in [5,10]:assert f[f'd1_{w}_announcement_count'].ge(f[f'd0_{w}_announcement_count']).all()
assert f.loc[f.media_complete.eq(0),'d1_5_news_count'].isna().all()
assert cov.searched.eq(1).all() and len(cov)==306
for p in R.glob('*.csv'):
 assert not any(str(v).startswith("'") for v in pd.read_csv(p).select_dtypes('object').stack())
checks={'rows':709,'v2_rows':306,'case_trades':20,'case_companies':19,'latest_report_loss_companies':7,'official_windows_complete':709,'media_windows_complete':int(f.media_complete.sum()),'v2_media_windows_complete':int(f.loc[f.v2_allowed,'media_complete'].sum()),'public_search_windows':306,'window_nesting':True,'missing_media_is_nan':True,'numeric_csv_no_apostrophe':True,'financial_numbers_in_primary_pdf':all(x['three_numbers_in_primary_pdf'] for x in json.loads((R/'company_numeric_verification.json').read_text())),'account_baseline':'evaluate_factors.py asserted R73 daily NAV and cash-ledger conservation','production_changed':False}
(R/'verification.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
browser={'stock':'600053.SH','d0':'2024-01-26','url':'https://finance.sina.com.cn/roll/2024-01-26/doc-inaeumis0786306.shtml?from=pcsearch','observed_published':'2024-01-26 01:31','original_source':'上海证券报/公司风险公告','method':'logged-in browser visible date and article text','facts':'PE110.68倍，PB3.17倍；2023前三季度归母净利润同比下降83.37%；选股当天凌晨已经披露。','historical_index_complete':False}
(R/'browser_timestamp_check.json').write_text(json.dumps(browser,ensure_ascii=False,indent=2))
registration={'experiment':'B2_R74_NEWS_FACTORS_20261022','parent_strategy':'imac/B2_V2','definition_sha256':'54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00','parent_runs':['R69','R72','R73'],'sample_count':709,'selected_count':306,'new_model':False,'production_registration':'not_submitted','cloud_readback':'not_done','server_artifact_root':str(R),'signal_join_key':'stk_code|d0_date','sources':'manifest.json'}
(R/'registration_payload.json').write_text(json.dumps(registration,ensure_ascii=False,indent=2))
files=[]
for p in sorted(R.rglob('*')):
 if not p.is_file() or 'deps' in p.parts or '__pycache__' in p.parts or p.name=='manifest.json':continue
 if p.suffix not in ['.csv','.json','.pkl','.py','.md','.pdf','.txt']:continue
 files.append({'path':str(p.relative_to(R)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
inputs=[]
for path in ['/data/cyris/work/b2_d2_limit_follow_r69_20261021/all_signals.pkl','/data/cyris/work/b2_model_economics_r73_20261022/joined_98.pkl','/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json','/data/cyris/work/b2_first_board_flow_r72_20261022/统一三分类平衡逐笔预测.csv']:
 p=Path(path);inputs.append({'path':path,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
try:commit=subprocess.check_output(['git','-C','/data/cyris/fin_agent_deploy/b2-20261015','rev-parse','HEAD'],stderr=subprocess.DEVNULL,text=True).strip()
except Exception:commit='unavailable'
(R/'manifest.json').write_text(json.dumps({'python':platform.python_version(),'server_code_commit':commit,'code_identity':'per-script hashes; server commit excludes research edits','inputs':inputs,'files':files},ensure_ascii=False,indent=2))
print(json.dumps(checks,ensure_ascii=False));print('files',len(files))


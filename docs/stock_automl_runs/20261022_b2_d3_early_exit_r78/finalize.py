from pathlib import Path
import json,hashlib
import pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_d3_early_exit_r78_20261022')
daily=pd.read_csv(R/'91笔含开盘跌停.csv')
minute=pd.read_csv(R/'84笔分钟预约卖出对照.csv')
signal=pd.read_csv(R/'306笔D2_1455事前信号.csv')
a=pd.read_csv(R/'D2_1455条件延持账户.csv')
p=pd.read_csv(R/'1455持有_分钟与解封退出账户.csv')
capacity=pd.read_csv(R/'D3首个可卖分钟容量.csv')
idle=pd.read_csv(R/'轮空112笔D3开盘账户.csv')
assert len(daily)==91 and daily['分钟完整'].sum()==84
assert len(minute)==84*8 and len(signal)==306 and signal.D2_1455在涨停.sum()==90
assert (signal.D2_1455在涨停&signal.D2最终涨停).sum()==88
assert (signal.D2_1455在涨停&~signal.D2最终涨停).sum()==2
assert (~signal.D2_1455在涨停&signal.D2最终涨停).sum()==3
assert (daily['D3开盘涨幅']>0).sum()==56
assert (daily['D3开盘涨幅']>=.05).sum()==28
assert (daily['D3开盘跌停']).sum()==5
assert (daily['D3收盘跌停']).sum()==16
assert len(capacity)==80 and capacity.participation.max()<.062
baseline=a[(a.范围=='全期')&(a.规则=='原V2 D2收盘')].iloc[0]
proxy=p[p.范围=='全期'].iloc[0]
assert abs(baseline.账户收益率-1.6107495569)<1e-9
assert abs(proxy.账户收益率-3.776493)<1e-5
verification=dict(strategy_id='B2_V2',parent_definition_sha256='54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00',cohort=306,d2_final_limit_up=91,d3_complete_minutes=84,d3_missing_minutes=7,d2_1455_limit_signal=90,signal_final_limit_up=88,signal_false_positive=2,signal_missed_final_limit_up=3,minute_daily_OHLC_volume_reconciled=True,d3_limit_flags_reconciled=True,baseline_daily_nav_exact_reproduced=True,baseline_account_return=float(baseline.账户收益率),minute_proxy_account_return=float(proxy.账户收益率),minute_proxy_cash_short_skip=int(proxy.现金不足跳过买入),minute_proxy_missing_open_assumptions=int(proxy.缺失分钟开盘代理笔数),first_eligible_capacity_samples=len(capacity),first_eligible_capacity_max_share_of_minute_volume=float(capacity.participation.max()),all_idle_d3_open_assumed_return=float(idle[idle.范围=='全期'].iloc[0].账户收益率),production_db_write=False,cloud_readback=False)
(R/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n')
payload=dict(experiment_id='B2_D2_LIMIT_D3_EARLY_EXIT_R78_20261022',parent_strategy_owner='imac',parent_strategy_id='B2_V2',parent_definition_sha256=verification['parent_definition_sha256'],record_kind='read_only_counterfactual_research',cohort='306_frozen_V2_buys_91_D2_close_limit',intervention='14:55 known limit price signal; study D3 opening and first eligible minute exits',production_db_status='NOT_WRITTEN_REQUIRES_HUMAN_GATE',cloud_readback_status='PENDING',training='no_new_model')
(R/'registration_payload.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
skip={'manifest.json','quick.py','test_db.py','inspect_sparse.py','check_sparse.py','check_r20.py','sources.py'}
manifest={}
for f in sorted(R.rglob('*')):
 if not f.is_file() or 'raw' in f.relative_to(R).parts or f.name in skip:continue
 manifest[str(f.relative_to(R))]=dict(size=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest())
(R/'manifest.json').write_text(json.dumps({'experiment_id':payload['experiment_id'],'files':manifest,'raw_source_location':'server only; source hashes in 分钟来源及核验.csv'},ensure_ascii=False,indent=2)+'\n')
print('verified',len(manifest),'files','signal',verification['d2_1455_limit_signal'],'proxy_return',verification['minute_proxy_account_return'])

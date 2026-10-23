import json,sys
from pathlib import Path
import pandas as pd,numpy as np
O=Path('/data/cyris/work/b2_data_repair_r95_20261023');R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');M=Path('/data/cyris/tushare_market_data')
def save(f,n):f.to_csv(O/(n+'.csv'),index=False,encoding='utf-8-sig')
# Bulk raw archive manifest counts. Expected means API daily universe, not verified true historical listings.
rows=[];miss=[]
for p in sorted(M.glob('[0-9][0-9][0-9][0-9]/*.json')):
 x=json.loads(p.read_text());day=x.get('trade_date');n=x.get('active_codes');actual=x.get('codes_with_bars')
 if not day or n is None or actual is None:continue
 codes=x.get('listed_codes_without_minute_bars',[]);nmain=sum(c.endswith(('.SH','.SZ')) for c in codes)
 rows.append(dict(date=day,month=day[:4]+'-'+day[4:6],expected_stockdays=n,returned_stockdays=actual,missing_stockdays=n-actual,missing_shsz=nmain,missing_other=len(codes)-nmain,expected_raw_rows=n*241,actual_raw_rows=x['bars'],missing_raw_rows=n*241-x['bars'],returned_rows_vs_241=actual*241-x['bars'],file_exists=p.with_suffix('.csv.gz').exists()))
 miss.extend(dict(date=day,ts_code=c) for c in codes)
f=pd.DataFrame(rows);save(f,'archive_daily_inventory');save(pd.DataFrame(miss),'archive_missing_codes')
cols=[c for c in f if c not in ['date','month','file_exists']];g=f.groupby('month')[cols].sum();g['archive_days']=f.groupby('month').size();g['missing_fraction']=g.missing_stockdays/g.expected_stockdays;save(g.reset_index(),'archive_monthly_inventory')
# 2015/2016 calendar coverage: 2015 absent, do not claim complete by date or stock.
print('ARCHIVE',f.groupby(f.date.str[:4])[cols].sum().to_string(),flush=True)
# Actual strategy candidate demand: zero volume is not missing trade data.
a=pd.read_pickle(O/'union_facts.pkl');d=pd.read_pickle(O/'union_demand.pkl')
a=a.merge(d[['symbol','d1_date','d1_preclose','d1_volume']],on=['symbol','d1_date'],validate='one_to_one',suffixes=('','_ref'))
a['month']=a.d1_date.str[:7];a['zero_volume']=a.d1_volume.fillna(0).le(0);a['trading_expected']=~a.zero_volume;a['missing_trading_day']=a.missing&a.trading_expected;a['extrema_bad']=a.extrema_proxy_gap_pct.fillna(0).mul(a.d1_preclose).gt(.011);a['price_bad']=a.price_conflict&a.trading_expected;a['quality_bad_union']=(a.extrema_bad|a.price_bad)&a.trading_expected
# Accepted complete records have 240 canonical bars by original converter; 'full rows' does not mean OHLC accurate.
a['usable_price_day']=a.trading_expected&~a.missing_trading_day&~a.quality_bad_union
m=a.groupby('month').agg(candidate_stockdays=('symbol','size'),zero_volume_days=('zero_volume','sum'),expected_trading_stockdays=('trading_expected','sum'),missing_trading_stockdays=('missing_trading_day','sum'),extrema_bad_stockdays=('extrema_bad','sum'),other_price_bad_stockdays=('price_bad','sum'),bad_price_union_stockdays=('quality_bad_union','sum'),price_pass_stockdays=('usable_price_day','sum')).reset_index();m['expected_canonical_rows']=m.expected_trading_stockdays*240;m['missing_canonical_rows']=m.missing_trading_stockdays*240;m['bad_price_stockdays_fraction']=m.bad_price_union_stockdays/m.expected_trading_stockdays
save(m,'strategy_monthly_inventory');save(a[['symbol','d1_date','month','reason','zero_volume','missing_trading_day','extrema_bad','price_bad','quality_bad_union']],'strategy_problem_detail')
print('STRATEGY',m.sum(numeric_only=True).to_dict(),flush=True)
# Chinese titles for convenient use. Values remain numeric, without leading apostrophes.
save(m.rename(columns={'month':'月份','candidate_stockdays':'候选股票日','zero_volume_days':'零成交股票日','expected_trading_stockdays':'应有交易股票日','missing_trading_stockdays':'整日真缺档','extrema_bad_stockdays':'极值超容差股票日','other_price_bad_stockdays':'其他价格冲突股票日','bad_price_union_stockdays':'价格问题去重股票日','price_pass_stockdays':'价格审计通过股票日','expected_canonical_rows':'应有标准分钟行','missing_canonical_rows':'缺少标准分钟行','bad_price_stockdays_fraction':'价格问题占比'}),'策略分钟逐月')
save(g.reset_index().rename(columns={'month':'月份','expected_stockdays':'接口名单应有股票日','returned_stockdays':'已返回股票日','missing_stockdays':'未返回股票日','missing_shsz':'沪深未返回股票日','missing_other':'其他未返回股票日','expected_raw_rows':'接口应有原始行','actual_raw_rows':'实际原始行','missing_raw_rows':'差额原始行','returned_rows_vs_241':'已返回股票内行数差额','archive_days':'有档交易日','missing_fraction':'未返回比例'}),'全市场档案逐月')

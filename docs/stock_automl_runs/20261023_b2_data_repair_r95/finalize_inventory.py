from pathlib import Path
import json,hashlib,sys,platform
import pandas as pd
O=Path('/data/cyris/work/b2_data_repair_r95_20261023');R=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
f=pd.read_csv(O/'auction_fix_audit.csv',dtype={'symbol':str});f['month']=f.date.str[:7];a=f.groupby('month').repaired.sum().rename('auction_repaired_stockdays')
m=pd.read_csv(O/'strategy_monthly_inventory.csv').drop(columns=['auction_repaired_stockdays','remaining_bad_price_stockdays'],errors='ignore').merge(a,on='month',how='left');m.auction_repaired_stockdays=m.auction_repaired_stockdays.fillna(0).astype(int);m['remaining_bad_price_stockdays']=m.bad_price_union_stockdays-m.auction_repaired_stockdays;m.to_csv(O/'strategy_monthly_inventory.csv',index=False,encoding='utf-8-sig')
rename={'month':'月份','candidate_stockdays':'候选股票日','zero_volume_days':'零成交股票日','expected_trading_stockdays':'应有交易股票日','missing_trading_stockdays':'整日真缺档','extrema_bad_stockdays':'极值超容差股票日','other_price_bad_stockdays':'其他价格冲突股票日','bad_price_union_stockdays':'价格问题去重股票日','price_pass_stockdays':'原价格审计通过股票日','expected_canonical_rows':'应有标准分钟行','missing_canonical_rows':'缺少标准分钟行','bad_price_stockdays_fraction':'原价格问题占比','auction_repaired_stockdays':'本轮转换修复股票日','remaining_bad_price_stockdays':'仍有价格问题股票日'}
m.rename(columns=rename).to_csv(O/'策略分钟逐月.csv',index=False,encoding='utf-8-sig')
# Examples preserve prices, volumes, and original first-minute sequence.
rows=[]
for name in ['2016-09-14_300543','2019-05-24_300313','2020-07-14_002457','2018-02-26_600579']:
 day,sym=name.split('_');q=json.loads((O/'fresh_minutes'/(name+'_1.json')).read_text());b=pd.DataFrame(q['rows'],columns=q['fields']).sort_values('trade_time').head(8);b.insert(0,'example',name);rows.append(b)
pd.concat(rows).to_csv(O/'example_first_minutes.csv',index=False,encoding='utf-8-sig')
# Inspect all classifications numerically, not by visual flatness.
x=pd.read_csv(O/'extrema_pattern_by_year.csv');x=x[x.year.isin([2019,2020,2021])];n=x[['total','high_missing','low_missing','both']].sum();print('PATTERN',n.to_dict(),flush=True)
print('SAMPLE',f[f.symbol.eq('603603')].to_dict('records'),flush=True)
print('MONTH_EXAMPLES',m[m.month.isin(['2019-05','2020-07','2021-01','2021-03','2021-04','2021-09','2021-12'])].to_string(index=False),flush=True)
print('EARLY_ANNUAL',pd.read_csv(O/'early_calendar_coverage.csv').groupby(lambda i:pd.read_csv(O/'early_calendar_coverage.csv').iloc[i]['month'][:4]).sum(numeric_only=True).to_dict('index'),flush=True)
# D2 uses the 2018-2021 frozen shadow trades; do not imply later D2 coverage.
t=pd.read_pickle(R/'signals_exit.pkl');t=t[t.d0_date.ge('2018-01-01')].copy();t['month']=t.d2_date.str[:7];t['zero_volume']=t.d2_quality.eq('minute_missing');t['expected_trading']=~t.zero_volume;t['extrema_bad']=t.d2_extrema_proxy_gap_pct.fillna(0).mul(t.d2_preclose).gt(.011);t['close_bad']=t.d2_quality.eq('close_mismatch');t['bad_union']=t.extrema_bad|t.close_bad
z=t.groupby('month').agg(stockdays=('symbol','size'),zero_volume=('zero_volume','sum'),expected_trading=('expected_trading','sum'),extrema_bad=('extrema_bad','sum'),close_bad=('close_bad','sum'),bad_union=('bad_union','sum')).reset_index();z['expected_rows']=z.expected_trading*240;z['missing_trading_days']=0;z.to_csv(O/'d2_monthly_inventory.csv',index=False,encoding='utf-8-sig');print('D2TOTAL',z.sum(numeric_only=True).to_dict(),flush=True)
# References for exact reproduction; all immutable source paths are unchanged.
manifest={}
manifest.update(strategy='imac/B2_V2',definition_sha256='54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00',production_changed=False,models_retrained=False,original_archive_changed=False,positive_volume_days_recovered_by_retry=0,zero_volume_placeholders_quarantined=32,auction_conversion_repaired_days=int(f.repaired.sum()),overlay_usage='10 corrected stock-day sidecars; not applied to frozen model/account replay',tests='5 passed; 2 pandas deprecation warnings in isolated research environment',python=sys.version,platform=platform.platform(),registration='未提交生产；云盘待同步')
manifest['source_hashes']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [R/'minute_facts.pkl',R/'minute_demand.pkl',R/'enriched.pkl',R/'rules_frozen.json',R/'signals_exit.pkl',R/'daily_sources.json',R/'primary_model_year.csv',R/'rejected_strict_extrema/primary_model_year.csv']}
manifest['raw_hashes']={str(p.relative_to(O)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['fresh_minutes','recovery_raw','zero_volume_placeholder','minute_overlay','auction_conflict_raw','akshare_daily','akshare_year_sample','early_minute_probe'] for p in (O/folder).glob('*')}
manifest['script_hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in O.glob('*.py')};manifest['output_hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in O.glob('*.csv')}
(O/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))

from pathlib import Path
import hashlib,json
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(4*1024*1024),b''):h.update(c)
 return h.hexdigest()
names=[
'run.py','replay.py','analyze_primary.py','risk.py','audit.py','prepare_actions.py','quality_summary.py','publish_tables.py','hash_d1_sources.py','fetch_daily.py','repair_capped.py','fetch_db.py','prepare_prices.py','run_backtest.sh','manifest_final.py',
'scripts/experiment_limit_rebound_shape_r19.py','scripts/replay_limit_rebound_cash_r23.py','scripts/experiment_automl_limit_rebound_d0_open_r5.py','scripts/experiment_automl_limit_rebound_st_size_r4.py','scripts/experiment_b2_validation_r31.py',
'specification.json','risk_spec.json','daily_sources.json','d1_minute_sources.json','d2_minute_sources.json','panel_manifest.json','audit.json','model_reload_checks.json','corporate_action_receipt.json','coverage.json','rules_frozen.json','signals.pkl','signals_exit.pkl','primary_overall.json','primary_model_year.csv','primary_monthly_model.csv','risk_split_effect.csv','risk_account_comparison.csv','quality_by_year.csv','gate_sensitivity.csv','d2_quality_issues.csv',
'rejected_strict_extrema/coverage.json','rejected_strict_extrema/rules_frozen.json','rejected_strict_extrema/signals.pkl','rejected_strict_extrema/primary_overall.json','rejected_strict_extrema/primary_model_year.csv']
names += [str(p.relative_to(R)) for p in sorted((R/'deliverables').iterdir()) if p.is_file()]
files={name:sha(R/name) for name in names}
record={'strategy_id':'imac/B2_V2','strategy_sha256':'54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00','experiment':'R90_2018_2021','server_root':str(R),'files_sha256':files,'database_registered':False,'cloud_uploaded':False,'production_changed':False}
(R/'final_manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
print('manifest',len(files),'sha',sha(R/'final_manifest.json'),flush=True)

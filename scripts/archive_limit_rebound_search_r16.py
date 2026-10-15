"""Create and read back the local R16 handoff archive; does not upload it."""
from __future__ import annotations
import ast,hashlib,json,subprocess,zipfile
from pathlib import Path
from scripts.experiment_limit_rebound_search_r16 import OUT,PREV,SOURCE
from scripts.report_limit_rebound_search_r16 import DOC
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha

def run():
 roots=[Path('scripts')/n for n in ('experiment_limit_rebound_search_r16.py','report_limit_rebound_search_r16.py',
  'verify_limit_rebound_search_r16.py','archive_limit_rebound_search_r16.py')]
 sources=set();pending=roots[:]
 while pending:
  p=pending.pop()
  if p in sources or not p.exists():continue
  sources.add(p)
  for node in ast.walk(ast.parse(p.read_text())):
   names=([node.module] if isinstance(node,ast.ImportFrom) else [a.name for a in node.names] if isinstance(node,ast.Import) else [])
   for name in names:
    if name and name.startswith('scripts.'):
     f=Path(name.replace('.','/')+'.py')
     if f not in sources:pending.append(f)
 files=set(p for p in OUT.rglob('*') if p.is_file() and not p.is_symlink() and 'node_modules' not in p.parts and p.name!='archive_status.json')
 files.update(sources);files.update(p for p in DOC.rglob('*') if p.is_file() and p.name!='archive_status.json')
 files.update([SOURCE/'panel_replay_through_20261014.pkl',SOURCE/'calendar_through_20261014.json',PREV/'minute_facts.pkl',PREV/'account_results.csv',
  Path('scripts/build_limit_rebound_search_r16.mjs'),Path('tests/automl/test_limit_rebound_search_r16.py'),
  Path('tests/automl/test_limit_rebound_two_stage_r15.py'),Path('tests/automl/test_limit_rebound_monthly_r14.py'),Path('AUTOML.MD')])
 for pattern in ('*/rf.joblib','*/*_forecast_scores.pkl','*/stage1_frozen.json'):
  files.update(PREV.glob(pattern))
 files=sorted(files);manifest=[{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)} for p in files]
 context={'base_git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
  'new_research_files_uncommitted':True,'date':'2026-10-15','model_replay':'Use saved bundles and runtime.json; all sampled keys are embedded.',
  'raw_minutes':'Shared unadjusted 2025/2026 minute ZIPs were prepared in submission_20261014_three_strategies. This archive includes extracted minute facts and day caches, not duplicate market-wide raw minutes.',
  'commands':['experiment_limit_rebound_search_r16.py train --workers 6','experiment_limit_rebound_search_r16.py choose',
   'report_limit_rebound_search_r16.py minutes','report_limit_rebound_search_r16.py risk','report_limit_rebound_search_r16.py report',
   'report_limit_rebound_search_r16.py individual','verify_limit_rebound_search_r16.py'],
  'cloud_uploaded':False}
 dest=Path('outputs/stock_automl/aliyun_staging/涨停冷静期/D0判断-D1开盘伺机买入/20261015/R16_三算法72模型_样本均衡搜索_20261015.zip')
 dest.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
  for p in files:z.write(p,str(p))
  z.writestr('PACKAGE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
  z.writestr('RUN_CONTEXT.json',json.dumps(context,ensure_ascii=False,indent=2))
 with zipfile.ZipFile(dest) as z:
  for row in manifest:assert hashlib.sha256(z.read(row['path'])).hexdigest()==row['sha256']
 result={'archive':str(dest.resolve()),'bytes':dest.stat().st_size,'sha256':sha(dest),'source_files':len(manifest),
  'local_readback_verified':True,'cloud_uploaded':False,'cloud_readback_verified':False,
  'intended_cloud_directory':'策略训练/涨停冷静期/D0判断-D1开盘伺机买入/20261015'}
 dump(OUT/'archive_status.json',result);dump(DOC/'archive_status.json',result);print(result)

if __name__=='__main__':run()

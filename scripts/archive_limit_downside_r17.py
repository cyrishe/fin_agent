"""Save a verified local transfer package. No cloud upload or deployment."""
from __future__ import annotations
import ast,hashlib,json,shutil,subprocess,zipfile
from pathlib import Path
from scripts.experiment_limit_downside_r17 import OUT,SOURCE
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha

DOC=Path('docs/stock_automl_runs/20261015_limit_downside_r17')


def run():
    assert json.loads((OUT/'verification.json').read_text())['status']=='passed'
    prev=Path('outputs/stock_automl/limit_rebound_search_r16_20261015')
    shutil.copytree(prev/'runtime_wheels',OUT/'runtime_wheels',dirs_exist_ok=True)
    for name in ['runtime.json','requirements.txt']:shutil.copy2(prev/name,OUT/name)
    roots=[Path('scripts')/f'{name}_limit_downside_r17.py' for name in ('experiment','report','verify','archive')]
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
    files={f for f in OUT.rglob('*') if f.is_file() and not f.is_symlink() and 'node_modules' not in f.parts and f.name!='archive_status.json'}
    files.update(sources);files.update(f for f in DOC.rglob('*') if f.is_file() and f.name!='archive_status.json')
    files.update([Path('scripts/build_limit_downside_r17.mjs'),Path('tests/automl/test_limit_downside_r17.py'),Path('AUTOML.MD'),
                  SOURCE/'panel_replay_through_20261014.pkl',SOURCE/'calendar_through_20261014.json'])
    for folder in ['limit_rebound_two_stage_r15_20261015','limit_rebound_search_r16_20261015']:
        for name in ['test_top5_scored.pkl','selected_trades.pkl']:files.add(OUT.parent/folder/name)
    manifest=[{'path':str(f),'bytes':f.stat().st_size,'sha256':sha(f)} for f in sorted(files)]
    context={'base_git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
      'experiment':'R17 independent downside classifier','cloud_uploaded':False,'research_only':True,
      'sources':'source panel and raw-price audit extracts included; full original monthly database snapshots already held in shared research archive',
      'pilot':'Partial 240-tree RF pilot was stopped before test evaluation; formal run uses 80 trees/seed. Pilot is retained locally, not mixed into final models.',
      'reuse':'Each joblib contains fitted models, feature list, exact sampled panel indices and specification hash. panel.pkl is immutable and included.',
      'runtime':'Use runtime.json and saved runtime_wheels to restore exact sklearn/lightgbm/xgboost; do not load with default sklearn1.9.1.',
      'commands':['python -m scripts.experiment_limit_downside_r17 prepare','python -m scripts.experiment_limit_downside_r17 train --workers 4',
                  'python -m scripts.experiment_limit_downside_r17 choose','python -m scripts.experiment_limit_downside_r17 test',
                  'python -m scripts.report_limit_downside_r17','python -m scripts.verify_limit_downside_r17 prices',
                  'python -m scripts.verify_limit_downside_r17']}
    dest=OUT.parent/'aliyun_staging/涨停冷静期/D0判断-D1开盘伺机买入/20261015/R17_独立三日大跌两极模型_20261015.zip'
    dest.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        for f in sorted(files):z.write(f,str(f))
        z.writestr('PACKAGE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        z.writestr('RUN_CONTEXT.json',json.dumps(context,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(dest) as z:
        for row in manifest:assert hashlib.sha256(z.read(row['path'])).hexdigest()==row['sha256']
    status={'archive':str(dest.resolve()),'bytes':dest.stat().st_size,'sha256':sha(dest),'source_files':len(manifest),
      'local_readback_verified':True,'cloud_uploaded':False,'cloud_readback_verified':False,
      'intended_cloud_directory':'策略训练/涨停冷静期/D0判断-D1开盘伺机买入/20261015'}
    dump(OUT/'archive_status.json',status);dump(DOC/'archive_status.json',status);print(status)


if __name__=='__main__':run()

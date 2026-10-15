"""Self-contained local handoff, with hashes; no claim of cloud upload."""
import ast,hashlib,json,platform,shutil,sys,zipfile
from pathlib import Path
import joblib,numpy,pandas,sklearn
from scripts.experiment_limit_rebound_segments_r18 import OUT,OLD,SOURCE,MONTHS
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha

DOC=Path('docs/stock_automl_runs/20261015_limit_rebound_segments_r18')


def run():
    assert json.loads((OUT/'verification.json').read_text())['status']=='passed'
    wheel=OUT.parent/'limit_rebound_search_r16_20261015/runtime_wheels/scikit_learn-1.7.1-cp312-cp312-macosx_10_13_x86_64.whl'
    (OUT/'runtime_wheels').mkdir(exist_ok=True);shutil.copy2(wheel,OUT/'runtime_wheels'/wheel.name)
    dump(OUT/'runtime.json',{'python':sys.version,'platform':platform.platform(),'numpy':numpy.__version__,
      'pandas':pandas.__version__,'joblib':joblib.__version__,'sklearn':sklearn.__version__,
      'training_command_prefix':'DYLD_LIBRARY_PATH=/tmp/fin_agent_sklearn171/sklearn/.dylibs PYTHONPATH=/tmp/fin_agent_sklearn171:. OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv-automl/bin/python'})
    roots=[Path(f'scripts/{s}_limit_rebound_segments_r18.py') for s in ['experiment','report','verify','archive']]
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
    files.update([Path('scripts/build_limit_rebound_segments_r18.mjs'),Path('tests/automl/test_limit_rebound_segments_r18.py'),
      Path('AUTOML.MD'),SOURCE/'panel_replay_through_20261014.pkl',SOURCE/'calendar_through_20261014.json'])
    for m in MONTHS:
        files.add(OLD/m/'rf.joblib')
        for part in ['train','valid','forecast']:files.add(OLD/m/f'rf_{part}_scores.pkl')
    manifest=[{'path':str(f),'bytes':f.stat().st_size,'sha256':sha(f)} for f in sorted(files)]
    dest=OUT.parent/'aliyun_staging/涨停冷静期/D0判断-D1开盘伺机买入/20261015/R18_板前与连板分段特征_20261015.zip'
    dest.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        for f in sorted(files):z.write(f,str(f))
        z.writestr('PACKAGE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        z.writestr('RUN_CONTEXT.json',json.dumps({'experiment':'R18 feature ablation','cloud_uploaded':False,
          'models':'All 66 R18 model bundles plus 22 original R15 RF references are included.',
          'data':'All fit/scoring samples and exact features, raw daily segment extracts and provenance are included. Full raw monthly source snapshots are identified by hash in source_manifest.json and remain in the shared research archive.',
          'replay':['python -m scripts.verify_limit_rebound_segments_r18','python -m scripts.report_limit_rebound_segments_r18'],
          'research_only':True},ensure_ascii=False,indent=2))
    with zipfile.ZipFile(dest) as z:
        for r in manifest:assert hashlib.sha256(z.read(r['path'])).hexdigest()==r['sha256']
    status={'archive':str(dest.resolve()),'bytes':dest.stat().st_size,'sha256':sha(dest),'source_files':len(manifest),
      'local_readback_verified':True,'cloud_uploaded':False,'cloud_readback_verified':False,
      'intended_cloud_directory':'策略训练/涨停冷静期/D0判断-D1开盘伺机买入/20261015'}
    dump(OUT/'archive_status.json',status);dump(DOC/'archive_status.json',status);print(status)


if __name__=='__main__':run()

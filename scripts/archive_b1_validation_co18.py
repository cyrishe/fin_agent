"""Package CO18's actual samples, models, scores and executable source locally."""
from pathlib import Path
import ast,hashlib,json,shutil,subprocess,sys
from scripts.archive_automl_training import verify_package

ROOT=Path('outputs/stock_automl/b1_validation_co18_20261026')
DOC=Path('docs/stock_automl_runs/20261026_b1_validation_co18')

def main():
    assert json.loads((ROOT/'complete.json').read_text())['verified']
    package=ROOT.parent/'co18_package_20261026'
    if package.exists():raise ValueError('Immutable package already exists')
    package.mkdir()
    for source in [ROOT/'folds',DOC]:
        shutil.copytree(source,package/source.name)
    for name in ['source.json','complete.json','analysis.json','daily.csv','monthly.csv','validation_picks.csv.gz']:
        shutil.copy2(ROOT/name,package/name)
    inputs=package/'inputs';inputs.mkdir()
    for name in json.loads((ROOT/'source.json').read_text())['hashes']:
        shutil.copy2(ROOT.parent/'b1_history_20261016/run'/name,inputs/name)
    # Include the repository scripts imported by this experiment; environment is pinned below.
    pending=[Path('scripts')/name for name in ['research_b1_validation_co18.py','analyze_b1_validation_co18.py','archive_b1_validation_co18.py']];seen=set()
    while pending:
        source=pending.pop()
        if source in seen:continue
        seen.add(source);target=package/source;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        for node in ast.walk(ast.parse(source.read_text())):
            modules=[node.module] if isinstance(node,ast.ImportFrom) else ([n.name for n in node.names] if isinstance(node,ast.Import) else [])
            for module in modules:
                if module and module.startswith(('scripts.','src.')):
                    dependency=Path(module.replace('.','/')+'.py')
                    if dependency.is_file():pending.append(dependency)
    shutil.copy2('tests/automl/test_b1_validation_co18.py',package/'test_b1_validation_co18.py')
    context=dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        python=sys.version,production_changed=False,cloud_verified=False,
        command='PYTHONPATH=/tmp/fin_agent_sklearn171 .venv-automl/bin/python -m scripts.research_b1_validation_co18',
        analyze='PYTHONPATH=/tmp/fin_agent_sklearn171 .venv-automl/bin/python -m scripts.analyze_b1_validation_co18',
        dependencies=subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True).splitlines(),
        note='PYTHONPATH target scikit-learn==1.7.1 overrides base environment; research on previously observed history.')
    (package/'RUN_CONTEXT.json').write_text(json.dumps(context,ensure_ascii=False,indent=2))
    manifest=[]
    for p in sorted(package.rglob('*')):
        if p.is_file():manifest.append(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(package)))
    (package/'SHA256SUMS').write_text('\n'.join(manifest)+'\n')
    digest,count=verify_package(package)
    print(json.dumps(dict(local_path=str(package.resolve()),manifest_sha256=digest,files=count,local_readback=True,cloud_verified=False),indent=2))
if __name__=='__main__':main()

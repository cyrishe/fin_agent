"""Pack independently reproducible CO10 models, market samples, and B1 evaluation."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from scripts.experiment_independent_open_drop_co10 import OUT,REPORT,CO8
from scripts.experiment_b1_market_trade_gate_co8 import sha,save_json
from scripts.archive_automl_training import verify_package


def run():
    package=OUT/'package'
    if package.exists():raise RuntimeError('Immutable package exists')
    files=[p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip']
    files += [p for p in REPORT.rglob('*') if p.is_file() and not p.name.startswith('archive_')]
    files += list(Path('scripts').glob('*independent_open_drop_co10.py'))
    files += [Path('scripts/experiment_b1_market_trade_gate_co8.py'),Path('scripts/report_b1_market_trade_gate_co8.py'),
              Path('tests/automl/test_independent_open_drop_co10.py'),CO8/'dataset.csv.gz',CO8/'valuation_paths.csv.gz']
    files += list((CO8/'days').glob('*/snapshot.csv.gz'))+list((CO8/'days').glob('*/amounts.csv.gz'))
    package.mkdir()
    for p in sorted(set(files)):
        q=package/p;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
    m=json.loads((REPORT/'run_manifest.json').read_text())
    (package/'requirements.txt').write_text('\n'.join(f'{p}=={m[k]}' for p,k in [
        ('scikit-learn','sklearn'),('numpy','numpy'),('pandas','pandas'),('scipy','scipy'),('joblib','joblib')])+'\nthreadpoolctl==3.7.0\n')
    (package/'REPRODUCE.txt').write_text(
        'CO10 independent market risk model. Python '+m['python']+'. Install requirements.txt.\n'
        'Use a disposable copy and initialize/commit a local Git repository before fit (records HEAD).\n'
        'Set OPENBLAS_NUM_THREADS=1 and OMP_NUM_THREADS=1.\n'
        'On the disposable copy only, run python relocate_freeze.py before fit. It preserves the original receipt and all expected hashes.\n'
        'python -m scripts.experiment_independent_open_drop_co10 fit\n'
        'python -m scripts.experiment_independent_open_drop_co10 report\n'
        'prepare uses included frozen daily input and market snapshot/amount files, no database required.\n'
        'Do not replace original pre_run_freeze.json. Source was uncommitted; archived hashes define identity.\n')
    (package/'relocate_freeze.py').write_text(
        'import json, shutil\nfrom pathlib import Path\n'
        'root=Path(__file__).resolve().parent\n'
        "p=root/'docs/stock_automl_runs/20261015_independent_open_drop_co10/pre_run_freeze.json'\n"
        "backup=p.with_name('pre_run_freeze.original.json')\n"
        'if not backup.exists(): shutil.copy2(p,backup)\n'
        'd=json.loads(p.read_text())\n'
        "d['files']={k.removeprefix('/Volumes/ext/fin_agent/'):v for k,v in d['files'].items()}\n"
        "p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\\n')\n")
    (package/'SHA256SUMS').write_text('\n'.join(f'{sha(p)}  {p.relative_to(package)}' for p in sorted(package.rglob('*')) if p.is_file())+'\n')
    digest,count=verify_package(package)
    zpath=OUT/'CO10_独立全市场_次晨大跌与不跌分类_20261015.zip'
    with zipfile.ZipFile(zpath,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(package.rglob('*')):
            if p.is_file():z.write(p,str(p.relative_to(package)))
    with zipfile.ZipFile(zpath) as z:
        assert len(z.namelist())==count+1
        for line in z.read('SHA256SUMS').decode().splitlines():
            expected,name=line.split('  ',1);h=hashlib.sha256()
            with z.open(name) as f:
                for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
            assert h.hexdigest()==expected,name
    receipt=dict(package=str(package.resolve()),files=count,manifest_sha256=digest,
        zip_path=str(zpath.resolve()),zip_sha256=sha(zpath),zip_bytes=zpath.stat().st_size,
        zip_all_members_readback_verified=True,cloud_verified=False,cloud_status='待同步')
    save_json(REPORT/'archive_receipt.json',receipt);print(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__=='__main__':run()

"""Immutable package of all actual CO12 samples, model heads and evaluation evidence."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from scripts.experiment_independent_0940_netloss_co12 import OUT,REPORT,CO8,CO10,REPORT11,sha,save_json
from scripts.archive_automl_training import verify_package


def run():
    package=OUT/'package'
    if package.exists():raise RuntimeError('Immutable package exists')
    files=[p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip']
    files += [p for p in REPORT.rglob('*') if p.is_file() and not p.name.startswith('archive_')]
    files += list(Path('scripts').glob('*independent_0940_netloss_co12.py'))
    files += [Path('scripts/experiment_b1_market_trade_gate_co8.py'),Path('scripts/report_b1_market_trade_gate_co8.py'),
        Path('scripts/archive_automl_training.py'),
        Path('scripts/experiment_b1_calibrated_risk_co11.py'),Path('scripts/experiment_independent_open_drop_co10.py'),
        Path('tests/automl/test_independent_0940_netloss_co12.py'),Path('tests/automl/test_independent_open_drop_co10.py'),
        Path('docs/stock_automl_runs/HANDOFF_20261015_CO12.md'),CO10/'folds.json',
        CO8/'dataset.csv.gz',CO8/'valuation_paths.csv.gz',REPORT11/'daily.csv.gz',REPORT11/'summary.csv']
    package.mkdir()
    for p in sorted(set(files)):
        q=package/p;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
    manifest=json.loads((REPORT/'run_manifest.json').read_text())
    (package/'requirements.txt').write_text('\n'.join(f'{name}=={manifest[key]}' for name,key in [
        ('scikit-learn','sklearn'),('numpy','numpy'),('scipy','scipy'),('pandas','pandas'),('joblib','joblib')])+'\nthreadpoolctl==3.7.0\n')
    (package/'REPRODUCE.txt').write_text(
        'Python '+manifest['python']+'. Install requirements.txt. All actual training rows are in CO12 dataset.csv.gz; per-fold membership is explicit.\n'
        'No DB or raw minute archives needed to fit or report. Use a disposable copy, not the immutable original.\n'
        'Run python relocate_freeze.py in the disposable copy only. Set OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1.\n'
        'Report/audit: python -m scripts.report_independent_0940_netloss_co12\n'
        'Re-fit: move the disposable outputs/stock_automl/independent_0940_netloss_co12/models directory aside first; then\n'
        'python -m scripts.experiment_independent_0940_netloss_co12 fit\n'
        'python -m scripts.report_independent_0940_netloss_co12\n'
        'Independent inference: python -m scripts.predict_independent_0940_netloss_co12 --help\n'
        'Rebuilding features from raw source additionally requires the original CO10 archive; rebuilding endpoints from raw requires cited minute sources.\n'
        'Included endpoints and hashes retain all extracted label prices. Unknown labels were not negative training cases.\n')
    (package/'relocate_freeze.py').write_text(
        'import json,shutil\nfrom pathlib import Path\nroot=Path(__file__).resolve().parent\n'
        "p=root/'docs/stock_automl_runs/20261015_independent_0940_netloss_co12/pre_run_freeze.json'\n"
        "backup=p.with_name('pre_run_freeze.original.json')\n"
        'if not backup.exists():shutil.copy2(p,backup)\n'
        'd=json.loads(p.read_text())\n'
        "d['files']={k.removeprefix('/Volumes/ext/fin_agent/'):v for k,v in d['files'].items()}\n"
        "p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\\n')\n")
    (package/'SHA256SUMS').write_text('\n'.join(f'{sha(p)}  {p.relative_to(package)}' for p in sorted(package.rglob('*')) if p.is_file())+'\n')
    digest,count=verify_package(package)
    zpath=OUT/'CO12_独立0940净亏概率与幅度_B1轮空减仓_20261015.zip'
    with zipfile.ZipFile(zpath,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        for p in sorted(package.rglob('*')):
            if p.is_file():z.write(p,str(p.relative_to(package)))
    with zipfile.ZipFile(zpath) as z:
        assert len(z.namelist())==count+1
        for line in z.read('SHA256SUMS').decode().splitlines():
            expected,name=line.split('  ',1);h=hashlib.sha256()
            with z.open(name) as f:
                for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
            assert h.hexdigest()==expected,name
    receipt=dict(package=str(package.resolve()),manifest_sha256=digest,files=count,
        zip_path=str(zpath.resolve()),zip_sha256=sha(zpath),zip_bytes=zpath.stat().st_size,
        zip_all_members_readback_verified=True,cloud_verified=False,cloud_status='待同步')
    save_json(REPORT/'archive_receipt.json',receipt);print(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__=='__main__':run()

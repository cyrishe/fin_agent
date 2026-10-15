"""Immutable daily rolling-20 research package, all actual rows and historical calibration scores."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from scripts.experiment_daily20_risk_co13 import OUT,REPORT,CO12,REPORT12,sha,save_json
from scripts.archive_automl_training import verify_package


def run():
    package=OUT/'package'
    if package.exists():raise RuntimeError('Immutable package exists')
    files=[p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip']
    files += [p for p in REPORT.rglob('*') if p.is_file() and not p.name.startswith('archive_')]
    files += [Path(p) for p in json.loads((REPORT/'pre_run_freeze.json').read_text())['files']]
    files += [Path(p) for p in json.loads((REPORT/'rank_gate_pre_run_freeze.json').read_text())['files']]
    files += [Path(p) for p in json.loads((REPORT/'smallk_pre_run_freeze.json').read_text())['files']]
    files += list(Path('scripts').glob('*daily20_risk_co13.py'))
    files += [Path('scripts/experiment_independent_open_drop_co10.py'),Path('scripts/archive_automl_training.py'),
              Path('docs/stock_automl_runs/HANDOFF_20261015_CO13.md')]
    files += [p for p in REPORT12.iterdir() if p.is_file() and not p.name.startswith('archive_')]
    files += [CO12/'folds.json']
    files += [p for p in (CO12/'models').glob('*/*') if p.name in ['model.joblib','calibration.json','fold.json']]
    package.mkdir()
    for p in sorted(set(files)):
        q=package/p;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
    manifest=json.loads((REPORT/'run_manifest.json').read_text())
    (package/'requirements.txt').write_text('\n'.join(f'{name}=={manifest[key]}' for name,key in [
        ('scikit-learn','sklearn'),('numpy','numpy'),('scipy','scipy'),('pandas','pandas'),('joblib','joblib')])+'\nthreadpoolctl==3.7.0\n')
    (package/'REPRODUCE.txt').write_text(
        'Python '+manifest['python']+'. Install requirements.txt. Set OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1.\n'
        'All actual training/calibration samples are CO12 dataset.csv.gz; each CO13 fold explicitly saves sample IDs and training weights.\n'
        'All 308 daily bundles and raw OOS scores are present; 20 warmup bundles have no calibrator.\n'
        'Run only in a disposable copy. No DB or raw minute archives required for fit, report or verification.\n'
        'Report: python -m scripts.report_daily20_risk_co13\n'
        'Verify: python -m scripts.verify_daily20_risk_co13\n'
        'Re-fit: move the disposable outputs/stock_automl/daily20_risk_co13/models directory aside, then\n'
        'python -m scripts.experiment_daily20_risk_co13 fit\n'
        'The 60-day control uses immutable CO12 daily results; original 60-day bundles and report are also included.\n'
        'Rebuilding the original CO12 experiment from source needs its separate complete archive.\n')
    (package/'SHA256SUMS').write_text('\n'.join(f'{sha(p)}  {p.relative_to(package)}' for p in sorted(package.rglob('*')) if p.is_file())+'\n')
    digest,count=verify_package(package)
    zp=OUT/'CO13_风险20日每日滚动_对照60日_B1固定_20261015.zip'
    with zipfile.ZipFile(zp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        for p in sorted(package.rglob('*')):
            if p.is_file():z.write(p,str(p.relative_to(package)))
    with zipfile.ZipFile(zp) as z:
        assert len(z.namelist())==count+1
        for line in z.read('SHA256SUMS').decode().splitlines():
            expected,name=line.split('  ',1);h=hashlib.sha256()
            with z.open(name) as f:
                for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
            assert h.hexdigest()==expected,name
    receipt=dict(package=str(package.resolve()),manifest_sha256=digest,files=count,
        zip_path=str(zp.resolve()),zip_sha256=sha(zp),zip_bytes=zp.stat().st_size,
        zip_all_members_readback_verified=True,cloud_verified=False,cloud_status='待同步')
    save_json(REPORT/'archive_receipt.json',receipt);print(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__=='__main__':run()

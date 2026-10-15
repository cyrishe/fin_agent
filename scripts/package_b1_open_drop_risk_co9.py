"""Immutable CO9 evidence bundle, including local inputs needed for preparation."""
import json
from pathlib import Path
import shutil
import zipfile
import hashlib
from scripts.experiment_b1_open_drop_risk_co9 import OUT,REPORT,SOURCE
from scripts.experiment_b1_market_trade_gate_co8 import sha,save_json
from scripts.archive_automl_training import verify_package


def run():
    package=OUT/'package'
    if package.exists():raise RuntimeError('Do not overwrite immutable package')
    files=[p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip']
    files += [p for p in REPORT.rglob('*') if p.is_file() and not p.name.startswith('archive_')]
    files += list(Path('scripts').glob('*b1_open_drop_risk_co9.py'))
    files += [Path('scripts/experiment_b1_market_trade_gate_co8.py'),
              Path('scripts/report_b1_market_trade_gate_co8.py'),
              Path('tests/automl/test_b1_open_drop_risk_co9.py')]
    files += [SOURCE/p for p in ['dataset.csv.gz','daily_input.csv.gz','valuation_paths.csv.gz']]
    files += list((SOURCE/'days').glob('*/paths.csv.gz'))
    files += list((SOURCE/'days').glob('*/complete.json'))
    package.mkdir()
    for p in sorted(set(files)):
        q=package/p;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
    m=json.loads((REPORT/'run_manifest.json').read_text())
    (package/'requirements.txt').write_text('\n'.join(f'{p}=={m[k]}' for p,k in [
        ('scikit-learn','sklearn'),('numpy','numpy'),('pandas','pandas'),('scipy','scipy'),('joblib','joblib')])+'\nthreadpoolctl==3.7.0\n')
    (package/'REPRODUCE.txt').write_text(
        'CO9: work on a disposable copy, preserving the immutable original. Python '+m['python']+'.\n'
        'Install requirements.txt and set OPENBLAS_NUM_THREADS=1, OMP_NUM_THREADS=1.\n'
        'Initialize a disposable Git repository and commit the archived files before fit (records git HEAD).\n'
        'python -m scripts.experiment_b1_open_drop_risk_co9 fit\n'
        'python -m scripts.experiment_b1_open_drop_risk_co9 report\n'
        'prepare can rebuild dataset from included CO8 local inputs; no database needed.\n'
        'Do not rerun freeze or overwrite the historical pre-run freeze. Source was uncommitted; use included hashes.\n')
    (package/'SHA256SUMS').write_text('\n'.join(f'{sha(p)}  {p.relative_to(package)}' for p in sorted(package.rglob('*')) if p.is_file())+'\n')
    digest,count=verify_package(package)
    zpath=OUT/'CO9_B1两极样本_次晨大跌风险_20261015.zip'
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

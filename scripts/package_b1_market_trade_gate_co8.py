"""Freeze CO8 inputs, models, code and reports; independently read every ZIP member."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from scripts.experiment_b1_market_trade_gate_co8 import OUT, REPORT, OLD, sha, save_json
from scripts.archive_automl_training import verify_package


def run():
    package=OUT/'package'
    if package.exists():
        raise RuntimeError('An immutable package already exists; do not overwrite it')
    package.mkdir()
    files=[]
    files += [p for p in OUT.rglob('*') if p.is_file() and 'package' not in p.relative_to(OUT).parts and p.suffix!='.zip']
    files += [p for p in REPORT.rglob('*') if p.is_file() and p.name!='archive_receipt.json']
    files += list(Path('scripts').glob('*b1_market_trade_gate_co8.py'))
    files += [Path('tests/automl/test_b1_market_trade_gate_co8.py'),OLD/'daily.csv',
              Path('docs/stock_automl_runs/20261011_mk_2025_mar_aug_backtest/top2_verified.csv'),
              Path('docs/stock_automl_runs/20261011_mk_2025_mar_aug_backtest/folds.csv')]
    for p in sorted(set(files)):
        dest=package/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
    manifest=json.loads((REPORT/'run_manifest.json').read_text())
    (package/'REPRODUCE.txt').write_text(
        'CO8 workspace-shaped archive. B1 picks are frozen inputs. No credentials included.\n'
        'Fit/report use only archived inputs and do not need the database.\n'
        'Install Python '+manifest['python']+' and requirements.txt in an isolated environment.\n'
        'Work on a copy of the archive. The fit command rewrites model/output files.\n'
        'Initialize a disposable Git repository and commit this copy first (fit records git HEAD).\n'
        'Set OPENBLAS_NUM_THREADS=1 and OMP_NUM_THREADS=1 before running Python.\n'
        'python -m scripts.experiment_b1_market_trade_gate_co8 fit\n'
        'python -m scripts.report_b1_market_trade_gate_co8\n'
        'python -m scripts.check_b1_market_trade_gate_co8 drop2pct\n'
        'Working-tree scripts are included and hash-bound; the recorded base commit alone is insufficient.\n')
    (package/'requirements.txt').write_text('\n'.join([f'{p}=={manifest[k]}' for p,k in [
        ('numpy','numpy'),('scipy','scipy'),('pandas','pandas'),('scikit-learn','sklearn'),
        ('joblib','joblib'),('threadpoolctl','threadpoolctl')]])+'\n')
    sums=[]
    for p in sorted(package.rglob('*')):
        if p.is_file():sums.append(f'{sha(p)}  {p.relative_to(package)}')
    (package/'SHA256SUMS').write_text('\n'.join(sums)+'\n')
    digest,count=verify_package(package)
    archive=OUT/'CO8_B1固定选股_市场个股交易开关_20261015.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(package.rglob('*')):
            if p.is_file():z.write(p,str(p.relative_to(package)))
    with zipfile.ZipFile(archive) as z:
        assert len(z.namelist())==count+1
        for line in z.read('SHA256SUMS').decode().splitlines():
            expected,name=line.split('  ',1);h=hashlib.sha256()
            with z.open(name) as f:
                for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
            assert h.hexdigest()==expected,name
    receipt={'package':str(package.resolve()),'files':count,'manifest_sha256':digest,
             'zip_path':str(archive.resolve()),'zip_sha256':sha(archive),'zip_bytes':archive.stat().st_size,
             'zip_all_members_readback_verified':True,'cloud_verified':False,'cloud_status':'待同步'}
    save_json(REPORT/'archive_receipt.json',receipt)
    print(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__=='__main__':run()

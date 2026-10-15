"""Freeze the CO11 calibration experiment with an explicit CO10 dependency."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from scripts.experiment_b1_calibrated_risk_co11 import OUT, REPORT, CO8, sha, save_json
from scripts.archive_automl_training import verify_package


def run():
    package=OUT/'package'
    if package.exists():raise RuntimeError('Immutable package exists')
    files=[p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip']
    files += [p for p in REPORT.rglob('*') if p.is_file() and not p.name.startswith('archive_')]
    files += list(Path('scripts').glob('*b1_calibrated_risk_co11.py'))
    files += [Path('tests/automl/test_b1_calibrated_risk_co11.py'),
        Path('scripts/experiment_b1_market_trade_gate_co8.py'),Path('scripts/report_b1_market_trade_gate_co8.py'),
        CO8/'dataset.csv.gz',CO8/'valuation_paths.csv.gz',
        Path('docs/stock_automl_runs/HANDOFF_20261015_CO11.md')]
    package.mkdir()
    for p in sorted(set(files)):
        q=package/p;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
    dependency=json.loads(Path('docs/stock_automl_runs/20261015_independent_open_drop_co10/archive_receipt.json').read_text())
    save_json(package/'DEPENDENCIES.json',dict(CO10=dict(experiment_id='CO10_20261015_independent_market_open_drop',
        manifest_sha256=dependency['manifest_sha256'],zip_sha256=dependency['zip_sha256'],
        purpose='Frozen independent trees, full-market labels and validation/test raw scores. Extract into same disposable reproduction root.')))
    (package/'requirements.txt').write_text('scikit-learn==1.7.1\nnumpy==2.5.2\npandas==2.3.3\nscipy==1.18.1\njoblib==1.6.0\nthreadpoolctl==3.7.0\n')
    (package/'REPRODUCE.txt').write_text(
        'Python 3.12.14. CO11 contains all actual calibration samples, parameters, B1 scores and accounting inputs.\n'
        'Full market regeneration also needs immutable CO10 identified in DEPENDENCIES.json.\n'
        'Extract CO10, then CO11 into a disposable directory; install requirements.txt. No DB needed.\n'
        'Run python relocate_freeze.py in that disposable copy only. Set OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1.\n'
        'To reproduce accounting: python -m scripts.experiment_b1_calibrated_risk_co11 report\n'
        'To reproduce calibration: first move the disposable outputs/stock_automl/b1_calibrated_risk_co11/b1_probabilities.csv.gz aside, then\n'
        'python -m scripts.experiment_b1_calibrated_risk_co11 run\n'
        'python -m scripts.report_b1_calibrated_risk_co11\n'
        'Original frozen archive must not be overwritten. Cloud upload remains pending until independent read-back.\n')
    (package/'relocate_freeze.py').write_text(
        'import json, shutil\nfrom pathlib import Path\nroot=Path(__file__).resolve().parent\n'
        "p=root/'docs/stock_automl_runs/20261015_b1_calibrated_risk_co11/pre_run_freeze.json'\n"
        "backup=p.with_name('pre_run_freeze.original.json')\n"
        'if not backup.exists(): shutil.copy2(p,backup)\n'
        'd=json.loads(p.read_text())\n'
        "d['files']={k.removeprefix('/Volumes/ext/fin_agent/'):v for k,v in d['files'].items()}\n"
        "p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\\n')\n")
    (package/'SHA256SUMS').write_text('\n'.join(f'{sha(p)}  {p.relative_to(package)}' for p in sorted(package.rglob('*')) if p.is_file())+'\n')
    digest,count=verify_package(package)
    zpath=OUT/'CO11_B1叠加独立下跌概率_校准轮空与减仓_20261015.zip'
    with zipfile.ZipFile(zpath,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        for p in sorted(package.rglob('*')):
            if p.is_file():z.write(p,str(p.relative_to(package)))
    with zipfile.ZipFile(zpath) as z:
        for line in z.read('SHA256SUMS').decode().splitlines():
            expected,name=line.split('  ',1);h=hashlib.sha256()
            with z.open(name) as f:
                for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
            assert h.hexdigest()==expected,name
        assert len(z.namelist())==count+1
    receipt=dict(package=str(package.resolve()),manifest_sha256=digest,files=count,
        zip_path=str(zpath.resolve()),zip_sha256=sha(zpath),zip_bytes=zpath.stat().st_size,
        zip_all_members_readback_verified=True,cloud_verified=False,cloud_status='待同步')
    save_json(REPORT/'archive_receipt.json',receipt);print(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__=='__main__':run()

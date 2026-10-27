"""Package RF research, common samples, original baselines and full replay evidence."""
from pathlib import Path
import argparse,json,shutil,subprocess,sys
import joblib,numpy,pandas,sklearn,pyarrow
from scripts.archive_automl_training import sha256_file,verify_package
ROOT=Path(__file__).resolve().parents[1];R=ROOT/'outputs/stock_automl/low_base_rf_co27_20261027';PREV=ROOT/'outputs/stock_automl/low_base_ml_co26_20261027';D=ROOT/'docs/stock_automl_runs/20261027_low_base_rf_co27';P=ROOT/'outputs/stock_automl/co27_package_20261027'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source-commit',required=True);a=ap.parse_args();commit=subprocess.check_output(['git','rev-parse',a.source_commit],text=True).strip();assert commit==a.source_commit
 check=json.loads((D/'verification.json').read_text());corr=json.loads((D/'correlation_verification.json').read_text());assert check['models']==42 and check['months']==21 and corr['independent_scipy_checks']==66
 assert not P.exists();P.mkdir(parents=True)
 paths=[p for p in R.rglob('*') if p.is_file() and not any(x.startswith('._') for x in p.parts)];paths += [p for p in D.iterdir() if p.is_file() and not p.name.startswith('._') and p.name!='archive_receipt.json']
 paths += [PREV/n for n in ['samples.parquet','build_manifest.json','selection.json','validation_daily.csv.gz','test_daily.csv.gz']]
 paths += [p for phase in ['validation','test'] for p in (PREV/phase).glob('*/members_h*.npz')]
 for name in ['train_low_base_rf_co27','evaluate_low_base_rf_co27','diagnose_low_base_rf_co27','analyze_low_base_rf_co27_auc','analyze_low_base_rf_co27_score_quality','verify_low_base_rf_co27','package_low_base_rf_co27','audit_low_base_co27_windows','analyze_low_base_co27_correlations','analyze_low_base_co27_cross_section','verify_low_base_co27_correlations','train_low_base_co26','build_low_base_co26','evaluate_low_base_co26','research_gentle_rise_gr1','archive_automl_training']:
  paths.append(ROOT/'scripts'/f'{name}.py')
 paths += [ROOT/'tests/experiments/test_low_base_rf_co27.py',ROOT/'tests/experiments/test_low_base_co26.py']
 for p in paths:
  dest=P/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
 meta=dict(experiment='CO27_20261027_low_base_rf',source_commit=commit,python=sys.version,numpy=numpy.__version__,pandas=pandas.__version__,sklearn=sklearn.__version__,pyarrow=pyarrow.__version__,joblib=joblib.__version__,models=42,months=21,cloud_verified=False,production_changed=False)
 (P/'RUN_CONTEXT.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));(P/'README.txt').write_text('CO27: restore paths into a work copy and see docs/stock_automl_runs/20261027_low_base_rf_co27/README.md. Package contains CO26 1.26m candidate rows, members and daily baselines, CO27 42 forests, candidate scores, dates, correlations and verification. Upstream raw daily snapshot is recorded by hash in CO26 build_manifest and its separate archive; full source package remains CO26. Cloud sync pending.\n')
 files=sorted(p for p in P.rglob('*') if p.is_file() and not any(x.startswith('._') for x in p.parts));(P/'SHA256SUMS').write_text(''.join(f'{sha256_file(p)}  {p.relative_to(P)}\n' for p in files));digest,n=verify_package(P);print(json.dumps(dict(path=str(P),manifest_sha256=digest,files=n,local_readback=True,cloud_verified=False),indent=2))
if __name__=='__main__':main()

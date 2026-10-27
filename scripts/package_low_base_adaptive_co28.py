"""Package CO28 samples, fitted folds, score panels, and evidence."""
from pathlib import Path
import argparse,json,shutil,subprocess,sys
import joblib,numpy,pandas,sklearn,pyarrow
from scripts.archive_automl_training import sha256_file,verify_package
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'outputs/stock_automl/low_base_adaptive_co28_20261027'
D=ROOT/'docs/stock_automl_runs/20261027_low_base_adaptive_co28'
P=ROOT/'outputs/stock_automl/co28_package_20261027'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source-commit',required=True);a=ap.parse_args();commit=subprocess.check_output(['git','rev-parse',a.source_commit],text=True).strip();assert commit==a.source_commit
 verification=json.loads((D/'verification.json').read_text());assert verification['models_readback']==294
 assert not P.exists();P.mkdir(parents=True)
 paths=[p for p in R.rglob('*') if p.is_file() and not p.name.startswith('._')]
 paths += [p for p in D.iterdir() if p.is_file() and not p.name.startswith('._') and p.name!='archive_receipt.json']
 for name in ['research_low_base_adaptive_co28','evaluate_low_base_adaptive_co28','verify_low_base_adaptive_co28','package_low_base_adaptive_co28','train_low_base_co26','evaluate_low_base_co26','research_gentle_rise_gr1','archive_automl_training']:
  paths.append(ROOT/'scripts'/f'{name}.py')
 for p in paths:
  dest=P/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
 meta={'experiment':'CO28_20261027_low_base_adaptive','source_commit':commit,'python':sys.version,'numpy':numpy.__version__,'pandas':pandas.__version__,'sklearn':sklearn.__version__,'pyarrow':pyarrow.__version__,'joblib':joblib.__version__,'models':294,'months':21,'cloud_verified':False,'production_changed':False,'source_CO26_sample_sha256':json.loads((R/'build.json').read_text())['source_sha256']}
 (P/'RUN_CONTEXT.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));(P/'README.txt').write_text('CO28: restored paths reproduce all 2025 validation and 2026 test folds. The full transformed sample and 294 models/scores are included. Original CO26 source and CO27 comparison models are separately archived with source hashes here. Cloud sync pending.\n')
 files=sorted(p for p in P.rglob('*') if p.is_file() and not p.name.startswith('._'))
 (P/'SHA256SUMS').write_text(''.join(f'{sha256_file(p)}  {p.relative_to(P)}\n' for p in files))
 digest,n=verify_package(P);print(json.dumps({'path':str(P),'manifest_sha256':digest,'files':n,'local_readback':True,'cloud_verified':False},indent=2))
if __name__=='__main__':main()

"""Package CO26 samples, original sources, models and audits for local staging."""
from pathlib import Path
import argparse,json,shutil,subprocess,sys
import joblib,numpy,pandas,sklearn,pyarrow
from scripts.archive_automl_training import sha256_file,verify_package
ROOT=Path(__file__).resolve().parents[1]
O=ROOT/'outputs/stock_automl/low_base_ml_co26_20261027';D=ROOT/'docs/stock_automl_runs/20261027_low_base_ml_co26';P=ROOT/'outputs/stock_automl/co26_package_20261027'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source-commit',required=True);args=ap.parse_args()
 commit=subprocess.check_output(['git','rev-parse',args.source_commit],text=True).strip();assert commit==args.source_commit
 verify=json.loads((O/'verification.json').read_text());assert verify['models_verified']==66 and verify['membership_windows_verified']==42
 assert not P.exists();P.mkdir(parents=True)
 paths=[p for p in O.rglob('*') if p.is_file() and not any(x.startswith('._') for x in p.parts)]
 paths += [p for p in D.iterdir() if p.is_file() and not p.name.startswith('._') and p.name!='archive_receipt.json']
 names=['discover','build','train','evaluate','summarize','verify','verify_low_base_co26_inputs','audit_low_base_co26_unknown','inspect_low_base_co26_factors','package']
 for name in names:
  full=name if name.endswith('co26_inputs') or name.endswith('co26_unknown') or name.endswith('co26_factors') else f'{name}_low_base_co26'
  paths.append(ROOT/'scripts'/f'{full}.py')
 paths += [ROOT/'scripts/research_low_base_co25.py',ROOT/'scripts/research_gentle_rise_gr1.py',ROOT/'scripts/archive_automl_training.py',ROOT/'tests/experiments/test_low_base_co26.py']
 paths += [ROOT/'outputs/stock_automl/low_base_co25_20261027'/name for name in ['daily_inputs.parquet','panel.parquet','calendar.csv']]
 for src in paths:
  dst=P/src.relative_to(ROOT);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
 context={'experiment':'CO26_20261027_low_base_ml','source_commit':commit,'python':sys.version,'numpy':numpy.__version__,'pandas':pandas.__version__,'sklearn':sklearn.__version__,'pyarrow':pyarrow.__version__,'joblib':joblib.__version__,'models':66,'training_windows':42,'cloud_verified':False,'production_changed':False,'input_vintage':'CO25 historical daily snapshot; no contemporaneous funds'}
 (P/'RUN_CONTEXT.json').write_text(json.dumps(context,ensure_ascii=False,indent=2))
 (P/'README.txt').write_text('CO26 local package: restore source paths into a working copy; see docs/stock_automl_runs/20261027_low_base_ml_co26/README.md and DESIGN.md. The package contains raw CO25 daily input, original candidate panel, CO26 candidate rows, all models, training membership, scores and audits. Output paths must be copied to a new working directory before rerun. Cloud upload is pending.\n')
 files=sorted(f for f in P.rglob('*') if f.is_file() and not any(x.startswith('._') for x in f.parts))
 (P/'SHA256SUMS').write_text(''.join(f'{sha256_file(f)}  {f.relative_to(P)}\n' for f in files));digest,count=verify_package(P)
 print(json.dumps({'source_path':str(P),'manifest_sha256':digest,'files':count,'local_readback':True,'cloud_verified':False},indent=2))
if __name__=='__main__':main()

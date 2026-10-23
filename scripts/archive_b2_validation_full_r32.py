"""Save a verifiable standalone R32 trained-model and replay snapshot."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tarfile
from scripts.experiment_b2_validation_full_r32 import OUT,BASE,EARLY,CONT,COMMON,OLD31,r
from scripts.report_b2_validation_full_r32 import DOC

def main():
    paths=set()
    for root in [OUT,DOC]:
        paths.update(p for p in root.rglob('*') if p.is_file() and not p.name.startswith('._') and p.name not in ['asset_manifest.json','archive_receipt.json'])
    paths.update(BASE/n for n in ['panel.pkl','calendar.json','source_manifest.json','specification.json'])
    paths.update(r.four.OUT/n for n in ['four_rules_frozen.json','policy_picks.pkl','four_all_top3.pkl','first10_evidence.pkl'])
    paths.update(EARLY/n for n in ['signals.pkl','four_rules_frozen.json','top3_four.pkl','decisions.pkl'])
    paths.update(CONT/n for n in ['receipt.json','replay.json'])
    paths.update(OLD31/n for n in ['specification.json','accounts.json','rules_frozen.json','signals.pkl'])
    paths.add(r.shape.OUT/'historical_status.csv.gz')
    for x in json.loads((OUT/'score_provenance.json').read_text()):paths.add(Path(x['path']))
    for month in COMMON:
        paths.add(BASE/month/'segments.joblib')
        paths.update(BASE/month/f'segments_{part}_scores.pkl' for part in ['train','valid','forecast'])
    for module in list(sys.modules.values()):
        file=getattr(module,'__file__',None)
        if not file:continue
        try:p=Path(file).resolve().relative_to(Path.cwd())
        except ValueError:continue
        if str(p).startswith('scripts/') and p.suffix=='.py':paths.add(p)
    paths.update(Path('scripts')/n for n in ['experiment_b2_validation_full_r32.py','report_b2_validation_full_r32.py','archive_b2_validation_full_r32.py','verify_b2_validation_full_r32_workbook.py','build_b2_validation_r31.mjs','automl_b2.py'])
    paths.add(Path('AUTOML.md'))
    entries=[dict(path=str(p),bytes=p.stat().st_size,sha256=r.sha(p)) for p in sorted(paths)]
    manifest=dict(experiment='R32',git_base=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),files=entries,formal_registration=False,production_changed=False,cloud_uploaded=False,
        suggested_cloud_folder='策略训练/涨停冷静期/D0判断-D1开盘伺机买入/20261019/R32_两层一月验证全期',
        note='Saved daily panel,27new-version models,18baseline models,actual samples,all scores/rules/first10bars and ledger support replays. Full upstream minutes remain external, identified in saved minute_source paths; do not claim a cloud upload.')
    r.dump(OUT/'asset_manifest.json',manifest)
    archive=OUT.parent/(OUT.name+'_assets.tar.gz')
    with tarfile.open(archive,'w:gz',compresslevel=6) as tf:
        for p in sorted(paths):tf.add(p,arcname=str(p),recursive=False)
        tf.add(OUT/'asset_manifest.json',arcname=str(OUT/'asset_manifest.json'))
    expected={x['path']:x for x in entries};verified=0
    with tarfile.open(archive,'r:gz') as tf:
        for member in tf:
            if member.name not in expected:continue
            h=hashlib.sha256();source=tf.extractfile(member)
            for chunk in iter(lambda:source.read(1024*1024),b''):h.update(chunk)
            assert h.hexdigest()==expected[member.name]['sha256'],member.name
            verified+=1
    assert verified==len(entries)
    receipt=dict(path=str(archive),bytes=archive.stat().st_size,sha256=r.sha(archive),verified_members=verified,local_readback=True,cloud_uploaded=False)
    r.dump(OUT/'archive_receipt.json',receipt);r.dump(DOC/'archive_receipt.json',receipt)
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()

"""Save R31 local training/replay assets and verify every archived member."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tarfile

from scripts.experiment_b2_validation_r31 import OUT, BASE, MONTHS, HISTORY, four
from scripts.report_b2_validation_r31 import DOC
from scripts.replay_limit_rebound_cash_r23 import dump, sha


def main():
    paths = set()
    paths.update(p for p in OUT.rglob('*') if p.is_file() and not p.name.startswith('._')
                 and p.name not in ['asset_manifest.json', 'archive_receipt.json'])
    paths.update(p for p in DOC.rglob('*') if p.is_file() and not p.name.startswith('._')
                 and p.name not in ['asset_manifest.json', 'archive_receipt.json'])
    paths.update(BASE/name for name in ['panel.pkl', 'calendar.json', 'source_manifest.json'])
    paths.update(four.OUT/name for name in ['four_rules_frozen.json', 'policy_picks.pkl'])
    for item in json.loads((OUT/'score_provenance.json').read_text()):
        paths.add(Path(item['path']))
    for month in HISTORY:
        paths.add(BASE/month/'segments.joblib')
    for month in MONTHS:
        paths.add(BASE/month/'segments_train_scores.pkl')
    for module in list(sys.modules.values()):
        file = getattr(module, '__file__', None)
        if file:
            p = Path(file).resolve()
            try:
                relative = p.relative_to(Path.cwd())
            except ValueError:
                continue
            if str(relative).startswith('scripts/') and relative.suffix == '.py':
                paths.add(relative)
    paths.update(Path('scripts')/name for name in [
        'experiment_b2_validation_r31.py', 'report_b2_validation_r31.py',
        'build_b2_validation_r31.mjs', 'archive_b2_validation_r31.py', 'automl_b2.py'])
    entries = [dict(path=str(p), bytes=p.stat().st_size, sha256=sha(p)) for p in sorted(paths)]
    manifest = dict(experiment='R31', git_base=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                    original_b2_unchanged=True, formal_registration=False, cloud_verified=False,
                    files=entries, note='Snapshot supports saved-panel model training, frozen-rule and account replay. Minute raw upstream archives remain external; hashes and audit excerpts are saved.')
    dump(OUT/'asset_manifest.json', manifest)
    archive = OUT.parent/(OUT.name+'_assets.tar.gz')
    with tarfile.open(archive, 'w:gz', compresslevel=6) as tf:
        for p in sorted(paths):
            tf.add(p, arcname=str(p), recursive=False)
        tf.add(OUT/'asset_manifest.json', arcname=str(OUT/'asset_manifest.json'))
    expected = {r['path']:r for r in entries}
    with tarfile.open(archive, 'r:gz') as tf:
        for member in tf:
            if member.name not in expected:
                continue
            source=tf.extractfile(member);h=hashlib.sha256()
            for chunk in iter(lambda:source.read(1024*1024),b''):h.update(chunk)
            assert h.hexdigest()==expected[member.name]['sha256'],member.name
    receipt=dict(path=str(archive), bytes=archive.stat().st_size, sha256=sha(archive),
                 verified_members=len(entries), local_readback=True, cloud_uploaded=False)
    dump(OUT/'archive_receipt.json',receipt)
    dump(DOC/'archive_receipt.json',receipt)
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':main()

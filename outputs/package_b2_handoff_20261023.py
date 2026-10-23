from pathlib import Path
import json,hashlib,tarfile,subprocess,re
R=Path('/Volumes/ext/fin_agent'); O=R/'outputs/b2_handoff_20261023';O.mkdir(exist_ok=True)
roots=list((R/'docs/stock_automl_runs').glob('2026102*_b2*'))+list((R/'docs/stock_automl_runs').glob('B2_*.md'))
roots+=list((R/'scripts').glob('*b2*'))+[R/'scripts/backfill_tushare_minutes.py',R/'scripts/import_tushare_minutes_2024.py',R/'scripts/readable_strategy_execution.py']
roots+=list((R/'tests/automl').glob('*b2*'))+[R/'tests/automl/test_import_tushare_minutes_2024.py']
roots += [R/'outputs/b2_r94_20261023',R/'outputs/b2_r96_20261023']
files=set()
for p in roots:
    for f in ([p] if p.is_file() else p.rglob('*')):
        if f.is_file() and not f.is_symlink() and not any(x.startswith('._') or x in ('node_modules','__pycache__') for x in f.parts): files.add(f)
files=sorted(files)
blocked={'raw','minutes','documents','extra_documents','cninfo_windows','flow_raw','public_search','sina_windows','public_pages','minute_db','company_reports'}
allowed={'.md','.csv','.json','.py','.mjs','.xlsx','.html','.sh','.gz','.log'}
gitfiles=[p for p in files if p.suffix in allowed and not blocked.intersection(p.parts) and p.stat().st_size<5_000_000 and not p.name.startswith('preview')]
# Report names only if a literal credential-like assignment is found; never print values.
pat=re.compile(r'''(?i)(?:password|api_key|access_token|secret_key|tushare_token)\s*[=:]\s*['"]([A-Za-z0-9_+/=.-]{20,})['"]''')
sus=[]
for p in gitfiles:
    if p.suffix in {'.py','.json','.md','.sh','.mjs'} and pat.search(p.read_text(errors='ignore')):sus.append(str(p.relative_to(R)))
if sus: raise RuntimeError('Review credential-like literals in '+repr(sus))
manifest={str(p.relative_to(R)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files}
(O/'handoff_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
archive=O/'B2_session_20261023.tar.gz'
with tarfile.open(archive,'w:gz',compresslevel=1) as t:
    for p in files:t.add(p,arcname=str(p.relative_to(R)),recursive=False)
    t.add(O/'handoff_manifest.json',arcname='handoff_manifest.json')
with tarfile.open(archive,'r:gz') as t:
    for name,v in manifest.items():
        assert hashlib.sha256(t.extractfile(name).read()).hexdigest()==v['sha256'],name
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
(O/'SHA256SUMS').write_text(digest+'  '+archive.name+'\n')
(O/'git_paths.txt').write_text('\n'.join(str(p.relative_to(R)) for p in gitfiles)+'\n')
receipt={'archive':archive.name,'sha256':digest,'bytes':archive.stat().st_size,'files':len(files),'git_files':len(gitfiles),'git_bytes':sum(p.stat().st_size for p in gitfiles),'server_path':'/data/cyris/work/b2_handoff_20261023/B2_session_20261023.tar.gz','local_archive_readback':'all member hashes verified','server_readback':'pending','database':'not submitted','aliyun_drive':'pending'}
(R/'docs/stock_automl_runs/B2_ARCHIVE_RECEIPT_20261023.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
print(json.dumps(receipt,ensure_ascii=False))

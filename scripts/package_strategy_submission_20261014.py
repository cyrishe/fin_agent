"""Freeze the three requested research directions into hashed upload packages."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/stock_automl/submission_20261014_three_strategies'
GIT='/Users/imac/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/git'
SKIP={'node_modules','__pycache__','.DS_Store','.git'}


def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()


def files_under(paths):
    result=set()
    for p in paths:
        for f in ([p] if p.is_file() else p.rglob('*')):
            if f.is_file() and not f.is_symlink() and not SKIP.intersection(f.parts) and not f.name.startswith('._'):
                if '.inspect.ndjson' not in f.name and not f.name.startswith('preview_'):
                    result.add(f)
    return sorted(result)


def build():
    OUT.mkdir(exist_ok=True)
    base=ROOT/'outputs/stock_automl'
    docs=ROOT/'docs/stock_automl_runs'
    commit=subprocess.check_output([GIT,'rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    d0=[p for p in base.glob('limit_rebound*') if p.is_dir() and '1430' not in p.name and 'may_july' not in p.name]
    late=[p for p in base.glob('limit_rebound_1430*') if p.is_dir()]
    other=[p for p in base.iterdir() if p.is_dir() and p.name not in SKIP and
           not any(x in p.name for x in ['limit_rebound','deep_v','aliyun','submission','mk_1m_compact','branch_switch_backup','tdx_august'])]
    doc_d0=[p for p in docs.glob('*limit_rebound*') if '1430' not in p.name and 'may_july' not in p.name]
    doc_late=[p for p in docs.glob('*limit_rebound_1430*')]
    doc_other=[p for p in docs.iterdir() if p.is_dir() and 'limit_rebound' not in p.name and 'deep_v' not in p.name]
    shared=[ROOT/'AUTOML.MD',docs/'ACTIVE.md',docs/'EXPERIMENT_REGISTER.md',
            docs/'HANDOFF_20261014_THREE_STRATEGIES.md',docs/'TRAINING_ASSET_ARCHIVE.md']
    py=list((ROOT/'scripts').glob('*automl*.py'))+list((ROOT/'scripts').glob('*limit_rebound*.py'))
    py=[p for p in py if not any(s in p.name for s in ['deep_v','may_july'])]
    py += [Path(__file__),ROOT/'scripts/c1430_cv_utils.py']
    tests=[p for p in (ROOT/'tests/automl').glob('*.py') if 'deep_v' not in p.name]
    specs=[
      ('D0涨停_D1开盘_R1至R14_20261014',d0+doc_d0+shared+py+tests,'策略训练/涨停冷静期_20261014'),
      ('D1盘末判定_C1430_ABCD_20261014',late+doc_late+shared+py+tests,'策略训练/涨停冷静期_20261014'),
      ('收盘开盘_代码结论与可用模型_20261014',other+doc_other+shared+py+tests,'策略训练/收盘开盘_20261014'),
      ('共享分钟_2025未复权全年_20261014',[base/'mk_1m_compact_2025',docs/'20261014_mk_2025_full',ROOT/'scripts/audit_mk_2025_compact.py'],'策略训练/涨停冷静期_20261014'),
      ('共享分钟_2026本地未复权_20261014',[base/'mk_1m_compact',base/'tdx_august_20261014',docs/'20261014_limit_rebound_1430_aug_extension'],'策略训练/涨停冷静期_20261014'),
    ]
    inventory=[]
    for name,paths,cloud in specs:
        target=OUT/(name+'.zip')
        entries=[]
        with zipfile.ZipFile(target,'w',allowZip64=True) as z:
            for p in files_under(paths):
                rel=str(p.relative_to(ROOT));before=p.stat()
                sha=digest(p)
                z.write(p,rel,compress_type=zipfile.ZIP_STORED if p.suffix in {'.zst','.gz','.joblib','.xlsx','.zip','.png'} else zipfile.ZIP_DEFLATED,compresslevel=2)
                after=p.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                    raise RuntimeError(f'Source changed while packaging: {rel}')
                entries.append({'path':rel,'bytes':after.st_size,'sha256':sha})
            manifest={'name':name,'source_commit':commit,'cloud_directory':cloud,'files':entries}
            z.writestr('PACKAGE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
            z.writestr('SHA256SUMS',''.join(f"{e['sha256']}  {e['path']}\n" for e in entries))
        with zipfile.ZipFile(target) as z:
            for e in entries:
                h=hashlib.sha256()
                with z.open(e['path']) as f:
                    for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
                assert h.hexdigest()==e['sha256'],e['path']
        entry={'name':name,'path':str(target),'cloud_directory':cloud,'files':len(entries),
               'bytes':target.stat().st_size,'sha256':digest(target),'source_commit':commit,
               'local_verified':True,'cloud_uploaded':False,'cloud_readback_verified':False}
        inventory.append(entry)
        (OUT/'upload_inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in entry.items() if k!='path'},ensure_ascii=False),flush=True)


if __name__=='__main__':build()

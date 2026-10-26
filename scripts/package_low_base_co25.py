"""Freeze CO25 actual daily inputs, samples, folds and evidence locally."""
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from scripts.research_low_base_co25 import ROOT,OUT,REPORT
from scripts.archive_automl_training import LOCAL_ROOT,CLOUD_ROOT,sha256_file,verify_package,record_local_package


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source-commit',required=True);args=ap.parse_args()
    assert subprocess.check_output(['git','rev-parse',args.source_commit],cwd=ROOT,text=True).strip()==args.source_commit
    category='03_因子筛选_7至14';folder='20261027_CO25_低位蓄势_二三日涨幅'
    dest=LOCAL_ROOT/category/folder
    assert not dest.exists(),'Immutable package already exists'
    src=Path(tempfile.mkdtemp(prefix='fin_agent_co25_'))
    paths=[p for p in OUT.rglob('*') if p.is_file() and not any(x.startswith('._') for x in p.parts)]
    paths += [p for p in REPORT.iterdir() if p.is_file() and not p.name.startswith('._') and p.name!='archive_receipt.json']
    paths += [ROOT/'scripts'/f'{name}_low_base_co25.py' for name in ['research','analyze','verify','report','package']]
    paths += [ROOT/'tests/experiments/test_low_base_co25.py']
    for p in paths:
        to=src/p.relative_to(ROOT);to.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,to)
    (src/'README.md').write_text('''# CO25 低位蓄势二三日研究完整包

包含实际原始日线快照daily_inputs.parquet、全基础池特征/标签panel.parquet、完整预测、42模型及各折实际成员和日权重、全部结果与独立检查。研究信号在D收盘后生成；进入参考价D+1开盘，退出D+2/D+3收盘，存在停牌/一字未知执行，无重叠仓位账户成绩。详细规则、年度/月度及限制见报告。

恢复对应Git提交，将包内outputs/docs/scripts/tests恢复到检出目录。环境sklearn1.7.1、pandas2.3.2、numpy2.3.2。python -m scripts.research_low_base_co25 train重新拟合；python -m scripts.analyze_low_base_co25生成结果；python -m scripts.verify_low_base_co25独立验证。勿覆盖本不可变包，使用工作副本。build默认从原仓库月快照取数；包内daily_inputs.parquet保留同一原始OHLC量额，可调用calculate重算所有规则/特征，不依赖云端查询。源文件哈希与路径见build_manifest.json。

云端按用户要求待同步，数据库记录只证明本机包回读核验。
''')
    (src/'package_manifest.json').write_text(json.dumps({'experiment_id':'CO25_20261027_low_base','source_commit':args.source_commit,'models':42,'signal':'D close','entry':'D+1 open','exit':'D+2/D+3 close'},ensure_ascii=False,indent=2)+'\n')
    files=sorted(p for p in src.rglob('*') if p.is_file())
    (src/'SHA256SUMS').write_text(''.join(f'{sha256_file(p)}  {p.relative_to(src)}\n' for p in files))
    identity=verify_package(src);dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.co25-stage-',dir=dest.parent) as temp:
        stage=Path(temp)/folder;shutil.copytree(src,stage)
        for p in stage.rglob('._*'):
            if p.is_file():p.unlink()
        assert verify_package(stage)==identity
        stage.rename(dest)
    assert verify_package(dest)==identity
    record={'user_id':'imac','experiment_id':'CO25_20261027_low_base','experiment_date':'2026-10-27',
            'title':'CO25 低位蓄势规则与二三日简单预测','category_path':category,'local_path':str(dest),
            'cloud_path':'/'.join([CLOUD_ROOT,category,folder]),'source_commit':args.source_commit,
            'manifest_sha256':identity[0],'file_count':identity[1]}
    record_local_package(ROOT/'.env',record)
    receipt={**record,'local_check':'ok','db_recorded':True,'cloud_verified_at':None,'cloud_status':'待同步；用户要求先保留本地包'}
    (REPORT/'archive_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(receipt,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()

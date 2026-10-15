"""Build the immutable local CO5 research package; no cloud upload."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import subprocess
import zipfile
import numpy as np
import pandas as pd
from scripts.experiment_automl_close_open_simple_rolling import OUT, REPORT, SOURCE, mature, retained_rows, sha
from scripts.archive_automl_training import verify_package

PACKAGE=OUT/'package'
ZIP=OUT/'CO5_简单模型滚动_76因子_20261014.zip'

def membership(panel,dates,cutoff,path):
    train=mature(panel,dates,cutoff)
    result=train[['signal_date','symbol6','next_date']].copy()
    result.insert(0,'sample_id',train.index)
    result['retained_after_drop2pct']=retained_rows(train)
    counts=train.groupby('signal_date').signal_date.transform('count')
    w=1/counts;result['simple_weight']=w*(len(w)/w.sum())
    reduced=train.loc[result.retained_after_drop2pct]
    rw=1/reduced.groupby('signal_date').signal_date.transform('count')
    result['simple_weight_drop2pct']=rw*(len(rw)/rw.sum())
    path.parent.mkdir(parents=True,exist_ok=True)
    result.to_csv(path,index=False,float_format='%.17g')
    return set(train.index)


def run():
    if PACKAGE.exists(): raise ValueError('Immutable package already exists; do not overwrite')
    PACKAGE.mkdir(parents=True)
    panel=pd.read_csv(SOURCE/'factor_panel.csv.gz',dtype={'symbol6':str},float_precision='round_trip').sort_values(['signal_date','symbol6']).reset_index(drop=True)
    calendar=pd.read_csv(SOURCE/'availability.csv').date.tolist()
    union=set();bindings=[]
    for f in sorted((OUT/'folds').glob('*/complete.json')):
        meta=json.loads(f.read_text());month=meta['month']
        for name,dates,cutoff in [('discovery',meta['discovery_dates'],meta['validation_dates'][0]),
                                   ('validation',meta['validation_dates'],meta['first_test']),
                                   ('refit',meta['discovery_dates']+meta['validation_dates'],meta['first_test'])]:
            path=PACKAGE/'membership'/f'{month}_{name}.csv.gz'
            ids=membership(panel,dates,cutoff,path)
            if name!='validation': union.update(ids)
        for day in meta['test_days']:
            pos=calendar.index(day)
            union.update(membership(panel,calendar[pos-20:pos],day,PACKAGE/'membership'/f'{day}_tree20.csv.gz'))
        for model in meta['models']:
            member=(f"{model['decision_day']}_tree20.csv.gz" if 'decision_day' in model else f'{month}_refit.csv.gz')
            bindings.append({**model,'package_model':f"run/models/{Path(model['file']).name}",
                             'membership':f'membership/{member}',
                             'drop_filter':model['model'].endswith('_drop2pct'),
                             'weight_column':None if 'decision_day' in model else (
                                 'simple_weight_drop2pct' if model['model'].endswith('_drop2pct') else 'simple_weight')})
    training=panel.loc[sorted(union)].copy();training.insert(0,'sample_id',training.index)
    training.to_csv(PACKAGE/'training.csv.gz',index=False,float_format='%.17g')
    sample_dest=PACKAGE/SOURCE/'factor_panel.csv.gz';sample_dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(SOURCE/'factor_panel.csv.gz',sample_dest)
    shutil.copy2(SOURCE/'availability.csv',sample_dest.parent/'availability.csv')
    for name in ('models','folds','amounts'):
        shutil.copytree(OUT/name,PACKAGE/'run'/name)
    for name in ('picks_before_amount_gate.csv','daily_picks_with_gate.csv','daily_execution.csv.gz',
                 'run_manifest.json','training_daily_associations.csv.gz','training.log','aggregate.log','amounts.log'):
        shutil.copy2(OUT/name,PACKAGE/'run'/name)
    shutil.copytree(REPORT,PACKAGE/'metrics')
    code=PACKAGE/'code'/'scripts';code.mkdir(parents=True)
    # Preserve imported research helpers. Reproduction uses the recorded repo commit.
    for p in sorted(Path('scripts').glob('*automl*.py')):
        shutil.copy2(p,code/p.name)
    shutil.copy2(Path('scripts/mk_minute_archive.py'),code/'mk_minute_archive.py')
    for p in Path('tests/automl').glob('test_close_open*.py'):
        target=PACKAGE/'code'/p;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    legacy=PACKAGE/'original_B1';legacy.mkdir()
    for rel in ('20261011_mk_1m_tradable_st_rolling20/top1_verified.csv',
                '20261011_mk_1m_tradable_st_rolling20/folds.json','20261011_mk_1m_tradable_st_rolling20/summary.json',
                '20261011_market_state_asof/daily_2026.csv'):
        p=Path('docs/stock_automl_runs')/rel
        shutil.copy2(p,legacy/(p.parent.name+'_'+p.name))
    deps={n:version(n) for n in ('numpy','pandas','scikit-learn','scipy','joblib','threadpoolctl','matplotlib','zstandard','pytest')}
    meta={'experiment_id':'CO5_20261014_simple_rolling76','source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
          'python':subprocess.check_output(['.venv-automl/bin/python','--version'],text=True).strip(),
          'dependencies':deps,'training_rows':len(training),'panel_rows':len(panel),'saved_models':len(bindings),
          'panel_sha256':sha(sample_dest),'training_sha256':sha(PACKAGE/'training.csv.gz'),
          'models':bindings,'cloud_status':'user_deferred_upload'}
    (PACKAGE/'model_manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
    (PACKAGE/'requirements-versions.txt').write_text('\n'.join(f'{k}=={v}' for k,v in deps.items())+'\n')
    (PACKAGE/'README.md').write_text('''# CO5 简单模型滚动研究包

本包仅研究、未替换B1。结论、逐月指标与方法见 `metrics/README.md`，所有候选分数在 `run/folds/*/predictions.csv.gz`，逐日决策在 `run/daily_execution.csv.gz`。用户暂缓阿里云盘上传。

`training.csv.gz` 为实际至少进入一次拟合的逐行样本，`sample_id` 对应按信号日、股票代码排序后的冻结面板行号。`membership` 保存每个发现/验证/重拟合窗口及每天树模型的精确样本名单；`model_manifest.json` 将每个保存模型绑定到样本名单、扰动过滤、权重、因子顺序与哈希。验证行可在完整面板按 sample_id 查询；验证样本不自动进入该轮发现期。约2%扰动过滤列为False的行不进入扰动模型。树模型等行权重，简单模型使用指定权重列。

完整CO4面板按原仓库路径保存，SHA与原始研究包一致；14:40成交额在 `run/amounts`，其来源、哈希和限制见 `metrics/input_bindings.json` 与CO4原研究包。未复制巨量原始分钟行情，已有因子面板和成交额足以离线复跑本轮训练与评价。

复现请检出 `model_manifest.json` 的仓库commit，使用记录的Python/依赖版本，恢复完整面板及 availability.csv 到保存的相对位置，恢复 `run` 内容至 `outputs/stock_automl/close_open_simple_rolling_r1`。现有 complete.json 会复用已完成本轮结果；如需真正重训，将该运行目录另存后在新空目录恢复 amounts，再运行训练命令。不要覆盖本冻结包。模型为本地可信来源的 joblib 文件，只加载本包核验通过的文件。

`code` 保存本轮及其研究依赖脚本作追溯；完整运行入口仍以仓库commit为准。每个文件（除清单自身）均记录于 `SHA256SUMS`；打包后会逐文件从ZIP回读验证。
''')
    files=sorted(p for p in PACKAGE.rglob('*') if p.is_file())
    (PACKAGE/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(PACKAGE)}\n' for p in files))
    digest,count=verify_package(PACKAGE)
    with zipfile.ZipFile(ZIP,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        for p in sorted(PACKAGE.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(PACKAGE))
    with zipfile.ZipFile(ZIP) as z:
        expected={name:hashval for hashval,name in
                  (line.split('  ',1) for line in (PACKAGE/'SHA256SUMS').read_text().splitlines())}
        assert set(z.namelist())==set(expected)|{'SHA256SUMS'}
        for name,hashval in expected.items():
            with z.open(name) as stream:
                actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!=hashval: raise ValueError(f'ZIP readback failed: {name}')
    receipt={'source_package':str(PACKAGE.resolve()),'zip_path':str(ZIP.resolve()),'zip_bytes':ZIP.stat().st_size,
             'zip_sha256':sha(ZIP),'manifest_sha256':digest,'files':count,'source_commit':meta['source_commit'],
             'training_rows':len(training),'models':len(bindings),'zip_readback':'all_files_match',
             'cloud_verified_at':None,'upload_note':'用户要求先保留本地包，稍后上传'}
    (OUT/'package_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(receipt,ensure_ascii=False,indent=2))

if __name__=='__main__':run()

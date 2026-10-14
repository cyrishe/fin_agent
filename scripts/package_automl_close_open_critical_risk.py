"""Freeze CO6 models, exact training rows, predictions and offline inputs locally."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import subprocess
import zipfile
import pandas as pd
from scripts.experiment_automl_close_open_critical_risk import OUT, REPORT, SOURCE, CO5, sha, net_values, net_classes
from scripts.archive_automl_training import verify_package

PACKAGE=OUT/'package'
ZIP=OUT/'CO6_严重净亏与跨档惩罚_20261014.zip'


def copy_to(relative, target=None):
    source=Path(relative)
    destination=PACKAGE/(source if target is None else target)
    destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,destination)


def run():
    if PACKAGE.exists():raise ValueError('Frozen package exists; do not overwrite')
    audit=json.loads((REPORT/'audit.json').read_text())
    PACKAGE.mkdir(parents=True)
    panel=pd.read_csv(SOURCE/'factor_panel.csv.gz',dtype={'symbol6':str},float_precision='round_trip').sort_values(['signal_date','symbol6']).reset_index(drop=True)
    union=set();bindings=[]
    for f in sorted((OUT/'folds').glob('*/complete.json')):
        meta=json.loads(f.read_text())
        for phase in ('discovery','refit'):
            member=pd.read_csv(f.parent/f'{phase}_membership.csv.gz')
            union.update(member.sample_id)
        for model in meta['models']:
            kind=model['kind']
            bindings.append({**model,
                'package_model':str(Path('run')/Path(model['file']).relative_to(OUT)),
                'membership':f"run/folds/{meta['month']}/{model['phase']}_membership.csv.gz",
                'weight_column':'critical_weight' if 'critical' in kind else 'day_weight',
                'target':'binary_net_lt_minus_2pct' if kind=='risk_binary' else ('net_five_class' if kind=='nominal' else 'cumulative_four_boundaries'),
                'soft_targets':kind.endswith('fuzzy')})
    training=panel.loc[sorted(union)].copy()
    training.insert(0,'sample_id',training.index)
    training['actual_net_return']=net_values(training)
    training['net_class']=net_classes(training.actual_net_return)
    training.to_csv(PACKAGE/'training.csv.gz',index=False,float_format='%.17g')
    assert len(training)==audit['unique_fitting_rows'] and len(bindings)==audit['saved_model_bundles_checked']
    shutil.copytree(OUT/'folds',PACKAGE/'run'/'folds')
    for name in ('risk_scores.csv.gz','new_picks.csv','daily_execution.csv.gz','run_manifest.json','training.log'):
        copy_to(OUT/name,Path('run')/name)
    for name in ('factor_panel.csv.gz','availability.csv'):
        copy_to(SOURCE/name)
    # These frozen base picks and all-market amounts allow report reproduction
    # without refitting CO5 or querying a mutable database.
    copy_to(CO5/'daily_picks_with_gate.csv')
    copy_to(CO5/'run_manifest.json')
    shutil.copytree(CO5/'amounts',PACKAGE/CO5/'amounts')
    for relative in (
        'docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top1_verified.csv',
        'docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/folds.json',
        'docs/stock_automl_runs/20261011_market_state_asof/daily_2026.csv',
        'docs/stock_automl_runs/20261014_close_open_simple_rolling/input_bindings.json',
    ):copy_to(relative)
    shutil.copytree(REPORT,PACKAGE/'metrics')
    for p in sorted(Path('scripts').glob('*automl*.py')):copy_to(p,Path('code')/p)
    copy_to(Path('scripts/mk_minute_archive.py'),Path('code/scripts/mk_minute_archive.py'))
    for p in Path('tests/automl').glob('test_close_open*.py'):copy_to(p,Path('code')/p)
    deps={n:version(n) for n in ('numpy','pandas','scikit-learn','scipy','joblib','threadpoolctl','pytest')}
    meta={'experiment_id':'CO6_20261014_critical_net_loss',
          'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
          'training_source_commit':json.loads((OUT/'run_manifest.json').read_text())['source_commit'],
          'python':platform.python_version(),'dependencies':deps,
          'training_rows':len(training),'panel_rows':len(panel),'saved_model_bundles':len(bindings),
          'estimators':audit['estimators_checked'],'training_sha256':sha(PACKAGE/'training.csv.gz'),
          'panel_sha256':sha(SOURCE/'factor_panel.csv.gz'),
          'base_picks_sha256':sha(CO5/'daily_picks_with_gate.csv'),
          'models':bindings,'cloud_status':'user_deferred_upload'}
    (PACKAGE/'model_manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
    (PACKAGE/'requirements-versions.txt').write_text('\n'.join(f'{k}=={v}' for k,v in deps.items())+'\n')
    (PACKAGE/'README.md').write_text('''# CO6 严重净亏与跨档惩罚研究包

结论见 `metrics/README.md`，预先固定方案见 `metrics/DESIGN.md`。保护手段已真实训练与比较，未找到可替换B1的稳定方案，原B1保持不变。用户要求先保留本地，暂缓云盘上传。

`training.csv.gz` 保存实际至少进入一次发现或重拟合的147,968行逐行样本（含76因子、原标签和本轮实际净收益/五类标签）。sample_id为按signal_date、symbol6排序后的冻结CO4面板行号。`run/folds/<month>/*_membership.csv.gz` 保存发现、验证与重拟合的精确名单、day_weight与critical_weight；`model_manifest.json` 将每个模型包绑定样本、权重、因子顺序、哈希。验证行可从完整面板按sample_id提取；不能据它们在其他月份进入拟合，就误认为它们进入当前发现模型。

保存160个joblib模型包，共448个估计器；包含发现80日与重拟合100日两个阶段。普通Logit和独立风险模型使用day_weight；严重加权的有序模型使用critical_weight。soft_targets为真时0/1/3%边界±0.2个百分点内线性软化，−2%严重边界保持硬标签；精确实现见实验脚本。全部测试候选分布和风险概率、验证风险概率、逐月混淆、Top1与轮空、未知结果都已保留。

已把CO4完整面板及日历、CO5基础选择及350日全股票成交额放在原仓库相对路径下，未改动旧研究包。原B1名单、日历及成交额账本也按原路径保留。因子和成交额来源绑定在 `docs/stock_automl_runs/20261014_close_open_simple_rolling/input_bindings.json`。本包足以离线复跑本轮训练与统计，不包含巨量原始分钟行情；不是对原分钟数据重新采集的替代证明。

复现时检出model_manifest中的source_commit、使用记录的依赖版本，在新的隔离目录恢复outputs/docs相对路径，将run恢复至outputs/stock_automl/close_open_critical_risk_r1，将metrics恢复至docs/stock_automl_runs/20261014_close_open_critical_risk。运行 `python -m scripts.report_automl_close_open_critical_risk` 与审计/中文报告入口可重算统计。要从头拟合，另存原run后移除新工作副本中的folds完成文件，再运行 `python -m scripts.experiment_automl_close_open_critical_risk`；不要改写本冻结包。现有complete.json代表已完成结果缓存，不应把重复汇报当作重新训练。

每个文件除SHA256SUMS自身都记录哈希，ZIP逐文件回读核验。code目录提供脚本追溯，运行以完整仓库commit为准。joblib只加载核验通过的本地可信模型。
''')
    files=sorted(p for p in PACKAGE.rglob('*') if p.is_file())
    (PACKAGE/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(PACKAGE)}\n' for p in files))
    digest,count=verify_package(PACKAGE)
    with zipfile.ZipFile(ZIP,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        for p in sorted(PACKAGE.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(PACKAGE))
    with zipfile.ZipFile(ZIP) as z:
        expected={name:hashval for hashval,name in (line.split('  ',1) for line in (PACKAGE/'SHA256SUMS').read_text().splitlines())}
        assert set(z.namelist())==set(expected)|{'SHA256SUMS'}
        for name,hashval in expected.items():
            with z.open(name) as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!=hashval:raise ValueError(f'ZIP readback mismatch: {name}')
    receipt={'source_package':str(PACKAGE.resolve()),'zip_path':str(ZIP.resolve()),
             'zip_bytes':ZIP.stat().st_size,'zip_sha256':sha(ZIP),'manifest_sha256':digest,'files':count,
             'source_commit':meta['source_commit'],'training_source_commit':meta['training_source_commit'],
             'training_rows':len(training),'model_bundles':len(bindings),'estimators':audit['estimators_checked'],
             'zip_readback':'all_files_match','cloud_verified_at':None,
             'upload_note':'用户要求先保留本地包，稍后上传'}
    (OUT/'package_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__=='__main__':run()

"""Freeze CO7's two stages, exact fit memberships and offline evidence locally."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import subprocess
import zipfile
import pandas as pd
from scripts.build_automl_close_open_conditional_b1 import OUT, REPORT, SOURCE, LEGACY25, LEGACY26, FEATURES, sha
from scripts.archive_automl_training import verify_package

PACKAGE = OUT / 'package'
ZIP = OUT / 'CO7_原B1折外TopK_二阶段风险_20261015.zip'


def copy_to(source, target=None):
    source = Path(source)
    destination = PACKAGE / (source if target is None else target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def run():
    if PACKAGE.exists() or ZIP.exists():
        raise ValueError('Frozen package exists; do not overwrite')
    audit = json.loads((REPORT / 'audit.json').read_text())
    manifest = json.loads((OUT / 'run_manifest.json').read_text())
    assert sha(Path('scripts/experiment_automl_close_open_conditional_b1.py')) == manifest['script_sha256']
    assert sha(REPORT / 'DESIGN.md') == manifest['design_sha256']
    assert sha(OUT / 'b1_candidates.csv.gz') == manifest['input_sha256']
    PACKAGE.mkdir(parents=True)
    panel = pd.read_csv(OUT / 'b1_candidates.csv.gz', dtype={'symbol6': str}, float_precision='round_trip').set_index('sample_id', drop=False)
    oof = pd.read_csv(OUT / 'oof_with_context.csv.gz', dtype={'symbol6': str}, float_precision='round_trip').set_index('sample_id', drop=False)
    assert panel.index.is_unique and oof.index.is_unique
    stage1_ids, stage2_ids, bindings = set(), set(), []
    for f in sorted((OUT / 'stage1').glob('*/complete.json')):
        meta = json.loads(f.read_text())
        member = pd.read_csv(f.parent / 'membership.csv.gz')
        stage1_ids.update(member.sample_id)
        assert sha(f.parent / 'model.joblib') == meta['model_sha256']
        bindings.append({
            'stage': 1, 'day': meta['day'], 'model': f'run/stage1/{meta["day"]}/model.joblib',
            'sha256': meta['model_sha256'], 'training_data': 'training.csv.gz',
            'membership': f'run/stage1/{meta["day"]}/membership.csv.gz',
            'join_key': 'sample_id', 'features': list(FEATURES), 'weight_column': None,
            'target': 'stage1_second_high / signal_price - 1; boundaries 0%, 1%, 3%',
            'training_rows': len(member), 'complete': f'run/stage1/{meta["day"]}/complete.json'})
    for f in sorted((OUT / 'stage2').glob('*/*/complete.json')):
        meta = json.loads(f.read_text())
        prefix = f'run/stage2/{meta["month"]}/{meta["arm"]}'
        for phase in ('discovery', 'refit'):
            member = pd.read_csv(f.parent / f'{phase}_membership.csv.gz')
            stage2_ids.update(member.sample_id)
            assert sha(f.parent / f'{phase}.joblib') == meta[f'{phase}_sha256']
            bindings.append({
                'stage': 2, 'month': meta['month'], 'arm': meta['arm'], 'phase': phase,
                'model': f'{prefix}/{phase}.joblib', 'sha256': meta[f'{phase}_sha256'],
                'training_data': 'stage2_training.csv.gz', 'membership': f'{prefix}/{phase}_membership.csv.gz',
                'join_key': 'sample_id', 'features': meta['factors'], 'weight_column': 'weight',
                'target': 'net_return < -0.02; fixed 50000 CNY price-return fees',
                'training_rows': len(member), 'complete': f'{prefix}/complete.json'})
    training = panel.loc[sorted(stage1_ids)].copy()
    conditional = oof.loc[sorted(stage2_ids)].copy()
    conditional['critical_net_loss'] = conditional.net_return.lt(-.02)
    assert len(training) == audit['stage1_unique_fitting_rows']
    assert len(conditional) == audit['stage2_unique_fitting_rows']
    assert len(bindings) == audit['stage1_folds'] + audit['stage2_model_bundles_checked']
    training.to_csv(PACKAGE / 'training.csv.gz', index=False, float_format='%.17g')
    conditional.to_csv(PACKAGE / 'stage2_training.csv.gz', index=False, float_format='%.17g')
    for folder in ('stage1', 'stage2', 'amounts', 'daily_adjusted', 'minima'):
        shutil.copytree(OUT / folder, PACKAGE / 'run' / folder)
    for name in ('b1_candidates.csv.gz', 'calendar.csv', 'input_manifest.json', 'oof_top5.csv.gz',
                 'oof_with_context.csv.gz', 'stage1_folds.json', 'stage2_folds.json',
                 'stage2_predictions.csv.gz', 'daily_execution.csv.gz', 'run_manifest.json'):
        copy_to(OUT / name, Path('run') / name)
    for p in sorted(OUT.glob('*.log')):
        copy_to(p, Path('run') / p.name)
    for p in (LEGACY25, LEGACY26, SOURCE / 'factor_panel.csv.gz', SOURCE / 'availability.csv', SOURCE / 'historical_st.csv.gz'):
        copy_to(p)
    for relative in (
        'outputs/stock_automl/close_open_simple_rolling_r1/daily_picks_with_gate.csv',
        'docs/stock_automl_runs/20261011_mk_2025_mar_aug_backtest/top2_verified.csv',
        'docs/stock_automl_runs/20261011_mk_2025_sep_oct_backtest/top2_verified.csv',
        'docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top2_verified.csv',
        'docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top1_verified.csv',
        'docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/folds.json',
        'docs/stock_automl_runs/20261014_close_open_simple_rolling/input_bindings.json',
    ):
        copy_to(relative)
    shutil.copytree(REPORT, PACKAGE / 'metrics')
    for p in sorted(Path('scripts').glob('*automl*.py')):
        copy_to(p, Path('code') / p)
    copy_to('scripts/mk_minute_archive.py', 'code/scripts/mk_minute_archive.py')
    for p in sorted(Path('tests/automl').glob('test_close_open*.py')):
        copy_to(p, Path('code') / p)
    deps = {n: version(n) for n in ('numpy', 'pandas', 'scikit-learn', 'scipy', 'joblib', 'threadpoolctl', 'pytest', 'matplotlib')}
    meta = {
        'experiment_id': 'CO7_20261015_B1_conditional_risk',
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'training_source_commit': manifest['source_commit'], 'python': platform.python_version(),
        'dependencies': deps, 'training_rows': len(training), 'stage2_training_rows': len(conditional),
        'panel_rows': len(panel), 'oof_rows': len(oof), 'model_bundles': len(bindings),
        'training_sha256': sha(PACKAGE / 'training.csv.gz'),
        'stage2_training_sha256': sha(PACKAGE / 'stage2_training.csv.gz'),
        'models': bindings, 'cloud_status': 'user_deferred_upload'}
    (PACKAGE / 'model_manifest.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n')
    (PACKAGE / 'requirements-versions.txt').write_text('\n'.join(f'{k}=={v}' for k, v in deps.items()) + '\n')
    (PACKAGE / 'README.md').write_text('''# CO7 原B1折外TopK第二阶段风险研究包

结论见 `metrics/README.md`，训练前固定方案见 `metrics/DESIGN.md`。月收益与回撤为主，每月删最好一笔仅辅助。温和风险过滤仍未达到跨年多数月份盈利，不替换原B1。用户要求先保留本地，暂缓云盘上传。

`training.csv.gz` 保存155,027条至少进入一次基础拟合的逐行样本。`stage2_training.csv.gz` 保存1,752条至少进入一次风险拟合的历史真实折外TopK样本；每条基础预测只由此前20个市场日产生。两者sample_id都指向 `run/b1_candidates.csv.gz` 的原行号，须按名单连接，不能按文件行号取样。完整验证与测试样本保留在run下，完整基础面板155,444行，折外Top5共1,868行。训练文件里附带的未来结果列仅供标签/审计，实际输入以模型清单features为准。

`model_manifest.json` 将408个基础模型和120个风险模型包逐一绑定训练数据、因子顺序、样本名单、权重列和内容哈希。基础模型等权；风险模型按信号日总权重相等。每月风险发现60日、验证40日、100日重拟合；验证阈值不会改写发现模型，下一月测试不用于选阈值。日期、标签成熟时间、拟合/验证/测试名单及全部概率都在对应complete、membership、validation和test文件。

run同时保存24个月历史复权日线快照、扩展日期分钟低点、428日全股票成交额、基础全候选评分、逐日交易和资金曲线。原2025/2026候选快照、CO4完整因子面板/日历/ST、官方Top2和原B1成交额账本按仓库相对路径保留；这些足够离线重训本轮与复核，不包含巨量原分钟行情。原2025/2026冻结名单/概率已匹配，扩展日期不是原来归档的预测。

复现时检出model_manifest的source_commit并安装记录依赖，在新隔离目录恢复包内outputs/docs相对路径；把run恢复到outputs/stock_automl/close_open_conditional_b1_r1，把metrics恢复到docs/stock_automl_runs/20261015_close_open_conditional_b1。执行report、audit、summarize_automl_close_open_conditional_b1模块可重算统计和核验。从头重训时先备份工作副本的stage1/stage2，移走完成标记和模型目录后依次运行experiment的stage1与stage2；已保存amounts可直接使用。不得改写这个冻结包。完整仓库commit为运行依据，code提供方便追溯的脚本快照。

单笔标签含费用；净值按初始10万元、全额可用现金投入、下一日09:40退出计算，回撤只含已结算时点。未知入场信息不下单、原未知字段保留；本轮实际222笔退出均已知。8条历史TopK带除权标记，4条价格大亏，尚未还原分红送转权益，限制见报告。训练前预设不等于全项目从未查看过该历史时期。

SHA256SUMS记录除自身外每个文件；ZIP逐文件读取并比较哈希。模型只加载核验通过的本地可信文件。最终本地镜像/数据库登记/ZIP回执位于源仓库报告目录archive_receipt.json，回执形成在包冻结之后，不反向修改包。
''')
    files = sorted(p for p in PACKAGE.rglob('*') if p.is_file())
    (PACKAGE / 'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(PACKAGE)}\n' for p in files))
    digest, count = verify_package(PACKAGE)
    with zipfile.ZipFile(ZIP, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for p in sorted(PACKAGE.rglob('*')):
            if p.is_file():
                z.write(p, p.relative_to(PACKAGE))
    with zipfile.ZipFile(ZIP) as z:
        expected = {name: hashval for hashval, name in (line.split('  ', 1) for line in (PACKAGE / 'SHA256SUMS').read_text().splitlines())}
        assert len(z.namelist()) == len(set(z.namelist()))
        assert set(z.namelist()) == set(expected) | {'SHA256SUMS'}
        for name, hashval in expected.items():
            with z.open(name) as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != hashval:
                raise ValueError(f'ZIP readback mismatch: {name}')
        assert z.read('SHA256SUMS') == (PACKAGE / 'SHA256SUMS').read_bytes()
    receipt = {
        'source_package': str(PACKAGE.resolve()), 'zip_path': str(ZIP.resolve()),
        'zip_bytes': ZIP.stat().st_size, 'zip_sha256': sha(ZIP), 'manifest_sha256': digest, 'files': count,
        'source_commit': meta['source_commit'], 'training_source_commit': meta['training_source_commit'],
        'training_rows': len(training), 'stage2_training_rows': len(conditional), 'model_bundles': len(bindings),
        'zip_readback': 'all_files_match', 'cloud_verified_at': None,
        'upload_note': '用户要求先保留本地包，稍后上传'}
    (OUT / 'package_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    run()

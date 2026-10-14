"""Create an immutable local handoff of the restored forest and its inputs."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'outputs/stock_automl/aliyun_drive_local/策略训练/D0涨停_D1开盘_R11_20261014'
R8 = ROOT / 'outputs/stock_automl/limit_rebound_version_robust_r8_20261014'
R11 = ROOT / 'outputs/stock_automl/limit_rebound_forest_restored_r11_20261014'
HIST = ROOT / 'outputs/stock_automl/limit_rebound_2024_may_sep_r11_20261014'


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def run() -> None:
    gate = json.loads((R11 / 'reproduction_gate.json').read_text())
    if not gate['gate_passed']:
        raise ValueError('Reproduction gate failed')
    sources = {
        'models': sorted(R11.glob('*.joblib')) + [R11 / 'reproduction_gate.json'],
        'training_snapshots': [R8 / name for name in ('train.pkl', 'valid.pkl',
            'valid_all.pkl', 'refit.pkl', 'test.pkl', 'test_all.pkl',
            'valid_scores_forest_sk171.csv.gz', 'test_scores_forest_sk171.csv.gz')],
        '2024_replay': [HIST / 'summary.json', HIST / 'internal_audit.csv.gz',
                        *sorted((HIST / 'price_months').glob('*.csv.gz'))],
        'documentation': [ROOT / 'docs/stock_automl_runs/20261014_limit_rebound_restored_r11/README.md',
                          ROOT / 'scripts/reproduce_limit_rebound_forest_r11.py',
                          ROOT / 'scripts/replay_limit_rebound_forest_2024_r11.py',
                          *[ROOT / 'scripts' / name for name in (
                              'experiment_limit_rebound_version_robust_r8.py',
                              'analyze_limit_rebound_effect_r9.py',
                              'experiment_automl_limit_rebound_d0_open_r5.py',
                              'build_automl_limit_rebound_features.py',
                              'experiment_automl_limit_rebound_st_size_r4.py',
                              'export_limit_rebound_d0_top5_r9_detail.py',
                              'benchmark_automl_1440_inference.py')]],
    }
    DEST.mkdir(parents=True, exist_ok=True)
    lines = []
    for category, paths in sources.items():
        for src in paths:
            if not src.is_file():
                raise FileNotFoundError(src)
            target = DEST / category / src.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and sha256(target) != sha256(src):
                raise ValueError(f'Existing archive differs: {target}')
            if not target.exists():
                shutil.copy2(src, target)
            if sha256(target) != sha256(src):
                raise ValueError(f'Copy failed hash check: {target}')
            lines.append(f'{sha256(target)}  {target.relative_to(DEST)}')
    (DEST / 'SHA256SUMS').write_text('\n'.join(sorted(lines)) + '\n')
    manifest = {'local_archive': str(DEST), 'files': len(lines),
                'manifest_sha256': sha256(DEST / 'SHA256SUMS'),
                'cloud_sync_verified': False, 'database_registered': False}
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    run()

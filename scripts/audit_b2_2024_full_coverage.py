"""Audit the complete 2024 minute archive and the first causal B2 test month."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from scripts.experiment_limit_rebound_two_stage_r15 import window


SOURCE = Path('/Volumes/ext/tushare_market_data/2024')
ARCHIVE = Path('outputs/stock_automl/mk_1m_compact_2024')
CALENDAR = Path('outputs/stock_automl/limit_rebound_segments_r18_20261015/calendar.json')
R18 = Path('outputs/stock_automl/limit_rebound_segments_r18_20261015')
EARLY = Path('outputs/stock_automl/limit_rebound_2024_minute_extension_20261017/early_models')
OLD_BUNDLE = Path('docs/stock_automl_runs/20261017_limit_rebound_2024_minutes/results/repro_bundle.zip')
OUT = Path('docs/stock_automl_runs/20261018_b2_full_replay/2024_full_coverage_receipt.json')


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main() -> None:
    manifest = json.loads((ARCHIVE / 'manifest.json').read_text())
    rows = manifest['rows']
    days = [row['day'] for row in rows]
    calendar = json.loads(CALENDAR.read_text())
    expected = [day for day in calendar if day.startswith('2024-')]
    if len(rows) != manifest['days'] or len(set(days)) != len(days) or sorted(days) != expected:
        raise AssertionError('2024 archive and market calendar do not match exactly')
    source_days = sorted(f'{p.name[:4]}-{p.name[4:6]}-{p.name[6:8]}'
                         for p in SOURCE.glob('2024????.csv.gz'))
    if source_days != expected:
        raise AssertionError('Raw source and market calendar do not match exactly')
    with ZipFile(OLD_BUNDLE) as bundle:
        old_bytes = bundle.read('data/minute_import_manifest.json')
        old = json.loads(old_bytes)
        old_summary = json.loads(bundle.read('run/extension_summary.json'))
    old_hash = hashlib.sha256(old_bytes).hexdigest()
    if old_summary['input_hashes']['minute_2024_manifest'] != old_hash:
        raise AssertionError('Archived experiment does not reference its archived minute manifest')
    old_by_day = {row['day']: row for row in old['rows']}
    if len(old_by_day) != old['days']:
        raise AssertionError('Earlier archive manifest is inconsistent')

    monthly = defaultdict(lambda: dict(days=0, stock_days=0, matched=0, bars=0))
    for row in rows:
        day = row['day']
        raw = SOURCE / f'{day.replace("-", "")}.csv.gz'
        compact = ARCHIVE / f'{day}.csv.zst'
        metadata = json.loads(raw.with_suffix('').with_suffix('.json').read_text())
        if (sha256(raw) != row['source_sha256'] or
                sha256(compact) != row['compact_sha256'] or
                metadata['sha256'] != row['source_sha256'] or
                metadata['bars'] != row['bars'] + row['minute_symbols'] or
                row['bars'] != 240 * row['minute_symbols']):
            raise AssertionError(f'{day}: raw/compact hash or bar count mismatch')
        if day in old_by_day and row != old_by_day[day]:
            raise AssertionError(f'{day}: existing September–December record changed')
        month = monthly[day[:7]]
        month['days'] += 1
        month['stock_days'] += row['joined_symbols']
        month['matched'] += row['price_volume_match']
        month['bars'] += row['bars']
    if len(old_by_day) != 80:
        raise AssertionError('Earlier four-month archive was not 80 days')
    panel = pd.read_pickle(R18 / 'panel.pkl')
    first_panel_month = panel.d0_date.min()[:7]
    first_full_stage1_month = str(pd.Period(first_panel_month, freq='M') + 8)
    first_full_b2_month = str(pd.Period(first_full_stage1_month, freq='M') + 8)
    if first_full_stage1_month != '2024-09' or first_full_b2_month != '2025-05':
        raise AssertionError('Training-window boundary changed; re-evaluate earliest test')

    def score_exists(month: str) -> bool:
        path = (EARLY / month / 'segments_forecast_scores.pkl' if month < '2025-01'
                else R18 / month / 'segments_forecast_scores.pkl')
        return path.exists()

    windows = []
    for month in pd.period_range('2025-01', '2025-05', freq='M'):
        name = str(month)
        required = [str(value) for value in pd.period_range(*window(name)['train'], freq='M')]
        windows.append(dict(test_month=name, train_months=required,
                            missing_causal_score_months=[m for m in required if not score_exists(m)]))
    if any(not row['missing_causal_score_months'] for row in windows[:-1]):
        raise AssertionError('An earlier B2 test month became eligible; run a new replay')
    if windows[-1]['missing_causal_score_months']:
        raise AssertionError('Previously tested 2025-05 is no longer eligible')

    receipt = dict(status='PASS', raw_days=len(source_days), archive_days=len(rows),
                   prior_days=len(old_by_day), added_days=len(rows) - len(old_by_day),
                   first_day=days[0], last_day=days[-1],
                   bars=sum(row['bars'] for row in rows),
                   stock_days=sum(row['minute_symbols'] for row in rows),
                   matched_stock_days=sum(row['price_volume_match'] for row in rows),
                   joined_stock_days=sum(row['joined_symbols'] for row in rows),
                   monthly=dict(sorted(monthly.items())),
                   prior_manifest_sha256=old_hash,
                   full_manifest_sha256=sha256(ARCHIVE / 'manifest.json'),
                   first_panel_month=first_panel_month,
                   first_full_stage1_month=first_full_stage1_month,
                   causal_window_check=windows,
                   earliest_same_rule_test_month=first_full_b2_month)
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

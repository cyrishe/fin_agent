"""Compare B2 monthly RF score gaps using frozen historical metrics.

Run on the research server. No model fitting or production writes.
"""

from __future__ import annotations

import csv
import itertools
import json
import math
import random
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

BASE = Path('/data/cyris/work/b2_2016_2020_r90_20261023')
R91 = BASE / 'audit_r91'
ARCHIVE = R91 / 'b2_2021_2024_metrics_r92.tgz'
DEPLOY = Path('/data/cyris/fin_agent_deploy/b2-20261015/assets/months')


def phase_metrics(raw: dict, phase: str) -> dict:
    top = raw.get('metrics', raw)
    return top[phase]['open']


rows: dict[str, dict] = {}
for path in sorted((BASE / 'daily2').glob('*/metrics.json')):
    month = path.parent.name
    if '2018-01' <= month <= '2021-12':
        raw = json.loads(path.read_text())
        rows[month] = dict(month=month, source='R90', raw=raw)

with tarfile.open(ARCHIVE) as archive:
    for member in archive.getmembers():
        if not member.name.endswith('/metrics.json'):
            continue
        month = Path(member.name).parent.name
        if '2022-01' <= month <= '2024-12':
            raw = json.load(archive.extractfile(member))
            if month in rows:
                raise ValueError(f'duplicate month {month}')
            rows[month] = dict(month=month, source=member.name.split('/')[2], raw=raw)

forecast_late = {}
with (R91 / 'later_model_months.csv').open(newline='') as f:
    for row in csv.DictReader(f):
        forecast_late[row['month']] = row
for path in sorted(DEPLOY.glob('*/manifest.json')):
    month = path.parent.name
    if '2025-01' <= month <= '2026-09':
        raw = json.loads(path.read_text())['source_metrics']
        rows[month] = dict(month=month, source='R18_deploy', raw=raw)

out = []
for month in sorted(rows):
    r = rows[month]
    raw = r['raw']
    assert raw['window']['test'] == month, (month, raw['window'])
    x = dict(month=month, year=int(month[:4]), source=r['source'])
    for phase in ('train', 'valid'):
        m = phase_metrics(raw, phase)
        x[f'{phase}_n'] = int(m['top3']['n'])
        x[f'{phase}_hits'] = int(m['top3']['hits'])
        x[f'{phase}_rate'] = float(m['top3']['rate'])
        x[f'{phase}_base'] = float(m['rate'])
        x[f'{phase}_ap'] = float(m['ap'])
    if month < '2025-01':
        m = phase_metrics(raw, 'forecast')
        x['forecast_n'] = int(m['top3']['n'])
        x['forecast_hits'] = int(m['top3']['hits'])
        x['forecast_rate'] = float(m['top3']['rate'])
        x['forecast_base'] = float(m['rate'])
        x['forecast_ap'] = float(m['ap'])
    else:
        f = forecast_late[month]
        x['forecast_n'] = int(f['top3_n'])
        x['forecast_hits'] = int(f['top3_hits'])
        x['forecast_rate'] = x['forecast_hits'] / x['forecast_n'] if x['forecast_n'] else math.nan
        x['forecast_base'] = float(f['base_rate'])
        x['forecast_ap'] = float(f['ap'])
    for phase in ('valid', 'forecast'):
        x[f'train_minus_{phase}'] = x['train_rate'] - x[f'{phase}_rate']
        x[f'train_minus_{phase}_ap'] = x['train_ap'] - x[f'{phase}_ap']
    out.append(x)

expected = [f'{year}-{m:02d}' for year in range(2018, 2027) for m in range(1, 13) if f'{year}-{m:02d}' <= '2026-09']
assert [x['month'] for x in out] == expected, (len(out), len(expected), sorted(set(expected) - set(rows)))


def summary(items):
    result = {'months': len(items)}
    for phase in ('train', 'valid', 'forecast'):
        hits = sum(x[f'{phase}_hits'] for x in items)
        n = sum(x[f'{phase}_n'] for x in items)
        result[f'{phase}_hits'] = hits
        result[f'{phase}_n'] = n
        result[f'{phase}_rate'] = hits/n if n else None
        result[f'{phase}_ap_mean'] = sum(x[f'{phase}_ap'] for x in items)/len(items)
    for phase in ('valid', 'forecast'):
        result[f'train_minus_{phase}_pooled'] = result['train_rate'] - result[f'{phase}_rate']
        result[f'train_minus_{phase}_month_mean'] = sum(x[f'train_minus_{phase}'] for x in items)/len(items)
        result[f'train_minus_{phase}_ap_mean'] = sum(x[f'train_minus_{phase}_ap'] for x in items)/len(items)
    result['valid_minus_forecast_pooled'] = result['valid_rate'] - result['forecast_rate']
    return result


yearly = {str(y): summary([x for x in out if x['year'] == y]) for y in range(2018, 2027)}
periods = {
    '2018-2020': summary([x for x in out if 2018 <= x['year'] <= 2020]),
    '2021-2026_09': summary([x for x in out if 2021 <= x['year'] <= 2026]),
    '2018-2021': summary([x for x in out if 2018 <= x['year'] <= 2021]),
    '2022-2026_09': summary([x for x in out if 2022 <= x['year'] <= 2026]),
    '2022-2025': summary([x for x in out if 2022 <= x['year'] <= 2025]),
}

# Exact year-block permutation: 2018–21 vs 2022–25, full years only.
perm = {}
for phase in ('valid', 'forecast'):
    years = list(range(2018, 2026))
    perm[phase] = {}
    for metric, field in (('top3', f'train_minus_{phase}_month_mean'), ('ap', f'train_minus_{phase}_ap_mean')):
        vals = {y: yearly[str(y)][field] for y in years}
        observed = sum(vals[y] for y in years[:4])/4 - sum(vals[y] for y in years[4:])/4
        differences = []
        for left in itertools.combinations(years, 4):
            a = set(left)
            differences.append(sum(vals[y] for y in a)/4 - sum(vals[y] for y in years if y not in a)/4)
        rng = random.Random(20261023)
        boots = []
        for _ in range(20000):
            left = rng.choices(years[:4], k=4)
            right = rng.choices(years[4:], k=4)
            boots.append(sum(vals[y] for y in left)/4 - sum(vals[y] for y in right)/4)
        boots.sort()
        perm[phase][metric] = dict(observed=observed, two_sided_p=sum(abs(x) >= abs(observed)-1e-12 for x in differences)/len(differences), permutations=len(differences), bootstrap_95=[boots[500], boots[19499]])

dest = R91 / 'model_gap_r92.csv'
with dest.open('w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(out[0]))
    writer.writeheader()
    writer.writerows(out)
stats = dict(periods=periods, yearly=yearly, year_permutation=perm, source_note='R90 2018-21; R60/R41/R35 2022-24; R18 deployed manifests and R91 forecast rescoring 2025-26')
(R91 / 'model_gap_r92.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(stats, ensure_ascii=False, indent=2))

"""Frozen, stratified historical CC sample; current public REST data-only evaluation."""
import argparse
from collections import Counter
import json
from pathlib import Path
import random

from eval_finance_rest_detail import ROOT, main as run_rest

SOURCE = ROOT / 'outputs/d4f10504-8df6-435e-9316-3d89b5fd1015/source_cases.json'
BASELINE = ROOT / 'outputs/d4f10504-8df6-435e-9316-3d89b5fd1015/finance_query_api_batch_results.json'
STRATA = {
    'stock.quote': 2, 'stock.moneyflow': 2, 'stock.pricevalue': 1,
    'stock.financial_3_table': 1, 'stock.margin': 1, 'stock.shareholder': 1,
    'stock.corporate_action': 1, 'index.quote': 1, 'index.constitution': 1,
    'industry.constitution': 1, 'plate.quote': 1, 'plate.moneyflow': 1,
    'plate.constitution': 1, 'fund.quote': 1, 'fund.basic_info': 1,
    'bond.quote': 1, 'hot_event.state': 1, 'multi_entry': 1,
}


def select(seed):
    source = json.loads(SOURCE.read_text())['cases']
    baseline = {c['case_id']: c for c in json.loads(BASELINE.read_text())['cases']}
    rng = random.Random(seed)
    selected = []
    for category, n in STRATA.items():
        pool = [c for c in source if (',' in c['dataview'] if category == 'multi_entry'
                                     else c['dataview'] == category)]
        for case in rng.sample(sorted(pool, key=lambda c: c['case_id']), n):
            old = baseline[case['case_id']]
            assert case['question'] == old['question']
            selected.append({'category': category, 'case': case, 'baseline': old})
    rng.shuffle(selected)
    assert len(selected) == len({c['case']['case_id'] for c in selected}) == 20
    return {'seed': seed, 'source': str(SOURCE), 'baseline_source': str(BASELINE),
            'selection': 'stratified random; no filtering by baseline success; no question changes',
            'category_counts': dict(Counter(c['category'] for c in selected)), 'cases': selected}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=20260907)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        assert manifest['seed'] == args.seed
    else:
        manifest = select(args.seed)
        with manifest_path.open('x') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    for item in manifest['cases']:
        c = item['case']
        print(c['case_id'], item['category'], c['question'], flush=True)
    if args.run:
        import sys
        sys.argv = [sys.argv[0], '--output-dir', str(args.output_dir), '--case-ids',
                    ','.join(c['case']['case_id'] for c in manifest['cases']),
                    '--timeout', '330', '--record-query-errors']
        run_rest()

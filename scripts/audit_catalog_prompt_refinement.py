"""Capture the actual model surfaces and executable contract of two checkouts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


CAPTURE = r'''
import hashlib, json
from pathlib import Path
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
from src.experiments.staged_data_protocol.phase2.call_structure import _compatible_arguments, _argument_contract
s = FinanceDataToolCatalogService()
p = s.load_raw_catalog()
packs, executable = {}, {}
for subject, views in p['subjects'].items():
    for view, definition in views.items():
        if view.startswith('_'): continue
        full = s.get_model_dataview(subject, view)
        for fn in full['functions']:
            op = fn['operation']
            packs[f'{subject}.{view}/{op}'] = s.get_model_dataview(subject, view, op)
        for fn in definition['api']:
            pattern = fn['api_name']
            concrete = pattern
            if '<field>' in concrete:
                field, methods = next(iter(definition['kd'].items()))
                concrete = concrete.replace('<field>', field).replace('<method>', methods[0])
            resolved = resolve_api(concrete)
            required, optional = _argument_contract(p['api_class_patterns'][fn['api_class']])
            executable[pattern] = {
                'runtime_type': resolved['type'], 'fields': resolved['view']['fields'],
                'required': sorted(required),
                'accepted': sorted(required | optional | _compatible_arguments(resolved)),
                **{k: resolved['view'].get(k) for k in ('kd','computed','aggregate_fields','value_domains')},
            }
files = ['src/scenarios/financial_qa/' + n for n in ('dsh_system.md','dsh_loop_policy.mjs','dsh_service.py','tools.py')]
providers = {str(f): hashlib.sha256(f.read_bytes()).hexdigest()
             for f in sorted(Path('src/experiments/staged_data_protocol/phase2').glob('*.py'))
             if f.name != 'catalog.py'}
print(json.dumps({
    'catalog_revision': s.catalog_revision(), 'executable_contracts': executable,
    'execution_files': providers, 'packs': packs,
    'routing': FinanceDataQueryCcTools()._catalog_routing_index(),
    'resident_prompt': Path(files[0]).read_text(),
    'prompt_source_hashes': {f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files},
}, ensure_ascii=False))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='A/B evaluation output root')
    args = parser.parse_args()
    root = args.root.resolve()
    snapshots = {}
    for phase in ('before', 'after'):
        checkout = root / phase / 'dsh-workspace'
        snapshots[phase] = json.loads(subprocess.check_output(
            [sys.executable, '-c', CAPTURE], cwd=checkout, text=True))
        (root / phase / 'catalog-surfaces.json').write_text(
            json.dumps(snapshots[phase], ensure_ascii=False, indent=2) + '\n')
    before, after = snapshots['before'], snapshots['after']
    summary = {
        'executable_contracts_unchanged': before['executable_contracts'] == after['executable_contracts'],
        'execution_files_unchanged': before['execution_files'] == after['execution_files'],
        'changed_execution_contracts': [name for name, contract in before['executable_contracts'].items()
            if contract != after['executable_contracts'].get(name)],
        'methods': len(after['executable_contracts']),
        'characters': {phase: {
            'resident': len(s['resident_prompt']), 'routing': len(s['routing']),
            'execution_packs': sum(len(json.dumps(p, ensure_ascii=False, separators=(',', ':')))
                                   for p in s['packs'].values()),
        } for phase, s in snapshots.items()},
    }
    (root / 'catalog-audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    results = {}
    for phase in ('before', 'after'):
        path = root / phase / 'dsh/results.jsonl'
        if not path.is_file():
            continue
        rows = []
        for line in path.read_text().splitlines():
            row = json.loads(line)
            response = row.get('response', {})
            finance = response.get('financial_qa', {})
            calls = finance.get('tool_calls', [])
            steps = finance.get('execution_steps', [])
            usage = response.get('llm_usage', {})
            rows.append({
                'case_id': row['case_id'], 'question': row['question'],
                'seconds': round(row['wall_duration_ms'] / 1000, 2),
                'llm_requests': len(finance.get('llm_step_usages', [])),
                'llm_seconds': round(sum(x.get('duration_ms', 0) for x in steps if x.get('kind') == 'llm') / 1000, 2),
                'api_seconds': round(sum(x.get('api_execution_ms', 0) for x in calls) / 1000, 2),
                'tokens_with_cache': usage.get('total_tokens', 0) + usage.get('cache_read_tokens', 0),
                'static_failures': sum(bool(x.get('validation_errors')) for x in calls),
                'catalog_calls': [{k:x.get(k) for k in ('subject','dataview','operation')}
                                  for x in calls if x.get('tool') == 'read_finance_catalog'],
                'requests': [x['request'] for x in calls if x.get('request')],
                'api_rows': [(x['api'],x['row_count']) for x in response.get('data',{}).get('results',[])],
                'error': row.get('error') or finance.get('error') or '',
            })
        results[phase] = rows
    if results:
        comparison = {'cases': results, 'totals': {
            phase: {k: round(sum(row[k] for row in rows), 2) for k in
                    ('seconds','llm_requests','llm_seconds','api_seconds','tokens_with_cache','static_failures')}
            for phase, rows in results.items()
        }}
        (root / 'comparison.json').write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

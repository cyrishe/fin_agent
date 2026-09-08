"""Emit an apply_patch patch migrating catalog examples; never rewrite files.

This is an offline source migration, not a model-request conversion layer.
"""
import difflib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.experiments.staged_data_protocol.phase2.call_structure import _parse_filter_node
from src.experiments.staged_data_protocol.phase2 import python_filter as pf

PATH = ROOT / 'src/tools/finance_data/catalog/api_view_catalog.json'
old = PATH.read_text()
data = json.loads(old)
if data.get('version') in {'2026-09-07-python-filter-v1', '2026-09-07-python-filter-v2'}:
    print('Catalog examples are already migrated; no changes.', file=sys.stderr)
    sys.exit(0)
data['version'] = '2026-09-07-python-filter-v1'
changed = 0
skipped = []

def convert_filter(raw):
    tree = _parse_filter_node(raw)
    def leaf(p):
        if p['operator'] == 'like':
            value = str(p['value'])
            if '%' not in value and '_' not in value:
                # Existing bare LIKE examples are report institution contains.
                p = {**p, 'operator': 'contains'}
            elif value.startswith('%') and value.endswith('%') and '%' not in value[1:-1] and '_' not in value:
                p = {**p, 'operator': 'contains', 'value': value[1:-1]}
            else:
                raise ValueError('nontrivial legacy LIKE example')
        return p
    return pf.to_source(pf.map_predicates(tree, leaf))

def convert_text(value):
    global changed
    marker = 'filter = "'
    pos = value.find(marker)
    if pos < 0:
        return value
    start = pos + len(marker)
    end = start
    escaped = False
    while end < len(value):
        char = value[end]
        if escaped:
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == '"':
            break
        end += 1
    raw = value[start:end]
    try:
        new = convert_filter(raw)
        pf.parse_python_filter(new)
    except ValueError as exc:
        skipped.append([raw, str(exc)])
        return value
    # JSON-quoted outer filter safely escapes literal double quotes/backslashes.
    result = value[:start-1] + json.dumps(new, ensure_ascii=False) + value[end+1:]
    changed += result != value
    return result

def walk(value):
    if isinstance(value, dict):
        return {key: walk(child) for key, child in value.items()}
    if isinstance(value, list):
        return [walk(child) for child in value]
    return convert_text(value) if isinstance(value, str) else value

data = walk(data)
data['api_class_patterns']['kday_metric']['rules'].append('行情 kd_pct_sum 是窗口首尾收盘价相对涨幅，不是每日 pct 简单求和；其他字段按对应方法或已定义的特殊逻辑计算。身份条件限定计算对象，value/k/end_date 等条件筛选计算结果，不先截断原始窗口。')
data['api_class_patterns']['stock_quote_kday_metric']['rules'].append('日线 kd_pct_sum 是窗口首尾收盘价相对涨幅，不是每日 pct 简单求和。身份条件限定计算对象，value/change_pct 等结果条件在完整窗口计算后筛选。')
new = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
patch = '\n'.join('@@' if line.startswith('@@ ') else line for line in list(difflib.unified_diff(old.splitlines(), new.splitlines(), n=3, lineterm=''))[2:])
print('*** Begin Patch\n*** Update File: '+str(PATH)+'\n'+patch+'\n*** End Patch')
print(json.dumps({'changed': changed, 'skipped': skipped}, ensure_ascii=False), file=sys.stderr)

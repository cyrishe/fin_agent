"""Independent October 2025 control for the 2024-minute extension path."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_limit_rebound_short_mixed_r12 import sha
from scripts.experiment_limit_rebound_segments_r18 import OUT as R18
import scripts.experiment_limit_rebound_shape_r19 as r19
import scripts.extend_limit_rebound_2024_minutes as ext


MONTH = '2025-10'
ORIGINAL = Path('outputs/stock_automl/limit_rebound_shape_r19_20261015')
OUT = Path('outputs/stock_automl/limit_rebound_2025_oct_control_20261018')
OUT.mkdir(parents=True, exist_ok=True)
ext.OUT = OUT
ext.MONTHS = [MONTH]
panel = pd.read_pickle(R18 / 'panel.pkl')
calendar = json.loads((R18 / 'calendar.json').read_text())
ext.enrich(ext.candidate_rows(panel, calendar))
r19.OUT = OUT
r19.TEST_MONTHS = [MONTH]
r19.choose()
r19.report()

old_rule = json.loads((ORIGINAL / MONTH / 'rule_frozen.json').read_text())
new_rule = json.loads((OUT / MONTH / 'rule_frozen.json').read_text())
if old_rule['selected'] != new_rule['selected']:
    raise AssertionError('October rule changed')
old_metrics = [r for r in json.loads((ORIGINAL / 'monthly_metrics.json').read_text())
               if r['月份'] == MONTH and r['集合'] == 'test']
new_metrics = [r for r in json.loads((OUT / 'monthly_metrics.json').read_text())
               if r['月份'] == MONTH and r['集合'] == 'test']
if old_metrics != new_metrics:
    raise AssertionError('October test metrics changed')

cols = ['symbol','d0_date','d1_date','score','d0_rank','audit_ok','reason','eligible',
        'high5','low5','body5','pullback5','position5','below5','whole5','touch5',
        'shape','fill','entry_time','entry','ret2','ret3','ret4','strategy',
        'label_3','rule_pass','selected_trade']
old = pd.read_pickle(ORIGINAL / 'test_top1.pkl')
old = old[old.d0_date.str.startswith(MONTH)][cols].sort_values('d0_date').reset_index(drop=True)
new = pd.read_pickle(OUT / 'test_top1.pkl')[cols].sort_values('d0_date').reset_index(drop=True)
if len(old) != len(new):
    raise AssertionError('October candidate count changed')
for col in cols:
    a,b = old[col],new[col]
    if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
        if not np.allclose(a.to_numpy(dtype=float),b.to_numpy(dtype=float),rtol=0,atol=1e-10,equal_nan=True):
            raise AssertionError(f'October column changed: {col}')
    elif not a.fillna('<missing>').equals(b.fillna('<missing>')):
        raise AssertionError(f'October column changed: {col}')

result = {
    'month': MONTH,
    'original_run': str(ORIGINAL),
    'new_control_run': str(OUT),
    'r18_panel_sha256': sha(R18 / 'panel.pkl'),
    'new_control_test_sha256': sha(OUT / 'test_top1.pkl'),
    'candidates': len(new),
    'minute_audit_pass': int(new.audit_ok.eq(True).sum()),
    'rule': new_rule['selected'],
    'test_metrics': new_metrics,
    'columns_compared': cols,
    'rules_equal': True,
    'test_metrics_equal': True,
    'candidate_fields_equal': True,
}
(OUT / 'control_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('test_metrics','columns_compared')},ensure_ascii=False,indent=2))

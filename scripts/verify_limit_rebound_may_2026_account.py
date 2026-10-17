"""Recompute one original R23C account month from the frozen inputs."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_limit_rebound_shape_top3_r20 import select
from scripts.experiment_limit_rebound_shape_r19 import mask
from scripts.replay_limit_rebound_body8_r21 import body_keep
from scripts.replay_limit_rebound_cash_r23 import replay


source = pd.read_pickle('outputs/stock_automl/limit_rebound_body8_nofallback_r21b_20261015/all_candidates.pkl')
source = source[source.d0_date.ge('2026-01-01')].copy()
rule_root = Path('outputs/stock_automl/limit_rebound_shape_r19_20261015')
rules = {r['month']: r['selected'] for r in json.loads((rule_root / 'all_rules_frozen.json').read_text())}
source['shape_pass'] = False
for month, rule in rules.items():
    selected = source.d0_date.str.startswith(month)
    if selected.any():
        source.loc[selected, 'shape_pass'] = source.loc[selected, 'eligible'] & mask(source.loc[selected], rule)
picked = select(source, 3, True)
picked = picked[body_keep(picked.body5)].copy()
calendar = json.loads(Path('outputs/stock_automl/limit_rebound_segments_r18_20261015/calendar.json').read_text())
account, monthly, daily, ledger, excluded = replay(picked, calendar, fee_rate=.001)
assert not excluded
original = Path('outputs/stock_automl/limit_rebound_fee001_r23c_20261015')
old_month = next(r for r in json.loads((original / 'monthly.json').read_text()) if r['month'] == '2026-05')
new_month = next(r for r in monthly if r['month'] == '2026-05')
assert old_month == new_month
old_daily = pd.read_pickle(original / 'daily_nav.pkl')
for col in ['date','buy','sales','cash','market_value','nav','return_','drawdown','fees','positions']:
    a = old_daily[old_daily.date.str.startswith('2026-05')][col].reset_index(drop=True)
    b = daily[daily.date.str.startswith('2026-05')][col].reset_index(drop=True)
    if pd.api.types.is_numeric_dtype(a):
        assert np.allclose(a,b,rtol=0,atol=1e-7),col
    else:
        assert a.equals(b),col
old_ledger = pd.read_pickle(original / 'ledger.pkl')
keys = ['symbol','d0_date','entry_date','entry_time','buy','exit_date','sell','pnl','return_']
a = old_ledger[old_ledger.entry_date.str.startswith('2026-05')][keys].reset_index(drop=True)
b = ledger[ledger.entry_date.str.startswith('2026-05')][keys].reset_index(drop=True)
pd.testing.assert_frame_equal(a,b,check_dtype=False,atol=1e-7,rtol=0)
result = {'month':'2026-05','rule_2026_05':rules['2026-05'],'entries':len(a),'trading_days':len(old_daily[old_daily.date.str.startswith('2026-05')]),
          'original_month':old_month,'recomputed_month':new_month,
          'monthly_equal':True,'daily_equal':True,'ledger_equal':True}
Path('outputs/stock_automl/limit_rebound_2025_oct_control_20261018/account_2026_may_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'month':'2026-05','entries':len(a),'monthly_return':new_month['return_'],
                  'month_end_nav':new_month['closing_nav'],'monthly_equal':True,'daily_equal':True,'ledger_equal':True},ensure_ascii=False))
